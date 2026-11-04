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
    analyst_min_confidence: float = 0.60
    penny_analyst_min_confidence: float = 0.75
    # Going live small (step 4)
    live_starter_cap: float = 1_000.0  # most real money the bot may use at first
    live_starter_days: int = 30  # how long the starter cap lasts after Live first starts
    live_auto_stop_drawdown: float = 0.10  # Live stops itself after losing this much of its money
    live_auto_stop_errors: int = 3  # ...or after this many failed cycles in a row
    stop_out_cooloff_days: int = 7  # after a protective sell, don't rebuy that stock for a week
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
    # Lowest trend score worth buying (the strategy lab can raise it; 0 means any uptrend).
    min_score: float = 0.0
    max_signal_lookups: int = 10  # stocks per cycle checked for insider/Congress buying
    # Live trades wait for a one-tap approval until this is switched off.
    approve_mode: bool = True
    # New buys in pre-market (4am-9:30am) and after hours (4pm-8pm). Selling to protect
    # holdings happens in those hours either way. On for Test, off for Live until switched on.
    after_hours_buys: bool = False
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
            analyst_min_confidence=max(self.analyst_min_confidence, h.analyst_min_confidence),
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
