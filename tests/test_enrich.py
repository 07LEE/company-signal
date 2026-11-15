import json

import duckdb
import httpx
import pytest
from conftest import register_functions

from company_signal.api import FetchError
from company_signal.enrich import candidate_names, enrich
from company_signal.fsc import DailyLimitReached, FscClient
from company_signal.store import Store


def outline(total, page=1, rows=100):
    """A normal response: `total` matches, of which this page carries up to `rows`."""
    count = max(0, min(rows, total - (page - 1) * rows))
    items = [{"corpNm": f"회사{page}-{n}", "bzno": "1238100000"} for n in range(count)]
    body = {"items": {"item": items}, "numOfRows": rows, "pageNo": page, "totalCount": total}
    return json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."}, "body": body}})


LIMIT_ERROR = json.dumps(
    {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR", "returnReasonCode": "22"}}}
)
KEY_ERROR = json.dumps(
    {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "returnReasonCode": "30"}}}
)


def make_client(handler):
    return FscClient("secret-key", interval=0, sleep=lambda _: None, transport=httpx.MockTransport(handler))


@pytest.fixture
def warehouse(tmp_path):
    """A warehouse file with just the columns the candidate query reads."""
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    register_functions(con)
    con.execute("create schema mart")
    con.execute(
        """
        create table mart.mart_workplace as
        select * from (values
            ('한빛소프트(주)',            '한빛소프트',                  '123810', '1', true,  300),
            ('(주)가람 (Garam Inc.)',     '가람(GARAMINC.)',             '123860', '1', true,  900),
            ('한빛소프트(주) 제2사업장',  '한빛소프트',                  '123810', '1', true,   40),
            ('다온건설(주)/일용/신축공사', '다온건설/일용/신축공사',      '123810', '1', true,  500),
            ('나래상사 부산지점',          '나래상사부산지점',            '123850', '1', true,  700),
            ('새솔식당',                   '새솔식당',                    '123010', '2', true,  600),
            ('옛날회사(주)',               '옛날회사',                    '123810', '1', false, 800),
            ('(주)별',                     '별',                          '123810', '1', true,  100)
        ) as t (workplace_name, name_norm, biz_reg_no6, workplace_type_code, in_latest_data, subscriber_count)
        """
    )
    con.close()
    return path


def test_candidates_are_head_offices_of_corporations_largest_first(warehouse):
    # Left out: the construction site, the branch (85), the individual business,
    # the workplace no longer in the data and the one-character name. The
    # bracketed English name is cut off, and a name shared by two workplaces is asked once.
    assert candidate_names(warehouse) == ["가람", "한빛소프트"]


def test_client_returns_the_text_as_received_and_the_match_count():
    text, total = make_client(lambda request: httpx.Response(200, text=outline(14))).fetch_outline_page("한빛", 1)
    assert total == 14
    assert text == outline(14)


def test_client_stops_at_the_daily_limit_and_fails_on_a_bad_key_without_showing_it():
    with pytest.raises(DailyLimitReached):
        make_client(lambda request: httpx.Response(429, text=LIMIT_ERROR)).fetch_outline_page("한빛", 1)
    with pytest.raises(FetchError) as error:
        make_client(lambda request: httpx.Response(403, text=KEY_ERROR)).fetch_outline_page("한빛", 1)
    assert "secret-key" not in str(error.value)


def test_enrich_stores_answers_including_no_match_and_does_not_ask_twice(tmp_path, warehouse):
    asked = []

    def handler(request):
        name = request.url.params["corpNm"]
        asked.append((name, int(request.url.params["pageNo"])))
        return httpx.Response(200, text=outline(0 if name == "가람" else 14))

    store = Store(tmp_path / "collected.duckdb")
    enrich(make_client(handler), store, warehouse, max_calls=100, log=lambda _: None)
    assert asked == [("가람", 1), ("한빛소프트", 1)]
    assert store.fsc_counts() == (2, 1, 0)  # asked, matched, truncated

    asked.clear()
    enrich(make_client(handler), store, warehouse, max_calls=100, log=lambda _: None)
    assert asked == []

    with store.connect() as con:
        body = con.execute("select body from raw.fsc_corp_outline_page where query_name = '한빛소프트'").fetchone()[0]
    assert body == outline(14)


def test_enrich_caps_the_pages_of_a_common_name_and_marks_it_truncated(tmp_path, warehouse):
    def handler(request):
        return httpx.Response(200, text=outline(3164, page=int(request.url.params["pageNo"])))

    store = Store(tmp_path / "collected.duckdb")
    enrich(make_client(handler), store, warehouse, max_calls=3, log=lambda _: None)
    with store.connect() as con:
        rows = con.execute("select query_name, total_count, pages_fetched, truncated from meta.fsc_query").fetchall()
    # Three calls pay for the three pages of the first name; the second name waits for the next run.
    assert rows == [("가람", 3164, 3, True)]


def test_enrich_does_not_store_half_a_name_when_the_calls_run_out(tmp_path, warehouse):
    def handler(request):
        return httpx.Response(200, text=outline(250, page=int(request.url.params["pageNo"])))

    store = Store(tmp_path / "collected.duckdb")
    enrich(make_client(handler), store, warehouse, max_calls=2, log=lambda _: None)
    assert store.fsc_counts() == (0, 0, 0)


def test_enrich_keeps_what_it_has_when_the_daily_limit_is_reached(tmp_path, warehouse):
    def handler(request):
        if request.url.params["corpNm"] == "가람":
            return httpx.Response(200, text=outline(5))
        return httpx.Response(429, text=LIMIT_ERROR)

    store = Store(tmp_path / "collected.duckdb")
    logs = []
    enrich(make_client(handler), store, warehouse, max_calls=100, log=logs.append)
    assert store.fsc_counts() == (1, 1, 0)
    assert any("daily limit" in line for line in logs)
