"""The bot's main cycle: watch what it holds, then look for new buys.

Test and Live run this exact same code. The only differences are which
Alpaca account the broker talks to and, in Live, that buys wait for your
approval while approve mode is on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import penny, strategy
from . import indicators as ind
from .analyst import AnalystUnavailable, AnalystView
from .broker.base import Broker
from .config import HARD_LIMITS, Mode, Settings
from .journal import Journal, StopState
from .market import MARKET, Mood, market_mood
from .models import Bar, OrderRequest, Position, Side, Signal
from .risk import RiskManager
from .universe import build_universe, sector_of

log = logging.getLogger("autotrader")

HISTORY_DAYS = strategy.MIN_HISTORY + 10
APPROVAL_MAX_AGE = timedelta(hours=24)
DUST = 1e-6  # share counts below this are treated as zero


def bot_positions(broker: Broker, journal: Journal, mode: str) -> list[Position]:
    """The bot's own holdings, counting only the shares it bought.

    Stocks it didn't buy, and any extra shares of the same stock you bought
    yourself, are left out, so the bot never sells or counts them.
    """
    out = []
    for p in broker.get_positions():
        state = journal.get_stop(mode, p.symbol)
        if state is None:
            continue
        if state.qty is not None:
            p.qty = min(p.qty, state.qty)
        if p.qty > DUST:
            p.sector = sector_of(p.symbol)
            out.append(p)
    return out


@dataclass
class CycleReport:
    sells: list[str] = field(default_factory=list)
    buys: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    halted: str | None = None
    mood: str | None = None
    mood_level: str | None = None


class Engine:
    def __init__(self, broker: Broker, settings: Settings, journal: Journal,
                 filings_check: penny.FilingsCheck = penny.no_filings_check,
                 signals=None, analyst=None, penny_needs_analyst: bool = True,
                 keep_going: Callable[[], bool] = lambda: True,
                 earnings: Callable[[str], str | None] | None = None,
                 pause_buying: str | None = None) -> None:
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
        self.working: set[str] = set()  # stocks with one of the bot's own orders still working
        self.assets: dict[str, object] = {}
        self.earnings = earnings  # e.g. EarningsCalendar.reports_soon
        self.mood = Mood("calm", 1.0, True, "")
        # Set when Live stopped itself (e.g. after a big loss): keep protecting holdings, buy nothing.
        self.pause_buying = pause_buying

    @property
    def needs_approval(self) -> bool:
        return self.s.mode is Mode.LIVE and self.s.approve_mode

    # ----- the bot's own orders -----
    def settle(self, symbol: str, held_qty: float | None) -> StopState | None:
        """Bring one stock's record up to date with its last order. None means the bot no longer owns it."""
        state = self.journal.get_stop(self.mode, symbol)
        if state is None:
            return None
        if state.order_id:
            try:
                update = self.broker.get_order(state.order_id)
            except Exception as exc:  # can't tell yet; leave it alone this cycle
                log.warning("Couldn't check order %s for %s: %s", state.order_id, symbol, exc)
                return state
            if not update.done:
                return state
            if state.order_side == "buy":
                state.qty = update.filled_qty if update.filled_qty is not None else state.qty
            else:
                sold = update.filled_qty if update.filled_qty is not None else (state.qty or 0.0)
                state.qty = max(0.0, (state.qty if state.qty is not None else (held_qty or 0.0)) - sold)
            state.order_id = state.order_side = None
            if state.qty is not None and state.qty <= DUST:
                self.journal.clear_stop(self.mode, symbol)
                return None
            self.journal.save_stop(self.mode, symbol, state)
            return state
        if held_qty is None or held_qty <= DUST:
            # Nothing working and nothing held: sold outside the bot, or a buy that never filled.
            self.journal.clear_stop(self.mode, symbol)
            return None
        if state.qty is None:  # journal from before the bot tracked its own share count
            state.qty = held_qty
            self.journal.save_stop(self.mode, symbol, state)
        return state

    def sync_holdings(self) -> tuple[list[Position], set[str]]:
        """The bot's holdings after settling its orders, and the stocks it still has an order working for."""
        held = {p.symbol: p.qty for p in self.broker.get_positions()}
        working = set()
        for symbol in sorted(self.journal.owned_symbols(self.mode)):
            state = self.settle(symbol, held.get(symbol))
            if state is not None and state.order_id:
                working.add(symbol)
        return bot_positions(self.broker, self.journal, self.mode), working

    def order_placed(self, symbol: str, state: StopState, side: str, order_id: str) -> None:
        state.order_id, state.order_side = order_id, side
        self.journal.save_stop(self.mode, symbol, state)
        held = {p.symbol: p.qty for p in self.broker.get_positions()}
        self.settle(symbol, held.get(symbol))  # most orders fill at once

    def run_cycle(self) -> CycleReport:
        report = CycleReport()
        expired = self.journal.expire_approvals(self.mode, APPROVAL_MAX_AGE)
        if expired:
            self.journal.add_event(self.mode, "approval",
                                   f"{expired} suggested buy{'s' if expired > 1 else ''} expired after a day")
        account = self.broker.get_account()
        self.journal.record_equity(self.mode, account.equity)
        # Anyone's unfilled orders: the bot won't open a new trade in those stocks.
        self.open_orders = self.broker.open_order_symbols()
        # The bot only manages stocks it bought itself; anything else in the account is yours.
        positions, self.working = self.sync_holdings()

        universe = build_universe(self.s.always_watch, self.s.never_buy)
        symbols = sorted(set(universe) | {p.symbol for p in positions} | {MARKET})
        bars = self.broker.get_daily_bars(symbols, HISTORY_DAYS)
        self.mood = market_mood(bars.get(MARKET))
        report.mood = self.mood.note
        report.mood_level = self.mood.level

        kept = self._monitor_positions(positions, bars, report)

        risk = RiskManager(self.s, account, kept, self.journal.peak_equity(self.mode),
                           mood_multiplier=self.mood.size_multiplier)
        report.halted = (self.pause_buying or risk.buying_halted()
                         or (self.mood.note if self.mood.blocks_buying else None))
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
            if p.symbol in self.working:  # the bot's own order for it is still working
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
        state = self.journal.get_stop(self.mode, p.symbol)
        state.qty = p.qty if state.qty is None else state.qty
        self.order_placed(p.symbol, state, "sell", order_id)
        report.sells.append(f"{p.symbol}: {reason}")
        self.journal.add_event(self.mode, "sell", f"Sold {p.qty:g} {p.symbol}: {reason}")
        log.info("SELL %s x%s: %s", p.symbol, p.qty, reason)
        return True

    # ----- finding new buys -----
    def _scan_for_buys(self, universe: list[str], bars: dict[str, list[Bar]],
                       risk: RiskManager, report: CycleReport) -> None:
        held = {p.symbol for p in risk.positions} | self.journal.owned_symbols(self.mode)
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
            asset = self.assets.get(sig.symbol)
            fractional = bool(getattr(asset, "fractionable", False)) and not is_penny
            size = risk.size_buy(sig.symbol, sig.price, sector, is_penny, fractional)
            if size.qty <= 0:
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
                report.queued.append(f"{sig.symbol} x{size.qty:g}")
                self.journal.add_event(self.mode, "approval",
                                       f"Wants to buy {size.qty:g} {sig.symbol} at about ${sig.price:.2f}. Tap to approve.")
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
            self.order_placed(sig.symbol, StopState(sig.price, stop, sig.price, qty=size.qty), "buy", order_id)
            report.buys.append(f"{sig.symbol} x{size.qty:g} @ ${sig.price:.2f}")
            self.journal.add_event(self.mode, "buy", f"Bought {size.qty:g} {sig.symbol} at about ${sig.price:.2f}")
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
        data = self.broker.get_daily_bars([symbol, MARKET], HISTORY_DAYS)
        self.mood = market_mood(data.get(MARKET))
        if self.mood.blocks_buying:
            return None, [self.mood.note]
        history = data.get(symbol, [])
        if not history:
            return None, ["no current prices"]
        sig = strategy.score_trend(symbol, history)
        if sig is None:
            return None, ["the uptrend no longer holds"]
        return sig, self._buy_problems(sig, history)

    def _buy_problems(self, sig: Signal, history: list[Bar]) -> list[str]:
        asset = self.broker.get_asset(sig.symbol)
        self.assets[sig.symbol] = asset
        if not asset.tradable:
            return ["not tradable"]
        if asset.is_otc:
            return ["OTC/pink-sheet stocks are never traded"]
        sold = self.journal.last_protective_sell(self.mode, sig.symbol)
        cooloff = timedelta(days=HARD_LIMITS.stop_out_cooloff_days)
        if sold is not None and datetime.now(timezone.utc) - sold < cooloff:
            back = sold + cooloff
            until = f"{back:%b} {back.day}"
            return [f"cooling off after it was sold to protect you (can buy again from {until})"]
        if sig.price < self.s.min_price:
            return [f"price under ${self.s.min_price:.2f}"]
        if self.earnings is not None:
            soon = self.earnings(sig.symbol)
            if soon:
                return [soon]
        if penny.is_penny(sig.price, self.s):
            if not self.mood.allow_penny:
                return ["no penny stocks while the market is weak"]
            if self.analyst is None and self.penny_needs_analyst:
                return ["penny stocks are only bought with the AI analyst's approval"]
            return penny.penny_buy_problems(asset, history, sig, self.s, self.filings_check)
        dv = ind.avg_dollar_volume(history, 20)
        if dv is None or dv < self.s.min_dollar_volume:
            return ["not enough trading volume"]
        return []
