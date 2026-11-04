"""The bot server: runs the Test and Live bots and serves the app's API.

    python -m autotrader.server            # listens on 0.0.0.0:8000

Every request (except /health) needs ``Authorization: Bearer <token>``, where
the token is the ``AUTOTRADER_APP_TOKEN`` secret. You type the same token
into the app once, when you connect it.

Each bot has its own lock, so a trading cycle, an approval and the kill switch
never run at the same time. The kill switch stops the bot at once (the cycle
checks before every order), then waits briefly for the cycle to finish before
selling, so the same stock is never sold twice.
"""

from __future__ import annotations

import dataclasses
import hmac
import json
import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import penny, strategy
from .analyst import QUICK_MODEL
from .broker.base import Broker
from .config import HARD_LIMITS, Mode, Settings, load_settings
from .engine import APPROVAL_MAX_AGE, Engine, bot_positions, limit_for
from .readiness import live_checks, test_results
from .journal import Journal, StopState
from .lab import Lab, clamp_rules
from .learning import Learner, LearnerBusy
from .learning import report as learning_report
from .models import OrderRequest, Side
from .risk import RiskManager
from .universe import sector_of

log = logging.getLogger("autotrader.server")

EDITABLE = ("budget", "approve_mode", "always_watch", "never_buy", "after_hours_buys",
            "quick_reviews")


class SettingsStore:
    """The settings you change in the app, saved per mode in a JSON file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data: dict[str, dict] = {"test": {}, "live": {}}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    def get(self, mode: Mode) -> dict:
        return dict(self.data.get(mode.value, {}))

    def update(self, mode: Mode, changes: dict) -> None:
        with self._lock:
            self.data.setdefault(mode.value, {}).update(changes)
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)  # atomic, so a crash can't leave a half-written file

    def ai_cap(self, default: float) -> float:
        """The monthly Claude spending cap (one for the whole server; both bots share the API key)."""
        value = self.data.get("ai", {}).get("monthly_cap")
        return default if value is None else float(value)

    def set_ai_cap(self, cap: float) -> None:
        with self._lock:
            self.data.setdefault("ai", {})["monthly_cap"] = cap
            self._save()

    def starter_active(self) -> bool:
        """True until Live has been running for the starter period."""
        first = self.get(Mode.LIVE).get("first_started")
        if not first:
            return True
        age = datetime.now(timezone.utc) - datetime.fromisoformat(first)
        return age < timedelta(days=HARD_LIMITS.live_starter_days)

    def settings_for(self, mode: Mode, with_lab: bool = True) -> Settings:
        saved = self.get(mode)
        overrides = {k: (tuple(v) if isinstance(v, list) else v)
                     for k, v in saved.items() if k in EDITABLE}
        if with_lab:  # rule changes the strategy lab proved (Live: only once you approved them)
            overrides.update(clamp_rules(saved.get("lab_rules", {})))
        # After-hours buying starts on for Test (fake money) and off for Live.
        overrides.setdefault("after_hours_buys", mode is Mode.TEST)
        # Cheaper AI reviews too: tried in Test first; switch on for Live in the app.
        overrides.setdefault("quick_reviews", mode is Mode.TEST)
        if mode is Mode.LIVE:
            # Start small: never more than the starter cap of real money, whatever the file says.
            # Live never falls back to the whole account: without a saved cap it stays at the starter cap.
            cap = HARD_LIMITS.live_starter_cap
            budget = overrides.get("budget")
            if budget is None:
                overrides["budget"] = cap
            elif self.starter_active():
                overrides["budget"] = min(budget, cap)
        return load_settings(mode=mode, **overrides)


SESSION_NOTE = {"pre": " (pre-market, limit orders only)", "after": " (after hours, limit orders only)"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BotRunner:
    """One bot (Test or Live) running its cycle in a background thread."""

    APPROVAL_MAX_AGE = APPROVAL_MAX_AGE
    KILL_WAIT_SECONDS = 60

    def __init__(self, mode: Mode, store: SettingsStore, journal: Journal, lock: threading.RLock,
                 broker_factory: Callable[[Mode], Broker], engine_kwargs: dict | None = None,
                 live_enabled: bool = True) -> None:
        self.mode = mode
        self.live_enabled = live_enabled
        self.store = store
        self.journal = journal
        self.lock = lock
        self.engine_kwargs = engine_kwargs or {}
        self.error: str | None = None
        self.last_cycle: str | None = None
        self.halted: str | None = None
        self.mood: str | None = None
        self.mood_level: str | None = None
        self.failures = 0  # cycles failed in a row
        # What the app's "What the bot is doing" section shows. Kept in memory only.
        self.now_doing = "Stopped"
        self.now_since = _now_iso()
        self.next_check: str | None = None
        self.steps: deque[dict] = deque(maxlen=60)
        self._status_lock = threading.Lock()  # the bot thread writes, app requests read
        self.last_report: dict | None = None
        self.busy = False  # True while a check is running
        self._wake = threading.Event()  # set by Start so the bot begins at once
        try:
            self.broker: Broker | None = broker_factory(mode)
        except Exception as exc:  # e.g. Live keys not added yet
            self.broker = None
            self.error = str(exc)
        self._stop = threading.Event()
        self.halted_by_you = threading.Event()  # set by Stop or the kill switch, cleared by Start
        self._thread: threading.Thread | None = None
        self.learner: Learner | None = None  # set on the Test bot: it does the weekly learning
        self.lab: Lab | None = None  # ...and the weekly strategy-lab experiment
        self._lab_thread: threading.Thread | None = None

    def step(self, text: str, log_it: bool = True) -> None:
        """Record what the bot is doing right now (and, if log_it, add it to the recent steps)."""
        with self._status_lock:
            self.now_doing, self.now_since = text, _now_iso()
            if log_it:
                self.steps.append({"ts": self.now_since, "text": text})

    def live_status(self) -> dict:
        with self._status_lock:
            now, since, steps = self.now_doing, self.now_since, list(reversed(self.steps))
        running = self.running
        if not running and not self.busy:
            # Derived, not stored, so a finishing check can't make a stopped bot look active.
            now = "Stopped. Press Start bot to begin."
        return {
            "now": now, "since": since, "working": self.busy, "steps": steps,
            "next_check": self.next_check if running else None,
            "last_cycle": self.last_report,
        }

    @property
    def settings(self) -> Settings:
        return self.store.settings_for(self.mode)

    @property
    def running(self) -> bool:
        return bool(self.store.get(self.mode).get("running", False))

    def engine(self, session: str | None = None) -> Engine:
        if session is None:
            session = self.session()
        return Engine(self.broker, self.settings, self.journal,
                      keep_going=lambda: not self.halted_by_you.is_set(),
                      pause_buying=self.store.get(self.mode).get("buying_paused"),
                      progress=self.step, session=session,
                      **self.engine_kwargs)

    def session(self) -> str:
        """The market session right now; "closed" if the broker can't say."""
        if self.broker is None:
            return "closed"
        try:
            return self.broker.market_session()
        except Exception as exc:
            log.warning("Couldn't get the market session: %s", exc)
            try:
                return "regular" if self.broker.is_market_open() else "closed"
            except Exception:
                return "closed"

    def pause_buying(self, reason: str) -> None:
        """Automatic safety stop: no new buys until you press Start. Holdings stay protected."""
        if self.store.get(self.mode).get("buying_paused"):
            return
        self.store.update(self.mode, {"buying_paused": reason})
        self.journal.add_event(self.mode.value, "kill",
                               f"Safety stop: {reason}. No new buys until you press Start. "
                               "Stops on your holdings stay active.")
        log.warning("%s buying paused: %s", self.mode.value, reason)

    def check_live_safety(self, equity: float) -> None:
        if self.mode is not Mode.LIVE:
            return
        capital = self.settings.budget or equity
        # Peak since you last pressed Start, so resuming after a safety stop starts fresh.
        saved = self.store.get(self.mode).get("safety_peak")
        peak = max(saved or equity, equity)
        if peak != saved:
            self.store.update(self.mode, {"safety_peak": peak})
        if capital > 0:
            lost = (peak - equity) / capital
            if lost >= HARD_LIMITS.live_auto_stop_drawdown:
                self.pause_buying(f"Live lost {lost:.0%} of its money from its peak")

    def run_once(self, session: str | None = None) -> None:
        if self.broker is None:
            return
        if session is None:
            session = self.session()
        self.next_check = None
        with self.lock:
            self.busy = True
            try:
                self.step("Starting a check" + SESSION_NOTE.get(session, ""))
                report = self.engine(session).run_cycle()
                self.check_live_safety(self.broker.get_account().equity)
            finally:
                self.busy = False
        summary = report.summary()
        self.step(summary)
        self.last_report = {
            "ts": _now_iso(), "summary": summary, "checked": report.checked,
            "buys": report.buys, "sells": report.sells, "queued": report.queued,
            "skipped": report.skipped[:40], "skipped_total": len(report.skipped),
            "halted": report.halted, "mood": report.mood,
        }
        self.halted = report.halted
        self.mood = report.mood
        self.mood_level = report.mood_level
        self.last_cycle = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def loop(self) -> None:
        while not self._stop.is_set():
            wait = self.settings.poll_seconds
            try:
                if not self.running:
                    self.step("Stopped. Press Start bot to begin.", log_it=False)
                    self.next_check = None
                elif self.broker is None:
                    self.step(f"Can't reach the broker: {self.error}", log_it=False)
                elif (session := self.session()) == "closed":
                    self.step("Market closed. Trading runs 4am to 8pm New York time on weekdays "
                              "(regular hours 9:30am to 4pm)", log_it=False)
                    wait = min(wait, 60)  # notice the open (and Start) quickly
                    self.next_check = None
                else:
                    self.run_once(session)
                    self.error = None
                    self.failures = 0
                    if self.running:  # not if Stop was pressed during the check
                        self.next_check = (datetime.now(timezone.utc)
                                           + timedelta(seconds=wait)).isoformat(timespec="seconds")
                        self.step(f"Resting until the next check. {self.last_report['summary']}.",
                                  log_it=False)
            except Exception as exc:  # keep the thread alive; show the problem in the app
                log.exception("%s cycle failed", self.mode.value)
                self.error = str(exc)
                self.step(f"A check failed: {exc}. It will try again.")
                self.failures += 1
                if self.mode is Mode.LIVE and self.failures >= HARD_LIMITS.live_auto_stop_errors:
                    self.pause_buying(f"{self.failures} cycles failed in a row ({exc})")
            self.learn_if_due()
            self.lab_if_due()
            self._pause(wait)

    def lab_if_due(self, force: bool = False) -> bool:
        """Start the weekly strategy-lab experiment in the background (it replays years of prices)."""
        if self.lab is None or (self._lab_thread and self._lab_thread.is_alive()):
            return False
        try:
            if not (force or self.lab.due()):
                return False
        except Exception:
            log.exception("Strategy lab check failed")
            return False

        def work():
            try:
                self.lab.run()
            except Exception:
                log.exception("Strategy lab experiment failed")

        self._lab_thread = threading.Thread(target=work, name="strategy-lab", daemon=True)
        self._lab_thread.start()
        return True

    def learn_if_due(self) -> None:
        """Once a week, learn from finished trades (runs even while stopped or the market is closed)."""
        if self.learner is None:
            return
        try:
            if self.learner.due():
                self.step("Reviewing how its finished trades turned out", log_it=False)
                if self.learner.learn():
                    self.step("Updated what it learned from its trades")
        except Exception:
            log.exception("Learning from trades failed")

    def _pause(self, seconds: float) -> None:
        """Wait between checks; Start (or shutting down) cuts the wait short."""
        self._wake.wait(seconds)
        self._wake.clear()

    def stop_thread(self) -> None:
        self._stop.set()
        self._wake.set()

    def start_thread(self) -> None:
        self._thread = threading.Thread(target=self.loop, name=f"bot-{self.mode.value}", daemon=True)
        self._thread.start()

    # ----- actions from the app -----
    def approve(self, approval_id: int) -> dict:
        if self.mode is Mode.LIVE and not self.live_enabled:
            raise HTTPException(403, "Live trading is off on the server (AUTOTRADER_LIVE_ENABLED)")
        # A cycle can take a few minutes (analyst reviews); don't hang the app waiting.
        if not self.lock.acquire(timeout=10):
            raise HTTPException(409, "The bot is in the middle of a cycle. Try again in a minute.")
        try:
            return self._approve(approval_id)
        finally:
            self.lock.release()

    def _approve(self, approval_id: int) -> dict:
        a = self.journal.get_approval(approval_id)
        if a is None or a["mode"] != self.mode.value:
            raise HTTPException(404, "No such approval")
        if a["status"] != "pending":
            raise HTTPException(409, f"Already {a['status']}")
        if self.broker is None:
            raise HTTPException(503, self.error or "Broker not connected")
        paused = self.store.get(self.mode).get("buying_paused")
        if paused:
            raise HTTPException(409, f"Buying is paused by the safety stop ({paused}). Press Start first.")
        queued = datetime.fromisoformat(a["ts"])
        if datetime.now(timezone.utc) - queued > self.APPROVAL_MAX_AGE:
            self.journal.set_approval_status(approval_id, "expired")
            raise HTTPException(409, "This suggestion is over a day old, so it expired")
        engine = self.engine()
        positions, _ = engine.sync_holdings()
        if a["symbol"] in self.journal.owned_symbols(self.mode.value):
            self.journal.set_approval_status(approval_id, "expired")
            raise HTTPException(409, f"The bot already owns {a['symbol']}")
        sig, problems = engine.recheck_buy(a["symbol"])
        if sig is None or problems:
            self.journal.set_approval_status(approval_id, "expired")
            raise HTTPException(409, f"Can't buy now: {'; '.join(problems)}")
        account = self.broker.get_account()
        price = sig.price
        if engine.extended:
            price = self.broker.latest_prices([a["symbol"]]).get(a["symbol"], price)
        s = engine.s
        is_penny = penny.is_penny(price, s)
        # Pre-market/after hours with after-hours buying on: a limit order now. Otherwise (closed,
        # buying off, or a penny stock) the order waits for the 9:30am open, as it always has.
        extended = engine.extended and s.after_hours_buys and not is_penny
        risk = RiskManager(s, account, positions, self.journal.peak_equity(self.mode.value),
                           mood_multiplier=engine.mood.size_multiplier)
        asset = engine.assets.get(a["symbol"])
        fractional = bool(getattr(asset, "fractionable", False)) and not is_penny and not extended
        size = risk.size_buy(a["symbol"], price, sector_of(a["symbol"]), is_penny, fractional)
        qty = min(float(a["qty"]), size.qty)  # re-checked: prices and the account may have moved
        if not fractional:
            qty = float(int(qty))
        if qty <= 0:
            self.journal.set_approval_status(approval_id, "expired")
            raise HTTPException(409, f"Can't buy now: {size.note}")
        limit = limit_for(Side.BUY, price, extended, is_penny)
        order_id = self.broker.submit_order(
            OrderRequest(a["symbol"], Side.BUY, qty, limit, "Approved in app", extended))
        # Tagged by how it's bought now, not how things stood when it was suggested.
        sources = [x for x in a["sources"] if x != "after_hours"] + (["after_hours"] if extended else [])
        self.journal.record_trade(self.mode.value, a["symbol"], "buy", qty, price, order_id,
                                  [*a["reasons"], "Approved by you in the app"], sources)
        stop = strategy.initial_stop(price, sig.atr, strategy.stop_multiple(price, s))
        engine.order_placed(a["symbol"], StopState(price, stop, price, qty=qty), "buy", order_id)
        self.journal.set_approval_status(approval_id, "approved")
        self.journal.add_event(self.mode.value, "buy", f"Bought {qty:g} {a['symbol']} at about ${price:.2f} (you approved)")
        return {"bought": qty, "price": price, "stop": round(stop, 2)}

    def reject(self, approval_id: int) -> None:
        with self.lock:
            a = self.journal.get_approval(approval_id)
            if a is None or a["mode"] != self.mode.value:
                raise HTTPException(404, "No such approval")
            if a["status"] != "pending":
                raise HTTPException(409, f"Already {a['status']}")
            self.journal.set_approval_status(approval_id, "rejected")

    def kill(self, sell_all: bool) -> dict:
        """Kill switch: stop the bot now, and optionally sell everything it bought.

        Only the bot's own shares and orders are touched; your own holdings and
        orders never are.
        """
        self.halted_by_you.set()  # a running cycle places no more orders
        self.store.update(self.mode, {"running": False})
        self.step("Stopped by the kill switch")
        cancelled = self.journal.cancel_pending(self.mode.value)
        sold, failed = [], []
        if sell_all and self.broker is not None:
            # Wait for a running cycle to finish so a stock it's selling isn't sold twice.
            # (It stops at its next order, so this is short; after the wait, sell anyway.)
            got_lock = self.lock.acquire(timeout=self.KILL_WAIT_SECONDS)
            try:
                sold, failed = self._sell_everything()
            finally:
                if got_lock:
                    self.lock.release()
        self.journal.add_event(self.mode.value, "kill",
                               "Kill switch: bot stopped" + (f", sold {', '.join(sold)}" if sold else ""))
        if failed:
            self.journal.add_event(self.mode.value, "kill", f"Kill switch couldn't sell {', '.join(failed)}")
        return {"stopped": True, "sold": sold, "failed": failed, "cancelled_approvals": cancelled}

    def _sell_everything(self) -> tuple[list[str], list[str]]:
        session = self.session()
        engine = Engine(self.broker, self.settings, self.journal, session=session)
        mode = self.mode.value
        for symbol in self.journal.owned_symbols(mode):  # unfilled buys first
            state = self.journal.get_stop(mode, symbol)
            if state and state.order_id and state.order_side == "buy":
                try:
                    self.broker.cancel_order(state.order_id)
                except Exception as exc:
                    log.error("Kill switch couldn't cancel the %s buy: %s", symbol, exc)
        positions, working = engine.sync_holdings()
        if engine.extended and positions:
            latest = self.broker.latest_prices(p.symbol for p in positions)
            positions = [dataclasses.replace(p, current_price=latest.get(p.symbol, p.current_price))
                         for p in positions]
        sold, failed = [], []
        for p in positions:
            state = self.journal.get_stop(mode, p.symbol)
            if p.symbol in working:
                if state.order_side == "sell":
                    sold.append(p.symbol)  # already being sold
                else:
                    failed.append(p.symbol)  # buy still being cancelled; press again in a moment
                continue
            # Closed market: a market order waits for the open. Pre-market/after hours: limit order.
            limit = limit_for(Side.SELL, p.current_price, engine.extended,
                              penny.is_penny(p.current_price, engine.s))
            try:
                order_id = self.broker.submit_order(
                    OrderRequest(p.symbol, Side.SELL, p.qty, limit, "Kill switch", engine.extended))
            except Exception as exc:
                log.error("Kill switch sell %s failed: %s", p.symbol, exc)
                failed.append(p.symbol)
                continue
            self.journal.record_trade(mode, p.symbol, "sell", p.qty, p.current_price, order_id, ["Kill switch"])
            engine.order_placed(p.symbol, state, "sell", order_id)
            sold.append(p.symbol)
        return sold, failed


# ----- request bodies -----
class SettingsUpdate(BaseModel):
    budget: float | None = Field(default=None, ge=0)
    clear_budget: bool = False
    approve_mode: bool | None = None
    after_hours_buys: bool | None = None
    quick_reviews: bool | None = None
    always_watch: list[str] | None = None
    never_buy: list[str] | None = None


class Decision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")


class KillRequest(BaseModel):
    sell_all: bool = False


class AiBudget(BaseModel):
    monthly_cap: float = Field(ge=0, le=10_000)


class LabDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")


class LearningDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: str = "test"


def create_app(runners: dict[Mode, BotRunner], journal: Journal, token: str,
               ask_fn: Callable[[str, dict], str] | None = None,
               live_enabled: bool = False, skip_results: bool = False,
               learner: Learner | None = None, spend=None,
               ask_stream_fn: Callable[[str, dict], Iterable[str]] | None = None) -> FastAPI:
    if not token:
        raise RuntimeError("Set AUTOTRADER_APP_TOKEN before starting the server")
    learner = learner or Learner(journal)
    app = FastAPI(title="Auto Stock Trader")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    def auth(authorization: str = Header(default="")) -> None:
        supplied = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "Wrong or missing app token")

    def runner(mode: str) -> BotRunner:
        try:
            return runners[Mode(mode)]
        except (ValueError, KeyError):
            raise HTTPException(404, "Mode must be test or live") from None

    def broker_of(r: BotRunner) -> Broker:
        if r.broker is None:
            raise HTTPException(503, r.error or "Broker not connected")
        return r.broker

    @app.get("/health")
    def health():
        return {"ok": True}

    secured = {"dependencies": [Depends(auth)]}

    @app.get("/api/{mode}/summary", **secured)
    def summary(mode: str):
        r = runner(mode)
        s = r.settings
        out = {
            "mode": r.mode.value, "running": r.running, "error": r.error, "halted": r.halted,
            "last_cycle": r.last_cycle, "budget": s.budget, "approve_mode": s.approve_mode,
            "after_hours_buys": s.after_hours_buys,
            "mood": r.mood, "mood_level": r.mood_level, "buying_paused": r.store.get(r.mode).get("buying_paused"),
            "live_enabled": live_enabled,
            "pending_approvals": len(journal.pending_approvals(r.mode.value)),
        }
        if r.broker is not None:
            acct = r.broker.get_account()
            positions = bot_positions(r.broker, journal, r.mode.value)
            session = r.session()
            market_open = session == "regular"
            out.update({
                "equity": acct.equity, "cash": acct.cash,
                "today_change": acct.equity - acct.last_equity,
                "today_change_pct": (acct.equity / acct.last_equity - 1) if acct.last_equity else 0.0,
                "invested": sum(p.market_value for p in positions),
                "positions": len(positions), "market_open": market_open, "session": session,
            })
        return out

    @app.get("/api/{mode}/positions", **secured)
    def positions(mode: str):
        r = runner(mode)
        held = bot_positions(broker_of(r), journal, r.mode.value)
        buys = {t["symbol"]: t for t in journal.trades(r.mode.value) if t["side"] == "buy"}
        out = []
        for p in held:
            stop = journal.get_stop(r.mode.value, p.symbol)
            out.append({
                "symbol": p.symbol, "qty": p.qty, "avg_price": p.avg_price,
                "price": p.current_price, "value": p.market_value,
                "gain": (p.current_price - p.avg_price) * p.qty,
                "gain_pct": p.current_price / p.avg_price - 1 if p.avg_price else 0.0,
                "stop": stop.stop_price if stop else None,
                "sector": sector_of(p.symbol),
                "why_bought": buys.get(p.symbol, {}).get("reasons", []),
            })
        return sorted(out, key=lambda x: -x["value"])

    @app.get("/api/{mode}/live", **secured)
    def live_status(mode: str):
        """What the bot is doing right now, its recent steps and its last check's results."""
        return runner(mode).live_status()

    @app.get("/api/{mode}/trades", **secured)
    def trades(mode: str):
        return list(reversed(journal.trades(runner(mode).mode.value)))

    @app.get("/api/{mode}/equity", **secured)
    def equity(mode: str):
        return journal.equity_history(runner(mode).mode.value)

    @app.get("/api/{mode}/events", **secured)
    def events(mode: str):
        return journal.events(runner(mode).mode.value)

    @app.get("/api/{mode}/analyst", **secured)
    def analyst_views(mode: str):
        views = list(reversed(journal.views(runner(mode).mode.value)))
        buys = [v for v in views if v["action"] == "buy"]
        quick = sum(v.get("model") == QUICK_MODEL for v in views)
        return {"views": views[:200], "total": len(views), "approved": len(buys), "quick": quick}

    @app.get("/api/{mode}/learning", **secured)
    def learning(mode: str):
        """What the bot learned from its trades, and (Live) new lessons waiting for your OK."""
        return learning_report(journal, runner(mode).mode.value)

    @app.post("/api/live/learning", **secured)
    def decide_learning(body: LearningDecision):
        r = runners[Mode.LIVE]
        # Not halfway through a Live check, but don't hang the app if one is running long.
        if not r.lock.acquire(timeout=10):
            raise HTTPException(409, "The bot is in the middle of a check. Try again in a minute.")
        try:
            if body.decision == "approve":
                learner.approve_live()
            else:
                learner.reject_live()
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            r.lock.release()
        return learning_report(journal, Mode.LIVE.value)

    @app.post("/api/test/learning/run", **secured)
    def learn_now():
        """Learn from finished trades now instead of waiting for the weekly review."""
        try:
            learned = learner.learn()
        except LearnerBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        if learned is None:
            raise HTTPException(409, "Not enough finished trades to learn from yet")
        return learning_report(journal, Mode.TEST.value)

    @app.get("/api/ai/spending", **secured)
    def ai_spending():
        """What the AI analyst has cost this month, and the cap."""
        if spend is None:
            return {"enabled": False, "month": None, "spent": 0.0, "cap": None}
        return {"enabled": True, **spend.report()}

    @app.put("/api/ai/spending", **secured)
    def set_ai_spending(body: AiBudget):
        if spend is None:
            raise HTTPException(503, "The AI analyst is off (add ANTHROPIC_API_KEY on the server)")
        runners[Mode.TEST].store.set_ai_cap(body.monthly_cap)
        journal.add_event("test", "settings", f"AI monthly spending cap set to ${body.monthly_cap:,.2f}")
        return ai_spending()

    @app.get("/api/{mode}/lab", **secured)
    def lab(mode: str):
        """The strategy lab: rule changes in use, one waiting for your OK (Live), recent experiments."""
        r = runner(mode)
        test = runners[Mode.TEST]
        if test.lab is None:
            return {"enabled": False, "changes": [], "proposed": None, "experiments": [],
                    "every_days": 7, "limits_note": "", "running": False}
        running = bool(test._lab_thread and test._lab_thread.is_alive())
        return test.lab.report(r.mode.value) | {"running": running}

    @app.post("/api/live/lab", **secured)
    def decide_lab(body: LabDecision):
        test, live = runners[Mode.TEST], runners[Mode.LIVE]
        if test.lab is None:
            raise HTTPException(503, "The strategy lab isn't running on this server")
        if not live.lock.acquire(timeout=10):
            raise HTTPException(409, "The bot is in the middle of a check. Try again in a minute.")
        try:
            test.lab.approve_live() if body.decision == "approve" else test.lab.reject_live()
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            live.lock.release()
        return lab("live")

    @app.post("/api/{mode}/lab/undo", **secured)
    def undo_lab(mode: str):
        r, test = runner(mode), runners[Mode.TEST]
        if test.lab is None:
            raise HTTPException(503, "The strategy lab isn't running on this server")
        if not r.lock.acquire(timeout=10):
            raise HTTPException(409, "The bot is in the middle of a check. Try again in a minute.")
        try:
            test.lab.undo(r.mode.value)
        finally:
            r.lock.release()
        return lab(mode)

    @app.post("/api/test/lab/run", **secured)
    def run_lab():
        """Run this week's experiment now (in the background; check back in a few minutes)."""
        test = runners[Mode.TEST]
        if test.lab is None:
            raise HTTPException(503, "The strategy lab isn't running on this server")
        if not test.lab_if_due(force=True):
            raise HTTPException(409, "An experiment is already running")
        return lab("test")

    @app.get("/api/{mode}/approvals", **secured)
    def approvals(mode: str):
        return journal.pending_approvals(runner(mode).mode.value)

    @app.post("/api/{mode}/approvals/{approval_id}", **secured)
    def decide(mode: str, approval_id: int, body: Decision):
        r = runner(mode)
        if body.decision == "approve":
            return r.approve(approval_id)
        r.reject(approval_id)
        return {"rejected": True}

    @app.get("/api/{mode}/settings", **secured)
    def get_settings(mode: str):
        s = runner(mode).settings
        return {k: getattr(s, k) for k in EDITABLE} | {
            "max_position_pct": s.max_position_pct, "max_open_positions": s.max_open_positions,
            "daily_loss_pct": s.daily_loss_pct, "cash_reserve_pct": s.cash_reserve_pct,
        }

    @app.put("/api/{mode}/settings", **secured)
    def put_settings(mode: str, body: SettingsUpdate):
        r = runner(mode)
        changes = {}
        if r.mode is Mode.LIVE and body.clear_budget:
            raise HTTPException(400, "Live always needs a budget cap. Set an amount instead.")
        if r.mode is Mode.LIVE and r.store.starter_active():
            cap = HARD_LIMITS.live_starter_cap
            if body.budget is not None and body.budget > cap:
                raise HTTPException(400, f"For the first {HARD_LIMITS.live_starter_days} days of Live, "
                                         f"the cap can't be over ${cap:,.0f}")
        if body.clear_budget:
            changes["budget"] = None
        elif body.budget is not None:
            changes["budget"] = body.budget
        if body.approve_mode is not None:
            changes["approve_mode"] = body.approve_mode
        for key in ("after_hours_buys", "quick_reviews"):
            if getattr(body, key) is not None:
                changes[key] = getattr(body, key)
        for key in ("always_watch", "never_buy"):
            value = getattr(body, key)
            if value is not None:
                changes[key] = sorted({v.strip().upper() for v in value if v.strip()})
        r.store.update(r.mode, changes)
        journal.add_event(r.mode.value, "settings", f"Settings changed: {', '.join(changes) or 'nothing'}")
        return get_settings(mode)

    def readiness_report() -> dict:
        test, live = runners[Mode.TEST], runners[Mode.LIVE]
        daily = journal.daily_equity(Mode.TEST.value)
        spy: list[tuple[str, float]] = []
        if daily and test.broker is not None:
            days = (datetime.now(timezone.utc).date()
                    - datetime.fromisoformat(daily[0][0]).date()).days + 10
            try:
                bars_ = test.broker.get_daily_bars(["SPY"], max(days, 5)).get("SPY", [])
                spy = [(b.time.date().isoformat(), b.close) for b in bars_]
            except Exception as exc:
                log.warning("Couldn't fetch SPY for readiness: %s", exc)
        results = test_results(daily, test.settings.budget, spy)
        report = live_checks(results, live_enabled=live_enabled, live_connected=live.broker is not None,
                             live_cap=live.settings.budget, starter_active=live.store.starter_active())
        report["skip_results_allowed"] = skip_results
        return report

    @app.get("/api/readiness", **secured)
    def readiness():
        return readiness_report()

    @app.post("/api/{mode}/start", **secured)
    def start(mode: str):
        r = runner(mode)
        if r.mode is Mode.LIVE:
            if not live_enabled:
                raise HTTPException(403, "Live trading is off on the server (AUTOTRADER_LIVE_ENABLED)")
            rep_ = readiness_report()
            allowed = rep_["ready"] or (skip_results and rep_["ready_except_results"])
            if not allowed:
                missing = [c["name"] for c in rep_["checks"] if not c["ok"]]
                raise HTTPException(403, "Not ready for real money yet: " + "; ".join(missing))
            if not rep_["ready"]:
                journal.add_event("live", "settings",
                                  "Started before the Test results goal was met (skipped by the server owner)")
        broker_of(r)
        if r.mode is Mode.LIVE and not r.store.get(Mode.LIVE).get("first_started"):
            r.store.update(Mode.LIVE, {"first_started": datetime.now(timezone.utc).isoformat()})
        r.halted_by_you.clear()
        r.failures = 0
        r.store.update(r.mode, {"running": True, "buying_paused": None, "safety_peak": None})
        journal.add_event(r.mode.value, "start", "Bot started")
        r.step("Starting up", log_it=False)
        if not r.busy:
            r._wake.set()  # start now instead of after the current pause
        return {"running": True}

    @app.post("/api/{mode}/stop", **secured)
    def stop(mode: str):
        r = runner(mode)
        r.halted_by_you.set()
        r.store.update(r.mode, {"running": False})
        journal.add_event(r.mode.value, "stop", "Bot stopped")
        r.step("Stopped. Press Start bot to begin.", log_it=False)
        return {"running": False}

    @app.post("/api/{mode}/kill", **secured)
    def kill(mode: str, body: KillRequest):
        return runner(mode).kill(body.sell_all)

    @app.get("/api/{mode}/report/weekly", **secured)
    def weekly(mode: str):
        m = runner(mode).mode.value
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        week_trades = [t for t in journal.trades(m) if t["ts"] >= since]
        curve = [e for e in journal.equity_history(m) if e["ts"] >= since]
        change = (curve[-1]["equity"] / curve[0]["equity"] - 1) if len(curve) > 1 and curve[0]["equity"] else 0.0
        views = [v for v in journal.views(m) if v["day"] >= since[:10]]
        return {
            "since": since[:10], "change_pct": change,
            "buys": sum(t["side"] == "buy" for t in week_trades),
            "sells": sum(t["side"] == "sell" for t in week_trades),
            "analyst_reviews": len(views),
            "analyst_approved": sum(v["action"] == "buy" for v in views),
        }

    @app.get("/api/bars/{symbol}", **secured)
    def bars(symbol: str, days: int = 180, mode: str = "test"):
        r = runner(mode)
        data = broker_of(r).get_daily_bars([symbol.upper()], min(max(days, 5), 1000))
        return [
            {"t": b.time.isoformat(), "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}
            for b in data.get(symbol.upper(), [])
        ]

    def ask_context(mode: str) -> dict:
        if ask_fn is None:
            raise HTTPException(503, "The AI analyst is off (add ANTHROPIC_API_KEY on the server)")
        r = runner(mode)
        context = {"summary": summary(mode), "recent_trades": trades(mode)[:20]}
        if r.broker is not None:
            context["positions"] = positions(mode)
        return context

    @app.post("/api/ask", **secured)
    def ask(body: AskRequest):
        context = ask_context(body.mode)
        from .analyst import AnalystUnavailable
        try:
            return {"answer": ask_fn(body.question, context)}
        except AnalystUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/ask/stream", **secured)
    def ask_streamed(body: AskRequest):
        """The answer as it's written: one JSON object per line, {"text": ...} or {"error": ...}."""
        context = ask_context(body.mode)
        from .analyst import AnalystUnavailable

        def lines():
            try:
                if ask_stream_fn is None:
                    yield json.dumps({"text": ask_fn(body.question, context)}) + "\n"
                else:
                    for piece in ask_stream_fn(body.question, context):
                        yield json.dumps({"text": piece}) + "\n"
            except AnalystUnavailable as exc:
                yield json.dumps({"error": str(exc)}) + "\n"
            except Exception as exc:  # the reply has started, so the error goes in the stream
                log.warning("Analyst chat failed: %s", exc)
                yield json.dumps({"error": "The analyst hit a problem. Try again in a minute."}) + "\n"
            yield json.dumps({"done": True}) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def make_lab(store: SettingsStore, test: BotRunner, journal: Journal) -> Lab:
    """The strategy lab, replaying the Test bot's own stock list through the backtester."""
    from .lab import HISTORY_DAYS
    from .market import MARKET
    from .universe import build_universe

    def history() -> dict:
        s = store.settings_for(Mode.TEST, with_lab=False)
        symbols = sorted(set(build_universe(s.always_watch, s.never_buy)) | {MARKET})
        return test.broker.get_daily_bars(symbols, HISTORY_DAYS)

    return Lab(store, history, lambda: store.settings_for(Mode.TEST, with_lab=False),
               events=lambda mode, message: journal.add_event(mode, "learning", message))


def main() -> None:
    import uvicorn

    from .broker.alpaca_broker import AlpacaBroker
    from .signals import EarningsCalendar, NewsDesk, QuiverClient, Screener, SecClient, SignalBook

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("AUTOTRADER_APP_TOKEN", "")
    live_enabled = os.environ.get("AUTOTRADER_LIVE_ENABLED", "").lower() == "true"
    base = load_settings()
    journal = Journal(base.journal_path)
    store = SettingsStore(Path(base.journal_path).with_name("app_settings.json"))

    analyst, ask_fn, ask_stream_fn, lessons_fn, spend = None, None, None, None, None
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        from .analyst import Analyst, ask, ask_stream
        from .learning import claude_lessons

        from .spend import DEFAULT_MONTHLY_CAP, MeteredClient, SpendTracker

        # Every Claude call is counted against the monthly cap you set in the app.
        spend = SpendTracker(journal, lambda: store.ai_cap(DEFAULT_MONTHLY_CAP))
        client = MeteredClient(anthropic.Anthropic(), spend)
        analyst = Analyst(client)
        ask_fn = lambda q, ctx: ask(client, q, ctx)  # noqa: E731
        ask_stream_fn = lambda q, ctx: ask_stream(client, q, ctx)  # noqa: E731
        lessons_fn = claude_lessons(client)
    learner = Learner(journal, lessons_fn)

    sec = SecClient()
    earnings = EarningsCalendar()
    screener = Screener() if os.environ.get("AUTOTRADER_SCREENER", "true").lower() != "false" else None
    news = NewsDesk()
    skip_results = os.environ.get("AUTOTRADER_SKIP_READINESS", "").lower() == "true"
    runners = {}
    for mode in (Mode.TEST, Mode.LIVE):
        r = BotRunner(mode, store, journal, threading.RLock(), AlpacaBroker,
                      live_enabled=live_enabled)
        bars_fetch = r.broker.get_daily_bars if r.broker else None
        r.engine_kwargs = {
            "filings_check": sec.penny_filings_check,
            "signals": SignalBook(sec=sec, quiver=QuiverClient(), bars_fetch=bars_fetch),
            "analyst": analyst,
            "earnings": earnings.reports_soon,
            "screener": screener.symbols if screener else None,
            "news": news.check,
            "memory": (lambda m=mode.value: learner.memory(m)),
        }
        if mode is Mode.TEST:
            r.learner = learner
            if r.broker is not None:  # prices for grading the analyst's month-old calls
                learner.prices_fn = r.broker.get_daily_bars
            if r.broker is not None:
                r.lab = make_lab(store, r, journal)
        if mode is Mode.LIVE and not live_enabled:
            store.update(mode, {"running": False})
        r.start_thread()
        runners[mode] = r

    app = create_app(runners, journal, token, ask_fn, live_enabled, skip_results, learner, spend,
                     ask_stream_fn)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
