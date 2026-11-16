"""Shrinks a collector database made before the page files became the only copy.

Such a database holds every pension record a second time, as JSON in
raw.nps_workplace_record. DuckDB does not give the space back when a table is
dropped, so the other tables are copied into a new file instead. The old file
is kept under another name, for the user to delete once satisfied.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .store import Store

DROPPED = ("raw", "nps_workplace_record")
OLD_SUFFIX = ".before-compact"


class CompactRefused(RuntimeError):
    """Nothing was changed; the message says why."""


@dataclass(frozen=True)
class CompactResult:
    pages_checked: int
    tables_copied: dict[str, int]  # "schema.table" -> rows
    size_before: int
    size_after: int
    old_path: Path


def check_page_files(db_path: Path, log=print) -> int:
    """Every recorded page file exists and holds what was recorded: the same
    hash of the text as received, and the same number of records."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        pages = con.execute(
            "select publication_id, page, file_path, sha256, row_count from meta.page_fetch order by all"
        ).fetchall()
    finally:
        con.close()
    problems = []
    for n, (pid, page, file_path, sha256, row_count) in enumerate(pages, 1):
        path = Path(file_path)
        if not path.exists():
            problems.append(f"{pid} page {page}: {path} is missing")
            continue
        with gzip.open(path, "rb") as f:
            body = f.read()
        if hashlib.sha256(body).hexdigest() != sha256:
            problems.append(f"{pid} page {page}: {path} does not match its recorded hash")
        elif len(json.loads(body)["data"]) != row_count:
            problems.append(f"{pid} page {page}: {path} does not hold the recorded {row_count} records")
        if n % 10_000 == 0:
            log(f"  {n:,}/{len(pages):,} page files checked")
    if problems:
        shown = "\n".join(f"  {p}" for p in problems[:20])
        more = f"\n  and {len(problems) - 20:,} more" if len(problems) > 20 else ""
        raise CompactRefused(f"{len(problems):,} page files are not as recorded; nothing was changed.\n{shown}{more}")
    return len(pages)


def compact(db_path: Path, log=print) -> CompactResult:
    """Copy every table but the record copy into a new file and put it in place of the old one."""
    old_path = db_path.with_name(db_path.name + OLD_SUFFIX)
    new_path = db_path.with_name(db_path.name + ".compacting")
    if old_path.exists():
        raise CompactRefused(f"{old_path} is still there from an earlier compaction. Delete it first; nothing was changed.")
    new_path.unlink(missing_ok=True)

    log("Checking the page files against what was recorded...")
    pages = check_page_files(db_path, log)

    log("Copying the other tables into a new file...")
    Store(new_path).init()
    con = duckdb.connect(str(new_path))
    try:
        con.execute(f"attach '{db_path}' as old (read_only)")
        tables = con.execute(
            "select table_schema, table_name from information_schema.tables"
            " where table_catalog = 'old' and table_type = 'BASE TABLE' order by all"
        ).fetchall()
        copied = {}
        for schema, table in tables:
            if (schema, table) == DROPPED:
                continue
            con.execute(f"insert into {schema}.{table} by name select * from old.{schema}.{table}")
            before = con.execute(f"select count(*) from old.{schema}.{table}").fetchone()[0]
            after = con.execute(f"select count(*) from {schema}.{table}").fetchone()[0]
            if before != after:
                raise CompactRefused(f"{schema}.{table}: {before:,} rows before, {after:,} after; the old file is unchanged.")
            copied[f"{schema}.{table}"] = after
        con.execute("detach old")
        con.execute("checkpoint")
    except BaseException:
        con.close()
        new_path.unlink(missing_ok=True)
        raise
    con.close()

    size_before = db_path.stat().st_size
    db_path.rename(old_path)
    new_path.rename(db_path)
    return CompactResult(pages, copied, size_before, db_path.stat().st_size, old_path)
