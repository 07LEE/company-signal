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
