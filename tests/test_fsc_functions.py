"""The SQL functions that read company disclosures, from the dbt project's definitions."""

import json

import duckdb
import pytest
from conftest import register_functions


@pytest.fixture
def con():
    con = duckdb.connect()
    register_functions(con)
    yield con
    con.close()


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("15.0", 15.0),
        ("9.32", 9.32),
        ("16년 11월", 16 + 11 / 12),
        ("약9년9개월", 9.75),
        ("80개월", 80 / 12),
        ("7년", 7.0),
        ("", None),
        ("0", None),
        ("0.0", None),
        ("75", None),  # more than 60 years is not a tenure
        ("해당없음", None),
        (None, None),
    ],
)
def test_parse_tenure_years(con, text, years):
    result = con.execute("select parse_tenure_years(?)", [text]).fetchone()[0]
    assert result == pytest.approx(years) if years is not None else result is None


@pytest.mark.parametrize(("text", "amount"), [("642", 642), ("53651000", 53651000), ("0", None), ("", None), (" ", None), (None, None)])
def test_fsc_amount_treats_zero_and_blank_as_not_disclosed(con, text, amount):
    assert con.execute("select fsc_amount(?)", [text]).fetchone()[0] == amount


def answer(items):
    return json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": items}, "totalCount": 9}}})


def test_fsc_outline_items_reads_a_list_a_single_object_and_an_empty_answer(con):
    item = {"crno": "1101110000001", "bzno": "1238100001", "corpNm": "(주)한빛", "enpEmpeCnt": "120", "extra": "ignored"}
    rows = con.execute("select unnest(fsc_outline_items(?))", [answer([item, item])]).fetchall()
    assert len(rows) == 2 and rows[0][0]["corpNm"] == "(주)한빛" and rows[0][0]["enpEmpeCnt"] == "120"
    assert len(con.execute("select unnest(fsc_outline_items(?))", [answer(item)]).fetchall()) == 1
    assert con.execute("select unnest(fsc_outline_items(?))", [answer([])]).fetchall() == []


@pytest.mark.parametrize(
    ("name_norm", "query"),
    [("한빛소프트", "한빛소프트"), ("가람(GARAMINC.)", "가람"), ("한빛-", "한빛"), ("한빛건설/일용/신축공사(2BL)", "한빛건설/일용/신축공사")],
)
def test_fsc_query_name_drops_bracketed_additions(con, name_norm, query):
    assert con.execute("select fsc_query_name(?)", [name_norm]).fetchone()[0] == query
