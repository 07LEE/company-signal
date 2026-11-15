"""Registers the shared SQL functions on a DuckDB connection.

Most functions are written out in sql/functions.sql. Two kinds are generated
here from data files instead, so the data stays in one readable place:

- nps_record_rows and nps_known_column, from sql/nps_columns.json: the names
  each source column has had in the pension data over the years.
- nps_contribution_rate, from sql/nps_contribution_rate.csv: the pension
  contribution rate by the month it took effect.
"""

from __future__ import annotations

import csv
import io
import json
from importlib.resources import files

import duckdb

SQL_DIR = files("company_signal").joinpath("sql")

# Canonical column -> how it is typed in the staging rows. `{value}` is the raw text.
NPS_COLUMN_TYPES = {
    "base_ym": "try_strptime({value}, ['%Y-%m', '%Y%m'])::date",
    "biz_reg_no": "lpad({value}, 6, '0')",
    "legal_dong_code": "lpad({value}, 10, '0')",
    "admin_dong_code": "lpad({value}, 10, '0')",
    "industry_code": "lpad({value}, 6, '0')",
    "applied_on": "try_strptime({value}, ['%Y-%m-%d', '%Y%m%d'])::date",
    "rejoined_on": "try_strptime({value}, ['%Y-%m-%d', '%Y%m%d'])::date",
    "withdrawn_on": "try_strptime({value}, ['%Y-%m-%d', '%Y%m%d'])::date",
    "subscriber_count": "({value})::integer",
    "billed_amount": "({value})::bigint",
    "new_subscriber_count": "({value})::integer",
    "lost_subscriber_count": "({value})::integer",
}
# The staging column names that differ from the canonical name.
NPS_COLUMN_ALIASES = {"biz_reg_no": "biz_reg_no6"}
# Staging column order.
NPS_STAGING_ORDER = [
    "base_ym", "workplace_name", "biz_reg_no", "join_status_code", "workplace_type_code", "zip_code",
    "lot_address", "road_address", "legal_dong_code", "admin_dong_code", "sido_code", "sigungu_code",
    "emd_code", "industry_code", "industry_name", "applied_on", "rejoined_on", "withdrawn_on",
    "subscriber_count", "billed_amount", "new_subscriber_count", "lost_subscriber_count",
]


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def nps_columns() -> dict[str, list[str]]:
    return json.loads(SQL_DIR.joinpath("nps_columns.json").read_text(encoding="utf-8"))


def _nps_sql() -> str:
    columns = nps_columns()
    assert set(columns) == set(NPS_STAGING_ORDER), "nps_columns.json and NPS_STAGING_ORDER disagree"
    selects = []
    for canonical in NPS_STAGING_ORDER:
        raw = "coalesce(" + ", ".join(f"record ->> {_literal(name)}" for name in columns[canonical]) + ")"
        typed = NPS_COLUMN_TYPES.get(canonical, "{value}").format(value=raw)
        selects.append(f"        {typed} as {NPS_COLUMN_ALIASES.get(canonical, canonical)}")
    known = ", ".join(_literal(name) for names in columns.values() for name in names)

    rates = sorted(csv.DictReader(io.StringIO(SQL_DIR.joinpath("nps_contribution_rate.csv").read_text(encoding="utf-8"))),
                   key=lambda row: row["valid_from"], reverse=True)
    cases = "\n".join(f"        when base_ym >= date '{row['valid_from']}' then {float(row["rate"])}::double" for row in rates)

    return f"""
-- nps_record_rows: one typed row per pension record, under one set of column
-- names. record_table has publication_id, page, row_no and record (the JSON of
-- one record as received). Column names differ by publication period; each
-- column is read from every name it has had (sql/nps_columns.json).
create or replace macro nps_record_rows(record_table) as table
    select
        publication_id,
        page,
        row_no,
{",\n".join(selects)}
    from query_table(record_table);

-- nps_known_column: whether a source column name is in sql/nps_columns.json.
create or replace macro nps_known_column(name) as name in ({known});

-- nps_contribution_rate: the pension contribution rate in force in a month
-- (sql/nps_contribution_rate.csv).
create or replace macro nps_contribution_rate(base_ym) as
    case
{cases}
    end;
"""


def functions_sql() -> str:
    return _nps_sql() + "\n" + SQL_DIR.joinpath("functions.sql").read_text(encoding="utf-8")


def register_functions(con: duckdb.DuckDBPyConnection, temporary: bool = False) -> None:
    """Create the functions. `temporary` keeps them to this connection, which is
    what a read-only connection allows and what keeps a warehouse file unchanged."""
    sql = functions_sql()
    if temporary:
        sql = sql.replace("create or replace macro ", "create or replace temp macro ")
    con.execute(sql)
