"""A company's disclosures for one workplace, when the user asks for them.

Collected answers are used first. A workplace whose name has not been asked yet
is asked about there and then, with the user's own API key, and the answer is
stored like a collected one so the next dbt build includes it.

Reading the answer, choosing the figures and tying the workplace to a
corporation are the SQL functions the dbt models use, run here over the answer
for this one workplace.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import duckdb

from .api import FetchError
from .enrich import MAX_PAGES_PER_NAME, fetch_name
from .fsc import DailyLimitReached, FscClient
from .lookup import LookupFailed, open_warehouse
from .store import Store

ELIGIBILITY_SQL = """
select fsc_query_name(name_norm), fsc_can_ask(workplace_type_code, biz_reg_no6, workplace_name, name_norm)
from mart.mart_workplace
where workplace_id = ?
"""


def query_name_if_askable(con: duckdb.DuckDBPyConnection, workplace_id: str) -> str | None:
    """The name to ask the API with, or None when the workplace cannot be tied by name."""
    try:
        row = con.execute(ELIGIBILITY_SQL, [workplace_id]).fetchone()
    except duckdb.CatalogException:
        return None  # a warehouse built before company disclosures were added
    return row[0] if row and row[1] else None


def company_from_answer(con: duckdb.DuckDBPyConnection, workplace_id: str, query_name: str, pages: list[str]) -> dict | None:
    """The tied corporation, in the shape of mart_workplace_company, or None."""
    con.execute("create or replace temp table answer_page (query_name varchar, page integer, body varchar)")
    con.executemany("insert into answer_page values (?, ?, ?)", [(query_name, n, body) for n, body in enumerate(pages, 1)])
    con.execute("create or replace temp table answer_item as from fsc_outline_rows('answer_page')")
    con.execute("create or replace temp table answer_workplace as from mart.mart_workplace where workplace_id = ?", [workplace_id])
    cursor = con.execute(
        """
        select t.workplace_id, c.*
        from fsc_ties('answer_workplace', 'answer_item') as t
        inner join fsc_corporations('answer_item') as c using (crno)
        """
    )
    row = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], row)) if row else None


def company_loader(warehouse: Path, store: Store, service_key: str, workplace_id: str, query_name: str, pages: list[str] | None):
    """A function that gives the company when called; it asks the API only if `pages` is None."""

    def load() -> dict | None:
        answer = pages
        if answer is None:
            client = FscClient(service_key)
            try:
                result = fetch_name(client, query_name, MAX_PAGES_PER_NAME)
            except DailyLimitReached:
                raise LookupFailed("the API key's daily limit is used up") from None
            except FetchError as exc:
                raise LookupFailed(str(exc)) from None
            finally:
                client.close()
            answer, total, truncated = result
            try:
                store.init()  # the tables may not exist yet if disclosures were never collected
                store.record_fsc_response(query_name, answer, total, truncated)
            except duckdb.Error:
                pass  # storing only saves asking again next time; the answer is still shown
        with closing(open_warehouse(warehouse)) as con:
            return company_from_answer(con, workplace_id, query_name, answer)

    return load
