"""Pension data for one search, fetched from the API when it is not collected.

The records go through the same SQL functions as the dbt build, into a small
in-memory database laid out like the warehouse (the mart tables), so the lookup
reads it the same way.

The search asks the latest publication by name. After a workplace is chosen,
earlier months are asked by its business registration number, the published 6
digits: a few dozen workplaces share them, and the workplace key picks the
chosen one out, even when its name is written a little differently from month
to month.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

import duckdb

from .api import PER_PAGE, Client, FetchError, Publication
from .sqlfunctions import nps_columns, register_functions

# A business registration number is shared by a few dozen workplaces; a page more
# would be an unusually large group, and three is plenty.
MAX_PAGES_PER_MONTH = 3


@dataclass
class LiveResult:
    con: duckdb.DuckDBPyConnection
    publications: list[Publication]  # every publication, newest first
    match_count: int  # workplaces the name search found
    fetched: int  # records the name search received; at most one page
    months_fetched: list[str] = field(default_factory=list)  # base months in the data, YYYY-MM

    @property
    def truncated(self) -> bool:
        return self.match_count > self.fetched

    @property
    def base_ym(self) -> str:
        return self.months_fetched[-1] if self.months_fetched else ""

    @property
    def span(self) -> str:
        if len(self.months_fetched) <= 1:
            return self.base_ym
        return f"{self.months_fetched[0]} to {self.months_fetched[-1]}"


def _setup(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("create table live_record (publication_id varchar, page integer, row_no integer, record json)")
    con.execute("create table live_publication (publication_id varchar primary key, published_on date)")


def _add(con: duckdb.DuckDBPyConnection, publication: Publication, records: list[dict]) -> None:
    start = con.execute("select count(*) from live_record where publication_id = ?", [publication.publication_id]).fetchone()[0]
    con.execute("insert or ignore into live_publication values (?, ?)", [publication.publication_id, publication.title_date])
    con.executemany(
        "insert into live_record values (?, 1, ?, ?)",
        [(publication.publication_id, start + n, json.dumps(r, ensure_ascii=False)) for n, r in enumerate(records, 1)],
    )


def _rebuild(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Derive the warehouse tables from every record fetched so far. Returns the base months."""
    con.execute(
        """
        create or replace table live_row as
        select n.publication_id, p.published_on, n.* exclude (publication_id)
        from nps_record_rows('live_record') as n
        inner join live_publication as p using (publication_id)
        """
    )
    con.execute("create or replace table live_selected as from nps_selected_publications('live_row')")
    con.execute("create or replace table live_month as from nps_workplace_months('live_row', 'live_selected')")
    con.execute("create schema if not exists mart")
    con.execute("create or replace table mart.mart_workplace_monthly as from nps_monthly('live_month')")
    con.execute("create or replace table mart.mart_workplace as from nps_workplaces('live_month', 'mart.mart_workplace_monthly')")
    months = [r[0] for r in con.execute("select strftime(base_ym, '%Y-%m') from live_selected order by base_ym").fetchall()]
    if len(months) > 1:
        con.execute("create or replace table mart.mart_workplace_yearly as from nps_years('mart.mart_workplace_monthly')")
        con.execute("create or replace table mart.mart_workplace_overall as from nps_overall('mart.mart_workplace_monthly', 'mart.mart_workplace')")
    return months


def search_live(client: Client, query: str, announce=lambda: None) -> LiveResult | None:
    """Ask the latest publication for workplaces whose name contains the query.

    The query is normalized first, the same way the warehouse names are, so
    "(주)한빛" also finds "주식회사 한빛". None when nothing is left to search for,
    in which case the API is not called. `announce` is called just before it is.
    """
    con = duckdb.connect()
    register_functions(con)
    _setup(con)
    needle = con.execute("select normalize_name(?)", [query]).fetchone()[0]
    if not needle:
        con.close()
        return None
    announce()
    publications = sorted([p for p in client.list_publications() if p.title_date], key=lambda p: p.title_date, reverse=True)
    latest = publications[0]
    # The latest publication uses today's column names: the first name in the mapping.
    name_column = nps_columns()["workplace_name"][0]
    body = json.loads(client.fetch_matching(latest.publication_id, {(name_column, "LIKE"): needle}) or b'{"data": []}')
    records = body.get("data", [])
    _add(con, latest, records)
    live = LiveResult(con=con, publications=publications, match_count=int(body.get("matchCount", len(records))), fetched=len(records))
    live.months_fetched = _rebuild(con)
    return live


def fetch_history(
    client: Client,
    live: LiveResult,
    workplace_id: str,
    months: int | None,
    start: date | None = None,
    progress=lambda done, total: None,
) -> str | None:
    """Fetch earlier months of the chosen workplace's registration number.

    `months` counts the latest month too; None means every publication. With
    `start`, publications from that month on are fetched instead. Returns why it
    stopped early, or None when every wanted month was fetched.
    """
    row = live.con.execute("select biz_reg_no6 from mart.mart_workplace where workplace_id = ?", [workplace_id]).fetchone()
    if row is None:
        return None
    # The source sends the number as an integer, so it has no leading zeros.
    number = str(int(row[0]))
    earlier = [p for p in live.publications[1:]]
    if start is not None:
        # A publication describes the month before the one it is published in.
        earlier = [p for p in earlier if p.title_date > start]
    elif months is not None:
        earlier = earlier[: max(0, months - 1)]

    # The column has had more than one name; try them in the order of the mapping
    # and keep the one that worked, going back in time.
    names = nps_columns()["biz_reg_no"]
    current = 0
    stopped = None
    for done, publication in enumerate(earlier, 1):
        try:
            records = []
            for page in range(1, MAX_PAGES_PER_MONTH + 1):
                body = None
                while current < len(names):
                    body = client.fetch_matching(publication.publication_id, {(names[current], "EQ"): number}, page=page)
                    if body is not None:
                        break
                    current += 1
                if body is None:
                    break
                data = json.loads(body)
                records += data.get("data", [])
                if len(records) >= int(data.get("matchCount", 0)) or len(data.get("data", [])) < PER_PAGE:
                    break
            if current >= len(names):
                stopped = f"no known column name works for {publication.title}"
                break
            _add(live.con, publication, records)
        except FetchError as exc:
            stopped = str(exc)
            break
        progress(done, len(earlier))
    live.months_fetched = _rebuild(live.con)
    return stopped
