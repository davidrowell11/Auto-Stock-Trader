"""Risk rules. Every buy goes through here before an order is placed."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .config import Settings
from .models import Account, Position


@dataclass(frozen=True)
class SizeDecision:
    qty: int
    note: str


class RiskManager:
    def __init__(
        self,
        settings: Settings,
        account: Account,
        positions: Sequence[Position],
        peak_equity: float,
    ) -> None:
        self.s = settings
        self.account = account
        self.positions = list(positions)
        self.peak_equity = max(peak_equity, account.equity)

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
        return 0.5 if self.drawdown >= self.s.drawdown_brake_pct else 1.0

    # ----- position sizing -----
    def size_buy(self, symbol: str, price: float, sector: str, penny: bool) -> SizeDecision:
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
        if penny:
            penny_value = sum(
                p.market_value for p in self.positions if p.current_price < self.s.penny_price
            )
            limits["penny stock total"] = cap * self.s.penny_max_total_pct - penny_value

        binding = min(limits, key=limits.get)
        dollars = limits[binding]
        qty = math.floor(dollars / price) if dollars > 0 else 0
        if qty < 1:
            return SizeDecision(0, f"No room under the {binding}")
        return SizeDecision(qty, f"{qty} shares, limited by {binding}")

    def register_buy(self, symbol: str, qty: int, price: float, sector: str) -> None:
        """Count a just-placed buy so the next sizing in the same cycle sees it."""
        self.positions.append(Position(symbol, qty, price, price, sector))
        self.account = Account(
            equity=self.account.equity,
            cash=self.account.cash - qty * price,
            last_equity=self.account.last_equity,
        )
