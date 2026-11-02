"""Extra safeguards for penny stocks (price under $5).

Penny stocks have wider spreads and are easier to manipulate, so the bot
only buys them when every check here passes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from . import indicators as ind
from .config import Settings
from .models import Asset, Bar, Signal

PUMP_GAIN_5D = 0.50  # a 50%+ jump in 5 days with no real news looks like a pump
VOLUME_DRY_UP_RATIO = 0.30  # recent volume under 30% of normal means "drying up"

# Filled in by step 2 (SEC filings check for dilution and late reports).
FilingsCheck = Callable[[str], tuple[bool, str]]


def no_filings_check(symbol: str) -> tuple[bool, str]:
    return True, "SEC filings check not enabled yet"


def is_penny(price: float, settings: Settings) -> bool:
    return price < settings.penny_price


def penny_buy_problems(
    asset: Asset,
    bars: Sequence[Bar],
    signal: Signal,
    settings: Settings,
    filings_check: FilingsCheck = no_filings_check,
) -> list[str]:
    """Every reason not to buy this penny stock. Empty list means OK."""
    problems: list[str] = []
    if asset.is_otc:
        problems.append("OTC/pink-sheet stocks are never traded")
    if signal.score < settings.penny_min_score:
        problems.append(
            f"Score {signal.score:.2f} is below the {settings.penny_min_score:.2f} needed for penny stocks"
        )
    dv = ind.avg_dollar_volume(bars, 20)
    if dv is None or dv < settings.penny_min_dollar_volume:
        problems.append(
            f"Average daily trading ${0 if dv is None else dv:,.0f} is below ${settings.penny_min_dollar_volume:,.0f}"
        )
    gain5 = ind.pct_change([b.close for b in bars], 5)
    if gain5 is not None and gain5 > PUMP_GAIN_5D:
        problems.append(f"Jumped {gain5:.0%} in 5 days, a possible pump-and-dump")
    ok, note = filings_check(asset.symbol)
    if not ok:
        problems.append(note)
    return problems


def volume_drying_up(bars: Sequence[Bar]) -> bool:
    recent = ind.avg_dollar_volume(bars, 5)
    normal = ind.avg_dollar_volume(bars, 50)
    if recent is None or normal is None or normal <= 0:
        return False
    return recent < VOLUME_DRY_UP_RATIO * normal


def limit_price_for_buy(price: float) -> float:
    """Pay at most 1% over the last price, so a wide spread can't cause a bad fill."""
    return round(price * 1.01, 4 if price < 1 else 2)
