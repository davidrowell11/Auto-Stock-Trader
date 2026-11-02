"""Is the bot ready for real money? (step 4: go live small)

The plan's rule: Live only starts after 8+ weeks of Test results that beat
simply holding SPY, without a drop worse than 10%. Then it starts small: a
Live budget cap is required and can't go above $1,000 for the first 30 days.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date

from .config import HARD_LIMITS

MIN_WEEKS = 8
MAX_TEST_DRAWDOWN = 0.10


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    can_skip: bool = False  # the server owner may skip results checks, never the safety ones


@dataclass
class TestResults:
    weeks: float
    bot_return: float
    spy_return: float | None
    max_drawdown: float


def test_results(daily_equity: Sequence[tuple[str, float]], capital: float | None,
                 spy_daily: Sequence[tuple[str, float]]) -> TestResults | None:
    """Score the Test account from its daily values. Returns are on the money the bot used."""
    if len(daily_equity) < 2:
        return None
    first_day, first = daily_equity[0]
    last_day, last = daily_equity[-1]
    base = capital or first
    if base <= 0:
        return None
    weeks = (date.fromisoformat(last_day) - date.fromisoformat(first_day)).days / 7
    peak, worst = first, 0.0
    for _, e in daily_equity:
        peak = max(peak, e)
        worst = max(worst, (peak - e) / base)
    spy = [c for d, c in spy_daily if first_day <= d <= last_day]
    spy_return = spy[-1] / spy[0] - 1 if len(spy) > 1 and spy[0] > 0 else None
    return TestResults(weeks, (last - first) / base, spy_return, worst)


def live_checks(results: TestResults | None, *, live_enabled: bool, live_connected: bool,
                live_cap: float | None, starter_active: bool) -> dict:
    checks: list[Check] = []
    if results is None:
        checks.append(Check("8+ weeks of Test results", False,
                            "Start the bot in Test first. Results appear after its first days.", True))
    else:
        checks.append(Check("8+ weeks of Test results", results.weeks >= MIN_WEEKS,
                            f"{results.weeks:.1f} of {MIN_WEEKS} weeks so far", True))
        if results.spy_return is None:
            checks.append(Check("Beats just holding SPY", False, "No SPY prices to compare with yet", True))
        else:
            checks.append(Check(
                "Beats just holding SPY", results.bot_return > results.spy_return,
                f"Bot {results.bot_return:+.1%} vs SPY {results.spy_return:+.1%}", True))
        checks.append(Check(
            f"Biggest drop under {MAX_TEST_DRAWDOWN:.0%}", results.max_drawdown < MAX_TEST_DRAWDOWN,
            f"Biggest drop so far {results.max_drawdown:.1%}", True))
    checks.append(Check("Live trading switched on in the server", live_enabled,
                        "Set AUTOTRADER_LIVE_ENABLED=true in Replit Secrets" if not live_enabled
                        else "AUTOTRADER_LIVE_ENABLED is on"))
    checks.append(Check("Live Alpaca account connected", live_connected,
                        "Add ALPACA_LIVE_KEY_ID and ALPACA_LIVE_SECRET_KEY in Replit Secrets"
                        if not live_connected else "Connected"))
    cap = HARD_LIMITS.live_starter_cap
    if starter_active:
        ok = live_cap is not None and 0 < live_cap <= cap
        detail = (f"Set a Live budget cap of ${cap:,.0f} or less for the first "
                  f"{HARD_LIMITS.live_starter_days} days" if not ok else f"Cap ${live_cap:,.0f}")
    else:
        ok = live_cap is not None and live_cap > 0
        detail = "Set a Live budget cap" if not ok else f"Cap ${live_cap:,.0f}"
    checks.append(Check("Live budget cap set (start small)", ok, detail))
    return {
        "ready": all(c.ok for c in checks),
        "ready_except_results": all(c.ok for c in checks if not c.can_skip),
        "checks": [asdict(c) for c in checks],
        "results": asdict(results) if results else None,
        "starter_cap": cap if starter_active else None,
    }
