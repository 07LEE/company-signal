import json

import httpx

from company_signal.api import Client
from company_signal.live import search_live
from company_signal.lookup import load_detail, search
from company_signal.sqlfunctions import nps_columns

SPEC = {"paths": {
    "/15083277/v1/uddi:old": {"get": {"summary": "국민연금공단_국민연금 가입 사업장 내역_20260923"}},
    "/15083277/v1/uddi:new": {"get": {"summary": "국민연금공단_국민연금 가입 사업장 내역_20261022"}},
}}


def record(name, biz, applied, subscribers):
    """A record under today's column names."""
    values = {"base_ym": "2026-09", "workplace_name": name, "biz_reg_no": biz, "join_status_code": 1,
              "workplace_type_code": 1, "zip_code": "08501", "lot_address": "서울특별시 구로구 구로동",
              "road_address": "서울특별시 구로구 디지털로", "legal_dong_code": 1153010200, "admin_dong_code": 1153051000,
              "sido_code": 11, "sigungu_code": 530, "emd_code": 102, "industry_code": 722000, "industry_name": "소프트웨어 개발",
              "applied_on": applied, "rejoined_on": None, "withdrawn_on": None, "subscriber_count": subscribers,
              "billed_amount": subscribers * 380_000, "new_subscriber_count": 2, "lost_subscriber_count": 1}
    return {nps_columns()[key][0]: value for key, value in values.items()}


def client_for(records, match_count=None, seen=None):
    def handler(request):
        if "oas/docs" in str(request.url):
            return httpx.Response(200, json=SPEC)
        if seen is not None:
            seen.append((request.url.path, dict(request.url.params)))
        body = {"data": records, "matchCount": len(records) if match_count is None else match_count, "totalCount": 589413}
        return httpx.Response(200, text=json.dumps(body, ensure_ascii=False))
    return Client("k", interval=0, sleep=lambda _: None, transport=httpx.MockTransport(handler))


def test_live_search_asks_the_latest_publication_with_the_normalized_name():
    seen = []
    live = search_live(client_for([record("(주)한빛", 123810, "2015-04-01", 120)], seen=seen), "주식회사 한빛")
    path, params = seen[0]
    assert path.endswith("/uddi:new")
    assert params["cond[사업장명::LIKE]"] == "한빛"
    assert live.base_ym == "2026-09" and not live.truncated


def test_live_rows_are_read_like_the_warehouse():
    records = [record("(주)한빛", 123810, "2015-04-01", 120), record("한빛소프트(주)", 123860, "2018-02-01", 40)]
    live = search_live(client_for(records), "한빛")
    hits, total = search(live.con, "한빛")
    assert total == 2 and [h.name for h in hits] == ["(주)한빛", "한빛소프트(주)"]
    workplace, months = load_detail(live.con, hits[0].workplace_id)
    assert workplace["biz_reg_no6"] == "123810" and workplace["in_latest_data"]
    assert len(months) == 1 and months[0]["previous_subscriber_count"] is None  # one month: no rates
    assert months[0]["est_avg_monthly_income"] == round(380_000 / 0.095)


def test_live_search_says_when_only_part_of_the_matches_was_read():
    live = search_live(client_for([record("(주)한빛", 123810, "2015-04-01", 120)], match_count=3164), "한빛")
    assert live.truncated and live.match_count == 3164 and live.fetched == 1


def test_live_search_with_nothing_left_to_search_for_does_not_call_the_api():
    seen = []
    assert search_live(client_for([], seen=seen), "(주)") is None
    assert seen == []


def publication_spec(*dates):
    return {"paths": {f"/15083277/v1/uddi:{d}": {"get": {"summary": f"국민연금공단_국민연금 가입 사업장 내역_{d}"}} for d in dates}}


def history_client(months, seen):
    """A fake API: publication YYYYMMDD holds the month before it; old ones use the old column name."""
    spec = publication_spec(*months)
    columns = nps_columns()

    def handler(request):
        if "oas/docs" in str(request.url):
            return httpx.Response(200, json=spec)
        stamp = request.url.path.rsplit(":", 1)[1]
        params = dict(request.url.params)
        seen.append((stamp, {k: v for k, v in params.items() if k.startswith("cond")}))
        period_old = stamp < "20211101"
        base = f"{int(stamp[:4]) - (stamp[4:6] == '01')}-{(int(stamp[4:6]) - 2) % 12 + 1:02d}"
        rec = record("(주)한빛", 123810, "2015-04-01", 100 + len(seen))
        rec[columns["base_ym"][0]] = base
        if period_old:  # old publications name the columns differently
            rec = {columns[k][-1]: rec[columns[k][0]] for k in columns}
            if "cond[사업자등록번호::EQ]" in params:
                return httpx.Response(200, text='{"currentCount":0}')  # a column it does not have
        body = {"data": [rec], "matchCount": 1, "totalCount": 500000}
        return httpx.Response(200, text=json.dumps(body, ensure_ascii=False))

    return Client("k", interval=0, sleep=lambda _: None, transport=httpx.MockTransport(handler))


def test_history_is_asked_by_number_and_switches_column_name_for_old_publications():
    from company_signal.live import fetch_history
    from company_signal.lookup import load_overall, load_years

    seen = []
    client = history_client(["20261022", "20260923", "20211118", "20211022"], seen)
    live = search_live(client, "한빛")
    wid = live.con.execute("select workplace_id from mart.mart_workplace").fetchone()[0]
    seen.clear()
    assert fetch_history(client, live, wid, months=None) is None
    stamps = [s for s, _ in seen]
    assert stamps[:2] == ["20260923", "20211118"]
    assert seen[0][1] == {"cond[사업자등록번호::EQ]": "123810"}
    # The oldest publication does not know the current name; the old one is tried and kept.
    assert seen[-1][1] == {f"cond[{nps_columns()['biz_reg_no'][-1]}::EQ]": "123810"}
    assert live.months_fetched == ["2021-09", "2021-10", "2026-08", "2026-09"]
    workplace, months = load_detail(live.con, wid)
    assert len(months) == 4 and months[-1]["previous_subscriber_count"] is not None
    assert load_years(live.con, wid) and load_overall(live.con, wid)


def test_history_takes_only_the_months_asked_for():
    from company_signal.live import fetch_history

    seen = []
    client = history_client(["20261022", "20260923", "20260825", "20260723"], seen)
    live = search_live(client, "한빛")
    wid = live.con.execute("select workplace_id from mart.mart_workplace").fetchone()[0]
    seen.clear()
    fetch_history(client, live, wid, months=2)
    assert [s for s, _ in seen] == ["20260923"]  # the latest month is already there


def conditional_calls(seen):
    return [s for s in seen if s[1]]


def test_a_stored_answer_is_reused_and_not_asked_again(tmp_path):
    from company_signal.live import fetch_history
    from company_signal.store import Store

    store = Store(tmp_path / "company_signal.duckdb")
    store.init()
    months = ["20261022", "20260923", "20211118", "20211022"]

    seen = []
    client = history_client(months, seen)
    live = search_live(client, "한빛", store)
    wid = live.con.execute("select workplace_id from mart.mart_workplace").fetchone()[0]
    fetch_history(client, live, wid, months=None, store=store)
    first = conditional_calls(seen)
    assert live.calls_made == len(first) == 5  # one name search, three months, one failed column name
    assert live.answers_reused == 0

    seen.clear()
    client = history_client(months, seen)
    again = search_live(client, "한빛", store)
    fetch_history(client, again, wid, months=None, store=store)
    assert conditional_calls(seen) == []  # only the list of publications is asked for
    assert again.answers_reused == 4 and again.calls_made == 0
    assert again.months_fetched == live.months_fetched


def test_after_a_new_month_only_the_new_queries_are_asked(tmp_path):
    from company_signal.live import fetch_history
    from company_signal.store import Store

    store = Store(tmp_path / "company_signal.duckdb")
    store.init()
    seen = []
    client = history_client(["20260923", "20260825"], seen)
    live = search_live(client, "한빛", store)
    wid = live.con.execute("select workplace_id from mart.mart_workplace").fetchone()[0]
    fetch_history(client, live, wid, months=None, store=store)

    # A month later the API has one more publication.
    seen.clear()
    client = history_client(["20261022", "20260923", "20260825"], seen)
    live = search_live(client, "한빛", store)
    fetch_history(client, live, wid, months=None, store=store)
    # The new latest month is searched by name. The month before was the latest
    # last time, so it was searched by name then; now it is asked by number once.
    # The oldest month comes from the store.
    assert [stamp for stamp, cond in conditional_calls(seen)] == ["20261022", "20260923"]
    assert live.answers_reused == 1
