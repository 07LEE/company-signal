"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from contextlib import closing, nullcontext
from datetime import date, datetime

import duckdb

from .api import Client, FetchError
from .collect import collect
from .config import KEY_ENV, Settings, load_settings
from .disclosure import company_from_answer, company_loader, query_name_if_askable
from .enrich import REFRESH_AFTER_DAYS, enrich
from .live import fetch_history, search_live
from .fsc import FscClient
from .lookup import (
    DEFAULT_MONTHS,
    WarehouseMissing,
    browse,
    choose,
    format_hits,
    format_notes,
    format_summary,
    load_detail,
    load_company,
    load_overall,
    load_years,
    open_warehouse,
    pick_months,
    search,
)
from .pipeline import StepFailed, Steps, run_dbt, update
from .store import Store


def cmd_collect(args: argparse.Namespace) -> int:
    settings = load_settings()
    client = Client(settings.service_key, interval=args.interval)
    try:
        collect(client, Store(settings.db_path), settings.raw_dir, args.months, args.max_pages, workers=args.workers)
    except FetchError as exc:
        print(f"Collection stopped: {exc}", file=sys.stderr)
        print("Run the same command again to resume where it stopped.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Run the same command again to resume.", file=sys.stderr)
        return 130
    finally:
        client.close()
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.warehouse_path.exists():
        print("Build the lookup tables first: dbt build --project-dir transform --profiles-dir transform", file=sys.stderr)
        return 1
    client = FscClient(settings.service_key, interval=args.interval)
    try:
        enrich(client, Store(settings.db_path), settings.warehouse_path, args.max_calls, refresh_after_days=args.refresh_after_days)
    except FetchError as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        print("Run the same command again to continue.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Run the same command again to continue.", file=sys.stderr)
        return 130
    finally:
        client.close()
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    settings = load_settings()
    nps = Client(settings.service_key)
    fsc = FscClient(settings.service_key)
    store = Store(settings.db_path)
    steps = Steps(
        collect=lambda: collect(nps, store, settings.raw_dir, args.months, workers=args.workers),
        enrich=lambda: enrich(fsc, store, settings.warehouse_path, args.max_calls),
        build=lambda select: run_dbt(settings.root / "transform", settings.data_dir, select),
        warehouse_exists=settings.warehouse_path.exists,
    )
    try:
        summary = update(steps, force_build=args.force_build)
    except StepFailed as exc:
        print("\nUpdate stopped. Run the same command again to resume.", file=sys.stderr)
        for line in exc.args[0]:
            print(f"  {line}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Run the same command again to resume.", file=sys.stderr)
        return 130
    finally:
        nps.close()
        fsc.close()
    print("\nUpdate done.")
    for line in summary:
        print(f"  {line}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = load_settings(require_key=False)
    if not settings.db_path.exists():
        print("No data collected yet.")
        return 0
    store = Store(settings.db_path)
    known, rows = store.counts()
    print(build_note(settings, store))
    print(f"{known} publications known, {rows:,} raw records")
    print(f"{'published':<12}{'status':<13}{'fetched':>12}{'total':>12}{'pages':>11}  title")
    for title_date, status, total, fetched, pages, page_count, title in store.summary():
        print(
            f"{str(title_date):<12}{status:<13}{fetched:>12,}{(total or 0):>12,}"
            f"{f'{pages}/{page_count or 0}':>11}  {title}"
        )
    try:
        asked, matched, truncated = store.fsc_counts()
    except duckdb.CatalogException:
        return 0  # a database made before company disclosures were collected
    if asked:
        print(f"company disclosures: {asked:,} names asked, {matched:,} with a match, {truncated:,} truncated")
    return 0


def build_note(settings: Settings, store: Store) -> str:
    """When the lookup tables were built, and whether data has come in since."""
    if not settings.warehouse_path.exists():
        return "lookup tables: not built yet. Run company-signal update."
    # Only dbt writes the warehouse; the lookup opens it read-only.
    built_at = datetime.fromtimestamp(settings.warehouse_path.stat().st_mtime).astimezone()
    publications, answers = store.added_since(built_at)
    note = f"lookup tables: built {built_at:%Y-%m-%d %H:%M}"
    if not publications and not answers:
        return note + ", up to date"
    return note + (
        f"; since then {publications:,} publications completed and {answers:,} disclosure answers stored."
        " Run company-signal update to include them."
    )


def month_count(text: str) -> int | None:
    """--months value: a positive number, or "all" (returned as None)."""
    if text == "all":
        return None
    if not text.isdecimal() or int(text) < 1:
        raise argparse.ArgumentTypeError('give a number of months, or "all"')
    return int(text)


def positive_int(text: str) -> int:
    if not text.isdecimal() or int(text) < 1:
        raise argparse.ArgumentTypeError("give a number of 1 or more")
    return int(text)


def month(text: str) -> date:
    """--from / --to value: a month written as YYYY-MM."""
    try:
        year, month_number = text.split("-")
        return date(int(year), int(month_number), 1)
    except ValueError:
        raise argparse.ArgumentTypeError("give a month as YYYY-MM, for example 2025-03") from None


def company_for_untied(settings: Settings, workplace_id: str, query_name: str, connect=None, refresh: bool = False):
    """For a workplace the warehouse has not tied: the company, a loader, or None.

    An answer already collected for its name is used straight away; it may be
    newer than the last dbt build. Without one, or with `refresh`, the workplace
    gets a loader that asks the API when the user opens the company view, if
    there is a key to ask with; the new answer replaces the stored one.
    `connect` gives the connection that holds the workplace; by default the warehouse.
    """
    if connect is None:
        def connect():
            return closing(open_warehouse(settings.warehouse_path))
    store = Store(settings.db_path)
    pages = store.fsc_pages(query_name) if settings.db_path.exists() and not refresh else None
    if pages is not None:
        with connect() as con:
            return company_from_answer(con, workplace_id, query_name, pages)
    if settings.service_key:
        return company_loader(connect, store, settings.service_key, workplace_id, query_name, None)
    return None


def fetch_live_history(settings: Settings, live, workplace_id: str, args: argparse.Namespace) -> None:
    """Fetch as many earlier months as the view asks for (--months, --from), showing progress."""
    shown = False

    def progress(done: int, total: int) -> None:
        nonlocal shown
        shown = True
        print(f"\rFetching earlier months from the pension API: {done}/{total}", end="", flush=True)

    client = Client(settings.service_key)
    try:
        stopped = fetch_history(client, live, workplace_id, args.months, args.start, progress, Store(settings.db_path))
    finally:
        client.close()
    if shown:
        print()
    if stopped:
        print(f"Stopped fetching earlier months: {stopped}")


def cmd_search(args: argparse.Namespace) -> int:
    if args.start and args.end and args.start > args.end:
        print("--from must not be later than --to.", file=sys.stderr)
        return 2
    settings = load_settings(require_key=False)
    if args.refresh and not settings.service_key:
        print(f"--refresh asks the API, so it needs {KEY_ENV} in .env.", file=sys.stderr)
        return 2
    warehouse = settings.warehouse_path
    if not warehouse.exists() and not settings.service_key:
        print(
            f"{warehouse} does not exist. Build it (dbt build --project-dir transform --profiles-dir transform),"
            f" or put {KEY_ENV} in .env to search the pension API directly.",
            file=sys.stderr,
        )
        return 1
    live = None  # set when the search went to the API: an in-memory warehouse for this search
    try:
        name = args.name
        if name is None:
            if not sys.stdin.isatty():
                print("Give a workplace name: company-signal search NAME", file=sys.stderr)
                return 2
            name = input("Workplace name: ").strip()
            if not name:
                return 0

        # The warehouse is open only while reading, never while waiting for a
        # key. A lookup left sitting at a prompt would otherwise keep the file
        # open and block the next dbt build from writing to it.
        hits, total = [], 0
        if warehouse.exists():
            with closing(open_warehouse(warehouse)) as con:
                hits, total = search(con, name, args.limit)
        if not hits and settings.service_key:
            client = Client(settings.service_key)
            store = Store(settings.db_path)
            store.init()
            try:
                live = search_live(client, name, store, announce=lambda: print("Not in the collected data; searching the pension API..."))
            except FetchError as exc:
                print(f"Could not search the pension API: {exc}", file=sys.stderr)
                return 1
            finally:
                client.close()
            if live is not None:
                hits, total = search(live.con, name, args.limit)
                origin = "a stored answer" if live.answers_reused else "the API"
                print(f"Live lookup of {live.base_ym}, the latest month, from {origin}.")
                if live.truncated:
                    print(f"The API found {live.match_count:,} workplaces; only the first {live.fetched:,} were read. Search with a longer name.")
        print(format_hits(hits, total, name))
        # Piped output gets the list only; a person at a terminal gets to pick one.
        if not sys.stdin.isatty():
            return 0
        selected = choose(hits)
        if selected is None:
            return 0
        if live:
            fetch_live_history(settings, live, selected.workplace_id, args)

        with nullcontext(live.con) if live else closing(open_warehouse(warehouse)) as con:
            workplace, months = load_detail(con, selected.workplace_id)
            years = load_years(con, selected.workplace_id)
            overall = load_overall(con, selected.workplace_id)
            # With --refresh the tie made by the last build is set aside and the company asked again.
            company = None if args.refresh else load_company(con, selected.workplace_id)
            query_name = None if company else query_name_if_askable(con, selected.workplace_id)
        if query_name:
            # A live lookup's workplace is in its in-memory database, not in the warehouse.
            connect = (lambda: nullcontext(live.con)) if live else None
            company = company_for_untied(settings, selected.workplace_id, query_name, connect, refresh=args.refresh)
        shown = pick_months(months, last=args.months, start=args.start, end=args.end)
        print()
        print(format_summary(workplace, months))
        print()
        browse(months, shown, years, overall, company)
        print()
        print(format_notes(workplace, months))
        if live:
            print(
                f"  - Live lookup of {live.span}: {live.calls_made} API calls, {live.answers_reused} stored answers reused."
                f" Answers are kept in {settings.db_path.name} for next time."
            )
    except WarehouseMissing as exc:
        print(exc, file=sys.stderr)
        return 1
    except LookupError:
        print("That workplace is no longer in the warehouse; it was rebuilt meanwhile. Search again.", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print()
        return 130
    finally:
        if live:
            live.con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="company-signal")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="download publications, newest first, into the database")
    p.add_argument("--months", type=int, default=13, help="number of publications to download (default 13)")
    p.add_argument("--max-pages", type=int, default=None, help="max pages per publication (for testing)")
    p.add_argument("--interval", type=float, default=0.2, help="seconds each request thread waits between calls")
    p.add_argument(
        "--workers",
        type=positive_int,
        default=8,
        help="pages requested at once (default 8; more was no faster when tried)",
    )
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("enrich", help="collect company disclosures for the workplaces, largest first")
    p.add_argument("--max-calls", type=int, default=9000, help="API calls to spend in this run (default 9000; the key allows 10000 a day)")
    p.add_argument("--interval", type=float, default=0.1, help="seconds to wait between calls")
    p.add_argument(
        "--refresh-after-days",
        type=positive_int,
        default=REFRESH_AFTER_DAYS,
        help=f"once every name is asked, ask again about answers older than this (default {REFRESH_AFTER_DAYS})",
    )
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser("update", help="collect, build, add company disclosures and build again, skipping what has nothing new")
    p.add_argument("--months", type=int, default=13, help="newest publications to keep collected (default 13)")
    p.add_argument("--workers", type=positive_int, default=8, help="pages requested at once (default 8)")
    p.add_argument("--max-calls", type=int, default=9000, help="disclosure API calls to spend (default 9000)")
    p.add_argument("--force-build", action="store_true", help="build even when no new publication was completed")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("status", help="show collection progress")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="find workplaces by name and open one")
    p.add_argument("name", nargs="?", help="workplace name, or part of it; asked for when omitted")
    p.add_argument("--limit", type=int, default=20, help="max workplaces to list (default 20)")
    p.add_argument(
        "--months",
        type=month_count,
        default=DEFAULT_MONTHS,
        metavar="N|all",
        help=f"months of history to show, counted back from the latest (default {DEFAULT_MONTHS})",
    )
    p.add_argument("--from", dest="start", type=month, metavar="YYYY-MM", help="first month to show")
    p.add_argument("--to", dest="end", type=month, metavar="YYYY-MM", help="last month to show")
    p.add_argument("--refresh", action="store_true", help="ask the API for the company details again instead of using the stored answer")
    p.set_defaults(func=cmd_search)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
