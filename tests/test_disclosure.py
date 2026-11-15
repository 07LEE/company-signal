import json
from datetime import date

import duckdb
import httpx
import pytest
from conftest import register_functions

from company_signal.cli import company_for_untied
from company_signal.config import Settings
from company_signal.disclosure import company_from_answer, company_loader, query_name_if_askable
from company_signal.fsc import FscClient
from company_signal.lookup import LookupFailed, browse, format_company
from company_signal.store import Store


def corp(crno, bzno, name="(주)한빛", employees="120", tenure="6.5", pay="50000000", published="20261114", first="20260508"):
    return {"crno": crno, "bzno": bzno, "corpNm": name, "enpEstbDt": "20110302", "enpEmpeCnt": employees,
            "empeAvgCnwkTermCtt": tenure, "enpPn1AvgSlryAmt": pay, "enpMainBizNm": "", "sicNm": "소프트웨어 개발",
            "fstOpegDt": first, "lastOpegDt": published}


def answer(*items):
    return json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": list(items)}, "totalCount": len(items)}}})


@pytest.fixture
def warehouse(tmp_path):
    path = tmp_path / "data" / "warehouse.duckdb"
    path.parent.mkdir()
    con = duckdb.connect(str(path))
    register_functions(con)
    con.execute("create schema mart")
    con.execute(
        """
        create table mart.mart_workplace as
        select * from (values
            ('w1', '한빛(주)', '한빛', '123810', '1'),
            ('w2', '한빛 부산지점', '한빛부산지점', '123850', '1'),
            ('w3', '한빛식당', '한빛식당', '123010', '2')
        ) as t (workplace_id, workplace_name, name_norm, biz_reg_no6, workplace_type_code)
        """
    )
    con.close()
    return path


def test_only_head_offices_of_corporations_can_be_asked(warehouse):
    con = duckdb.connect(str(warehouse), read_only=True)
    assert query_name_if_askable(con, "w1") == "한빛"
    assert query_name_if_askable(con, "w2") is None  # a branch
    assert query_name_if_askable(con, "w3") is None  # an individual business
    con.close()


def test_company_from_answer_ties_only_one_matching_corporation(warehouse):
    con = duckdb.connect(str(warehouse), read_only=True)
    page = answer(corp("c1", "1238100001"), corp("c1", "1238100001", employees="0", published="20251014"), corp("c2", "9998100002"))
    company = company_from_answer(con, "w1", "한빛", [page])
    assert company["crno"] == "c1" and company["employee_count"] == 120 and company["avg_tenure_years"] == 6.5
    assert company["figures_published_on"] == date(2026, 11, 14)
    # Two corporations with the workplace's 6 digits: either could be wrong, so none.
    assert company_from_answer(con, "w1", "한빛", [answer(corp("c1", "1238100001"), corp("c3", "1238100003"))]) is None
    assert company_from_answer(con, "w1", "한빛", [answer()]) is None
    con.close()


def test_the_lookup_and_dbt_read_an_answer_the_same_way(warehouse):
    """The lookup's result has exactly the columns of the mart the dbt build makes."""
    con = duckdb.connect(str(warehouse), read_only=True)
    company = company_from_answer(con, "w1", "한빛", [answer(corp("c1", "1238100001"))])
    assert list(company) == ["workplace_id", "crno", "corp_name", "bzno", "established_on", "main_business", "industry_name",
                             "latest_published_on", "employee_count", "avg_tenure_years", "avg_yearly_pay", "figures_published_on"]
    format_company(company)  # renders
    con.close()


def fsc_transport(calls, body):
    def handler(request):
        calls.append(request.url.params["corpNm"])
        return httpx.Response(200, text=body)
    return httpx.MockTransport(handler)


def test_a_collected_answer_is_used_without_asking(tmp_path, warehouse, monkeypatch):
    store = Store(tmp_path / "data" / "company_signal.duckdb")
    store.init()
    store.record_fsc_response("한빛", [answer(corp("c1", "1238100001"))], 1, False)
    monkeypatch.setattr("company_signal.fsc.FscClient.fetch_outline_page", lambda *a: pytest.fail("asked the API"))
    company = company_for_untied(Settings(root=tmp_path, service_key="k"), "w1", "한빛")
    assert company["crno"] == "c1"


def test_an_unasked_workplace_gets_a_loader_only_with_a_key(tmp_path, warehouse):
    assert company_for_untied(Settings(root=tmp_path, service_key=""), "w1", "한빛") is None
    assert callable(company_for_untied(Settings(root=tmp_path, service_key="k"), "w1", "한빛"))


def test_the_loader_asks_once_and_stores_the_answer(tmp_path, warehouse, monkeypatch):
    calls = []
    body = answer(corp("c1", "1238100001"))
    real_init = FscClient.__init__
    monkeypatch.setattr(FscClient, "__init__", lambda self, key, **kw: real_init(self, key, interval=0, sleep=lambda _: None, transport=fsc_transport(calls, body)))
    store = Store(tmp_path / "data" / "company_signal.duckdb")
    load = company_loader(warehouse, store, "k", "w1", "한빛", None)
    assert load()["crno"] == "c1"
    assert calls == ["한빛"]
    assert store.fsc_pages("한빛") == [body]  # the next dbt build includes it


def test_browse_calls_the_loader_when_the_company_view_opens(capsys):
    months = [{"base_ym": date(2026, n, 1), "subscriber_count": 100, "new_subscriber_count": 1, "lost_subscriber_count": 1,
               "previous_subscriber_count": 100 if n > 1 else None, "hire_rate": 0.01, "attrition_rate": 0.01,
               "est_avg_monthly_income": 3_000_000, "source_row_count": 1} for n in range(1, 13)]
    calls = []

    def loader():
        calls.append(1)
        return None

    keys = iter(["d", "m", "d", "q"])
    browse(months, months[-3:], company=loader, ask=lambda prompt: next(keys), in_place=False)
    out = capsys.readouterr().out
    assert calls == [1]  # asked once, even though the view was opened twice
    assert out.count("Looking up the company's disclosures...") == 1
    assert out.count("No company disclosure matches this workplace.") == 2


def test_browse_shows_a_failed_lookup_and_tries_again(capsys):
    months = [{"base_ym": date(2026, n, 1), "subscriber_count": 100, "new_subscriber_count": 1, "lost_subscriber_count": 1,
               "previous_subscriber_count": 100, "hire_rate": 0.01, "attrition_rate": 0.01,
               "est_avg_monthly_income": 3_000_000, "source_row_count": 1} for n in range(1, 13)]
    attempts = []

    def loader():
        attempts.append(1)
        if len(attempts) == 1:
            raise LookupFailed("the API key's daily limit is used up")
        return None

    keys = iter(["d", "m", "d", "q"])
    browse(months, months[-3:], company=loader, ask=lambda prompt: next(keys), in_place=False)
    out = capsys.readouterr().out
    assert "Could not look up the company: the API key's daily limit is used up" in out
    assert len(attempts) == 2


def test_browse_erases_the_lookup_line_too(capsys):
    months = [{"base_ym": date(2026, n, 1), "subscriber_count": 100, "new_subscriber_count": 1, "lost_subscriber_count": 1,
               "previous_subscriber_count": 100, "hire_rate": 0.01, "attrition_rate": 0.01,
               "est_avg_monthly_income": 3_000_000, "source_row_count": 1} for n in range(1, 13)]
    keys = iter(["d", "q"])
    browse(months, months[-3:], company=lambda: None, ask=lambda prompt: next(keys), in_place=True, terminal_size=lambda: (120, 50))
    # Three months take 5 rows, then a blank line, the prompt and the lookup line.
    assert "\x1b[8F\x1b[J" in capsys.readouterr().out
