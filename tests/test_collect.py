import json
from datetime import date

import httpx
import pytest

from company_signal.api import Client, FetchError, Publication, parse_title_date
from company_signal.collect import collect_publication, pick_recent
from company_signal.store import Store


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("국민연금 가입 사업장 내역 2019년 7월", date(2019, 7, 1)),
        ("국민연금공단_국민연금 가입 사업장 내역 2020년 5월_20200520", date(2020, 5, 20)),
        ("국민연금공단_국민연금 가입 사업장 내역_09/24/2021", date(2021, 9, 24)),
        ("국민연금공단_국민연금 가입 사업장 내역_20261022", date(2026, 10, 22)),
        ("title without a date", None),
    ],
)
def test_parse_title_date(title, expected):
    assert parse_title_date(title) == expected


def test_pick_recent_orders_by_title_date():
    pubs = [
        Publication("a", "a", date(2026, 8, 25)),
        Publication("b", "b", None),
        Publication("c", "c", date(2026, 10, 22)),
        Publication("d", "d", date(2026, 9, 23)),
    ]
    assert [p.publication_id for p in pick_recent(pubs, 2)] == ["c", "d"]


def make_client(handler):
    return Client("key", interval=0, sleep=lambda _: None, transport=httpx.MockTransport(handler))


def page_body(page, total, per_page=1000):
    start = (page - 1) * per_page
    rows = [{"사업장명": f"회사{i}", "가입자수": i} for i in range(start, min(start + per_page, total))]
    return json.dumps({"page": page, "totalCount": total, "data": rows}, ensure_ascii=False)


def test_collect_resumes_and_completes(tmp_path):
    total = 2500
    calls = []
    fail_on_page = {3}

    def handler(request):
        page = int(request.url.params["page"])
        calls.append(page)
        if page in fail_on_page:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=page_body(page, total))

    store = Store(tmp_path / "t.duckdb")
    store.init()
    pub = Publication("uddi:test", "시험_20261022", date(2026, 10, 22))
    store.upsert_publications([pub])
    logs = []

    # A page that keeps failing stops the run. Pages 1 and 2 stay recorded.
    with pytest.raises(FetchError):
        collect_publication(make_client(handler), store, tmp_path / "raw", pub, log=logs.append)
    assert store.fetched_pages("uddi:test") == {1, 2}
    assert store.status_of("uddi:test") == "in_progress"

    # A second run does not refetch recorded pages and finishes the rest.
    fail_on_page.clear()
    calls.clear()
    assert collect_publication(make_client(handler), store, tmp_path / "raw", pub, log=logs.append)
    assert calls == [3]
    assert store.status_of("uddi:test") == "complete"
    assert store.counts() == (1, total)

    with store.connect() as con:
        row = con.execute(
            "SELECT record->>'사업장명' FROM raw.nps_workplace_record WHERE page = 3 AND row_no = 1"
        ).fetchone()
    assert row[0] == "회사2000"


def test_record_page_is_idempotent(tmp_path):
    def handler(request):
        return httpx.Response(200, text=page_body(1, 10))

    store = Store(tmp_path / "t.duckdb")
    store.init()
    pub = Publication("uddi:test", "시험_20261022", date(2026, 10, 22))
    store.upsert_publications([pub])
    collect_publication(make_client(handler), store, tmp_path / "raw", pub, log=lambda _: None)

    path = tmp_path / "raw" / "uddi_test" / "page-000001.json.gz"
    store.record_page("uddi:test", 1, "x", path)
    assert store.counts() == (1, 10)


def test_ok_response_without_data_is_retried_then_fails():
    def handler(request):
        return httpx.Response(200, text='{"code":0,"msg":"정상"}')

    with pytest.raises(FetchError):
        make_client(handler).fetch_page("uddi:test", 1)
