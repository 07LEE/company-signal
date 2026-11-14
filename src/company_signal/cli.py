"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys

from .api import Client, FetchError
from .collect import collect
from .config import load_settings
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
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="company-signal")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="download publications, newest first, into the database")
    p.add_argument("--months", type=int, default=13, help="number of publications to download (default 13)")
    p.add_argument("--max-pages", type=int, default=None, help="max pages per publication (for testing)")
    p.add_argument("--interval", type=float, default=0.2, help="seconds to wait between calls")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("status", help="show collection progress")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
