"""Looks up workplaces in the warehouse built by dbt.

The lookup never calls the API. It only reads the local mart tables.
"""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

WORKPLACE_TYPES = {"1": "corporation", "2": "individual"}

# Below this many subscribers in the previous month, one or two people move a
# rate by tens of percent, so only the counts are shown.
MIN_HEADCOUNT_FOR_RATES = 5

# Months of history shown when no range is asked for.
DEFAULT_MONTHS = 13


class WarehouseMissing(RuntimeError):
    """The warehouse file has not been built yet."""


@dataclass(frozen=True)
class WorkplaceHit:
    workplace_id: str
    name: str
    road_address: str | None
    industry_name: str | None
    workplace_type_code: str
    subscriber_count: int
    latest_base_ym: date
    in_latest_data: bool


def open_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    if not path.exists():
        raise WarehouseMissing(
            f"{path} does not exist. Build it first: dbt build --project-dir transform --profiles-dir transform"
        )
    return duckdb.connect(str(path), read_only=True)


# Workplaces still present in the latest month come first, then an exact name
# match, then the larger workplace. normalize_name is the same SQL function the
# dbt models use to build name_norm, so the query and the data are normalized alike.
SEARCH_SQL = """
select
    workplace_id, workplace_name, road_address, industry_name, workplace_type_code,
    subscriber_count, latest_base_ym, in_latest_data,
    count(*) over () as total_matches
from mart.mart_workplace
where contains(name_norm, $needle)
order by in_latest_data desc, (name_norm = $needle) desc, subscriber_count desc, workplace_name
limit $limit
"""


def search(con: duckdb.DuckDBPyConnection, query: str, limit: int = 20) -> tuple[list[WorkplaceHit], int]:
    """Workplaces whose name contains the query. Returns (hits shown, total matches)."""
    needle = con.execute("select normalize_name(?)", [query]).fetchone()[0]
    if not needle:
        # A query such as "(주)" normalizes to nothing and would match every workplace.
        return [], 0
    rows = con.execute(SEARCH_SQL, {"needle": needle, "limit": limit}).fetchall()
    hits = [WorkplaceHit(*row[:8]) for row in rows]
    return hits, (rows[0][8] if rows else 0)


def display_width(text: str) -> int:
    """Terminal columns a string takes. Korean characters are two columns wide."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def fit(text: str | None, width: int) -> str:
    """Pad or cut a string to exactly `width` terminal columns."""
    text = text or ""
    if display_width(text) > width:
        cut = ""
        for ch in text:
            if display_width(cut + ch) > width - 1:
                break
            cut += ch
        text = cut + "…"
    return text + " " * (width - display_width(text))


def format_hits(hits: list[WorkplaceHit], total: int, query: str) -> str:
    if not hits:
        return f'No workplace matches "{query}".'
    noun = "workplace matches" if total == 1 else "workplaces match"
    lines = [f'{total:,} {noun} "{query}"' + (f", showing the first {len(hits)}" if total > len(hits) else "")]
    lines.append(f"{'#':>3}  {fit('name', 34)} {'subscribers':>11}  {fit('address', 30)} {fit('industry', 26)}")
    for number, hit in enumerate(hits, 1):
        line = (
            f"{number:>3}  {fit(hit.name, 34)} {hit.subscriber_count:>11,}  "
            f"{fit(hit.road_address, 30)} {fit(hit.industry_name, 26)}"
        )
        if not hit.in_latest_data:
            line += f" (last seen {hit.latest_base_ym:%Y-%m})"
        lines.append(line.rstrip())
    return "\n".join(lines)


def choose(hits: list[WorkplaceHit], ask=input) -> WorkplaceHit | None:
    """Ask for a number from the list. Returns None when the user cancels.

    A single hit is returned without asking.
    """
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    while True:
        try:
            answer = ask(f"Number to open (1-{len(hits)}), or Enter to cancel: ").strip()
        except EOFError:
            return None
        if not answer:
            return None
        if answer.isdecimal() and 1 <= int(answer) <= len(hits):
            return hits[int(answer) - 1]
        print(f"Enter a number from 1 to {len(hits)}.")


def load_detail(con: duckdb.DuckDBPyConnection, workplace_id: str) -> tuple[dict, list[dict]]:
    """One workplace and all of its months, oldest first."""

    def rows(sql: str, params: list) -> list[dict]:
        cursor = con.execute(sql, params)
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    workplace = rows("select * from mart.mart_workplace where workplace_id = ?", [workplace_id])
    if not workplace:
        raise LookupError(f"no workplace with id {workplace_id}")
    months = rows(
        "select * from mart.mart_workplace_monthly where workplace_id = ? order by base_ym",
        [workplace_id],
    )
    return workplace[0], months


def pick_months(
    months: list[dict],
    last: int | None = DEFAULT_MONTHS,
    start: date | None = None,
    end: date | None = None,
) -> list[dict]:
    """The part of the history to show.

    A start or end month selects a range and `last` is ignored. Otherwise the
    latest `last` months are taken; None means all of them.
    """
    if start or end:
        return [m for m in months if (not start or m["base_ym"] >= start) and (not end or m["base_ym"] <= end)]
    return months if last is None else months[-last:]


def shown_rate(rate: float | None, previous_count: int | None) -> str | None:
    """A rate as text, or None when it is unknown or the workplace is too small for it to mean much."""
    if rate is None or previous_count is None or previous_count < MIN_HEADCOUNT_FOR_RATES:
        return None
    return f"{rate * 100:.1f}%"


def format_history(months: list[dict], shown: list[dict]) -> str:
    """The monthly table for the months in `shown`, out of the whole history `months`."""
    if not shown:
        return "Monthly history\n  No data in this range."
    title = "Monthly history"
    if len(shown) < len(months):
        title += f" ({shown[0]['base_ym']:%Y-%m} to {shown[-1]['base_ym']:%Y-%m}, {len(shown)} of {len(months)} months)"
    lines = [title, f"  {'month':<8}{'subscribers':>12}{'joined':>8}{'left':>7}{'join %':>9}{'leave %':>9}{'est. income':>14}"]
    for m in shown:
        previous = m["previous_subscriber_count"]
        income = m["est_avg_monthly_income"]
        lines.append(
            f"  {m['base_ym']:%Y-%m} {m['subscriber_count']:>12,}{m['new_subscriber_count']:>8,}{m['lost_subscriber_count']:>7,}"
            f"{shown_rate(m['hire_rate'], previous) or '-':>9}{shown_rate(m['attrition_rate'], previous) or '-':>9}"
            f"{f'{income:,}' if income is not None else '-':>14}"
        )
    return "\n".join(lines)


def format_summary(workplace: dict, months: list[dict]) -> str:
    """Who the workplace is and its latest month. This part stays put while the history is paged."""
    latest = months[-1]
    month_count = workplace["months_observed"]
    lines = [
        workplace["workplace_name"],
        f"  address    {workplace['road_address'] or '-'}",
        f"  industry   {workplace['industry_name'] or '-'}",
        f"  type       {WORKPLACE_TYPES.get(workplace['workplace_type_code'], 'unknown')}"
        + (f", in the pension scheme since {workplace['applied_on']}" if workplace["applied_on"] else ""),
        f"  data       {workplace['first_base_ym']:%Y-%m} to {workplace['latest_base_ym']:%Y-%m}"
        f" ({month_count} month{'s' if month_count != 1 else ''})",
        "",
        f"Latest month: {latest['base_ym']:%Y-%m}",
        f"  {'subscribers':<26}{latest['subscriber_count']:>12,}",
    ]

    previous = latest["previous_subscriber_count"]
    for label, count, rate in (
        ("joined", latest["new_subscriber_count"], latest["hire_rate"]),
        ("left", latest["lost_subscriber_count"], latest["attrition_rate"]),
    ):
        text = shown_rate(rate, previous)
        suffix = f"   {text} of the previous month's {previous:,}" if text else ""
        lines.append(f"  {label:<26}{count:>12,}{suffix}")

    income = latest["est_avg_monthly_income"]
    lines.append(f"  {'est. avg monthly income':<26}{f'{income:,} KRW' if income is not None else '-':>12}")
    yearly = workplace["attrition_rate_12m"]
    if yearly is not None:
        lines.append(f"  {'12-month attrition':<26}{f'{yearly * 100:.1f}%':>12}")
    else:
        lines.append(f"  {'12-month attrition':<26}{'-':>12}   needs 12 months of data")

    return "\n".join(lines)


def format_notes(workplace: dict, months: list[dict]) -> str:
    lines = [
        "Notes",
        "  - Source: National Pension Service workplace data (data.go.kr 15083277), monthly.",
        '  - "joined" and "left" include resumed and suspended payments, so they are not',
        "    exactly hires and resignations.",
        "  - Est. income is the billed contribution per subscriber divided by the contribution",
        "    rate. Income above the ceiling counts as the ceiling, so high-income workplaces",
        "    come out lower than they are.",
        f"  - Rates are hidden when the previous month had fewer than {MIN_HEADCOUNT_FOR_RATES} subscribers.",
    ]
    if any(m["source_row_count"] > 1 for m in months):
        lines.append("  - Some months add up several source rows the data cannot tell apart.")
    if not workplace["in_latest_data"]:
        lines.append(
            f"  - Not in the latest data. Last seen {workplace['latest_base_ym']:%Y-%m}: it withdrew or fell"
            " below the size that is published."
        )
    return "\n".join(lines)


BROWSE_PROMPT = "[<] older  [>] newer  [a] all  [q] quit: "


def screen_rows(text: str, columns: int) -> int:
    """Terminal rows a block of text takes, counting lines too wide for the terminal."""
    return sum(max(1, -(-display_width(line) // columns)) for line in text.split("\n"))


def browse(
    months: list[dict],
    shown: list[dict],
    ask=input,
    in_place: bool | None = None,
    terminal_size=shutil.get_terminal_size,
) -> None:
    """Print the history table, then let the user page through it.

    `<` and `>` move the window by its own length toward older or newer months,
    `a` shows everything, and `q`, Enter or end of input leaves. Nothing is
    asked when the whole history is already on screen.

    On a terminal the table is redrawn where it stands, so the summary above
    stays in view. That needs the cursor to move back up over what was drawn,
    which only works while the table fits on the screen; a longer table is
    printed below instead, as it also is when the output is not a terminal.
    Only the table and the prompt are redrawn, to keep that part short: the
    caller prints the notes once paging is over.
    """
    if in_place is None:
        in_place = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"

    size = len(shown) or DEFAULT_MONTHS
    # The window is months[low:high]. An empty selection starts past the newest month.
    low = next((i for i, m in enumerate(months) if shown and m is shown[0]), len(months))
    high = low + len(shown)

    def block() -> str:
        return format_history(months, months[low:high])

    drawn = block()  # What is on screen right above the prompt, or None if it cannot be erased.
    print(drawn)
    if len(shown) >= len(months):
        return

    def erase_drawn(status_shown: str) -> bool:
        """Clear the table, a status line and the prompt line, if they can still be reached."""
        columns, rows = terminal_size()
        if not in_place or drawn is None:
            return False
        # Rows between the top of the table and the cursor: the table, a status line, the prompt line.
        used = screen_rows(drawn, columns) + (1 if status_shown else 0) + 1
        if used >= rows:
            return False
        # Go up to the first drawn row and clear from there to the end of the screen.
        print(f"\x1b[{used}F\x1b[J", end="")
        return True

    status = ""
    while True:
        try:
            answer = ask(BROWSE_PROMPT).strip().lower()
        except EOFError:
            return
        if answer in ("", "q"):
            # Leave only the table behind, without the prompt or a leftover message.
            if erase_drawn(status):
                print(drawn)
            return

        previous_status, status = status, ""
        if answer == "<":
            if low == 0:
                status = "No earlier months."
            else:
                low = max(0, low - size)
                high = min(len(months), low + size)
        elif answer == ">":
            if high >= len(months):
                status = "No later months."
            else:
                high = min(len(months), high + size)
                low = max(0, high - size)
        elif answer == "a":
            low, high = 0, len(months)
        else:
            status = "Type <, >, a or q."

        if erase_drawn(previous_status):
            drawn = block()
            print(drawn)
            if status:
                print(status)
        elif in_place and not status:
            # Too tall to erase: start a fresh copy below, which the next key can replace if it fits.
            drawn = block()
            print()
            print(drawn)
        else:
            drawn = None
            print(status if status else "\n" + block())
