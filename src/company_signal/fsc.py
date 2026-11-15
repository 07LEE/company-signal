"""Calls to the Financial Services Commission corporate outline API."""

from __future__ import annotations

import json
import time

import httpx

from .api import RETRY_STATUS, FetchError

OUTLINE_URL = "https://apis.data.go.kr/1160100/service/GetCorpBasicInfoService_V2/getCorpOutline_V2"
ROWS_PER_PAGE = 100


class DailyLimitReached(RuntimeError):
    """The portal refused the call because the day's quota is used up."""


class FscClient:
    def __init__(
        self,
        service_key: str,
        interval: float = 0.1,
        max_attempts: int = 4,
        sleep=time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # This API takes the key only as a query parameter, so it is never put
        # in a message: errors report the status and the body, not the URL.
        self._key = service_key
        self._http = httpx.Client(timeout=60, transport=transport)
        self._interval = interval
        self._max_attempts = max_attempts
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def fetch_outline_page(self, name: str, page: int) -> tuple[str, int]:
        """One page of corporations whose name contains `name`.

        Returns the response text exactly as received and the total number of
        matches the API reports.
        """
        params = {
            "serviceKey": self._key,
            "corpNm": name,
            "pageNo": page,
            "numOfRows": ROWS_PER_PAGE,
            "resultType": "json",
        }
        last_problem = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._http.get(OUTLINE_URL, params=params)
            except httpx.HTTPError as exc:
                last_problem = type(exc).__name__
            else:
                text = response.text
                if "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS" in text:
                    raise DailyLimitReached("the daily request limit of the API key is used up")
                total = _total_count(text) if response.status_code == 200 else None
                if total is not None:
                    self._sleep(self._interval)
                    return text, total
                last_problem = f"HTTP {response.status_code}: {text[:200]}"
                if response.status_code != 200 and response.status_code not in RETRY_STATUS:
                    raise FetchError(last_problem)
            if attempt < self._max_attempts:
                self._sleep(min(2**attempt, 60))
        raise FetchError(f"failed after {self._max_attempts} attempts. Last problem: {last_problem}")


def _total_count(text: str) -> int | None:
    """The match count of a normal response, or None if the body is anything else."""
    try:
        response = json.loads(text)["response"]
        if response["header"]["resultCode"] != "00":
            return None
        return int(response["body"]["totalCount"])
    except (ValueError, KeyError, TypeError):
        return None
