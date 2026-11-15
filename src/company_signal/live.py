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
from .store import Store

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
    calls_made: int = 0  # conditional queries sent to the API
    answers_reused: int = 0  # conditional queries answered from the store

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


def _ask(
    client: Client,
    store: Store | None,
    publication: Publication,
    names: list[str],
    first_name: int,
    operator: str,
    value: str,
    max_pages: int,
    live: LiveResult | None = None,
) -> tuple[list[dict], int, int] | None:
    """Answer one conditional query of one publication: from the store if it was
    asked before, otherwise from the API, storing the answer.

    `names` are the names the column has had; the API is tried from
    `first_name` on, since a name that failed for a newer publication will not
    work for an older one either. Returns (records, match count, index of the
    name that worked), or None when no name works.
    """
    if store is not None:
        for index, name in enumerate(names):
            pages = store.nps_live_pages(publication.publication_id, name, operator, value)
            if pages is not None:
                if live is not None:
                    live.answers_reused += 1
                records = [r for body in pages for r in json.loads(body).get("data", [])]
                match = int(json.loads(pages[0]).get("matchCount", len(records))) if pages else 0
                return records, match, index
    for index in range(first_name, len(names)):
        pages = []
        match = 0
        for page in range(1, max_pages + 1):
            body = client.fetch_matching(publication.publication_id, {(names[index], operator): value}, page=page)
            if live is not None:
                live.calls_made += 1
            if body is None:
                break  # the publication has no column of this name
            pages.append(body.decode("utf-8") if isinstance(body, bytes) else body)
            data = json.loads(body)
            match = int(data.get("matchCount", 0))
            if sum(len(json.loads(b).get("data", [])) for b in pages) >= match or len(data.get("data", [])) < PER_PAGE:
                break
        if not pages:
            continue
        if store is not None:
            store.record_nps_live_answer(publication.publication_id, names[index], operator, value, pages, match)
        records = [r for b in pages for r in json.loads(b).get("data", [])]
        return records, match, index
    return None


def search_live(client: Client, query: str, store: Store | None = None, announce=lambda: None) -> LiveResult | None:
    """Ask the latest publication for workplaces whose name contains the query.

    The query is normalized first, the same way the warehouse names are, so
    "(주)한빛" also finds "주식회사 한빛". None when nothing is left to search for,
    in which case the API is not called. `announce` is called just before it is.
    With a store, an answer given before is reused and a new one is kept.
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
    live = LiveResult(con=con, publications=publications, match_count=0, fetched=0)
    # The latest publication uses today's column names: the first name in the mapping.
    answer = _ask(client, store, latest, nps_columns()["workplace_name"][:1], 0, "LIKE", needle, 1, live)
    records, match, _ = answer if answer else ([], 0, 0)
    _add(con, latest, records)
    live.match_count, live.fetched = match, len(records)
    live.months_fetched = _rebuild(con)
    return live


def fetch_history(
    client: Client,
    live: LiveResult,
    workplace_id: str,
    months: int | None,
    start: date | None = None,
    progress=lambda done, total: None,
    store: Store | None = None,
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
    earlier = live.publications[1:]
    if start is not None:
        # A publication describes the month before the one it is published in.
        earlier = [p for p in earlier if p.title_date > start]
    elif months is not None:
        earlier = earlier[: max(0, months - 1)]

    # The column has had more than one name; going back in time, a name that
    # stopped working is not tried again.
    names = nps_columns()["biz_reg_no"]
    current = 0
    stopped = None
    for done, publication in enumerate(earlier, 1):
        try:
            answer = _ask(client, store, publication, names, current, "EQ", number, MAX_PAGES_PER_MONTH, live)
        except FetchError as exc:
            stopped = str(exc)
            break
        if answer is None:
            stopped = f"no known column name works for {publication.title}"
            break
        records, _, current = answer
        _add(live.con, publication, records)
        progress(done, len(earlier))
    live.months_fetched = _rebuild(live.con)
    return stopped
