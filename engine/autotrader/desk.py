"""The desk check: the facts a trader looks at before a buy, gathered for the AI analyst.

Everything here is worked out from prices the bot already has, so it costs nothing and is the
same every time. The analyst gets it with each candidate, and can look up other stocks (say a
competitor or the sector's fund) with the same summary through its ``look_up_stock`` tool.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from . import indicators as ind
from .market import MARKET, Mood
from .models import Bar, Position
from .risk import CORRELATION_DAYS, correlation, daily_moves


def _pct(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def stock_facts(symbol: str, bars: Sequence[Bar], market: Sequence[Bar] | None = None) -> dict:
    """Trend, momentum and volume facts for one stock, as plain numbers (shares of 1, not %)."""
    bars = list(bars)
    if len(bars) < 2:
        return {"symbol": symbol, "error": "no price history"}
    closes = [b.close for b in bars]
    price = closes[-1]

    def above(period: int) -> float | None:
        avg = ind.sma(closes, period)
        return None if not avg else _pct(price / avg - 1)

    high_52w = max(b.high for b in bars[-252:])
    atr = ind.atr(bars)  # None with too little history: unknown, not "barely moves"
    vol_avg = sum(b.volume for b in bars[-21:-1]) / max(1, len(bars[-21:-1]))
    facts = {
        "symbol": symbol,
        "price": round(price, 4),
        "above_20_day_average": above(20),
        "above_50_day_average": above(50),
        "above_200_day_average": above(200),
        "return_1_month": _pct(ind.pct_change(closes, 21)),
        "return_3_months": _pct(ind.pct_change(closes, 63)),
        "below_52_week_high": _pct(price / high_52w - 1) if high_52w else None,
        "volume_vs_20_day_average": round(bars[-1].volume / vol_avg, 2) if vol_avg else None,
        "average_daily_move": _pct(atr / price) if price and atr else None,
    }
    if market:
        mkt = ind.pct_change([b.close for b in market], 63)
        own = facts["return_3_months"]
        if mkt is not None and own is not None:
            # Beating the market over 3 months: the strongest single sign a trend will last.
            facts["return_3_months_vs_market"] = _pct(own - mkt)
    return facts


def portfolio_facts(symbol: str, sector: str, bars: dict, positions: Sequence[Position],
                    capital: float, sector_of: Callable[[str], str]) -> dict:
    """How this stock would sit with what the bot already holds."""
    in_sector = [p for p in positions if sector_of(p.symbol) == sector]
    exposure = sum(p.market_value for p in in_sector) / capital if capital > 0 else None
    mine = daily_moves(bars.get(symbol, []))
    closest = None
    for p in positions:
        theirs = daily_moves(bars.get(p.symbol, []))
        days = sorted(mine.keys() & theirs.keys())
        if len(days) < CORRELATION_DAYS // 2:
            continue
        c = correlation([mine[d] for d in days], [theirs[d] for d in days])
        if c is not None and (closest is None or c > closest[1]):
            closest = (p.symbol, c)
    return {
        "sector": sector,
        "stocks_held": len(positions),
        "held_in_same_sector": [p.symbol for p in in_sector],
        "share_of_budget_in_sector": _pct(exposure),
        "moves_most_like": None if closest is None else {
            "symbol": closest[0], "correlation": round(closest[1], 2)},
    }


def desk_check(symbol: str, bars: dict, positions: Sequence[Position], capital: float,
               sector: str, sector_of: Callable[[str], str], mood: Mood) -> dict:
    """Everything the analyst should see before a buy, beyond the rules' own reasons."""
    return {
        "stock": stock_facts(symbol, bars.get(symbol, []), bars.get(MARKET)),
        "market": {"mood": mood.level, "note": mood.note,
                   **({k: v for k, v in stock_facts(MARKET, bars[MARKET]).items()
                       if k in ("above_50_day_average", "above_200_day_average", "return_1_month")}
                      if bars.get(MARKET) else {})},
        "portfolio": portfolio_facts(symbol, sector, bars, positions, capital, sector_of),
        "units": "Shares of 1: 0.05 means 5%. volume_vs_20_day_average 2.0 means twice the usual.",
    }


def trade_facts(check: dict) -> dict:
    """The few desk-check numbers kept with a trade, for reviewing it once it's sold."""
    stock, port = check.get("stock", {}), check.get("portfolio", {})
    keep = ("above_20_day_average", "above_50_day_average", "above_200_day_average",
            "return_1_month", "return_3_months", "return_3_months_vs_market", "below_52_week_high",
            "volume_vs_20_day_average", "average_daily_move")
    twin = port.get("moves_most_like") or {}
    return {**{k: stock[k] for k in keep if stock.get(k) is not None},
            "market_mood": check.get("market", {}).get("mood"),
            "sector": port.get("sector"),
            "same_sector_held": len(port.get("held_in_same_sector") or []),
            **({"closest_twin_correlation": twin["correlation"]} if twin else {})}
