"""Registers the shared SQL functions on a DuckDB connection."""

from __future__ import annotations

from importlib.resources import files

import duckdb


def functions_sql() -> str:
    return files("company_signal").joinpath("sql/functions.sql").read_text(encoding="utf-8")


def register_functions(con: duckdb.DuckDBPyConnection, temporary: bool = False) -> None:
    """Create the functions. `temporary` keeps them to this connection, which is
    what a read-only connection allows and what keeps a warehouse file unchanged."""
    sql = functions_sql()
    if temporary:
        sql = sql.replace("create or replace macro ", "create or replace temp macro ")
    con.execute(sql)
