"""Earnings guard: don't buy just before a company reports earnings.

Prices can jump 10%+ overnight on an earnings report, which a stop loss
can't protect against. Uses Finnhub's free earnings calendar when
``FINNHUB_API_KEY`` is set; without it the AI analyst still checks for
upcoming earnings with a web search.
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

from .http import RateLimited

CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings?from={start}&to={end}&token={token}"


class EarningsCalendar:
    REFRESH_SECONDS = 6 * 3600

    def __init__(self, token: str | None = None, days_ahead: int = 7,
                 fetch: RateLimited | None = None, today: date | None = None) -> None:
        self.token = token if token is not None else os.environ.get("FINNHUB_API_KEY", "")
        self.days_ahead = days_ahead
        self.fetch = fetch or RateLimited(min_interval=1.0)
        self.today = today
        self._dates: dict[str, date] = {}
        self._loaded_at: float | None = None
        self.error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _load(self) -> None:
        start = self.today or date.today()
        end = start + timedelta(days=self.days_ahead)
        url = CALENDAR_URL.format(start=start, end=end, token=self.token)
        data = self.fetch.json(url, {"Accept": "application/json"})
        dates: dict[str, date] = {}
        for row in data.get("earningsCalendar") or []:
            try:
                d = date.fromisoformat(row["date"])
            except (KeyError, TypeError, ValueError):
                continue
            sym = str(row.get("symbol", "")).upper()
            if sym and (sym not in dates or d < dates[sym]):
                dates[sym] = d
        self._dates = dates
        self._loaded_at = time.monotonic()

    def reports_soon(self, symbol: str) -> str | None:
        """A reason not to buy, or None. Never blocks when the calendar is unavailable."""
        if not self.enabled:
            return None
        if self._loaded_at is None or time.monotonic() - self._loaded_at > self.REFRESH_SECONDS:
            try:
                self._load()
                self.error = None
            except Exception as exc:  # the analyst's own earnings check still applies
                self.error = str(exc)
                # Try again in 30 minutes rather than on every stock.
                self._loaded_at = time.monotonic() - self.REFRESH_SECONDS + 1800
                return None
        d = self._dates.get(symbol.upper().replace(".", "-")) or self._dates.get(symbol.upper())
        if d is None:
            return None
        return f"reports earnings on {d:%b %d}, too soon to buy"
