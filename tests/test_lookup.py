import re
from datetime import date
from pathlib import Path

import duckdb
import pytest

from company_signal.lookup import (
    browse,
    choose,
    display_width,
    fit,
    format_history,
    format_hits,
    format_notes,
    format_overall,
    format_summary,
    format_yearly,
    pick_months,
    screen_rows,
    search,
    shown_rate,
)

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


def format_detail(workplace, months, shown=None):
    """The whole view as the CLI prints it: summary, history, notes."""
    shown = pick_months(months) if shown is None else shown
    return "\n\n".join(
        [format_summary(workplace, months), format_history(months, shown), format_notes(workplace, months)]
    )


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


def year_of_months():
    """Twelve months, 2026-01 to 2026-12, of a workplace of 100."""
    return [month(date(2026, n, 1), 100, 1, 1, 100 if n > 1 else None) for n in range(1, 13)]


def test_pick_months_takes_the_latest_by_default_or_everything():
    months = year_of_months()
    assert [m["base_ym"].month for m in pick_months(months, last=3)] == [10, 11, 12]
    assert pick_months(months, last=None) == months
    assert pick_months(months, last=99) == months


def test_pick_months_by_range_ignores_the_count():
    months = year_of_months()
    picked = pick_months(months, last=2, start=date(2026, 3, 1), end=date(2026, 5, 1))
    assert [m["base_ym"].month for m in picked] == [3, 4, 5]
    assert [m["base_ym"].month for m in pick_months(months, start=date(2026, 11, 1))] == [11, 12]
    assert [m["base_ym"].month for m in pick_months(months, end=date(2026, 2, 1))] == [1, 2]


def test_history_title_says_when_only_part_is_shown():
    months = year_of_months()
    assert format_history(months, months).splitlines()[0] == "Monthly history"
    partial = format_history(months, pick_months(months, last=3))
    assert partial.splitlines()[0] == "Monthly history (2026-10 to 2026-12, 3 of 12 months)"
    assert "No data in this range." in format_history(months, [])


def test_detail_summarises_the_latest_month_even_when_an_older_range_is_shown():
    months = year_of_months()
    text = format_detail(workplace(months_observed=12), months, pick_months(months, end=date(2026, 2, 1)))
    assert "Latest month: 2026-12" in text
    assert "Monthly history (2026-01 to 2026-02, 2 of 12 months)" in text


def titles(capsys):
    """The history titles printed so far, in order."""
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("Monthly history")]


def test_browse_prints_the_table_then_moves_the_window(capsys):
    months = year_of_months()
    browse(months, pick_months(months, last=3), ask=answers("<", "<", ">"), in_place=False)
    out = capsys.readouterr().out
    assert [line for line in out.splitlines() if line.startswith("Monthly history")] == [
        "Monthly history (2026-10 to 2026-12, 3 of 12 months)",
        "Monthly history (2026-07 to 2026-09, 3 of 12 months)",
        "Monthly history (2026-04 to 2026-06, 3 of 12 months)",
        "Monthly history (2026-07 to 2026-09, 3 of 12 months)",
    ]


def test_browse_stops_at_both_ends_and_keeps_the_window_length(capsys):
    months = year_of_months()
    # Five months a page over twelve: the oldest page is still five months long.
    browse(months, pick_months(months, last=5), ask=answers(">", "<", "<", "<"), in_place=False)
    out = capsys.readouterr().out
    assert out.count("No later months.") == 1
    assert out.count("No earlier months.") == 1
    assert "Monthly history (2026-01 to 2026-05, 5 of 12 months)" in out


def test_browse_shows_everything_and_rejects_other_keys(capsys):
    months = year_of_months()
    browse(months, pick_months(months, last=3), ask=answers("x", "a", "q", "<"), in_place=False)
    out = capsys.readouterr().out
    assert "Type <, >, a or q." in out
    assert [line for line in out.splitlines() if line.startswith("Monthly history")][-1] == "Monthly history"


def test_browse_asks_nothing_when_the_whole_history_is_shown(capsys):
    months = year_of_months()
    browse(months, months, ask=answers(), in_place=False)  # answers() would raise if asked
    assert titles(capsys) == ["Monthly history"]


def test_browse_from_an_empty_range_starts_at_the_newest_months(capsys):
    months = year_of_months()
    browse(months, [], ask=answers("<"), in_place=False)
    assert titles(capsys) == ["Monthly history", "Monthly history"]  # no data, then all twelve months


def tall_terminal(columns=120, rows=50):
    return lambda: (columns, rows)


def test_browse_in_place_erases_exactly_what_it_drew(capsys):
    months = year_of_months()
    browse(months, pick_months(months, last=3), ask=answers("<"), in_place=True, terminal_size=tall_terminal())
    out = capsys.readouterr().out
    # Drawn: title, header and 3 months are 5 rows, plus the prompt line.
    assert "\x1b[6F\x1b[J" in out
    assert out.count("Monthly history") == 2


def test_browse_in_place_counts_the_status_line_when_erasing(capsys):
    months = year_of_months()
    browse(months, pick_months(months, last=3), ask=answers(">", ">"), in_place=True, terminal_size=tall_terminal())
    out = capsys.readouterr().out
    assert out.count("No later months.") == 2
    assert "\x1b[6F" in out and "\x1b[7F" in out  # the second erase also covers the first message


def test_browse_in_place_leaves_only_the_table_when_quitting(capsys):
    months = year_of_months()
    browse(months, pick_months(months, last=3), ask=answers("x", "q"), in_place=True, terminal_size=tall_terminal())
    out = capsys.readouterr().out
    assert "Type <, >, a or q." in out
    # What is drawn after the last erase is the table alone: no message, no prompt.
    final = out.rsplit("\x1b[J", 1)[1]
    assert final.startswith("Monthly history (2026-10 to 2026-12, 3 of 12 months)")
    assert "Type" not in final


def test_browse_does_not_erase_what_no_longer_fits_on_the_screen(capsys):
    months = year_of_months()
    browse(months, pick_months(months, last=3), ask=answers("<"), in_place=True, terminal_size=tall_terminal(rows=6))
    out = capsys.readouterr().out
    assert "\x1b[" not in out
    assert "Monthly history (2026-07 to 2026-09, 3 of 12 months)" in out


def test_screen_rows_counts_wrapped_lines_and_wide_characters():
    assert screen_rows("abc\ndef", 80) == 2
    assert screen_rows("a" * 81, 80) == 2
    assert screen_rows("한" * 41, 80) == 2  # 41 Korean characters are 82 columns
    assert screen_rows("", 80) == 1


def year(base_year, months=12, staff=100.0, joined=20, left=10, income=3_000_000, change=None):
    return {
        "base_year": base_year,
        "months_observed": months,
        "is_full_year": months == 12,
        "avg_subscriber_count": staff,
        "first_subscriber_count": int(staff) - 5,
        "last_subscriber_count": int(staff) + 5,
        "new_subscriber_total": joined,
        "lost_subscriber_total": left,
        "attrition_rate": left / staff,
        "replacement_rate": min(joined, left) / staff,
        "est_avg_monthly_income": income,
        "income_change_rate": change,
    }


def test_yearly_marks_years_with_missing_months():
    text = format_yearly([year(2025, change=0.063), year(2026, months=9)])
    full, partial = [line for line in text.splitlines() if line.startswith("  202")]
    assert "10.0%" in full and "10.0%*" not in full and "+6.3%" in full
    assert "10.0%*" in partial
    assert "* under 12 months of data." in text


def test_yearly_has_no_footnote_when_every_year_is_complete():
    assert "under 12 months" not in format_yearly([year(2024), year(2025)])


def test_yearly_hides_rates_of_small_workplaces():
    line = [l for l in format_yearly([year(2025, staff=4.0, joined=2, left=1)]).splitlines() if l.startswith("  2025")][0]
    assert "%" not in line


def test_browse_switches_between_monthly_and_yearly(capsys):
    months = year_of_months()
    browse(months, pick_months(months, last=3), [year(2026)], ask=answers("y", "<", "m", "<"), in_place=False)
    out = capsys.readouterr().out
    assert out.count("Yearly report") == 1
    assert "Type m or q." in out  # paging keys do nothing in the yearly view
    assert "Monthly history (2026-07 to 2026-09, 3 of 12 months)" in out  # paging works again after m


def test_browse_offers_the_yearly_view_even_when_all_months_are_shown(capsys):
    months = year_of_months()
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return "y" if len(prompts) == 1 else "q"

    browse(months, months, [year(2026)], ask=ask, in_place=False)
    assert "[y] yearly" in prompts[0]
    assert prompts[1] == "[m] monthly  [q] quit: "
    assert "Yearly report" in capsys.readouterr().out


def test_browse_without_yearly_data_does_not_offer_it(capsys):
    months = year_of_months()
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return "y" if len(prompts) == 1 else "q"

    browse(months, pick_months(months, last=3), [], ask=ask, in_place=False)
    assert "[y]" not in prompts[0]
    assert "Type <, >, a or q." in capsys.readouterr().out


def overall(**overrides):
    base = {
        "first_base_ym": date(2021, 1, 1),
        "last_base_ym": date(2025, 12, 1),
        "months_observed": 60,
        "first_subscriber_count": 80,
        "last_subscriber_count": 100,
        "avg_subscriber_count": 92.0,
        "new_subscriber_total": 120,
        "lost_subscriber_total": 100,
        "attrition_rate_per_year": 0.125,
        "attrition_rate_3y": 0.16,
        "attrition_rate_12m": 0.2,
        "replacement_rate_per_year": 0.11,
        "replacement_rate_3y": 0.14,
        "replacement_rate_12m": 0.18,
        "implied_tenure_years": 8.0,
        "implied_tenure_years_3y": 6.25,
        "implied_tenure_years_12m": 5.0,
        "first_est_income": 3_000_000,
        "last_est_income": 3_300_000,
        "income_growth_per_year": 0.019,
        "scheme_age_years": 12.0,
    }
    return base | overrides


def cells(text, label):
    """The three span cells of one row of the summary."""
    line = [line for line in text.splitlines() if line.strip().startswith(label)][0]
    return [line[20 + 16 * i : 20 + 16 * (i + 1)].strip() for i in range(3)]


def test_overall_shows_three_spans_side_by_side():
    text = format_overall(overall())
    assert text.splitlines()[0] == "Summary  2021-01 to 2025-12 (60 months)"
    assert "80 to 100  (+25.0%)" in text
    assert "120 / 100" in text
    assert cells(text, "attrition / year") == ["12.5%", "16.0%", "20.0%"]
    assert cells(text, "replaced / year") == ["11.0%", "14.0%", "18.0%"]
    assert cells(text, "implied tenure") == ["8.0 years", "6.2 years", "5.0 years"]
    assert "3,000,000 to 3,300,000  (+1.9% a year)" in text
    assert text.splitlines()[-1] == '  "-" needs more months. Implied tenure = 1 / attrition, not a measured tenure.'


def test_overall_leaves_a_span_empty_until_its_months_exist():
    text = format_overall(
        overall(months_observed=20, attrition_rate_3y=None, replacement_rate_3y=None, implied_tenure_years_3y=None)
    )
    assert cells(text, "attrition / year") == ["12.5%", "-", "20.0%"]
    assert cells(text, "implied tenure") == ["8.0 years", "-", "5.0 years"]


def test_overall_caps_tenure_at_the_age_of_the_workplace():
    text = format_overall(overall(scheme_age_years=5.5))
    assert cells(text, "implied tenure") == ["over 5.5 years", "over 5.5 years", "5.0 years"]


def test_overall_hides_rates_of_small_workplaces():
    text = format_overall(overall(avg_subscriber_count=3.0))
    assert cells(text, "attrition / year") == ["-", "-", "-"]
    assert '"-" hidden below 5 staff.' in text


def test_overall_shows_no_tenure_when_nobody_left():
    text = format_overall(overall(attrition_rate_12m=0.0, implied_tenure_years_12m=None))
    assert cells(text, "attrition / year")[2] == "0.0%"
    assert cells(text, "implied tenure")[2] == "-"


def test_browse_reaches_every_view_and_names_the_others_in_the_prompt(capsys):
    months = year_of_months()
    prompts = []
    keys = iter(["s", "y", "<", "m"])

    def ask(prompt):
        prompts.append(prompt)
        return next(keys, "q")

    browse(months, pick_months(months, last=3), [year(2026)], overall(), ask=ask, in_place=False)
    out = capsys.readouterr().out
    assert prompts[0].endswith("[y] yearly  [s] summary  [q] quit: ")
    assert prompts[1] == "[m] monthly  [y] yearly  [q] quit: "
    assert prompts[2] == "[m] monthly  [s] summary  [q] quit: "
    assert "Summary  2021-01 to 2025-12" in out and "Yearly report" in out
    assert "Type m, s or q." in out  # paging keys do nothing outside the monthly view
