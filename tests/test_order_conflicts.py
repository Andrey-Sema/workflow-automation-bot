from src.order_conflicts import build_order_summary
from src.summary_formatting import format_order_summary


def make_order(**overrides):
    order = {
        "deceased": {"fio": "Іванов Іван Іванович", "birth_date": "01.01.1950", "cemetery": "Западное"},
        "customer": {"fio": "Петров Петро"},
        "services": [{"name": "Катафалк", "price": 2000, "1c_search_key": "Катафалк"}],
        "goods": [],
        "transport": [],
        "warnings": [],
        "handwritten_total": 2000,
        "calculated_total": 2000,
    }
    order.update(overrides)
    return order


def test_matching_sums_no_conflict():
    summary = build_order_summary(make_order())
    assert summary.sum_mismatch is False
    assert summary.has_conflicts is False
    assert summary.can_auto_enter is True


def test_sum_mismatch_detected_beyond_tolerance():
    summary = build_order_summary(make_order(handwritten_total=2000, calculated_total=1800))
    assert summary.sum_mismatch is True
    assert summary.sum_diff == 200
    assert summary.has_conflicts is True


def test_sum_mismatch_within_tolerance_is_not_a_conflict():
    # 1% of 2000 = 20; a 10 grn gap should not trip the alarm
    summary = build_order_summary(make_order(handwritten_total=2000, calculated_total=1990))
    assert summary.sum_mismatch is False


def test_missing_handwritten_total_is_not_treated_as_mismatch():
    summary = build_order_summary(make_order(handwritten_total=0, calculated_total=2000))
    assert summary.sum_mismatch is False


def test_sentinel_birth_date_flagged():
    summary = build_order_summary(make_order(deceased={"fio": "Тест", "birth_date": "01.01.1920"}))
    assert summary.missing_birth_date is True
    assert summary.has_conflicts is True


def test_unmapped_items_block_auto_entry():
    order = make_order(services=[{"name": "Загадочная услуга", "price": 500}])
    summary = build_order_summary(order)
    assert summary.unmapped_items == ["Загадочная услуга"]
    assert summary.can_auto_enter is False
    assert summary.has_conflicts is True


def test_format_order_summary_escapes_html_and_reports_conflicts():
    order = make_order(
        deceased={"fio": "<script>Test</script>", "birth_date": "01.01.1920"},
        services=[{"name": "Без каталога", "price": 100}],
        handwritten_total=1000,
        calculated_total=100,
    )
    summary = build_order_summary(order)
    text = format_order_summary(summary, order_id="ORD-1")

    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "Без каталога" in text
    assert "Есть конфликты" in text
