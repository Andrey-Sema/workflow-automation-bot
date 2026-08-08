"""Turns a validated order dict into a short, human-checkable summary:
who it's for, how much the handwritten form says vs. what the pipeline
calculated, and exactly which lines need a human's attention before the
naряд gets typed into 1C.

This is the data behind the Telegram confirmation message the user asked
for: "краткая сводка по ФИО, сумме которая в бланках и сумме которая
получилась при парсинге, и вывод строк которые вызывают конфликт".
"""

from __future__ import annotations

from dataclasses import dataclass, field

SUM_MISMATCH_TOLERANCE_RATIO = 0.01
SENTINEL_BIRTH_DATE = "01.01.1920"


@dataclass
class OrderSummary:
    deceased_fio: str
    customer_fio: str
    cemetery: str
    handwritten_total: int
    calculated_total: int
    sum_mismatch: bool
    sum_diff: int
    missing_birth_date: bool
    unmapped_items: list[str] = field(default_factory=list)
    review_items: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    services_count: int = 0
    goods_count: int = 0
    transport_count: int = 0

    @property
    def has_conflicts(self) -> bool:
        return (
            self.sum_mismatch
            or self.missing_birth_date
            or bool(self.unmapped_items)
            or bool(self.review_items)
        )

    @property
    def can_auto_enter(self) -> bool:
        """Whether onec_order_entry can attempt every line automatically.

        Unmapped items always block full auto-entry (see
        `OneCOrderEntryBot.enter_item`); a sum mismatch, missing birth date
        or low-confidence match is a data-quality conflict to show the
        operator but doesn't by itself stop 1C typing.
        """
        return not self.unmapped_items


def build_order_summary(order_data: dict) -> OrderSummary:
    deceased = order_data.get("deceased", {}) or {}
    customer = order_data.get("customer", {}) or {}
    handwritten = order_data.get("handwritten_total", 0) or 0
    calculated = order_data.get("calculated_total", 0) or 0

    sum_mismatch = False
    if handwritten > 0:
        tolerance = max(1, handwritten * SUM_MISMATCH_TOLERANCE_RATIO)
        sum_mismatch = abs(handwritten - calculated) > tolerance

    unmapped: list[str] = []
    review: list[str] = []
    services = order_data.get("services", []) or []
    goods = order_data.get("goods", []) or []
    transport = order_data.get("transport", []) or []
    for item in (*services, *goods, *transport):
        if not item.get("1c_search_key"):
            unmapped.append(item.get("name", "?"))
        elif item.get("1c_match_confidence") == "low":
            review.append(item.get("name", "?"))

    return OrderSummary(
        deceased_fio=deceased.get("fio") or "НЕ УКАЗАНО",
        customer_fio=customer.get("fio") or "",
        cemetery=deceased.get("cemetery") or "",
        handwritten_total=handwritten,
        calculated_total=calculated,
        sum_mismatch=sum_mismatch,
        sum_diff=handwritten - calculated,
        missing_birth_date=deceased.get("birth_date") == SENTINEL_BIRTH_DATE,
        unmapped_items=unmapped,
        review_items=review,
        warnings=list(order_data.get("warnings", [])),
        services_count=len(services),
        goods_count=len(goods),
        transport_count=len(transport),
    )
