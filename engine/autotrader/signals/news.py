"""Recent news check: today's headlines before any buy.

Daily prices lag the news. Before the bot buys, it reads the last two days of
headlines for the stock from Alpaca's news feed (free with the trading
account). A clear red flag in a headline blocks the buy outright; the rest of
the headlines go to the AI analyst so its review starts from today's news.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

LOOKBACK = timedelta(days=2)
MAX_HEADLINES = 8
CACHE_SECONDS = 15 * 60  # one lookup per stock per check, not per candidate pass

# Headline patterns that mean "don't buy today", with the reason shown in the app.
RED_FLAGS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(public |stock |share |equity )?offering\b|\bdilut|\bat-the-market\b|\bATM program\b", re.I),
     "a share offering was just announced"),
    (re.compile(r"\b(SEC|DOJ|regulators?|federal|launches?|opens?|faces?) (an? )?(probe|investigat\w+|charge[sd]?|subpoena)|\bunder investigation\b", re.I),
     "it is under investigation"),
    (re.compile(r"\bclass action\b|\bsecurities fraud\b|\baccounting (irregularit|fraud|probe)", re.I),
     "there are fraud or class-action headlines"),
    (re.compile(r"\bbankrupt|\bchapter 11\b|\bgoing concern\b|\bdefault(s|ed)? on\b", re.I),
     "there are bankruptcy or default headlines"),
    (re.compile(r"\bdelist|\breverse (stock )?split\b|\btrading halt", re.I),
     "it faces a delisting, reverse split or trading halt"),
    (re.compile(r"\b(CEO|CFO|chief executive|chief financial officer) (resign|steps? down|depart|ousted|fired)", re.I),
     "a top executive just left"),
    (re.compile(r"\bshort[- ]seller report\b|\b(Hindenburg|Muddy Waters|Citron)\b", re.I),
     "a short-seller report just came out"),
)


@dataclass
class NewsCheck:
    headlines: list[str] = field(default_factory=list)  # newest first, "Nov 4: Headline (Source)"
    red_flag: str | None = None  # why the buy is blocked, or None


@dataclass(frozen=True)
class Headline:
    when: datetime
    text: str
    source: str = ""


# A fetch returns headlines for one symbol since a time, newest first.
Fetch = Callable[[str, datetime], list[Headline]]


class NewsDesk:
    def __init__(self, fetch: Fetch | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.fetch = fetch or alpaca_fetch
        self.now = now
        self._cache: dict[str, tuple[datetime, NewsCheck]] = {}

    def check(self, symbol: str) -> NewsCheck:
        now = self.now()
        hit = self._cache.get(symbol)
        if hit and now - hit[0] < timedelta(seconds=CACHE_SECONDS):
            return hit[1]
        try:
            items = self.fetch(symbol, now - LOOKBACK)
        except Exception as exc:  # no news is not a reason to stop trading
            log.warning("News unavailable for %s: %s", symbol, exc)
            return NewsCheck()
        result = review(items)
        self._cache[symbol] = (now, result)
        return result


def review(items: list[Headline]) -> NewsCheck:
    out = NewsCheck()
    for h in sorted(items, key=lambda h: h.when, reverse=True)[:MAX_HEADLINES]:
        source = f" ({h.source})" if h.source else ""
        out.headlines.append(f"{h.when:%b} {h.when.day}: {h.text}{source}")
        if out.red_flag is None:
            for pattern, reason in RED_FLAGS:
                if pattern.search(h.text):
                    out.red_flag = f"{reason}: \"{h.text}\""
                    break
    return out


def alpaca_fetch(symbol: str, since: datetime) -> list[Headline]:
    import os

    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest

    key = os.environ.get("ALPACA_TEST_KEY_ID") or os.environ.get("ALPACA_LIVE_KEY_ID")
    secret = os.environ.get("ALPACA_TEST_SECRET_KEY") or os.environ.get("ALPACA_LIVE_SECRET_KEY")
    client = NewsClient(key, secret)
    result = client.get_news(NewsRequest(symbols=symbol, start=since, limit=MAX_HEADLINES * 2,
                                         include_content=False, exclude_contentless=False))
    items = result.data.get("news", [])
    return [Headline(n.created_at, n.headline, n.source or "") for n in items]
