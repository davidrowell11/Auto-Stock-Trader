"""The bot's main cycle: watch what it holds, then look for new buys.

Test and Live run this exact same code. The only differences are which
Alpaca account the broker talks to and, in Live, that buys wait for your
approval while approve mode is on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from . import penny, strategy
from . import indicators as ind
from .analyst import AnalystUnavailable, AnalystView
from .broker.base import Broker
from .config import HARD_LIMITS, Mode, Settings
from .journal import Journal, StopState
from .models import Bar, OrderRequest, Position, Side, Signal
from .risk import RiskManager
from .universe import build_universe, sector_of

log = logging.getLogger("autotrader")

HISTORY_DAYS = strategy.MIN_HISTORY + 10


@dataclass
class CycleReport:
    sells: list[str] = field(default_factory=list)
    buys: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    halted: str | None = None


class Engine:
    def __init__(self, broker: Broker, settings: Settings, journal: Journal,
                 filings_check: penny.FilingsCheck = penny.no_filings_check,
                 signals=None, analyst=None, penny_needs_analyst: bool = True,
                 keep_going: Callable[[], bool] = lambda: True) -> None:
        self.broker = broker
        self.s = settings
        self.journal = journal
        self.mode = settings.mode.value
        self.filings_check = filings_check
        self.signals = signals  # SignalBook or None
        self.analyst = analyst  # Analyst or None
        self.penny_needs_analyst = penny_needs_analyst  # off only in backtests
        # Checked before every order, so Stop or the kill switch takes effect mid-cycle.
        self.keep_going = keep_going
        self.open_orders: set[str] = set()

    @property
    def needs_approval(self) -> bool:
        return self.s.mode is Mode.LIVE and self.s.approve_mode

    def run_cycle(self) -> CycleReport:
        report = CycleReport()
        account = self.broker.get_account()
        self.journal.record_equity(self.mode, account.equity)
        # The bot only manages stocks it bought itself; anything else in the account is yours.
        owned = self.journal.owned_symbols(self.mode)
        positions = [p for p in self.broker.get_positions() if p.symbol in owned]
        # Unfilled orders (e.g. penny limit orders) must not be placed again.
        self.open_orders = self.broker.open_order_symbols()
        for p in positions:
            p.sector = sector_of(p.symbol)

        universe = build_universe(self.s.always_watch, self.s.never_buy)
        symbols = sorted(set(universe) | {p.symbol for p in positions})
        bars = self.broker.get_daily_bars(symbols, HISTORY_DAYS)

        kept = self._monitor_positions(positions, bars, report)

        risk = RiskManager(self.s, account, kept, self.journal.peak_equity(self.mode))
        report.halted = risk.buying_halted()
        if report.halted:
            log.warning("No new buys: %s", report.halted)
            return report
        self._scan_for_buys(universe, bars, risk, report)
        return report

    # ----- keeping an eye on bought stocks -----
    def _monitor_positions(self, positions: list[Position], bars: dict[str, list[Bar]],
                           report: CycleReport) -> list[Position]:
        kept: list[Position] = []
        for p in positions:
            if p.symbol in self.open_orders:  # an order for it is still working
                kept.append(p)
                continue
            history = bars.get(p.symbol, [])
            atr = ind.atr(history, 14) if history else None
            multiple = strategy.stop_multiple(p.current_price, self.s)
            state = self.journal.get_stop(self.mode, p.symbol)
            if state is None:
                if atr:
                    stop = strategy.initial_stop(p.avg_price, atr, multiple)
                else:
                    stop = p.avg_price * (1 - HARD_LIMITS.max_stop_loss_pct)
                state = StopState(p.avg_price, stop, max(p.avg_price, p.current_price))
            state.highest_close = max(state.highest_close, p.current_price)
            if atr:
                state.stop_price = strategy.trail_stop(state.stop_price, state.highest_close,
                                                       atr, multiple)
            self.journal.save_stop(self.mode, p.symbol, state)

            reason = strategy.exit_reason(history, state.stop_price) if history else None
            if reason is None and p.current_price <= state.stop_price:
                reason = f"Hit stop at ${state.stop_price:.2f}"
            if reason is None and penny.is_penny(p.current_price, self.s) and penny.volume_drying_up(history):
                reason = "Penny stock trading volume is drying up"
            if reason is None or not self._sell(p, reason, report):
                kept.append(p)  # still held, so it still counts against the limits
        return kept

    def _sell(self, p: Position, reason: str, report: CycleReport) -> bool:
        # Protective sells never wait for approval: getting out fast is the point.
        limit = None
        if penny.is_penny(p.current_price, self.s):
            limit = round(p.current_price * 0.98, 4 if p.current_price < 1 else 2)
        order = OrderRequest(p.symbol, Side.SELL, p.qty, limit, reason)
        if not self.keep_going():
            return False
        try:
            order_id = self.broker.submit_order(order)
        except Exception as exc:  # broker errors must not stop the rest of the cycle
            log.error("Sell %s failed: %s", p.symbol, exc)
            report.skipped.append(f"{p.symbol}: sell failed ({exc})")
            return False
        self.journal.record_trade(self.mode, p.symbol, "sell", p.qty, p.current_price,
                                  order_id, [reason])
        self.journal.clear_stop(self.mode, p.symbol)
        report.sells.append(f"{p.symbol}: {reason}")
        self.journal.add_event(self.mode, "sell", f"Sold {p.qty:g} {p.symbol}: {reason}")
        log.info("SELL %s x%s: %s", p.symbol, p.qty, reason)
        return True

    # ----- finding new buys -----
    def _scan_for_buys(self, universe: list[str], bars: dict[str, list[Bar]],
                       risk: RiskManager, report: CycleReport) -> None:
        held = {p.symbol for p in risk.positions}
        candidates: list[Signal] = []
        for symbol in universe:
            if symbol in held or symbol in self.open_orders or self.journal.has_pending(self.mode, symbol):
                continue
            history = bars.get(symbol)
            if not history:
                continue
            sig = strategy.score_trend(symbol, history)
            if sig is None:
                continue
            try:
                problems = self._buy_problems(sig, history)
            except Exception as exc:  # one bad symbol must not stop the whole scan
                log.warning("Couldn't check %s: %s", symbol, exc)
                problems = [f"couldn't be checked ({exc})"]
            if problems:
                report.skipped.append(f"{symbol}: {'; '.join(problems)}")
                continue
            candidates.append(sig)

        candidates.sort(key=lambda c: c.score, reverse=True)
        if self.signals is not None:
            for sig in candidates[: self.s.max_signal_lookups]:
                expert = self.signals.for_symbol(sig.symbol)
                if expert.boost:
                    sig.score = round(min(1.0, sig.score + expert.boost), 4)
                    sig.reasons.extend(expert.reasons)
            candidates.sort(key=lambda c: c.score, reverse=True)

        reviews = 0
        for sig in candidates:
            if not self.keep_going():
                report.skipped.append("bot stopped mid-cycle")
                return
            is_penny = penny.is_penny(sig.price, self.s)
            sector = sector_of(sig.symbol)
            size = risk.size_buy(sig.symbol, sig.price, sector, is_penny)
            if size.qty < 1:
                report.skipped.append(f"{sig.symbol}: {size.note}")
                continue
            reasons = [f"Score {sig.score:.2f}", *sig.reasons, size.note]
            if self.analyst is not None:
                cached = self.journal.todays_view(self.mode, sig.symbol)
                if not cached:
                    if reviews >= self.s.max_analyst_reviews:
                        report.skipped.append(f"{sig.symbol}: waiting for an analyst review next cycle")
                        continue
                    reviews += 1
                verdict = self._analyst_verdict(sig, is_penny, cached)
                if isinstance(verdict, str):
                    report.skipped.append(f"{sig.symbol}: {verdict}")
                    continue
                if verdict is not None:
                    reasons.append(f"Analyst ({verdict.confidence:.0%} confident): {verdict.reasoning}")
            limit = penny.limit_price_for_buy(sig.price) if is_penny else None
            if self.needs_approval:
                self.journal.queue_approval(self.mode, sig.symbol, "buy", size.qty, limit, reasons)
                risk.register_buy(sig.symbol, size.qty, sig.price, sector)
                report.queued.append(f"{sig.symbol} x{size.qty}")
                self.journal.add_event(self.mode, "approval",
                                       f"Wants to buy {size.qty} {sig.symbol} at about ${sig.price:.2f}. Tap to approve.")
                continue
            if not self.keep_going():
                report.skipped.append("bot stopped mid-cycle")
                return
            try:
                order_id = self.broker.submit_order(
                    OrderRequest(sig.symbol, Side.BUY, size.qty, limit, reasons[0]))
            except Exception as exc:
                log.error("Buy %s failed: %s", sig.symbol, exc)
                report.skipped.append(f"{sig.symbol}: buy failed ({exc})")
                continue
            risk.register_buy(sig.symbol, size.qty, sig.price, sector)
            self.journal.record_trade(self.mode, sig.symbol, "buy", size.qty, sig.price,
                                      order_id, reasons)
            stop = strategy.initial_stop(sig.price, sig.atr, strategy.stop_multiple(sig.price, self.s))
            self.journal.save_stop(self.mode, sig.symbol, StopState(sig.price, stop, sig.price))
            report.buys.append(f"{sig.symbol} x{size.qty} @ ${sig.price:.2f}")
            self.journal.add_event(self.mode, "buy", f"Bought {size.qty} {sig.symbol} at about ${sig.price:.2f}")
            log.info("BUY %s x%s @ %.2f (stop %.2f)", sig.symbol, size.qty, sig.price, stop)

    def _analyst_verdict(self, sig: Signal, is_penny: bool,
                         cached: dict | None = None) -> AnalystView | str | None:
        """The analyst's approval, a reason string to skip, or None if it was unavailable."""
        if cached:
            view = AnalystView(cached["action"], cached["confidence"], cached["reasoning"],
                               cached["red_flags"])
        else:
            try:
                view = self.analyst.review({
                    "symbol": sig.symbol,
                    "price": sig.price,
                    "penny_stock": is_penny,
                    "score": sig.score,
                    "average_daily_move_dollars": round(sig.atr, 4),
                    "why_the_rules_like_it": sig.reasons,
                })
            except AnalystUnavailable as exc:
                log.warning("Analyst unavailable for %s: %s", sig.symbol, exc)
                if is_penny:
                    return "analyst unavailable, and penny stocks always need its approval"
                return None
            self.journal.save_view(self.mode, sig.symbol, sig.price, view.action, view.confidence,
                                   view.reasoning, view.red_flags)
        floor = self.s.penny_analyst_min_confidence if is_penny else self.s.analyst_min_confidence
        if not view.approves:
            flags = f" Red flags: {'; '.join(view.red_flags)}." if view.red_flags else ""
            return f"analyst said skip: {view.reasoning}{flags}"
        if view.confidence < floor:
            return f"analyst confidence {view.confidence:.0%} is below the {floor:.0%} needed"
        return view

    def recheck_buy(self, symbol: str) -> tuple[Signal | None, list[str]]:
        """Run the buy rules again for one stock (used when you approve a queued buy)."""
        history = self.broker.get_daily_bars([symbol], HISTORY_DAYS).get(symbol, [])
        if not history:
            return None, ["no current prices"]
        sig = strategy.score_trend(symbol, history)
        if sig is None:
            return None, ["the uptrend no longer holds"]
        return sig, self._buy_problems(sig, history)

    def _buy_problems(self, sig: Signal, history: list[Bar]) -> list[str]:
        asset = self.broker.get_asset(sig.symbol)
        if not asset.tradable:
            return ["not tradable"]
        if asset.is_otc:
            return ["OTC/pink-sheet stocks are never traded"]
        if sig.price < self.s.min_price:
            return [f"price under ${self.s.min_price:.2f}"]
        if penny.is_penny(sig.price, self.s):
            if self.analyst is None and self.penny_needs_analyst:
                return ["penny stocks are only bought with the AI analyst's approval"]
            return penny.penny_buy_problems(asset, history, sig, self.s, self.filings_check)
        dv = ind.avg_dollar_volume(history, 20)
        if dv is None or dv < self.s.min_dollar_volume:
            return ["not enough trading volume"]
        return []
