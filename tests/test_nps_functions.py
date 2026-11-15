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
