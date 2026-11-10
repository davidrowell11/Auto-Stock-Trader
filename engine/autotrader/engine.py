"""The bot's main cycle: watch what it holds, then look for new buys.

Test and Live run this exact same code. The only differences are which
Alpaca account the broker talks to and, in Live, that buys wait for your
approval while approve mode is on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from . import desk, penny, strategy
from . import indicators as ind
from .analyst import AnalystUnavailable, AnalystView
from .broker.base import Broker
from .config import HARD_LIMITS, Mode, Settings
from .journal import TRIM, Journal, StopState
from .learning import Memory
from .market import MARKET, Mood, market_mood
from .models import Bar, OrderRequest, Position, Side, Signal
from .signals.book import TOTAL_BOOST_MAX
from .signals.news import NewsCheck
from .risk import RiskManager
from .universe import build_universe, sector_of

log = logging.getLogger("autotrader")

HISTORY_DAYS = strategy.MIN_HISTORY + 10
APPROVAL_MAX_AGE = timedelta(hours=24)
DUST = 1e-6  # share counts below this are treated as zero
# Cheaper reviews: the fast model's "buy" stands only this far above the confidence needed.
QUICK_MARGIN = 0.15


EXTENDED_SESSIONS = {"pre", "after"}  # 4am-9:30am and 4pm-8pm New York time
EXTENDED_BUY_CUSHION = 0.005  # outside regular hours, pay at most 0.5% over the last trade
STALE_AFTER = timedelta(days=3)  # behind the market's latest day by more than a weekend
EXTENDED_SELL_CUSHION = 0.02  # ...and accept down to 2% under it to get out
# Safety checks (broker lookup, news, earnings) for at most this many of the best-scoring stocks
# per check, so scanning hundreds of stocks doesn't mean hundreds of lookups every few minutes.
MAX_SAFETY_CHECKS = 60
ENOUGH_CANDIDATES = 15  # stop checking once this many stocks have passed every check


def limit_for(side: Side, price: float, extended: bool, is_penny: bool) -> float | None:
    """The limit price for an order, or None for a market order (regular hours, not a penny stock)."""
    if side is Side.SELL and (extended or is_penny):
        return round(price * (1 - EXTENDED_SELL_CUSHION), 4 if price < 1 else 2)
    if side is Side.BUY and is_penny:
        return penny.limit_price_for_buy(price)
    if side is Side.BUY and extended:
        return round(price * (1 + EXTENDED_BUY_CUSHION), 4 if price < 1 else 2)
    return None


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
    checked: int = 0  # stocks looked at for buys
    paced: str | None = None  # e.g. "bought 3 new stocks today, the daily limit"

    def summary(self) -> str:
        """One line for the app, e.g. "Checked 52 stocks: bought MSFT"."""
        did = []
        if self.buys:
            did.append(f"bought {', '.join(b.split(' x')[0] for b in self.buys)}")
        if self.sells:
            did.append(f"sold {', '.join(x.split(':')[0] for x in self.sells)}")
        if self.queued:
            did.append(f"asked you about {', '.join(q.split(' x')[0] for q in self.queued)}")
        if "bot stopped mid-cycle" in self.skipped:
            did.append("stopped partway through")
        if self.halted:
            did.append(f"no new buys ({self.halted})")
            head = "Checked the stocks it owns"
        elif self.paced:
            did.append(f"no more buys today ({self.paced})")
            head = "Checked the stocks it owns"
        else:
            head = f"Checked {self.checked} stocks"
        if not did:
            did.append("no trades needed")
        return f"{head}: " + "; ".join(did)


class Engine:
    def __init__(self, broker: Broker, settings: Settings, journal: Journal,
                 filings_check: penny.FilingsCheck = penny.no_filings_check,
                 signals=None, analyst=None, penny_needs_analyst: bool = True,
                 keep_going: Callable[[], bool] = lambda: True,
                 earnings: Callable[[str], str | None] | None = None,
                 pause_buying: str | None = None,
                 progress: Callable[[str], None] = lambda step: None,
                 session: str = "regular",
                 screener: Callable[[], list[str]] | None = None,
                 news: Callable[[str], "NewsCheck"] | None = None,
                 memory: Callable[[], Memory] | None = None,
                 market_scan: Callable[[], list[str]] | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
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
        self.not_mine: set[str] = set()
        self.working: set[str] = set()  # stocks with one of the bot's own orders still working
        self.assets: dict[str, object] = {}
        self.earnings = earnings  # e.g. EarningsCalendar.reports_soon
        self.mood = Mood("calm", 1.0, True, "")
        # Set when Live stopped itself (e.g. after a big loss): keep protecting holdings, buy nothing.
        self.pause_buying = pause_buying
        # Plain-English steps for the app's "What the bot is doing" section.
        self.progress = progress
        # "regular", or "pre"/"after" for pre-market and after-hours trading (limit orders only).
        self.session = session
        # Live data: today's most-active stocks to scan too, and recent headlines.
        self.screener = screener
        self.news = news
        self.news_seen: dict[str, NewsCheck] = {}
        self.screened: set[str] = set()
        # What the bot learned from its own trades (see learning.py); read once per cycle.
        self.memory_fn = memory
        self.memory = Memory()
        # The most-traded US stocks (a few hundred), refreshed daily; scanned like the fixed list.
        self.market_scan = market_scan
        self.now = now

    @property
    def extended(self) -> bool:
        return self.session in EXTENDED_SESSIONS

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

    def sync_holdings(self, reprice: bool = False) -> tuple[list[Position], set[str]]:
        """The bot's holdings after settling its orders, and the stocks it still has an order working for.

        With reprice, a sell still unfilled since the last check (and, outside regular hours, an
        unfilled buy) is cancelled so this check can place it again at today's price. A stale limit
        order would otherwise sit below the market while a stock keeps falling.
        """
        held = {p.symbol: p.qty for p in self.broker.get_positions()}
        working = set()
        for symbol in sorted(self.journal.owned_symbols(self.mode)):
            state = self.settle(symbol, held.get(symbol))
            if (reprice and state is not None and state.order_id
                    and (state.order_side == "sell" or self.extended)):
                try:
                    self.broker.cancel_order(state.order_id)
                    self.progress(f"Re-pricing the unfilled {state.order_side} order for {symbol}")
                except Exception as exc:  # e.g. it filled a moment ago
                    log.info("Couldn't cancel the %s order for %s: %s", state.order_side, symbol, exc)
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
        self.progress("Checking the account")
        account = self.broker.get_account()
        if not self.journal.peak_equity(self.mode):  # a fresh history: start it from the broker's
            try:
                self.journal.seed_equity(self.mode, self.broker.equity_history(90))
            except Exception as exc:
                log.info("Couldn't fetch the account's past values: %s", exc)
        self.journal.record_equity(self.mode, account.equity)
        # The bot only manages stocks it bought itself; anything else in the account is yours.
        positions, self.working = self.sync_holdings(reprice=True)
        # Anyone's unfilled orders (after re-pricing the bot's): no new trade in those stocks.
        self.open_orders = self.broker.open_order_symbols()
        # Stocks already in the account that the bot doesn't manage (yours, or ones it lost track
        # of): buying more would double up without the bot knowing.
        self.not_mine = {p.symbol for p in self.broker.get_positions()} - {p.symbol for p in positions}
        if self.extended and positions:
            # Outside regular hours the position's price can lag; use the latest trade.
            latest = self.broker.latest_prices(p.symbol for p in positions)
            positions = [replace(p, current_price=latest.get(p.symbol, p.current_price))
                         for p in positions]
        self.progress(f"Watching {len(positions)} stock{'' if len(positions) == 1 else 's'} it owns"
                      if positions else "It doesn't own any stocks yet")

        if self.memory_fn is not None:
            try:
                self.memory = self.memory_fn()
            except Exception as exc:  # trade on without it
                log.warning("Couldn't read what the bot learned: %s", exc)
        universe = build_universe(self.s.always_watch, self.s.never_buy)
        never = {n.upper() for n in self.s.never_buy}
        if self.market_scan is not None:
            try:
                scanned = self.market_scan()
            except Exception as exc:  # trade the fixed list without it
                log.warning("Market scan unavailable: %s", exc)
                scanned = []
            have = set(universe)
            extra = [s for s in scanned if s not in have and s not in never]
            if extra:
                self.progress(f"Adding {len(extra)} more of the most-traded US stocks")
                universe = [*universe, *extra]
        if self.screener is not None:
            have = set(universe)
            extra = [s for s in self.screener() if s not in have and s not in never]
            self.screened = set(extra)
            if extra:
                self.progress(f"Adding {len(extra)} of today's most active stocks")
                universe = [*universe, *extra]
        symbols = sorted(set(universe) | {p.symbol for p in positions} | {MARKET})
        self.progress(f"Getting prices for {len(symbols)} stocks")
        bars = self.broker.get_daily_bars(symbols, HISTORY_DAYS)
        self.mood = market_mood(bars.get(MARKET))
        report.mood = self.mood.note
        report.mood_level = self.mood.level
        report.checked = len(universe)
        if self.mood.note:
            self.progress(f"Market mood: {self.mood.note}")

        kept = self._monitor_positions(positions, bars, report)

        # After hours, buys are sized by how the bot's earlier after-hours buys did.
        hours = self.memory.after_hours_size if self.extended else 1.0
        risk = RiskManager(self.s, account, kept, self.journal.peak_equity(self.mode),
                           mood_multiplier=self.mood.size_multiplier * hours)
        if not self.extended:  # trims aren't urgent: they wait for regular hours and market orders
            self._trim_oversized(risk, report)
        report.halted = (self.pause_buying or risk.buying_halted()
                         or (self.mood.note if self.mood.blocks_buying else None))
        if not report.halted and self.extended and not self.s.after_hours_buys:
            report.halted = "new buys wait for regular hours"
        if report.halted:
            log.warning("No new buys: %s", report.halted)
            self.progress(f"Not buying right now: {report.halted}")
            return report
        left = self.buys_left_today()
        if left <= 0:
            n = self.s.max_new_buys_per_day
            report.paced = f"bought {n} new stock{'' if n == 1 else 's'} today, the daily limit"
            self.progress(f"No more buys today: {report.paced}")
            return report
        self._scan_for_buys(universe, bars, risk, report, left)
        return report

    def buys_left_today(self) -> int:
        """New stocks the bot may still buy (or suggest, in approve mode) today, New York time."""
        return self.s.max_new_buys_per_day - len(self.bought_today())

    def bought_today(self) -> list[str]:
        """Stocks bought (or, in approve mode, suggested) since midnight New York time.
        A backtest's clock is the day it's replaying, so this works there too."""
        here = self.now().astimezone(strategy.NEW_YORK)
        midnight = here.replace(hour=0, minute=0, second=0, microsecond=0)
        done = sorted(self.journal.bought_since(self.mode, midnight))
        if self.needs_approval:
            done += [a["symbol"] for a in self.journal.pending_approvals(self.mode)
                     if a.get("side") == "buy"]
        return done

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

            self.progress(f"Checking {p.symbol}: price ${p.current_price:,.2f}, "
                          f"safety stop ${state.stop_price:,.2f}")
            closed = strategy.finished_days(history, self.now()) if history else history
            reason = (strategy.exit_reason(history, state.stop_price, closed, self.s.trend_exit_days)
                      if history else None)
            if reason is None and p.current_price <= state.stop_price:
                reason = f"Hit stop at ${state.stop_price:.2f}"
            if reason is None and penny.is_penny(p.current_price, self.s) and penny.volume_drying_up(history):
                reason = "Penny stock trading volume is drying up"
            if reason is None or not self._sell(p, reason, report):
                kept.append(p)  # still held, so it still counts against the limits
        return kept

    def _trim_oversized(self, risk: RiskManager, report: CycleReport) -> None:
        """Sell part of any holding that has grown well past its size limit, back to the limit.

        One stock that doubled can end up a big share of the money, so a single bad day in it
        hurts the whole account. Trims don't wait for approval (they lower risk, like a stop).
        """
        cap = risk.capital
        for i, p in enumerate(list(risk.positions)):
            if p.symbol in self.working or p.qty <= DUST or p.current_price <= 0 or cap <= 0:
                continue
            is_penny = penny.is_penny(p.current_price, self.s)
            pct = self.s.penny_max_position_pct if is_penny else self.s.max_position_pct
            limit = cap * pct
            if p.market_value <= limit * self.s.trim_over:
                continue
            excess = (p.market_value - limit) / p.current_price
            whole = abs(p.qty - round(p.qty)) < DUST
            qty = float(int(excess)) if whole else int(excess * 1000) / 1000
            qty = min(qty, p.qty)
            if qty <= DUST or qty * p.current_price < risk.MIN_FRACTIONAL_DOLLARS:
                continue
            share = p.market_value / cap
            reason = (f"{TRIM} {qty:g} shares: it grew to {share:.0%} of the money, over its "
                      f"{pct:.0%} limit")
            if self._sell(p, reason, report, qty=qty):
                risk.positions[i] = replace(p, qty=p.qty - qty)

    def _sell(self, p: Position, reason: str, report: CycleReport,
              qty: float | None = None) -> bool:
        # Protective sells never wait for approval: getting out fast is the point.
        qty = p.qty if qty is None else qty
        limit = limit_for(Side.SELL, p.current_price, self.extended,
                          penny.is_penny(p.current_price, self.s))
        order = OrderRequest(p.symbol, Side.SELL, qty, limit, reason, self.extended)
        if not self.keep_going():
            return False
        self.progress(f"Selling {p.symbol}: {reason}")
        try:
            order_id = self.broker.submit_order(order)
        except Exception as exc:  # broker errors must not stop the rest of the cycle
            log.error("Sell %s failed: %s", p.symbol, exc)
            report.skipped.append(f"{p.symbol}: sell failed ({exc})")
            return False
        self.journal.record_trade(self.mode, p.symbol, "sell", qty, p.current_price,
                                  order_id, [reason], when=self.now())
        state = self.journal.get_stop(self.mode, p.symbol)
        state.qty = p.qty if state.qty is None else state.qty
        self.order_placed(p.symbol, state, "sell", order_id)
        report.sells.append(f"{p.symbol}: {reason}")
        self.journal.add_event(self.mode, "sell", f"Sold {qty:g} {p.symbol}: {reason}")
        log.info("SELL %s x%s: %s", p.symbol, qty, reason)
        return True

    # ----- finding new buys -----
    def _scan_for_buys(self, universe: list[str], bars: dict[str, list[Bar]],
                       risk: RiskManager, report: CycleReport, left: int | None = None) -> None:
        left = self.s.max_new_buys_per_day if left is None else left
        # New buys per sector today (the bot learned that two oil stocks bought on the same
        # day fell together).
        sector_buys: dict[str, int] = {}
        for s in self.bought_today():
            sector_buys[sector_of(s)] = sector_buys.get(sector_of(s), 0) + 1
        held = {p.symbol for p in risk.positions} | self.journal.owned_symbols(self.mode)
        self.progress(f"Looking for buys among {len(universe)} stocks")
        trending: list[tuple[Signal, list[Bar]]] = []
        market = bars.get(MARKET)
        latest_day = market[-1].time if market else None
        for symbol in universe:
            if symbol in held or symbol in self.open_orders or self.journal.has_pending(self.mode, symbol):
                continue
            if symbol in self.not_mine:
                report.skipped.append(f"{symbol}: already in the account but not managed by the bot "
                                      "(tap Let the bot manage to hand it over)")
                continue
            history = bars.get(symbol)
            if not history:
                continue
            if latest_day is not None and latest_day - history[-1].time > STALE_AFTER:
                # No new prices while the market traded: halted, delisted or a data gap.
                last = history[-1].time
                report.skipped.append(f"{symbol}: its price data is out of date (last price "
                                      f"{last:%b} {last.day})")
                continue
            sig = strategy.score_trend(symbol, history)
            if sig is None:
                continue
            if sig.score < self.s.min_score:
                report.skipped.append(f"{symbol}: trend score {sig.score:.2f} is under the "
                                      f"{self.s.min_score:.2f} minimum")
                continue
            stretch = strategy.stretched(history, sig.atr, self.s.stretch_limit)
            if stretch:
                report.skipped.append(f"{symbol}: {stretch}")
                continue
            if symbol in self.screened:
                sig.sources.append("screener")
                sig.score = round(min(1.0, sig.score * self.memory.weight("screener")), 4)
            trending.append((sig, history))

        # Best first; the slower safety checks (broker, news, earnings) only for the top ones.
        trending.sort(key=lambda t: t[0].score, reverse=True)
        if len(trending) > ENOUGH_CANDIDATES:
            self.progress(f"{len(trending)} stocks are in uptrends; safety-checking the best ones")
        candidates: list[Signal] = []
        for sig, history in trending[:MAX_SAFETY_CHECKS]:
            if len(candidates) >= ENOUGH_CANDIDATES:
                break
            symbol = sig.symbol
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
        if candidates:
            self.progress(f"{len(candidates)} passed the trend and safety checks: "
                          + ", ".join(c.symbol for c in candidates[:8])
                          + ("…" if len(candidates) > 8 else ""))
        else:
            self.progress("No stock passed every check this time")
        if self.signals is not None and candidates:
            self.progress("Checking what Congress members and company insiders are buying")
        if self.signals is not None:
            for sig in candidates[: self.s.max_signal_lookups]:
                expert = self.signals.for_symbol(sig.symbol)
                if expert.boost:
                    # Each source's boost, scaled by how well its past trades did, within the usual cap.
                    boosts = expert.boosts or {"expert": expert.boost}
                    boost = min(TOTAL_BOOST_MAX,
                                sum(b * self.memory.weight(k) for k, b in boosts.items()))
                    sig.score = round(min(1.0, sig.score + boost), 4)
                    sig.reasons.extend(expert.reasons)
                    sig.sources.extend(k for k in boosts if k != "expert")
            candidates.sort(key=lambda c: c.score, reverse=True)

        if self.extended and candidates:
            # Daily prices are from the last close; size and price after-hours buys on the latest trade.
            latest = self.broker.latest_prices(c.symbol for c in candidates)
            candidates = [replace(c, price=latest.get(c.symbol, c.price)) for c in candidates]

        reviews = 0
        for sig in candidates:
            if not self.keep_going():
                report.skipped.append("bot stopped mid-cycle")
                return
            if left <= 0:
                # Spread buys over days instead of putting the budget on one day's prices.
                report.skipped.append(f"{sig.symbol}: waiting, it already bought "
                                      f"{self.s.max_new_buys_per_day} new stocks today")
                continue
            is_penny = penny.is_penny(sig.price, self.s)
            if is_penny and self.extended:
                report.skipped.append(f"{sig.symbol}: penny stocks are only bought in regular hours")
                continue
            sector = sector_of(sig.symbol)
            per_sector = self.s.max_buys_per_sector_per_day
            if per_sector and sector_buys.get(sector, 0) >= per_sector:
                report.skipped.append(f"{sig.symbol}: it already bought a stock in the {sector} sector today")
                continue
            asset = self.assets.get(sig.symbol)
            # Whole shares outside regular hours, where fractional orders aren't always accepted.
            fractional = (bool(getattr(asset, "fractionable", False)) and not is_penny
                          and not self.extended)
            stop = strategy.initial_stop(sig.price, sig.atr, strategy.stop_multiple(sig.price, self.s))
            size = risk.size_buy(sig.symbol, sig.price, sector, is_penny, fractional, bars,
                                 stop_price=stop)
            twin_note = size.twin
            if size.qty <= 0:
                report.skipped.append(f"{sig.symbol}: {size.note}"
                                      + (f" ({twin_note[0].lower()}{twin_note[1:]})" if twin_note else ""))
                continue
            reasons = [f"Score {sig.score:.2f}", *sig.reasons, size.note,
                       *([twin_note] if twin_note else [])]
            sources = [*sig.sources, *(["penny"] if is_penny else []),
                       *(["after_hours"] if self.extended else [])]
            similar_note, similar = self.memory.similar_note(sources, sig.score)
            if similar_note:
                reasons.append(similar_note)
            check = desk.desk_check(sig.symbol, bars, risk.positions, risk.capital, sector,
                                    sector_of, self.mood)
            facts = desk.trade_facts(check)
            if self.analyst is not None:
                cached = self.journal.todays_view(self.mode, sig.symbol)
                if not cached:
                    if reviews >= self.s.max_analyst_reviews:
                        report.skipped.append(f"{sig.symbol}: waiting for an analyst review next cycle")
                        continue
                    reviews += 1
                    self.progress(f"Asking the AI analyst about {sig.symbol}")
                verdict = self._analyst_verdict(sig, is_penny, cached, similar,
                                                None if cached else check, market=bars.get(MARKET))
                if isinstance(verdict, str):
                    report.skipped.append(f"{sig.symbol}: {verdict}")
                    self.progress(f"Skipped {sig.symbol}: {verdict}")
                    continue
                if verdict is not None:
                    reasons.append(f"Analyst ({verdict.confidence:.0%} confident): {verdict.reasoning}")
                    sources.append("analyst")
                    facts["analyst_confidence"] = round(verdict.confidence, 3)
            limit = limit_for(Side.BUY, sig.price, self.extended, is_penny)
            if self.needs_approval:
                self.journal.queue_approval(self.mode, sig.symbol, "buy", size.qty, limit, reasons,
                                           sources, facts)
                risk.register_buy(sig.symbol, size.qty, sig.price, sector)
                left -= 1
                sector_buys[sector] = sector_buys.get(sector, 0) + 1
                report.queued.append(f"{sig.symbol} x{size.qty:g}")
                self.progress(f"Asking you to approve buying {size.qty:g} {sig.symbol}")
                self.journal.add_event(self.mode, "approval",
                                       f"Wants to buy {size.qty:g} {sig.symbol} at about ${sig.price:.2f}. Tap to approve.")
                continue
            if not self.keep_going():
                report.skipped.append("bot stopped mid-cycle")
                return
            self.progress(f"Buying {size.qty:g} {sig.symbol} at about ${sig.price:,.2f}")
            try:
                order_id = self.broker.submit_order(
                    OrderRequest(sig.symbol, Side.BUY, size.qty, limit, reasons[0], self.extended))
            except Exception as exc:
                log.error("Buy %s failed: %s", sig.symbol, exc)
                report.skipped.append(f"{sig.symbol}: buy failed ({exc})")
                continue
            risk.register_buy(sig.symbol, size.qty, sig.price, sector)
            left -= 1
            sector_buys[sector] = sector_buys.get(sector, 0) + 1
            self.journal.record_trade(self.mode, sig.symbol, "buy", size.qty, sig.price,
                                      order_id, reasons, sources, when=self.now(), facts=facts)
            self.order_placed(sig.symbol, StopState(sig.price, stop, sig.price, qty=size.qty), "buy", order_id)
            report.buys.append(f"{sig.symbol} x{size.qty:g} @ ${sig.price:.2f}")
            self.journal.add_event(self.mode, "buy", f"Bought {size.qty:g} {sig.symbol} at about ${sig.price:.2f}")
            log.info("BUY %s x%s @ %.2f (stop %.2f)", sig.symbol, size.qty, sig.price, stop)

    def _look_up(self, market: list[Bar] | None) -> Callable[[str], dict]:
        """The analyst's look_up_stock tool: the desk-check summary for any symbol."""
        def look_up(symbol: str) -> dict:
            history = self.broker.get_daily_bars([symbol], HISTORY_DAYS).get(symbol, [])
            return desk.stock_facts(symbol, history, market)
        return look_up

    def _analyst_verdict(self, sig: Signal, is_penny: bool, cached: dict | None = None,
                         similar: list[dict] | None = None, check: dict | None = None,
                         market: list[Bar] | None = None) -> AnalystView | str | None:
        """The analyst's approval, a reason string to skip, or None if it was unavailable."""
        if cached:
            view = AnalystView(cached["action"], cached["confidence"], cached["reasoning"],
                               cached["red_flags"])
        else:
            candidate = {
                    "symbol": sig.symbol,
                    "price": sig.price,
                    "penny_stock": is_penny,
                    "score": sig.score,
                    "average_daily_move_dollars": round(sig.atr, 4),
                    "why_the_rules_like_it": sig.reasons,
                    "recent_headlines": self.news_seen.get(sig.symbol, NewsCheck()).headlines,
                    "lessons_from_past_trades": self.memory.lessons,
                    **({"buying_outside_regular_hours": True} if self.extended else {}),
                    **({"after_hours_record": self.memory.after_hours_note}
                       if self.extended and self.memory.after_hours_note else {}),
                    "similar_past_trades": similar or [],
                    **({"desk_check": check} if check else {}),
            }
            view = self._quick_view(candidate, is_penny)
            if view is None:
                try:
                    if getattr(self.analyst, "supports_lookup", False):
                        view = self.analyst.review(candidate, lookup=self._look_up(market))
                    else:
                        view = self.analyst.review(candidate)
                except AnalystUnavailable as exc:
                    log.warning("Analyst unavailable for %s: %s", sig.symbol, exc)
                    if is_penny:
                        return "analyst unavailable, and penny stocks always need its approval"
                    return None
            self.journal.save_view(self.mode, sig.symbol, sig.price, view.action, view.confidence,
                                   view.reasoning, view.red_flags, getattr(view, "model", None))
        floor = self.s.penny_analyst_min_confidence if is_penny else self.s.analyst_min_confidence
        if self.extended:  # pickier after hours when its after-hours buys have done worse
            floor = max(floor, min(0.95, floor + self.memory.after_hours_extra))
        if not view.approves:
            flags = f" Red flags: {'; '.join(view.red_flags)}." if view.red_flags else ""
            return f"analyst said skip: {view.reasoning}{flags}"
        sure = self.memory.adjust(view.confidence)  # corrected by its track record
        if sure < floor:
            if abs(sure - view.confidence) >= 0.005:
                return (f"analyst confidence {view.confidence:.0%} ({sure:.0%} after its track "
                        f"record) is below the {floor:.0%} needed")
            return f"analyst confidence {view.confidence:.0%} is below the {floor:.0%} needed"
        return view

    def _quick_view(self, candidate: dict, is_penny: bool) -> AnalystView | None:
        """The fast model's view when it's clear-cut, or None to ask the full analyst.

        Clear-cut means a confident buy with no red flags, or a skip that names a red flag.
        Penny stocks and everything in between always go to the full analyst.
        """
        if not (self.s.quick_reviews and not is_penny and getattr(self.analyst, "supports_quick", False)):
            return None
        try:
            view = self.analyst.review(candidate, quick=True)
        except AnalystUnavailable as exc:
            log.info("Quick review unavailable for %s, asking the full analyst: %s",
                     candidate["symbol"], exc)
            return None
        if view.approves and not view.red_flags and view.confidence >= self.s.analyst_min_confidence + QUICK_MARGIN:
            return view
        if not view.approves and view.red_flags:
            return view
        self.progress(f"Close call on {candidate['symbol']}; asking the full AI analyst")
        return None

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
        if asset.is_leveraged_fund:
            return ["leveraged and inverse funds are never bought (they lose value when held for weeks)"]
        if self.extended and penny.is_penny(sig.price, self.s):
            return ["penny stocks are only bought in regular hours"]
        sold = self.journal.last_protective_sell(self.mode, sig.symbol)
        cooloff = timedelta(days=HARD_LIMITS.stop_out_cooloff_days)
        if sold is not None and self.now() - sold < cooloff:
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
            return (penny.penny_buy_problems(asset, history, sig, self.s, self.filings_check)
                    or self._news_problems(sig.symbol))
        dv = ind.avg_dollar_volume(history, 20)
        if dv is None or dv < self.s.min_dollar_volume:
            return ["not enough trading volume"]
        return self._news_problems(sig.symbol)

    def _news_problems(self, symbol: str) -> list[str]:
        if self.news is None:
            return []
        check = self.news(symbol)
        self.news_seen[symbol] = check
        return [f"recent news: {check.red_flag}"] if check.red_flag else []
