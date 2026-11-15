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

-- One row per company name asked of the corporate outline API. A name that
-- matched nothing is recorded too, so it is not asked again.
CREATE TABLE IF NOT EXISTS meta.fsc_query (
    query_name    VARCHAR PRIMARY KEY,
    total_count   INTEGER NOT NULL,
    pages_fetched INTEGER NOT NULL,
    truncated     BOOLEAN NOT NULL,   -- more matches than the pages that were fetched
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The responses to those questions, the text exactly as received.
CREATE TABLE IF NOT EXISTS raw.fsc_corp_outline_page (
    query_name VARCHAR NOT NULL,
    page       INTEGER NOT NULL,
    body       VARCHAR NOT NULL,
    PRIMARY KEY (query_name, page)
);

-- Answers to the conditional queries of a live lookup, one row per query.
-- A publication never changes, so an answer stays valid for good.
CREATE TABLE IF NOT EXISTS meta.nps_live_query (
    publication_id VARCHAR NOT NULL,
    column_name    VARCHAR NOT NULL,
    operator       VARCHAR NOT NULL,   -- LIKE or EQ
    value          VARCHAR NOT NULL,
    match_count    INTEGER NOT NULL,
    pages_fetched  INTEGER NOT NULL,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (publication_id, column_name, operator, value)
);

-- The pages of those answers, exactly as received. Kept apart from the
-- collected publications so the dbt build only ever sees whole publications.
CREATE TABLE IF NOT EXISTS raw.nps_live_page (
    publication_id VARCHAR NOT NULL,
    column_name    VARCHAR NOT NULL,
    operator       VARCHAR NOT NULL,
    value          VARCHAR NOT NULL,
    page           INTEGER NOT NULL,
    body           VARCHAR NOT NULL,
    PRIMARY KEY (publication_id, column_name, operator, value, page)
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

    def fsc_queried_names(self) -> set[str]:
        with self.connect() as con:
            return {row[0] for row in con.execute("SELECT query_name FROM meta.fsc_query").fetchall()}

    def record_fsc_response(self, query_name: str, pages: list[str], total_count: int, truncated: bool) -> None:
        """Store every page of one name's answer, replacing an earlier answer to the same name."""
        with self.connect() as con:
            con.execute("BEGIN")
            try:
                con.execute("DELETE FROM raw.fsc_corp_outline_page WHERE query_name = ?", [query_name])
                con.executemany(
                    "INSERT INTO raw.fsc_corp_outline_page (query_name, page, body) VALUES (?, ?, ?)",
                    [(query_name, number, body) for number, body in enumerate(pages, 1)],
                )
                con.execute(
                    """
                    INSERT OR REPLACE INTO meta.fsc_query (query_name, total_count, pages_fetched, truncated)
                    VALUES (?, ?, ?, ?)
                    """,
                    [query_name, total_count, len(pages), truncated],
                )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise

    def nps_live_pages(self, publication_id: str, column_name: str, operator: str, value: str) -> list[str] | None:
        """A stored answer to one conditional query, page by page; None if never asked."""
        with self.connect() as con:
            try:
                asked = con.execute(
                    "SELECT 1 FROM meta.nps_live_query WHERE publication_id = ? AND column_name = ? AND operator = ? AND value = ?",
                    [publication_id, column_name, operator, value],
                ).fetchone()
            except duckdb.CatalogException:
                return None  # a database made before live lookups were stored
            if not asked:
                return None
            rows = con.execute(
                """
                SELECT body FROM raw.nps_live_page
                WHERE publication_id = ? AND column_name = ? AND operator = ? AND value = ?
                ORDER BY page
                """,
                [publication_id, column_name, operator, value],
            ).fetchall()
        return [row[0] for row in rows]

    def record_nps_live_answer(
        self, publication_id: str, column_name: str, operator: str, value: str, pages: list[str], match_count: int
    ) -> None:
        key = [publication_id, column_name, operator, value]
        with self.connect() as con:
            con.execute("BEGIN")
            try:
                con.execute(
                    "DELETE FROM raw.nps_live_page WHERE publication_id = ? AND column_name = ? AND operator = ? AND value = ?", key
                )
                con.executemany(
                    "INSERT INTO raw.nps_live_page VALUES (?, ?, ?, ?, ?, ?)",
                    [(*key, number, body) for number, body in enumerate(pages, 1)],
                )
                con.execute(
                    "INSERT OR REPLACE INTO meta.nps_live_query (publication_id, column_name, operator, value, match_count, pages_fetched) VALUES (?, ?, ?, ?, ?, ?)",
                    [*key, match_count, len(pages)],
                )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise

    def fsc_pages(self, query_name: str) -> list[str] | None:
        """The stored answer to one name, page by page; None if the name was never asked."""
        with self.connect() as con:
            try:
                asked = con.execute("SELECT 1 FROM meta.fsc_query WHERE query_name = ?", [query_name]).fetchone()
            except duckdb.CatalogException:
                return None  # a database made before company disclosures were collected
            if not asked:
                return None
            rows = con.execute(
                "SELECT body FROM raw.fsc_corp_outline_page WHERE query_name = ? ORDER BY page", [query_name]
            ).fetchall()
        return [row[0] for row in rows]

    def fsc_counts(self) -> tuple[int, int, int]:
        """(names asked, names with at least one match, names whose answer was truncated)."""
        with self.connect() as con:
            return con.execute(
                "SELECT count(*), count(*) FILTER (WHERE total_count > 0), count(*) FILTER (WHERE truncated) FROM meta.fsc_query"
            ).fetchone()
