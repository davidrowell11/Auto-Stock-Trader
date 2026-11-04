"""Analyst scorecard: were the AI analyst's calls right?

When the analyst blocks a buy, the bot never owns the stock, so it never
learns whether that was a good call. Once a week the scorecard looks back at
every analyst review that is at least a month old (20 trading days) and checks
what each stock did next, compared with the market (SPY) over the same days:

* the stocks it approved, and
* the stocks it blocked.

If the blocked stocks went on to do worse than the approved ones, its vetoes
are saving money. If they did better, it's being too strict and costing you
trades. The result is shown in the app and handed to the weekly lessons, so
the analyst hears about the winners it turned away as well as the losers it
let through.

(Idea from SPARC's reflection step: look back at each decision once its
outcome is known.)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .models import Bar

HORIZON = 20  # trading days after the review (about a month)
LOOK_BACK_DAYS = 180  # reviews older than this aren't graded
MIN_CALLS = 5  # graded calls needed on each side before it gives a verdict
MARKET = "SPY"
CLEAR_GAP = 0.01  # a gap under 1 point is called a draw


def _day(t) -> date:
    return t.date() if isinstance(t, datetime) else t


def _index_on_or_after(bars: list[Bar], day: date) -> int | None:
    for i, b in enumerate(bars):
        if _day(b.time) >= day:
            return i
    return None


def _close_on(bars: list[Bar], day: date) -> float | None:
    """The close on that day, or the last one before it."""
    price = None
    for b in bars:
        if _day(b.time) > day:
            break
        price = b.close
    return price


def _date(v: dict) -> date | None:
    try:
        return date.fromisoformat(str(v.get("day", ""))[:10])
    except ValueError:
        return None


def calls_to_grade(views: list[dict], today: date) -> list[dict]:
    """One call per stock per day (the latest one), from the last six months."""
    latest: dict[tuple[str, str], dict] = {}
    oldest = today - timedelta(days=LOOK_BACK_DAYS)
    for v in views:
        d = _date(v)
        if d is not None and d >= oldest:
            latest[(v["symbol"], v["day"])] = v
    return list(latest.values())


def any_ready(views: list[dict], today: date) -> bool:
    """Is at least one call old enough (about a month, in calendar days) to grade?"""
    ready = today - timedelta(days=HORIZON * 7 // 5)
    return any(_date(v) <= ready for v in calls_to_grade(views, today))


def grade(views: list[dict], bars: dict[str, list[Bar]], today: date) -> dict:
    """The scorecard: how approved and blocked stocks did a month after each review."""
    market = bars.get(MARKET, [])
    graded: dict[str, list[dict]] = {"buy": [], "skip": []}
    waiting = 0
    for v in calls_to_grade(views, today):
        history = bars.get(v["symbol"], [])
        day = _date(v)
        i = _index_on_or_after(history, day)
        if i is None or i + HORIZON >= len(history) or v["action"] not in graded:
            waiting += 1
            continue
        start, end = history[i], history[i + HORIZON]
        m0, m1 = _close_on(market, _day(start.time)), _close_on(market, _day(end.time))
        if start.close <= 0 or not m0 or not m1:
            waiting += 1  # missing prices: graded once they're there
            continue
        # Both from the same day's close, so the stock and the market are measured alike.
        ret = end.close / start.close - 1
        vs_market = ret - (m1 / m0 - 1)
        graded[v["action"]].append({"symbol": v["symbol"], "day": v["day"],
                                    "return": round(ret, 4), "vs_market": round(vs_market, 4)})

    def side(calls: list[dict]) -> dict:
        n = len(calls)
        return {"calls": n,
                "avg_return": round(sum(c["return"] for c in calls) / n, 4) if n else None,
                "avg_vs_market": round(sum(c["vs_market"] for c in calls) / n, 4) if n else None,
                "beat_market": round(sum(c["vs_market"] > 0 for c in calls) / n, 3) if n else None}

    approved, blocked = side(graded["buy"]), side(graded["skip"])
    return {
        "graded_on": today.isoformat(),
        "horizon_days": HORIZON,
        "approved": approved,
        "blocked": blocked,
        "waiting": waiting,
        "verdict": verdict(approved, blocked),
        # The blocked stocks that did best: the trades its vetoes cost.
        "missed": sorted(graded["skip"], key=lambda c: -c["vs_market"])[:3],
        # The approved stocks that did worst: what it let through.
        "let_through": sorted(graded["buy"], key=lambda c: c["vs_market"])[:3],
    }


def verdict(approved: dict, blocked: dict) -> str:
    if approved["calls"] < MIN_CALLS or blocked["calls"] < MIN_CALLS:
        return (f"Not enough graded calls yet. It needs {MIN_CALLS} approved and {MIN_CALLS} "
                f"blocked stocks reviewed at least a month ago (has {approved['calls']} and "
                f"{blocked['calls']}).")
    gap = approved["avg_vs_market"] - blocked["avg_vs_market"]
    if gap >= CLEAR_GAP:
        return (f"Its vetoes are helping: stocks it blocked did {gap:.1%} worse over the next "
                "month than stocks it approved.")
    if gap <= -CLEAR_GAP:
        return (f"It may be too strict: stocks it blocked did {-gap:.1%} better over the next "
                "month than stocks it approved.")
    return "About even: blocked and approved stocks did about the same over the next month."


def symbols_of(views: list[dict], today: date) -> list[str]:
    return sorted({v["symbol"] for v in calls_to_grade(views, today)} | {MARKET})


def bars_needed(views: list[dict], today: date) -> int:
    """Trading days of history that cover the oldest call plus the month after it."""
    calls = calls_to_grade(views, today)
    if not calls:
        return 0
    oldest = min(_date(v) for v in calls)
    return int((today - oldest).days * 252 / 365) + HORIZON + 5
