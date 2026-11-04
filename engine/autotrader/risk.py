"""Risk rules. Every buy goes through here before an order is placed."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .config import Settings
from .models import Account, Position


CORRELATION_DAYS = 63  # about three months of daily moves
CORRELATION_LIMIT = 0.8  # moves this closely with a holding: counted as the same bet


def correlation(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Pearson correlation of two equal-length series, or None if either never moves."""
    n = len(a)
    if n < 2 or n != len(b):
        return None
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 1e-12 or vb <= 1e-12:
        return None
    return cov / math.sqrt(va * vb)


def daily_moves(bars) -> dict:
    """Day -> that day's % change, for the last CORRELATION_DAYS days."""
    tail = list(bars)[-(CORRELATION_DAYS + 1):]
    return {b.time: b.close / a.close - 1 for a, b in zip(tail, tail[1:]) if a.close > 0}


def closest_holding(symbol: str, bars: dict, positions: Sequence[Position]):
    """The holding this stock moves most closely with, if at or over the limit: (position, corr)."""
    mine = daily_moves(bars.get(symbol, []))
    best = None
    for p in positions:
        theirs = daily_moves(bars.get(p.symbol, []))
        days = sorted(mine.keys() & theirs.keys())
        if len(days) < CORRELATION_DAYS // 2:
            continue
        c = correlation([mine[d] for d in days], [theirs[d] for d in days])
        if c is not None and c >= CORRELATION_LIMIT and (best is None or c > best[1]):
            best = (p, c)
    return best


@dataclass(frozen=True)
class SizeDecision:
    qty: float  # whole shares, or fractions when the stock allows it
    note: str
    twin: str | None = None  # "Moves closely with AAPL (0.91 correlation)..." when that applied


class RiskManager:
    def __init__(
        self,
        settings: Settings,
        account: Account,
        positions: Sequence[Position],
        peak_equity: float,
        mood_multiplier: float = 1.0,
    ) -> None:
        self.s = settings
        self.account = account
        self.positions = list(positions)
        self.peak_equity = max(peak_equity, account.equity)
        self.mood_multiplier = mood_multiplier  # from the market mood check

    # ----- how much money the bot may use -----
    @property
    def invested(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def capital(self) -> float:
        """Account value the bot sizes against: the budget if one is set."""
        if self.s.budget is None:
            return self.account.equity
        return min(self.s.budget, self.account.equity)

    # ----- circuit breakers -----
    @property
    def daily_change(self) -> float:
        if self.account.last_equity <= 0:
            return 0.0
        return self.account.equity / self.account.last_equity - 1.0

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return 1.0 - self.account.equity / self.peak_equity

    def buying_halted(self) -> str | None:
        """Reason new buys are blocked right now, or None."""
        if self.daily_change <= -self.s.daily_loss_pct:
            return f"Daily loss cap hit ({self.daily_change:.1%} today)"
        if self.drawdown >= self.s.pause_drawdown_pct:
            return f"Paused: account is {self.drawdown:.1%} below its peak"
        return None

    @property
    def size_multiplier(self) -> float:
        brake = 0.5 if self.drawdown >= self.s.drawdown_brake_pct else 1.0
        return brake * self.mood_multiplier

    # ----- position sizing -----
    MIN_FRACTIONAL_DOLLARS = 1.0  # Alpaca's smallest fractional order

    def size_buy(self, symbol: str, price: float, sector: str, penny: bool,
                 fractional: bool = False, bars: dict | None = None) -> SizeDecision:
        """How much to buy. Fractional shares let a small budget hold pricey stocks."""
        if price <= 0:
            return SizeDecision(0, "No valid price")
        if any(p.symbol == symbol for p in self.positions):
            return SizeDecision(0, "Already holding it")
        if len(self.positions) >= self.s.max_open_positions:
            return SizeDecision(0, f"Already at {self.s.max_open_positions} open positions")
        halted = self.buying_halted()
        if halted:
            return SizeDecision(0, halted)

        cap = self.capital
        pct = self.s.penny_max_position_pct if penny else self.s.max_position_pct
        limits = {
            "position size": cap * pct * self.size_multiplier,
            "cash (after reserve)": min(self.account.cash, cap - self.invested)
            - cap * self.s.cash_reserve_pct,
            "sector limit": cap * self.s.max_sector_pct
            - sum(p.market_value for p in self.positions if p.sector == sector),
        }
        twin_note = None
        match = closest_holding(symbol, bars, self.positions) if bars else None
        if match is not None and match[0].sector != sector:
            # It moves with something already held, so it's the same bet: it also has to fit
            # under that holding's sector limit (on top of its own).
            twin, corr = match
            limits[f"limit it shares with {twin.symbol}"] = cap * self.s.max_sector_pct - sum(
                p.market_value for p in self.positions if p.sector == twin.sector)
            twin_note = (f"Moves closely with {twin.symbol} ({corr:.2f} correlation), so it also "
                         f"counts toward {twin.symbol}'s {twin.sector} limit")
        if penny:
            penny_value = sum(
                p.market_value for p in self.positions if p.current_price < self.s.penny_price
            )
            limits["penny stock total"] = cap * self.s.penny_max_total_pct - penny_value

        binding = min(limits, key=limits.get)
        dollars = limits[binding]
        if fractional and not penny:
            qty = math.floor(dollars / price * 1000) / 1000 if dollars > 0 else 0
            if qty * price < self.MIN_FRACTIONAL_DOLLARS:
                qty = 0
        else:
            qty = math.floor(dollars / price) if dollars > 0 else 0
        if qty <= 0:
            return SizeDecision(0, f"No room under the {binding}", twin_note)
        return SizeDecision(qty, f"{qty:g} shares, limited by {binding}", twin_note)

    def register_buy(self, symbol: str, qty: float, price: float, sector: str) -> None:
        """Count a just-placed buy so the next sizing in the same cycle sees it."""
        self.positions.append(Position(symbol, qty, price, price, sector))
        self.account = Account(
            equity=self.account.equity,
            cash=self.account.cash - qty * price,
            last_equity=self.account.last_equity,
        )
