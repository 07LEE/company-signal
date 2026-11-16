import json
import subprocess
import sys
import threading
import time
from datetime import date

import duckdb
import httpx
import pytest
from conftest import register_functions

from company_signal.api import Client, FetchError, Publication, parse_title_date
from company_signal.collect import CollectResult, PublicationResult, collect, collect_publication, pick_recent
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
    result = collect_publication(make_client(handler), store, tmp_path / "raw", pub, log=logs.append)
    assert result == PublicationResult(complete=True, pages_fetched=1)
    assert calls == [3]
    assert store.status_of("uddi:test") == "complete"
    assert store.counts() == (1, total)

    # The records stay in the page files only, and read back as they were sent.
    con = duckdb.connect()
    register_functions(con, temporary=True)
    row = con.execute(
        "select record->>'사업장명' from nps_file_records(?) where page = 3 and row_no = 1",
        [str(tmp_path / "raw" / "uddi_test" / "page-*.json.gz")],
    ).fetchone()
    assert row == ("회사2000",)
    assert con.execute("select publication_id from nps_file_records(?) limit 1",
                       [str(tmp_path / "raw" / "uddi_test" / "page-*.json.gz")]).fetchone() == ("uddi:test",)
    con.close()
    with store.connect() as con:
        assert con.execute("select count(*) from information_schema.tables where table_name = 'nps_workplace_record'").fetchone() == (0,)



def test_pages_are_requested_at_once_and_written_by_one_thread(tmp_path):
    total = 8500  # 9 pages
    lock = threading.Lock()
    in_flight, most_in_flight = 0, 0

    def handler(request):
        nonlocal in_flight, most_in_flight
        with lock:
            in_flight += 1
            most_in_flight = max(most_in_flight, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        page = int(request.url.params["page"])
        if page == 7 and fail:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=page_body(page, total))

    store = Store(tmp_path / "t.duckdb")
    store.init()
    pub = Publication("uddi:test", "시험_20261022", date(2026, 10, 22))
    store.upsert_publications([pub])
    writers = set()
    record_page = store.record_page

    def record_in_thread(*args):
        writers.add(threading.get_ident())
        return record_page(*args)

    store.record_page = record_in_thread

    # A failing page stops the run; the pages that arrived are kept.
    fail = True
    with pytest.raises(FetchError):
        collect_publication(make_client(handler), store, tmp_path / "raw", pub, log=lambda _: None, workers=4)
    assert 7 not in store.fetched_pages("uddi:test")
    assert store.status_of("uddi:test") == "in_progress"

    fail = False
    assert collect_publication(make_client(handler), store, tmp_path / "raw", pub, log=lambda _: None, workers=4).complete
    assert store.fetched_pages("uddi:test") == set(range(1, 10))
    assert store.counts() == (1, total)
    assert most_in_flight > 1
    assert writers == {threading.get_ident()}

def test_record_page_is_idempotent(tmp_path):
    def handler(request):
        return httpx.Response(200, text=page_body(1, 10))

    store = Store(tmp_path / "t.duckdb")
    store.init()
    pub = Publication("uddi:test", "시험_20261022", date(2026, 10, 22))
    store.upsert_publications([pub])
    collect_publication(make_client(handler), store, tmp_path / "raw", pub, log=lambda _: None)

    path = tmp_path / "raw" / "uddi_test" / "page-000001.json.gz"
    store.record_page("uddi:test", 1, "x", path, 10)
    assert store.counts() == (1, 10)


def test_ok_response_without_data_is_retried_then_fails():
    def handler(request):
        return httpx.Response(200, text='{"code":0,"msg":"정상"}')

    with pytest.raises(FetchError):
        make_client(handler).fetch_page("uddi:test", 1)


def hold_lock(db_path, seconds):
    """Start another process that keeps the database file open for a while."""
    code = "import duckdb, sys, time; c = duckdb.connect(sys.argv[1]); print('ready', flush=True); time.sleep(float(sys.argv[2]))"
    proc = subprocess.Popen([sys.executable, "-c", code, str(db_path), str(seconds)], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ready"
    return proc


def test_connect_waits_for_another_process_to_release_the_file(tmp_path):
    db_path = tmp_path / "t.duckdb"
    Store(db_path).init()

    proc = hold_lock(db_path, 2)
    try:
        # Without waiting, the open fails while the other process holds the file.
        with pytest.raises(duckdb.IOException):
            Store(db_path, lock_wait=0).counts()
        # With waiting, it succeeds once the other process is done.
        assert Store(db_path, lock_wait=30).counts() == (0, 0)
    finally:
        proc.wait()



def test_collect_reports_what_this_run_added(tmp_path):
    """A second run over the same publications adds nothing, which tells a caller there is nothing to rebuild."""
    spec = {"paths": {"/15083277/v1/uddi:a": {"get": {"summary": "시험_20261022"}},
                      "/15083277/v1/uddi:b": {"get": {"summary": "시험_20260923"}}}}

    def handler(request):
        if "oas/docs" in str(request.url):
            return httpx.Response(200, json=spec)
        return httpx.Response(200, text=page_body(int(request.url.params["page"]), 1500))

    store = Store(tmp_path / "t.duckdb")
    first = collect(make_client(handler), store, tmp_path / "raw", months=2, log=lambda _: None)
    assert first == CollectResult(publications_completed=2, pages_fetched=4)
    again = collect(make_client(handler), store, tmp_path / "raw", months=2, log=lambda _: None)
    assert again == CollectResult(publications_completed=0, pages_fetched=0)
