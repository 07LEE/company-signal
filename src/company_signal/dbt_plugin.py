"""dbt-duckdb plugin: gives every dbt run the project's shared SQL functions.

dbt-duckdb calls configure_connection once on the connection that all models
share, so the functions exist before the first model runs.
"""

from __future__ import annotations

from dbt.adapters.duckdb.plugins import BasePlugin

from .sqlfunctions import register_functions


class Plugin(BasePlugin):
    def configure_connection(self, conn) -> None:
        register_functions(conn)
