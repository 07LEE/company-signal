"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from contextlib import closing
from datetime import date

import duckdb

from .api import Client, FetchError
from .collect import collect
from .config import load_settings
from .enrich import enrich
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
    load_overall,
    load_years,
    open_warehouse,
    pick_months,
    search,
)
from .store import Store


def cmd_collect(args: argparse.Namespace) -> int:
    settings = load_settings()
    client = Client(settings.service_key, interval=args.interval)
    try:
        collect(client, Store(settings.db_path), settings.raw_dir, args.months, args.max_pages)
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
        enrich(client, Store(settings.db_path), settings.warehouse_path, args.max_calls)
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


def cmd_status(args: argparse.Namespace) -> int:
    settings = load_settings(require_key=False)
    if not settings.db_path.exists():
        print("No data collected yet.")
        return 0
    store = Store(settings.db_path)
    known, rows = store.counts()
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


def month_count(text: str) -> int | None:
    """--months value: a positive number, or "all" (returned as None)."""
    if text == "all":
        return None
    if not text.isdecimal() or int(text) < 1:
        raise argparse.ArgumentTypeError('give a number of months, or "all"')
    return int(text)


def month(text: str) -> date:
    """--from / --to value: a month written as YYYY-MM."""
    try:
        year, month_number = text.split("-")
        return date(int(year), int(month_number), 1)
    except ValueError:
        raise argparse.ArgumentTypeError("give a month as YYYY-MM, for example 2025-03") from None


def cmd_search(args: argparse.Namespace) -> int:
    if args.start and args.end and args.start > args.end:
        print("--from must not be later than --to.", file=sys.stderr)
        return 2
    warehouse = load_settings(require_key=False).warehouse_path
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
        with closing(open_warehouse(warehouse)) as con:
            hits, total = search(con, name, args.limit)
        print(format_hits(hits, total, name))
        # Piped output gets the list only; a person at a terminal gets to pick one.
        if not sys.stdin.isatty():
            return 0
        selected = choose(hits)
        if selected is None:
            return 0

        with closing(open_warehouse(warehouse)) as con:
            workplace, months = load_detail(con, selected.workplace_id)
            years = load_years(con, selected.workplace_id)
            overall = load_overall(con, selected.workplace_id)
        shown = pick_months(months, last=args.months, start=args.start, end=args.end)
        print()
        print(format_summary(workplace, months))
        print()
        browse(months, shown, years, overall)
        print()
        print(format_notes(workplace, months))
    except WarehouseMissing as exc:
        print(exc, file=sys.stderr)
        return 1
    except LookupError:
        print("That workplace is no longer in the warehouse; it was rebuilt meanwhile. Search again.", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print()
        return 130
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="company-signal")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="download publications, newest first, into the database")
    p.add_argument("--months", type=int, default=13, help="number of publications to download (default 13)")
    p.add_argument("--max-pages", type=int, default=None, help="max pages per publication (for testing)")
    p.add_argument("--interval", type=float, default=0.2, help="seconds to wait between calls")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("enrich", help="collect company disclosures for the workplaces, largest first")
    p.add_argument("--max-calls", type=int, default=9000, help="API calls to spend in this run (default 9000; the key allows 10000 a day)")
    p.add_argument("--interval", type=float, default=0.1, help="seconds to wait between calls")
    p.set_defaults(func=cmd_enrich)

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
    p.set_defaults(func=cmd_search)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
