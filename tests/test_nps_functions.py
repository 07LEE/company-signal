"""The pension-data SQL functions, as generated from the package's data files."""

import json
from datetime import date

import duckdb
import pytest
from conftest import register_functions

from company_signal.sqlfunctions import nps_columns


@pytest.fixture
def con():
    con = duckdb.connect()
    register_functions(con)
    yield con
    con.close()


def record_in_period(period: int) -> dict:
    """The same record written with the column names of one publication period (0 = current)."""
    values = {
        "base_ym": "2020-03", "workplace_name": "(주)한빛", "biz_reg_no": 23810, "join_status_code": 1,
        "workplace_type_code": 1, "zip_code": "08501", "lot_address": "서울특별시 구로구 구로동",
        "road_address": "서울특별시 구로구 디지털로", "legal_dong_code": 1153010200, "admin_dong_code": 1153051000,
        "sido_code": 11, "sigungu_code": 530, "emd_code": 102, "industry_code": 72000, "industry_name": "소프트웨어 개발",
        "applied_on": "2015-04-01", "rejoined_on": None, "withdrawn_on": None,
        "subscriber_count": 120, "billed_amount": 40_000_000, "new_subscriber_count": 3, "lost_subscriber_count": 1,
    }
    columns = nps_columns()
    return {columns[name][min(period, len(columns[name]) - 1)]: value for name, value in values.items()}


@pytest.mark.parametrize("period", [0, 1, 2])
def test_nps_record_rows_reads_every_period_the_same_way(con, period):
    con.execute("create table r (publication_id varchar, page integer, row_no integer, record json)")
    con.execute("insert into r values ('p', 1, 1, ?)", [json.dumps(record_in_period(period), ensure_ascii=False)])
    row = con.execute("select base_ym, biz_reg_no6, legal_dong_code, industry_code, subscriber_count, billed_amount, lost_subscriber_count from nps_record_rows('r')").fetchone()
    # Codes come as numbers and lose their leading zeros; they are padded back as text.
    assert row == (date(2020, 3, 1), "023810", "1153010200", "072000", 120, 40_000_000, 1)


def test_nps_known_column(con):
    assert con.execute("select nps_known_column('가입자수'), nps_known_column('col-19'), nps_known_column('새 컬럼')").fetchone() == (True, True, False)


@pytest.mark.parametrize(("month", "rate"), [(date(2025, 12, 1), 0.09), (date(2026, 1, 1), 0.095), (date(2015, 11, 1), 0.09)])
def test_nps_contribution_rate(con, month, rate):
    assert con.execute("select nps_contribution_rate(?)", [month]).fetchone()[0] == rate


def workplace_months(con, rows):
    """nps_workplace_months over source rows given as (base month, name, 6 digits)."""
    columns = nps_columns()
    con.execute("create or replace table r (publication_id varchar, page integer, row_no integer, record json)")
    for n, (month, name, digits) in enumerate(rows, 1):
        record = record_in_period(0) | {columns["base_ym"][0]: month, columns["workplace_name"][0]: name, columns["biz_reg_no"][0]: digits}
        con.execute("insert into r values (?, 1, ?, ?)", [f"p{month}", n, json.dumps(record, ensure_ascii=False)])
    con.execute("create or replace table rows as select *, null::date as published_on from nps_record_rows('r')")
    con.execute("create or replace table picked as from nps_selected_publications('rows')")
    return con.execute(
        "select strftime(base_ym, '%Y-%m'), workplace_name, workplace_id from nps_workplace_months('rows', 'picked') order by 1, 2"
    ).fetchall()


def test_a_renamed_workplace_carries_on_under_its_latest_key(con):
    months = workplace_months(con, [
        ("2024-02", "주식회사 한빛", 23810),
        ("2024-03", "주식회사 한빛", 23810),
        ("2024-04", "주식회사 한빛 ( Hanbit Inc. )", 23810),
        ("2024-04", "한빛식품", 23810),  # another workplace with the same digits
    ])
    hanbit = {month: wid for month, name, wid in months if name.startswith("주식회사 한빛")}
    assert len(set(hanbit.values())) == 1
    latest = [wid for month, name, wid in months if name == "주식회사 한빛 ( Hanbit Inc. )"][0]
    assert hanbit["2024-02"] == latest
    assert len({wid for _, _, wid in months}) == 2


@pytest.mark.parametrize(
    "rows",
    [
        # Two units that differ only in their bracket stop and start together.
        [("2024-03", "제일기업(HDC)", 23810), ("2024-03", "제일기업(HDO)", 23810),
         ("2024-04", "제일기업(HCP)", 23810), ("2024-04", "제일기업(HSB)", 23810)],
        # A month without either is not a carrying on.
        [("2024-02", "한빛", 23810), ("2024-04", "한빛(HANBIT)", 23810)],
        # Both present in the same month are two workplaces.
        [("2024-03", "한빛", 23810), ("2024-03", "한빛(HANBIT)", 23810), ("2024-04", "한빛(HANBIT)", 23810)],
        # A different name is a different workplace, whatever the digits.
        [("2024-03", "한빛", 23810), ("2024-04", "가람", 23810)],
        # Units told apart by Korean text in the bracket keep it in the name core.
        [("2024-03", "영암군청(도시녹지관리원)", 23810), ("2024-04", "영암군청(산림자원조사단)", 23810)],
    ],
)
def test_keys_that_may_be_different_workplaces_are_not_joined(con, rows):
    months = workplace_months(con, rows)
    assert len({wid for _, _, wid in months}) == len({name for _, name, _ in months})


def test_nps_name_core(con):
    assert con.execute(
        "select nps_name_core('한빛로보틱스(HANBITROBOTICSINC.)'), nps_name_core('농업회사법인열정농산'), nps_name_core('영암군청(2025년녹지관리)')"
    ).fetchone() == ("한빛로보틱스", "열정농산", "영암군청(2025년녹지관리)")



def test_a_renamed_workplace_lists_its_earlier_name(con):
    workplace_months(con, [
        ("2024-03", "주식회사 한빛", 23810),
        ("2024-04", "주식회사 한빛 ( Hanbit Inc. )", 23810),
        ("2024-05", "주식회사  한빛 ( Hanbit Inc. )", 23810),  # spacing only: the same name
        ("2024-05", "가람", 23810),
    ])
    con.execute("create table m as from nps_workplace_months('rows', 'picked')")
    con.execute("create table mm as from nps_monthly('m')")
    rows = dict(con.execute("select workplace_name, former_names from nps_workplaces('m', 'mm')").fetchall())
    assert rows["주식회사  한빛 ( Hanbit Inc. )"] == [{"name": "주식회사 한빛", "last_base_ym": date(2024, 3, 1)}]
    assert rows["가람"] is None
