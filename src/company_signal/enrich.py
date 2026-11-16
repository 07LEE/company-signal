"""Collects what companies disclose about themselves, to go with the pension data.

The lookup tables say which workplaces exist; this asks the corporate outline
API about them by name, largest first, and keeps every response as received.
Which response belongs to which workplace is decided later, in dbt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from .fsc import ROWS_PER_PAGE, DailyLimitReached, FscClient
from .sqlfunctions import register_functions
from .store import Store

# An answer older than this many days is asked again once every name has been
# asked once. A company files new figures about once a year; the answer itself
# carries no date that says when its content last changed.
REFRESH_AFTER_DAYS = 30

# A common name can match thousands of corporations. Past this many pages the
# query is recorded as truncated instead of spending the day's quota on it.
MAX_PAGES_PER_NAME = 3

# Which workplaces can be asked about, and by which name, are fsc_can_ask and
# fsc_query_name: the same functions the lookup and the dbt models use.
CANDIDATES_SQL = """
select fsc_query_name(name_norm) as query_name
from mart.mart_workplace
where in_latest_data and fsc_can_ask(workplace_type_code, biz_reg_no6, workplace_name, name_norm)
group by 1
order by max(subscriber_count) desc, query_name
"""


def candidate_names(warehouse_path: Path) -> list[str]:
    """Search names for the API, the largest workplace's name first."""
    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        register_functions(con, temporary=True)
        return [row[0] for row in con.execute(CANDIDATES_SQL).fetchall()]
    except duckdb.CatalogException as exc:
        raise SystemExit("The lookup tables are missing or out of date. Rebuild them: dbt build --project-dir transform --profiles-dir transform") from exc
    finally:
        con.close()


def fetch_name(client: FscClient, name: str, calls_left: int) -> tuple[list[str], int, bool] | None:
    """All pages for one name, up to the page cap.

    Returns (response texts, total matches, truncated), or None when the calls
    left are not enough to finish the name; a name is stored whole or not at all.
    """
    first, total = client.fetch_outline_page(name, 1)
    pages = [first]
    wanted = min(math.ceil(total / ROWS_PER_PAGE), MAX_PAGES_PER_NAME)
    if wanted > calls_left:
        return None
    for page in range(2, wanted + 1):
        pages.append(client.fetch_outline_page(name, page)[0])
    return pages, total, total > len(pages) * ROWS_PER_PAGE


@dataclass(frozen=True)
class EnrichResult:
    names_asked: int
    calls: int
    names_left: int
    limit_reached: bool  # the key's daily limit, not --max-calls
    names_refreshed: int = 0  # of names_asked, those asked again because their answer was old


def enrich(
    client: FscClient,
    store: Store,
    warehouse_path: Path,
    max_calls: int,
    log=print,
    refresh_after_days: int = REFRESH_AFTER_DAYS,
    now: datetime | None = None,
) -> EnrichResult:
    """Ask about names never asked first, then, with the calls left, names whose answer is old, oldest first."""
    store.init()
    names = candidate_names(warehouse_path)
    done = store.fsc_queried_names()
    unasked = [name for name in names if name not in done]
    # Only names that are still asked about: a workplace gone from the data is not asked again.
    current = set(names)
    cutoff = (now or datetime.now().astimezone()) - timedelta(days=refresh_after_days)
    stale = [name for name in store.fsc_names_asked_before(cutoff) if name in current]
    todo = unasked + stale
    log(
        f"{len(names):,} company names to ask about: {len(unasked):,} never asked,"
        f" {len(stale):,} with an answer older than {refresh_after_days} days"
    )

    calls = asked = 0
    limit_reached = False
    for name in todo:
        if calls >= max_calls:
            break
        try:
            result = fetch_name(client, name, max_calls - calls)
        except DailyLimitReached:
            log("The API key's daily limit is used up.")
            limit_reached = True
            break
        if result is None:
            break
        pages, total, truncated = result
        store.record_fsc_response(name, pages, total, truncated)
        calls += len(pages)
        asked += 1
        if asked % 500 == 0:
            log(f"  {asked:,} names, {calls:,} calls")
    refreshed = max(0, asked - len(unasked))
    log(
        f"Asked about {asked:,} names with {calls:,} calls ({refreshed:,} asked again)."
        f" {len(todo) - asked:,} names left; run again to continue."
    )
    return EnrichResult(
        names_asked=asked, calls=calls, names_left=len(todo) - asked, limit_reached=limit_reached, names_refreshed=refreshed
    )
