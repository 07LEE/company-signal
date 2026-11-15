import re
from datetime import date
from pathlib import Path

import duckdb
import pytest

from company_signal.lookup import choose, display_width, fit, format_detail, format_hits, search, shown_rate

MACRO_FILE = Path(__file__).parent.parent / "transform" / "macros" / "create_functions.sql"


@pytest.fixture
def warehouse():
    """A tiny warehouse with the real normalize_name definition from the dbt project."""
    con = duckdb.connect()
    body = re.search(r"{% macro create_functions\(\) %}(.*){% endmacro %}", MACRO_FILE.read_text(), re.S)[1]
    con.execute(body)
    con.execute("create schema mart")
    con.execute(
        """
        create table mart.mart_workplace as
        select md5(name) as workplace_id, name as workplace_name, normalize_name(name) as name_norm,
               '서울특별시 구로구 디지털로' as road_address, '소프트웨어 개발' as industry_name,
               '1' as workplace_type_code, subscribers as subscriber_count,
               last_ym as latest_base_ym, last_ym = date '2026-09-01' as in_latest_data
        from (values
            ('(주)한빛', 5, date '2026-09-01'),
            ('주식회사 한빛소프트', 300, date '2026-09-01'),
            ('한빛소프트웨어(주)', 900, date '2026-07-01'),
            ('다른회사', 50, date '2026-09-01')
        ) as t (name, subscribers, last_ym)
        """
    )
    yield con
    con.close()


def test_search_ignores_legal_form_and_spacing(warehouse):
    hits, total = search(warehouse, "한빛 소프트 주식회사")
    assert total == 2
    assert [h.name for h in hits] == ["주식회사 한빛소프트", "한빛소프트웨어(주)"]


def test_search_orders_current_then_exact_then_size(warehouse):
    hits, total = search(warehouse, "한빛")
    assert total == 3
    # The exact match comes before the larger workplace; the one that left the data comes last.
    assert [h.name for h in hits] == ["(주)한빛", "주식회사 한빛소프트", "한빛소프트웨어(주)"]
    assert hits[-1].in_latest_data is False


def test_search_limit_keeps_total(warehouse):
    hits, total = search(warehouse, "한빛", limit=1)
    assert (len(hits), total) == (1, 3)


def test_query_that_normalizes_to_nothing_matches_nothing(warehouse):
    assert search(warehouse, "(주)") == ([], 0)


def test_fit_counts_korean_as_two_columns():
    assert display_width("한빛ab") == 6
    assert display_width(fit("한빛소프트웨어", 8)) == 8
    assert fit("ab", 4) == "ab  "


def test_format_marks_workplaces_not_in_latest_month(warehouse):
    hits, total = search(warehouse, "한빛")
    text = format_hits(hits, total, "한빛")
    assert "(last seen 2026-07)" in text
    assert format_hits([], 0, "없는이름") == 'No workplace matches "없는이름".'


def answers(*replies):
    """Stand in for input(): give the replies one at a time, then end of input."""
    queue = list(replies)

    def ask(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    return ask


def test_choose_returns_the_numbered_hit(warehouse):
    hits, _ = search(warehouse, "한빛")
    assert choose(hits, ask=answers("2")).name == "주식회사 한빛소프트"


def test_choose_asks_again_after_a_bad_answer(warehouse, capsys):
    hits, _ = search(warehouse, "한빛")
    assert choose(hits, ask=answers("0", "abc", "9", "3")).name == "한빛소프트웨어(주)"
    assert capsys.readouterr().out.count("Enter a number from 1 to 3.") == 3


def test_choose_cancels_on_empty_answer_or_end_of_input(warehouse):
    hits, _ = search(warehouse, "한빛")
    assert choose(hits, ask=answers("")) is None
    assert choose(hits, ask=answers()) is None


def test_choose_does_not_ask_when_there_is_one_hit(warehouse):
    hits, _ = search(warehouse, "다른회사")
    assert choose(hits, ask=answers()).name == "다른회사"


def month(ym, subscribers, joined, left, previous, income=3_000_000, source_rows=1):
    return {
        "base_ym": ym,
        "subscriber_count": subscribers,
        "new_subscriber_count": joined,
        "lost_subscriber_count": left,
        "previous_subscriber_count": previous,
        "hire_rate": joined / previous if previous else None,
        "attrition_rate": left / previous if previous else None,
        "est_avg_monthly_income": income,
        "source_row_count": source_rows,
    }


def workplace(**overrides):
    base = {
        "workplace_name": "(주)한빛",
        "road_address": "서울특별시 구로구 디지털로",
        "industry_name": "소프트웨어 개발",
        "workplace_type_code": "1",
        "applied_on": date(2020, 1, 1),
        "first_base_ym": date(2026, 8, 1),
        "latest_base_ym": date(2026, 9, 1),
        "months_observed": 2,
        "in_latest_data": True,
        "attrition_rate_12m": None,
    }
    return base | overrides


def test_shown_rate_hides_rates_of_small_workplaces():
    assert shown_rate(0.25, 40) == "25.0%"
    assert shown_rate(0.25, 5) == "25.0%"
    assert shown_rate(0.25, 4) is None
    assert shown_rate(None, 40) is None
    assert shown_rate(0.25, None) is None


def test_detail_shows_rates_and_says_why_yearly_attrition_is_missing():
    months = [month(date(2026, 8, 1), 100, 5, 3, None), month(date(2026, 9, 1), 104, 8, 4, 100)]
    text = format_detail(workplace(), months)
    assert "Latest month: 2026-09" in text
    assert "8.0% of the previous month's 100" in text
    assert "needs 12 months of data" in text
    assert "Not in the latest data" not in text


def test_detail_of_small_workplace_shows_counts_without_rates():
    months = [month(date(2026, 8, 1), 4, 0, 1, None), month(date(2026, 9, 1), 3, 0, 1, 4)]
    text = format_detail(workplace(), months)
    # The table header has "join %"; what must not appear is a number with a percent sign.
    assert not re.search(r"\d%", text.split("Notes")[0])


def test_detail_notes_merged_rows_and_departed_workplaces():
    months = [month(date(2026, 7, 1), 50, 1, 1, None, source_rows=2)]
    text = format_detail(workplace(latest_base_ym=date(2026, 7, 1), first_base_ym=date(2026, 7, 1), months_observed=1, in_latest_data=False, attrition_rate_12m=0.31), months)
    assert "several source rows" in text
    assert "Not in the latest data. Last seen 2026-07" in text
    assert "31.0%" in text


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("（주）한빛", "한빛"),
        ("주식회사\u3000한빛", "한빛"),
        ("한빛㈜", "한빛"),
        ("(유한)한빛", "한빛"),
        ("한빛 유한책임회사", "한빛"),
        ("한빛(합자)", "한빛"),
        ("ｈａｎｂｉｔ", "HANBIT"),
        # Bracketed text can tell units apart, so it stays.
        ("(주)한빛(Hanbit Inc.)", "한빛(HANBITINC.)"),
        ("한빛건설(주)/일용/신축공사(2BL)", "한빛건설/일용/신축공사(2BL)"),
    ],
)
def test_normalize_name(warehouse, name, expected):
    assert warehouse.execute("select normalize_name(?)", [name]).fetchone()[0] == expected
