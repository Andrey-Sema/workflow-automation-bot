"""Tests for the operator-facing confirmation message.

Two things matter here. It is the last thing a human reads before an
irreversible 1C entry, so every conflict must actually appear in the text.
And every dynamic value in it comes from a photographed handwritten form
via an LLM — untrusted input rendered into Telegram's HTML parse mode.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.order_conflicts import SENTINEL_BIRTH_DATE, OrderSummary, build_order_summary
from src.summary_formatting import format_order_summary


def make_summary(**overrides) -> OrderSummary:
    base = {
        "deceased_fio": "Іваненко Іван", "customer_fio": "Іваненко Галина",
        "cemetery": "Западное", "handwritten_total": 1000, "calculated_total": 1000,
        "sum_mismatch": False, "sum_diff": 0, "missing_birth_date": False,
    }
    base.update(overrides)
    return OrderSummary(**base)


# --- каждый конфликт обязан быть виден ---

def test_matching_totals_are_reported_as_clean():
    text = format_order_summary(make_summary(), order_id="A1B2C3D4")
    assert "Суммы совпадают" in text
    assert "Конфликтов не найдено" in text


def test_sum_mismatch_is_shown_with_a_sign():
    text = format_order_summary(
        make_summary(sum_mismatch=True, sum_diff=650, calculated_total=350), order_id="A1"
    )
    assert "+650" in text
    assert "Есть конфликты" in text


def test_negative_diff_is_shown_without_a_double_sign():
    text = format_order_summary(make_summary(sum_mismatch=True, sum_diff=-650), order_id="A1")
    assert "-650" in text
    assert "+-" not in text


def test_unrecognized_form_total_is_called_out():
    text = format_order_summary(make_summary(handwritten_total=0), order_id="A1")
    assert "не распознана" in text


def test_missing_birth_date_is_shown():
    text = format_order_summary(make_summary(missing_birth_date=True), order_id="A1")
    assert "Дата рождения" in text


def test_unmapped_lines_are_listed_individually():
    text = format_order_summary(
        make_summary(unmapped_items=["Труна", "Катафалк"]), order_id="A1"
    )
    assert "Труна" in text and "Катафалк" in text
    assert "ручной проверки" in text


def test_price_only_matches_get_their_own_section():
    """Позиции, сопоставленные только по цене, должны быть отделены от
    несопоставленных: их вобьют автоматически, но сверить надо."""
    text = format_order_summary(make_summary(review_items=["Рушник на фото 350"]), order_id="A1")
    assert "сверь с бланком" in text
    assert "Рушник на фото 350" in text
    assert "Есть конфликты" in text


def test_applied_rules_are_listed():
    text = format_order_summary(
        make_summary(warnings=["✂️ Разделено: Закопка (7700) и Рушник (1400)"]), order_id="A1"
    )
    assert "Закопка" in text


def test_optional_fields_are_omitted_when_empty():
    text = format_order_summary(make_summary(customer_fio="", cemetery=""), order_id="A1")
    assert "Заказчик" not in text
    assert "Кладбище" not in text


# --- безопасность вывода: HTML из рукописного бланка ---

def test_html_in_ocr_text_is_escaped_not_rendered():
    """ФИО приходит из фото через LLM. Незаэкранированный < ломает parse_mode
    HTML — Telegram отклонит сообщение, и оператор не увидит сводку вовсе."""
    text = format_order_summary(
        make_summary(deceased_fio="<b>жирный</b> & <script>alert(1)</script>"), order_id="A1"
    )
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&amp;" in text


def test_html_is_escaped_in_every_dynamic_field():
    payload = "<i>x</i>"
    text = format_order_summary(
        make_summary(
            deceased_fio=payload, customer_fio=payload, cemetery=payload,
            unmapped_items=[payload], review_items=[payload], warnings=[payload],
        ),
        order_id=payload,
    )
    assert "<i>" not in text
    assert text.count("&lt;i&gt;") == 7


@settings(max_examples=100, deadline=None)
@given(
    fio=st.text(max_size=60), customer=st.text(max_size=60), cemetery=st.text(max_size=40),
    unmapped=st.lists(st.text(max_size=30), max_size=5),
    order_id=st.text(max_size=20),
)
def test_no_raw_angle_brackets_survive_from_any_input(fio, customer, cemetery, unmapped, order_id):
    """Свойство: ни один пользовательский символ < или > не должен попасть
    в разметку — иначе сообщение не отправится."""
    text = format_order_summary(
        make_summary(
            deceased_fio=fio, customer_fio=customer, cemetery=cemetery, unmapped_items=unmapped
        ),
        order_id=order_id,
    )
    # Убираем наши собственные теги, всё остальное не должно содержать скобок.
    for tag in ("<b>", "</b>", "<i>", "</i>"):
        text = text.replace(tag, "")
    assert "<" not in text and ">" not in text


# --- согласованность со сводкой ---

@settings(max_examples=100, deadline=None)
@given(
    handwritten=st.integers(min_value=0, max_value=10**6),
    calculated=st.integers(min_value=0, max_value=10**6),
    unmapped=st.lists(st.text(min_size=1, max_size=20), max_size=4),
    review=st.lists(st.text(min_size=1, max_size=20), max_size=4),
    missing_dob=st.booleans(),
)
def test_conflict_banner_always_agrees_with_has_conflicts(
    handwritten, calculated, unmapped, review, missing_dob
):
    summary = build_order_summary({
        "deceased": {"birth_date": SENTINEL_BIRTH_DATE if missing_dob else "01.02.1970"},
        "customer": {},
        "handwritten_total": handwritten,
        "calculated_total": calculated,
        "services": [
            *({"name": n} for n in unmapped),
            *({"name": n, "1c_search_key": "K", "1c_match_confidence": "low"} for n in review),
        ],
    })
    text = format_order_summary(summary, order_id="A1")
    assert ("Есть конфликты" in text) == summary.has_conflicts
    assert ("Конфликтов не найдено" in text) != summary.has_conflicts
