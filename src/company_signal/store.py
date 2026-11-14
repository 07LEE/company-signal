"""Records collection state and raw records in DuckDB.

Connections are opened and closed per operation, so the status command can
read the database while a collection is running.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import duckdb

from .api import Publication

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS raw;

-- One row per publication: the list read from the API spec, plus collection progress.
CREATE TABLE IF NOT EXISTS meta.publication (
    publication_id VARCHAR PRIMARY KEY,
    title          VARCHAR NOT NULL,
    title_date     DATE,
    total_count    BIGINT,
    page_count     INTEGER,
    status         VARCHAR NOT NULL DEFAULT 'pending',  -- pending, in_progress, complete
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at   TIMESTAMPTZ
);

-- One row per fetched page. This is what resuming is based on.
CREATE TABLE IF NOT EXISTS meta.page_fetch (
    publication_id VARCHAR NOT NULL,
    page           INTEGER NOT NULL,
    row_count      INTEGER NOT NULL,
    sha256         VARCHAR NOT NULL,
    file_path      VARCHAR NOT NULL,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (publication_id, page)
);

-- Source records, kept as received. Column names differ by period, so they stay as JSON.
CREATE TABLE IF NOT EXISTS raw.nps_workplace_record (
    publication_id VARCHAR NOT NULL,
    page           INTEGER NOT NULL,
    row_no         INTEGER NOT NULL,
    record         JSON NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path, lock_wait: float = 600.0) -> None:
        self.db_path = db_path
        self.lock_wait = lock_wait  # Seconds to wait for another process to release the file.

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = self._open()
        try:
            yield con
        finally:
            con.close()

    def _open(self) -> duckdb.DuckDBPyConnection:
        """Open the database, waiting while another process holds the file.

        DuckDB lets only one process open a file for writing. A dbt run or a
        status check holds it for a while, and the collector must not die for that.
        """
        deadline = time.monotonic() + self.lock_wait
        while True:
            try:
                return duckdb.connect(str(self.db_path))
            except duckdb.IOException as exc:
                if "lock" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(1)

    def init(self) -> None:
        with self.connect() as con:
            con.execute(SCHEMA)

    def upsert_publications(self, publications: list[Publication]) -> int:
        """Add only publications that are new in the spec. Returns how many were added."""
        with self.connect() as con:
            before = con.execute("SELECT count(*) FROM meta.publication").fetchone()[0]
            con.executemany(
                """
                INSERT INTO meta.publication (publication_id, title, title_date)
                VALUES (?, ?, ?)
                ON CONFLICT (publication_id) DO NOTHING
                """,
                [(p.publication_id, p.title, p.title_date) for p in publications],
            )
            after = con.execute("SELECT count(*) FROM meta.publication").fetchone()[0]
        return after - before

    def fetched_pages(self, publication_id: str) -> set[int]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT page FROM meta.page_fetch WHERE publication_id = ?", [publication_id]
            ).fetchall()
        return {r[0] for r in rows}

    def status_of(self, publication_id: str) -> str | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT status FROM meta.publication WHERE publication_id = ?", [publication_id]
            ).fetchone()
        return row[0] if row else None

    def set_totals(self, publication_id: str, total_count: int, page_count: int) -> None:
        with self.connect() as con:
            con.execute(
                """
                UPDATE meta.publication
                SET total_count = ?, page_count = ?, status = 'in_progress'
                WHERE publication_id = ?
                """,
                [total_count, page_count, publication_id],
            )

    def record_page(self, publication_id: str, page: int, sha256: str, file_path: Path) -> int:
        """Load a saved page file into the raw table and mark the page as fetched.

        Existing rows of the page are deleted first, so loading the same page
        again gives the same result.
        """
        with self.connect() as con:
            con.execute("BEGIN")
            try:
                con.execute(
                    "DELETE FROM raw.nps_workplace_record WHERE publication_id = ? AND page = ?",
                    [publication_id, page],
                )
                con.execute(
                    """
                    INSERT INTO raw.nps_workplace_record
                    SELECT ?, ?, generate_subscripts(data, 1), unnest(data)
                    FROM read_json(?, columns = {'data': 'JSON[]'}, maximum_object_size = 67108864)
                    """,
                    [publication_id, page, str(file_path)],
                )
                row_count = con.execute(
                    "SELECT count(*) FROM raw.nps_workplace_record WHERE publication_id = ? AND page = ?",
                    [publication_id, page],
                ).fetchone()[0]
                con.execute(
                    """
                    INSERT OR REPLACE INTO meta.page_fetch
                        (publication_id, page, row_count, sha256, file_path)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [publication_id, page, row_count, sha256, str(file_path)],
                )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return row_count

    def try_complete(self, publication_id: str) -> tuple[bool, int, int]:
        """Mark complete only when fetched rows equal totalCount.

        Returns (complete, fetched rows, expected rows).
        """
        with self.connect() as con:
            fetched, expected = con.execute(
                """
                SELECT coalesce((SELECT sum(row_count) FROM meta.page_fetch WHERE publication_id = p.publication_id), 0),
                       p.total_count
                FROM meta.publication p WHERE p.publication_id = ?
                """,
                [publication_id],
            ).fetchone()
            done = expected is not None and fetched == expected
            if done:
                con.execute(
                    """
                    UPDATE meta.publication SET status = 'complete', completed_at = now()
                    WHERE publication_id = ? AND status <> 'complete'
                    """,
                    [publication_id],
                )
        return done, int(fetched), int(expected or 0)

    def summary(self) -> list[tuple]:
        with self.connect() as con:
            return con.execute(
                """
                SELECT p.title_date, p.status, p.total_count,
                       coalesce(f.row_sum, 0), coalesce(f.pages, 0), p.page_count, p.title
                FROM meta.publication p
                LEFT JOIN (
                    SELECT publication_id, sum(row_count) AS row_sum, count(*) AS pages
                    FROM meta.page_fetch GROUP BY 1
                ) f USING (publication_id)
                WHERE p.status <> 'pending'
                ORDER BY p.title_date DESC NULLS LAST
                """
            ).fetchall()

    def counts(self) -> tuple[int, int]:
        with self.connect() as con:
            total = con.execute("SELECT count(*) FROM meta.publication").fetchone()[0]
            rows = con.execute("SELECT count(*) FROM raw.nps_workplace_record").fetchone()[0]
        return total, rows
