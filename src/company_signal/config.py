"""Paths and API key settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

KEY_ENV = "ODCLOUD_SERVICE_KEY_ENCODED"


@dataclass(frozen=True)
class Settings:
    root: Path
    service_key: str

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw" / "nps_workplace"

    @property
    def db_path(self) -> Path:
        """Written by the collector: raw records and collection state."""
        return self.data_dir / "company_signal.duckdb"

    @property
    def warehouse_path(self) -> Path:
        """Written by dbt: the tables the lookup reads."""
        return self.data_dir / "warehouse.duckdb"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_settings(root: Path | None = None, require_key: bool = True) -> Settings:
    root = (root or Path.cwd()).resolve()
    encoded = os.environ.get(KEY_ENV) or read_env_file(root / ".env").get(KEY_ENV, "")
    if require_key and not encoded:
        raise SystemExit(f"{KEY_ENV} is not set. Add it to .env in the project root.")
    # The portal issues a URL-encoded key. The header needs the decoded value.
    return Settings(root=root, service_key=unquote(encoded))
