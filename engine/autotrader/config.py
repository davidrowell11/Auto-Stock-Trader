"""Settings for the trading engine.

Two layers:

* ``HardLimits`` are fixed in code. Nothing at runtime (the AI analyst, a
  settings file, an environment variable) can loosen them.
* ``Settings`` are the defaults the bot starts with. They can be tuned, but
  every value is clamped to the hard limits when it is loaded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from enum import Enum


class Mode(str, Enum):
    """Which account the bot trades. Both modes run the exact same bot."""

    TEST = "test"  # Alpaca paper account (fake money)
    LIVE = "live"  # Alpaca live account (real money)


@dataclass(frozen=True)
class HardLimits:
    max_position_pct: float = 0.10
    max_open_positions: int = 25
    max_stop_loss_pct: float = 0.08
    max_daily_loss_pct: float = 0.03
    pause_drawdown_pct: float = 0.15
    min_cash_reserve_pct: float = 0.05
    max_sector_pct: float = 0.25
    # Penny stocks (price under $5)
    penny_max_position_pct: float = 0.02
    penny_max_total_pct: float = 0.10
    penny_min_dollar_volume: float = 1_000_000.0
    penny_price_floor: float = 5.0  # anything under this is always treated as a penny stock
    min_price_floor: float = 1.0  # never buy below this
    penny_min_score: float = 0.75
    analyst_min_confidence: float = 0.55  # the lowest Test may use
    live_analyst_min_confidence: float = 0.60  # the lowest Live may use (real money)
    penny_analyst_min_confidence: float = 0.75
    # Going live small (step 4)
    live_starter_cap: float = 1_000.0  # most real money the bot may use at first
    live_starter_days: int = 30  # how long the starter cap lasts after Live first starts
    live_auto_stop_drawdown: float = 0.10  # Live stops itself after losing this much of its money
    live_auto_stop_errors: int = 3  # ...or after this many failed cycles in a row
    stop_out_cooloff_days: int = 7  # after a protective sell, don't rebuy that stock for a week
    max_new_buys_per_day: int = 6  # never more new stocks than this in one day
    max_risk_per_trade_pct: float = 0.005  # most of the money one trade may lose if its stop is hit
    # Things the bot may never do
    allow_otc: bool = False
    allow_options: bool = False
    allow_margin: bool = False


HARD_LIMITS = HardLimits()


@dataclass(frozen=True)
class Settings:
    mode: Mode = Mode.TEST
    # Money the bot may use. None means "the whole account".
    # In Test this is the budget slider; in Live it is the Live budget cap.
    budget: float | None = None
    max_position_pct: float = 0.05
    max_open_positions: int = 15
    atr_stop_multiple: float = 2.0
    daily_loss_pct: float = 0.02
    drawdown_brake_pct: float = 0.10  # halve position sizes past this
    pause_drawdown_pct: float = 0.15
    cash_reserve_pct: float = 0.10
    max_sector_pct: float = 0.25
    min_price: float = 1.0
    min_dollar_volume: float = 5_000_000.0
    penny_price: float = 5.0
    penny_max_position_pct: float = 0.02
    penny_max_total_pct: float = 0.10
    penny_min_dollar_volume: float = 1_000_000.0
    penny_min_score: float = 0.75  # penny stocks need a higher score to buy
    penny_atr_stop_multiple: float = 1.5  # tighter stops for penny stocks
    # AI analyst: minimum confidence it must give before a buy goes ahead
    analyst_min_confidence: float = 0.60
    penny_analyst_min_confidence: float = 0.75
    max_analyst_reviews: int = 5  # per cycle, to keep API costs down
    # Pace: new stocks bought per day. Buying 11 in an hour put the whole budget on one day's prices.
    max_new_buys_per_day: int = 3
    # Size by risk: a buy is sized so hitting its stop loses at most this share of the money,
    # so jumpy stocks (wide stops) get smaller positions than steady ones.
    risk_per_trade_pct: float = 0.003
    # Skip a buy when there's only room for less than this share of a normal position.
    min_position_fraction: float = 0.4
    # Trim a holding that has grown past this many times its size limit, back down to the limit.
    trim_over: float = 1.5
    # Don't buy a stock this many average daily moves above its 20-day average (0: no check).
    # Off: on the ~500 most-traded stocks it cut 3-year backtest returns from +35% to +10%,
    # since strong trends spend much of their time "stretched" (autotrader.compare --ablate).
    stretch_limit: float = 0.0
    # Sell when a holding closes below its average over this many days (the trend has broken).
    trend_exit_days: int = 50
    # New stocks from one sector per day (0: no limit), so one bad day for a sector hits less.
    max_buys_per_sector_per_day: int = 0
    # Lowest trend score worth buying (the strategy lab can raise it; 0 means any uptrend).
    min_score: float = 0.0
    max_signal_lookups: int = 10  # stocks per cycle checked for insider/Congress buying
    # Live trades wait for a one-tap approval until this is switched off.
    approve_mode: bool = True
    # New buys in pre-market (4am-9:30am) and after hours (4pm-8pm). Selling to protect
    # holdings happens in those hours either way. On for Test, off for Live until switched on.
    after_hours_buys: bool = False
    # New buys in the overnight session (8pm-4am New York time, Sunday night to Friday morning),
    # only in stocks the broker trades overnight. Test only for now (David, 2026-11-10).
    overnight_buys: bool = False
    # Cheaper AI reviews: a fast, low-cost Claude model reviews first and the full analyst only
    # sees the close calls (and every penny stock). On for Test, off for Live until switched on.
    quick_reviews: bool = False
    always_watch: tuple[str, ...] = ()
    never_buy: tuple[str, ...] = ()
    poll_seconds: int = 300
    journal_path: str = "data/journal.sqlite3"

    def clamped(self) -> "Settings":
        """Return a copy with every value forced inside the hard limits."""
        h = HARD_LIMITS
        return replace(
            self,
            max_position_pct=min(self.max_position_pct, h.max_position_pct),
            max_open_positions=min(self.max_open_positions, h.max_open_positions),
            max_new_buys_per_day=int(max(0, min(self.max_new_buys_per_day, h.max_new_buys_per_day))),
            risk_per_trade_pct=min(self.risk_per_trade_pct, h.max_risk_per_trade_pct),
            min_position_fraction=min(1.0, max(0.0, self.min_position_fraction)),
            trim_over=max(1.1, self.trim_over),
            stretch_limit=max(0.0, self.stretch_limit),
            trend_exit_days=int(min(200, max(20, self.trend_exit_days))),
            max_buys_per_sector_per_day=int(max(0, self.max_buys_per_sector_per_day)),
            daily_loss_pct=min(self.daily_loss_pct, h.max_daily_loss_pct),
            pause_drawdown_pct=min(self.pause_drawdown_pct, h.pause_drawdown_pct),
            drawdown_brake_pct=min(self.drawdown_brake_pct, h.pause_drawdown_pct),
            cash_reserve_pct=max(self.cash_reserve_pct, h.min_cash_reserve_pct),
            max_sector_pct=min(self.max_sector_pct, h.max_sector_pct),
            penny_max_position_pct=min(self.penny_max_position_pct, h.penny_max_position_pct),
            penny_max_total_pct=min(self.penny_max_total_pct, h.penny_max_total_pct),
            penny_min_dollar_volume=max(self.penny_min_dollar_volume, h.penny_min_dollar_volume),
            penny_price=max(self.penny_price, h.penny_price_floor),
            min_price=max(self.min_price, h.min_price_floor),
            penny_min_score=max(self.penny_min_score, h.penny_min_score),
            analyst_min_confidence=max(self.analyst_min_confidence,
                                       h.live_analyst_min_confidence if self.mode is Mode.LIVE
                                       else h.analyst_min_confidence),
            penny_analyst_min_confidence=max(self.penny_analyst_min_confidence,
                                             h.penny_analyst_min_confidence),
            budget=None if self.budget is None else max(0.0, self.budget),
        )


def _parse(value: str, current):
    if isinstance(current, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float) or current is None:
        return float(value)
    if isinstance(current, tuple):
        return tuple(s.strip().upper() for s in value.split(",") if s.strip())
    if isinstance(current, Mode):
        return Mode(value.strip().lower())
    return value


def load_settings(env: dict[str, str] | None = None, **overrides) -> Settings:
    """Build settings from ``AUTOTRADER_*`` environment variables.

    Example: ``AUTOTRADER_BUDGET=5000`` or ``AUTOTRADER_NEVER_BUY=GME,AMC``.
    """
    env = os.environ if env is None else env
    base = Settings()
    values = {}
    for f in fields(Settings):
        key = f"AUTOTRADER_{f.name.upper()}"
        if key in env and env[key] != "":
            values[f.name] = _parse(env[key], getattr(base, f.name))
    values.update(overrides)
    return replace(base, **values).clamped()


# ----- Safety rules you can change in the app -----
@dataclass(frozen=True)
class Rule:
    """One safety rule the app may change, always inside a range that sits within the hard limits."""

    key: str  # the Settings field
    label: str
    help: str
    low: float
    high: float
    step: float
    unit: str  # "percent", "count" or "times"
    looser_when: str = "higher"  # which direction takes more risk: "higher" or "lower"

    @property
    def recommended(self) -> float:
        return getattr(Settings(), self.key)

    def check(self, value) -> float | int:
        """The value rounded to the slider's step, or ValueError with a plain reason."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{self.label}: not a number") from None
        if v != v or v < self.low - 1e-9 or v > self.high + 1e-9:  # v != v: NaN
            raise ValueError(f"{self.label} must be between {self.show(self.low)} and {self.show(self.high)}")
        v = min(self.high, max(self.low, round(round(v / self.step) * self.step, 6)))
        return int(round(v)) if self.unit == "count" else v

    def looser(self, old: float, new: float) -> bool:
        return new > old + 1e-9 if self.looser_when == "higher" else new < old - 1e-9

    def show(self, v: float) -> str:
        if self.unit == "percent":
            return f"{v * 100:g}%"
        return f"{v:g}" if self.unit == "count" else f"{v:g}x"


_H = HARD_LIMITS
RULES: tuple[Rule, ...] = (
    Rule("max_position_pct", "Max per stock",
         "The most of the budget one stock may take when it's bought.",
         0.01, _H.max_position_pct, 0.005, "percent"),
    Rule("max_open_positions", "Max stocks at once",
         "Fewer stocks means bigger bets on each; more spreads the risk.",
         3, _H.max_open_positions, 1, "count"),
    Rule("daily_loss_pct", "Stop buying for the day after a loss of",
         "If the account falls this much in a day, no new buys until tomorrow. Protective sells still happen.",
         0.005, _H.max_daily_loss_pct, 0.0025, "percent"),
    Rule("cash_reserve_pct", "Cash always kept aside",
         "Money the bot never invests, as a cushion.",
         _H.min_cash_reserve_pct, 0.50, 0.01, "percent", looser_when="lower"),
    Rule("risk_per_trade_pct", "Most one trade may lose",
         "Each buy is sized so that if its safety stop is hit, the loss is at most this share of the budget.",
         0.001, _H.max_risk_per_trade_pct, 0.0005, "percent"),
    Rule("max_new_buys_per_day", "New stocks per day",
         "Spreads buying over several days instead of putting everything on one day's prices.",
         1, _H.max_new_buys_per_day, 1, "count"),
    Rule("max_sector_pct", "Max in one sector",
         "The most of the budget one industry (like tech or health care) may take.",
         0.10, _H.max_sector_pct, 0.01, "percent"),
    Rule("atr_stop_multiple", "Safety stop distance",
         "How far below the price the safety stop sits, in average daily moves. Wider stops sell less "
         f"often but lose more when hit; a stop is never more than {_H.max_stop_loss_pct:.0%} below the buy.",
         1.5, 3.5, 0.25, "times"),
)
RULES_BY_KEY = {r.key: r for r in RULES}
