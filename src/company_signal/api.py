"""Calls to the public data portal (odcloud) API."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date

import httpx

DATASET = "15083277"
SPEC_URL = f"https://infuser.odcloud.kr/oas/docs?namespace={DATASET}/v1"
BASE_URL = "https://api.odcloud.kr/api"
PER_PAGE = 1000  # 10000 returns an OK message with no data.

RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Raised when a request still fails after all retries."""


@dataclass(frozen=True)
class Publication:
    publication_id: str  # Last segment of the API path (uddi:...)
    title: str
    title_date: date | None  # Date read from the title. Used only to order downloads.


def parse_title_date(title: str) -> date | None:
    """Publication titles mix three date notations."""
    if m := re.search(r"(20\d{2})(\d{2})(\d{2})(?!\d)", title):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.search(r"(\d{2})/(\d{2})/(20\d{2})", title):
        return _safe_date(int(m[3]), int(m[1]), int(m[2]))
    if m := re.search(r"(20\d{2})년\s*(\d{1,2})월", title):
        return _safe_date(int(m[1]), int(m[2]), 1)
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_spec(spec: dict) -> list[Publication]:
    publications = []
    for path, methods in spec.get("paths", {}).items():
        title = methods.get("get", {}).get("summary", "")
        publications.append(
            Publication(
                publication_id=path.rsplit("/", 1)[-1],
                title=title,
                title_date=parse_title_date(title),
            )
        )
    return publications


class Client:
    def __init__(
        self,
        service_key: str,
        interval: float = 0.2,
        max_attempts: int = 5,
        sleep=time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            headers={"Authorization": f"Infuser {service_key}"},
            timeout=60,
            transport=transport,
        )
        self._interval = interval
        self._max_attempts = max_attempts
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def list_publications(self) -> list[Publication]:
        body = self._get(SPEC_URL, params=None, expect_key="paths")
        return parse_spec(json.loads(body))

    def fetch_page(self, publication_id: str, page: int) -> bytes:
        """Return the raw response body of one page, unmodified."""
        url = f"{BASE_URL}/{DATASET}/v1/{publication_id}"
        params = {"page": page, "perPage": PER_PAGE, "returnType": "JSON"}
        return self._get(url, params=params, expect_key="data")

    def fetch_matching(self, publication_id: str, conditions: dict[tuple[str, str], str], page: int = 1) -> bytes:
        """One page of the records of a publication that meet `conditions`, as received.

        A condition is (source column name, operator) -> value, sent as
        cond[name::OPERATOR]. LIKE matches names containing the value, ignoring
        case; EQ matches exactly. The published spec does not list these
        parameters, but the API accepts them (checked 2026-11-15).
        """
        url = f"{BASE_URL}/{DATASET}/v1/{publication_id}"
        params = {"page": page, "perPage": PER_PAGE, "returnType": "JSON"}
        params |= {f"cond[{column}::{operator}]": value for (column, operator), value in conditions.items()}
        return self._get(url, params=params, expect_key="data")

    def _get(self, url: str, params: dict | None, expect_key: str) -> bytes:
        last_problem = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._http.get(url, params=params)
            except httpx.HTTPError as exc:
                last_problem = f"{type(exc).__name__}"
            else:
                if response.status_code == 200:
                    # A 200 can still carry only a message and no data.
                    if _has_key(response.content, expect_key):
                        self._sleep(self._interval)
                        return response.content
                    last_problem = f"200 without '{expect_key}': {response.text[:200]}"
                elif response.status_code in RETRY_STATUS:
                    last_problem = f"HTTP {response.status_code}: {response.text[:200]}"
                else:
                    raise FetchError(f"HTTP {response.status_code}: {response.text[:200]}")
            if attempt < self._max_attempts:
                self._sleep(min(2**attempt, 60))
        raise FetchError(f"failed after {self._max_attempts} attempts. Last problem: {last_problem}")


def _has_key(content: bytes, key: str) -> bool:
    try:
        return key in json.loads(content)
    except ValueError:
        return False
