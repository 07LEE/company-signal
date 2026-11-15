"""Pension data for one search, fetched from the API when it is not collected.

The records go through the same SQL functions as the dbt build, into a small
in-memory database laid out like the warehouse (mart.mart_workplace and
mart.mart_workplace_monthly), so the lookup reads it the same way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import duckdb

from .api import Client
from .collect import pick_recent
from .sqlfunctions import nps_columns, register_functions


@dataclass
class LiveResult:
    con: duckdb.DuckDBPyConnection
    base_ym: str  # the month the records describe, YYYY-MM
    match_count: int  # records the API found
    fetched: int  # records received; at most one page

    @property
    def truncated(self) -> bool:
        return self.match_count > self.fetched


def build_live_warehouse(con: duckdb.DuckDBPyConnection, publication_id: str, published_on, records: list[dict]) -> None:
    """Load received records into `con` and derive the warehouse tables from them."""
    con.execute("create or replace table live_record (publication_id varchar, page integer, row_no integer, record json)")
    con.executemany(
        "insert into live_record values (?, 1, ?, ?)",
        [(publication_id, n, json.dumps(record, ensure_ascii=False)) for n, record in enumerate(records, 1)],
    )
    con.execute(
        """
        create or replace table live_row as
        select publication_id, ?::date as published_on, * exclude (publication_id)
        from nps_record_rows('live_record')
        """,
        [published_on],
    )
    con.execute("create or replace table live_selected as select distinct publication_id, base_ym from live_row")
    con.execute("create or replace table live_month as from nps_workplace_months('live_row', 'live_selected')")
    con.execute("create schema if not exists mart")
    con.execute("create or replace table mart.mart_workplace_monthly as from nps_monthly('live_month')")
    con.execute("create or replace table mart.mart_workplace as from nps_workplaces('live_month', 'mart.mart_workplace_monthly')")


def search_live(client: Client, query: str, announce=lambda: None) -> LiveResult | None:
    """Ask the latest publication for workplaces whose name contains the query.

    The query is normalized first, the same way the warehouse names are, so
    "(주)한빛" also finds "주식회사 한빛". None when nothing is left to search for,
    in which case the API is not called. `announce` is called just before it is.
    """
    con = duckdb.connect()
    register_functions(con)
    needle = con.execute("select normalize_name(?)", [query]).fetchone()[0]
    if not needle:
        con.close()
        return None
    announce()
    latest = pick_recent(client.list_publications(), 1)[0]
    # The latest publication uses today's column names: the first name in the mapping.
    name_column = nps_columns()["workplace_name"][0]
    body = json.loads(client.fetch_matching(latest.publication_id, {(name_column, "LIKE"): needle}))
    records = body["data"]
    build_live_warehouse(con, latest.publication_id, latest.title_date, records)
    base_ym = con.execute("select strftime(max(base_ym), '%Y-%m') from live_row").fetchone()[0] or ""
    return LiveResult(con=con, base_ym=base_ym, match_count=int(body.get("matchCount", len(records))), fetched=len(records))

