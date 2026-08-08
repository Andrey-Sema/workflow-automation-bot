"""Structural + semantic validation for `data/catalog.json`.

`config._load_catalog()` used to only catch "file missing" and "not JSON".
Everything past that was trusted blindly, so a catalog that parsed but was
*wrong* (a price as a string, a blank `search_key`, two different items
sharing one price in the same category) failed much later and much less
obviously — either as a `KeyError` deep inside `agent_logic`, or, worse,
silently: the mapping picked the wrong nomenclature line and the bot typed
a confidently wrong item into 1C.

This module makes those failures visible at load time and on demand:

* **errors** — the catalog is structurally unusable; the affected section is
  dropped rather than allowed to crash the pipeline mid-order.
* **warnings** — the catalog loads and works, but something in it is
  ambiguous or unreachable and a human should look at it. Ambiguity here is
  not academic: 1C entry is not undoable, so "two items share a price and
  the code has to guess" is exactly the class of problem worth surfacing
  before an operator taps ✅.

Run standalone against any catalog file:

    python -m src.catalog_schema data/catalog.sample.json
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

# Categories `agent_logic` can actually route a line item into. A category
# present in catalog_1c_mapping but absent here is dead data — it will never
# be consulted, so every item that should have used it stays unmapped.
KNOWN_MAPPING_CATEGORIES = frozenset(
    {"services", "coffins", "wreaths", "crosses", "baskets", "plaques", "towels"}
)

ERROR = "error"
WARNING = "warning"


# --------------------------------------------------------------------------
# Structural schema
# --------------------------------------------------------------------------

class MappingEntry(BaseModel):
    """One 1C nomenclature line: what to type, and how far down the dropdown."""

    model_config = ConfigDict(extra="ignore")

    name: str
    price: int
    search_key: str = ""
    dropdown_index: int = 0
    # Alternative wordings that should map to this entry. Use for lines
    # whose form wording shares nothing with the 1C name — e.g. the form
    # says "Послуги персоналу для поховання", 1C calls it "снос (Галстук)".
    # Without an alias such a line matches on price alone and is flagged
    # low-confidence for the operator to check on every single order.
    aliases: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name не может быть пустым")
        return v

    @field_validator("price")
    @classmethod
    def _price_not_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("price не может быть отрицательным")
        return v

    @field_validator("dropdown_index")
    @classmethod
    def _index_not_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("dropdown_index не может быть отрицательным")
        return v


class DiggingRules(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kopka_person_count: int = Field(default=4, gt=0)
    base_price_per_person: int = Field(default=1925, gt=0)
    towel_prices: list[int] = Field(default_factory=lambda: [1400])

    @field_validator("towel_prices")
    @classmethod
    def _towels_positive(cls, v: list[int]) -> list[int]:
        if any(p <= 0 for p in v):
            raise ValueError("towel_prices должны быть положительными")
        return v


class PersonnelPackage(BaseModel):
    """A bundled price: "6200" means 4 people of «снос» at 1550 each."""

    model_config = ConfigDict(extra="ignore")

    name: str
    qty: int = Field(gt=0)


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tariffs: dict[str, int] = Field(default_factory=dict)
    digging_rules: DiggingRules = Field(default_factory=DiggingRules)
    personnel_packages: dict[str, PersonnelPackage] = Field(default_factory=dict)
    known_unit_prices: dict[str, list[int]] = Field(default_factory=dict)
    catalog_1c_mapping: dict[str, list[MappingEntry]] = Field(default_factory=dict)
    services_list: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogIssue:
    level: str
    code: str
    message: str

    def __str__(self) -> str:
        icon = "❌" if self.level == ERROR else "⚠️"
        return f"{icon} [{self.code}] {self.message}"


@dataclass
class CatalogReport:
    issues: list[CatalogIssue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def add(self, level: str, code: str, message: str) -> None:
        self.issues.append(CatalogIssue(level, code, message))

    @property
    def errors(self) -> list[CatalogIssue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> list[CatalogIssue]:
        return [i for i in self.issues if i.level == WARNING]

    @property
    def ok(self) -> bool:
        """No structural errors. Warnings are informational and don't block."""
        return not self.errors

    def as_text(self, limit: int = 40) -> str:
        if not self.issues:
            return "✅ Каталог валиден, конфликтов не найдено."
        lines = [str(i) for i in self.issues[:limit]]
        if len(self.issues) > limit:
            lines.append(f"… и ещё {len(self.issues) - limit}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _normalize(name: str) -> str:
    return " ".join(str(name).lower().split())


def _check_structure(data: dict, report: CatalogReport) -> CatalogModel:
    """Parses into the schema, degrading section-by-section instead of
    all-or-nothing: a broken `wreaths` list must not take the (fine)
    `tariffs` and `services_list` down with it, because a partly-usable
    catalog still lets operators work while someone fixes the JSON."""
    try:
        return CatalogModel(**data)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            report.add(ERROR, "STRUCTURE", f"{loc}: {err['msg']}")

    salvaged: dict[str, Any] = {}
    for section in CatalogModel.model_fields:
        if section not in data:
            continue
        try:
            CatalogModel(**{section: data[section]})
        except ValidationError:
            report.add(ERROR, "SECTION_DROPPED", f"секция '{section}' отброшена — не проходит схему")
        else:
            salvaged[section] = data[section]

    try:
        return CatalogModel(**salvaged)
    except ValidationError:  # pragma: no cover - each section already validated alone
        return CatalogModel()


def _check_mapping_conflicts(model: CatalogModel, report: CatalogReport) -> None:
    seen_keys: dict[tuple[str, int], list[str]] = defaultdict(list)

    for category, entries in model.catalog_1c_mapping.items():
        if category not in KNOWN_MAPPING_CATEGORIES:
            report.add(
                WARNING, "UNKNOWN_CATEGORY",
                f"категория '{category}' не разбирается кодом (см. agent_logic.CATEGORY_KEYWORDS) "
                f"— её {len(entries)} позиций никогда не будут найдены",
            )

        by_price: dict[int, list[str]] = defaultdict(list)
        by_name = Counter(_normalize(e.name) for e in entries)

        for entry in entries:
            by_price[entry.price].append(entry.name)
            if not entry.search_key.strip():
                report.add(
                    WARNING, "BLANK_SEARCH_KEY",
                    f"[{category}] '{entry.name}': пустой search_key — позицию нельзя ввести в 1С "
                    "автоматически, она всегда уйдёт в конфликты",
                )
            else:
                seen_keys[(entry.search_key, entry.dropdown_index)].append(f"{category}/{entry.name}")

        for name, count in by_name.items():
            if count > 1:
                report.add(WARNING, "DUPLICATE_NAME", f"[{category}] имя встречается {count} раза: '{name}'")

        for price, names in sorted(by_price.items()):
            if len(names) > 1:
                report.add(
                    WARNING, "DUPLICATE_PRICE",
                    f"[{category}] цена {price} грн у {len(names)} позиций ({', '.join(names)}) "
                    "— подбор по цене неоднозначен, решает совпадение названия",
                )

    for (key, index), owners in sorted(seen_keys.items()):
        if len(owners) > 1:
            report.add(
                WARNING, "DROPDOWN_COLLISION",
                f"search_key '{key}' + dropdown_index {index} указывают на разные позиции "
                f"({', '.join(owners)}) — в 1С это одна строка выпадающего списка, "
                "верной может быть только одна",
            )


def _check_cross_references(model: CatalogModel, report: CatalogReport) -> None:
    """Rules that only make sense when two sections are read together."""
    tariffs = model.tariffs
    all_prices = {e.price for entries in model.catalog_1c_mapping.values() for e in entries}

    for total_str, package in model.personnel_packages.items():
        try:
            total = int(total_str)
        except (TypeError, ValueError):
            report.add(ERROR, "PACKAGE_KEY", f"ключ personnel_packages '{total_str}' — не целое число")
            continue
        if total % package.qty:
            report.add(
                WARNING, "PACKAGE_MATH",
                f"пакет {total} грн не делится нацело на {package.qty} чел. "
                f"('{package.name}') — цена за человека получится дробной",
            )
            continue
        per_person = total // package.qty
        if tariffs and per_person not in tariffs.values():
            report.add(
                WARNING, "PACKAGE_TARIFF",
                f"пакет {total} грн = {per_person} грн/чел ('{package.name}'), "
                f"но такого тарифа нет в tariffs {sorted(set(tariffs.values()))}",
            )

    # Both checks below ask "is there a 1C item for this?", which is only a
    # meaningful question once there is a mapping section to look in. On a
    # catalog that has no `catalog_1c_mapping` at all, every item trivially
    # has no match, and reporting that for each one is noise that buries the
    # one real message: the mapping section is missing.
    has_mapping = bool(all_prices)

    for price in model.digging_rules.towel_prices if has_mapping else ():
        if price not in all_prices:
            report.add(
                WARNING, "TOWEL_UNMAPPED",
                f"towel_prices содержит {price} грн, но позиции с такой ценой нет в catalog_1c_mapping "
                "— отделённый от закопки рушник останется без соответствия в 1С",
            )

    mapped_names = {
        _normalize(e.name) for entries in model.catalog_1c_mapping.values() for e in entries
    }
    for known_name in model.known_unit_prices if has_mapping else ():
        norm = _normalize(known_name)
        if not any(norm in mapped for mapped in mapped_names):
            report.add(
                WARNING, "KNOWN_PRICE_UNMAPPED",
                f"'{known_name}' есть в known_unit_prices, но не имеет позиции в catalog_1c_mapping "
                "— количество починится, а в 1С позиция уйдёт в ручной ввод",
            )

    if not model.services_list:
        report.add(WARNING, "EMPTY_SERVICES_LIST", "services_list пуст — нормализация названий услуг отключена")


def validate_catalog(data: dict) -> CatalogReport:
    """Validates a parsed catalog dict. Never raises — always returns a report."""
    report = CatalogReport()

    if not isinstance(data, dict):
        report.add(ERROR, "NOT_AN_OBJECT", f"catalog.json должен быть JSON-объектом, а не {type(data).__name__}")
        return report
    if not data:
        report.add(ERROR, "EMPTY", "catalog.json пуст")
        return report

    model = _check_structure(data, report)
    _check_mapping_conflicts(model, report)
    _check_cross_references(model, report)

    report.stats = {
        "services_list": len(model.services_list),
        "mapping_categories": len(model.catalog_1c_mapping),
        "mapping_items": sum(len(v) for v in model.catalog_1c_mapping.values()),
        "personnel_packages": len(model.personnel_packages),
        "tariffs": len(model.tariffs),
        "errors": len(report.errors),
        "warnings": len(report.warnings),
    }
    return report


def validate_catalog_file(path) -> CatalogReport:
    report = CatalogReport()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        report.add(ERROR, "NOT_FOUND", f"файл не найден: {path}")
        return report
    except json.JSONDecodeError as e:
        report.add(ERROR, "BAD_JSON", f"невалидный JSON: {e}")
        return report
    return validate_catalog(data)


def _main() -> int:
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "data/catalog.json"
    report = validate_catalog_file(path)

    print(f"📖 {path}")
    if report.stats:
        print("   " + " · ".join(f"{k}={v}" for k, v in report.stats.items()))
    print(report.as_text(limit=200))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(_main())
