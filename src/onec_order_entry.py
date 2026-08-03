"""Types a validated order into the 1C "наряд" form over the RDP session.

This is new orchestration code, not a rewrite of the proven low-level
primitives: tab switching still goes through
`src.win_1c_bot.click_tab_by_image` completely unchanged, and all typing
uses the same `pyautogui`-based technique already used there (not the
`keyboard` library, which needs root/uinput and doesn't work cleanly
against the in-container Xvfb display).

Field-to-field navigation (how many Tabs between Nomenclature -> Quantity
-> Price, whether Price auto-fills from the catalog, etc.) depends on the
exact 1C form layout, which we don't have pixel/tab-order data for yet.
Those knobs are collected in `FieldNav` below — an operator with access to
the real 1C window should confirm/adjust them once, with
`tools/calibrate_1c_typing.py` as a starting point, and everything else in
this module keeps working unchanged.

Every action always runs through `_act()`, which:
  * no-ops (just logs) when `dry_run=True` (the default — see
    `Settings.onec_dry_run`), so this module is safe to unit test and safe
    to leave on until an operator has actually verified the field
    navigation against the real 1C window;
  * raises `OneCInputError` with a friendly message on any pyautogui
    failure instead of leaving the order half-typed with no explanation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import StrEnum

from src.errors import OneCInputError, RdpConnectionError
from src.rdp_status import is_rdp_connected
from src.settings import Settings, get_settings
from src.win_1c_bot import click_tab_by_image

logger = logging.getLogger(__name__)


class OrderCategory(StrEnum):
    SERVICES = "services"
    GOODS = "goods"
    TRANSPORT = "transport"


# Which 1C tab (template image in data/templates/) each order category is
# entered under. Adjust if the real form groups them differently.
CATEGORY_TAB_MAP: dict[OrderCategory, str] = {
    OrderCategory.SERVICES: "tab_uslugi.png",
    OrderCategory.GOODS: "tab_sklad.png",
    OrderCategory.TRANSPORT: "tab_prochie.png",
}


@dataclass(frozen=True)
class FieldNav:
    """1C form field-navigation knobs — confirm against the real window."""

    tabs_after_name_select: int = 1   # Tab presses from the nomenclature dropdown to the Quantity field
    tabs_after_quantity: int = 1      # Tab presses from Quantity to Price (skip if price auto-fills)
    price_autofills: bool = True      # If True, price is not typed — 1C fills it from the catalog item
    tabs_to_commit_row: int = 1       # Tab/Enter presses to commit the row and land on a new one


try:
    import pyautogui
except ImportError:  # pragma: no cover - only missing outside the automation container
    pyautogui = None


def _act(description: str, fn, *, dry_run: bool) -> None:
    if dry_run:
        logger.info(f"🧪 [DRY-RUN] {description}")
        return
    if pyautogui is None:
        raise OneCInputError("pyautogui недоступен в этом окружении")
    try:
        fn()
    except Exception as e:
        raise OneCInputError(f"Действие '{description}' провалилось: {e}") from e


class OneCOrderEntryBot:
    """Types services/goods/transport line items into the open 1C наряд."""

    def __init__(self, settings: Settings | None = None, field_nav: FieldNav | None = None) -> None:
        self.settings = settings or get_settings()
        self.field_nav = field_nav or FieldNav()
        self.dry_run = self.settings.onec_dry_run
        self._active_tab: str | None = None

    def _pause(self) -> None:
        if not self.dry_run:
            time.sleep(self.settings.onec_action_pause)

    def _switch_tab(self, category: OrderCategory) -> None:
        template = CATEGORY_TAB_MAP[category]
        if self._active_tab == template:
            return

        def do_switch() -> None:
            ok = click_tab_by_image(template, confidence=self.settings.onec_template_confidence)
            if not ok:
                raise RuntimeError(f"вкладка {template} не найдена на экране")

        _act(f"Переключение на вкладку {template}", do_switch, dry_run=self.dry_run)
        self._active_tab = template
        self._pause()

    def _type(self, text: str) -> None:
        _act(
            f"Ввод текста: {text!r}",
            lambda: pyautogui.write(text, interval=self.settings.onec_keystroke_delay),
            dry_run=self.dry_run,
        )

    def _press(self, key: str, times: int = 1) -> None:
        if times <= 0:
            return
        _act(
            f"Нажатие '{key}' x{times}",
            lambda: pyautogui.press(key, presses=times, interval=self.settings.onec_keystroke_delay),
            dry_run=self.dry_run,
        )

    def enter_item(self, category: OrderCategory, item: dict) -> None:
        """Types a single service/good/transport line item into 1C.

        Deliberately does NOT fall back to typing the raw item `name` when
        `1c_search_key` is missing: an item without a confirmed catalog
        mapping is exactly the kind of ambiguous line that should be
        flagged as a conflict for a human to check (see
        `src.order_conflicts`), not guessed at with a blind keystroke that
        could select the wrong nomenclature entry in 1C.
        """
        search_key = item.get("1c_search_key", "")
        down_presses = int(item.get("1c_down_presses", 0) or 0)
        quantity = item.get("quantity", 1)
        unit_price = item.get("unit_price_for_1c", item.get("price", 0))

        if not search_key:
            raise OneCInputError(
                f"У позиции '{item.get('name', '?')}' нет подтверждённого соответствия в каталоге 1С "
                "— внеси её вручную."
            )

        self._switch_tab(category)

        self._type(search_key)
        self._pause()
        self._press("down", down_presses)
        self._press("enter")
        self._pause()

        self._press("tab", self.field_nav.tabs_after_name_select)
        self._type(str(quantity))

        if not self.field_nav.price_autofills:
            self._press("tab", self.field_nav.tabs_after_quantity)
            self._type(str(unit_price))

        self._press("enter", self.field_nav.tabs_to_commit_row)
        self._pause()

        logger.info(f"✅ Внесено в 1С: {item.get('name')} x{quantity} = {item.get('price')} грн")

    def enter_order(self, order_data: dict) -> list[str]:
        """Types every services/goods/transport line from a validated order.

        Returns the list of item names successfully entered. Stops and
        raises `OneCInputError` on the first failure — a half-typed наряд
        is exactly the situation the confirm/cancel step in the Telegram
        bot exists to avoid, so callers should treat any exception here as
        "stop and let a human look at the 1C window".
        """
        # Blind keystrokes into a screen that isn't actually showing 1C
        # (RDP dropped, container mid-reconnect, ...) could land anywhere —
        # a stray "Alt+F4" onto the wrong window is a much worse outcome
        # than just refusing to type. Dry-run never touches the real
        # screen, so it's exempt: this check exists purely to protect real
        # automation.
        if not self.dry_run and not is_rdp_connected(self.settings):
            raise RdpConnectionError()

        entered: list[str] = []
        categories: list[tuple[OrderCategory, list[dict]]] = [
            (OrderCategory.SERVICES, order_data.get("services", [])),
            (OrderCategory.GOODS, order_data.get("goods", [])),
            (OrderCategory.TRANSPORT, order_data.get("transport", [])),
        ]
        for category, items in categories:
            for item in items:
                self.enter_item(category, item)
                entered.append(item.get("name", "?"))
        return entered
