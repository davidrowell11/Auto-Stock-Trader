"""The bot server: runs the Test and Live bots and serves the app's API.

    python -m autotrader.server            # listens on 0.0.0.0:8000

Every request (except /health) needs ``Authorization: Bearer <token>``, where
the token is the ``AUTOTRADER_APP_TOKEN`` secret. You type the same token
into the app once, when you connect it.

Each bot has its own lock, so a trading cycle and an approval never run at the
same time. The kill switch doesn't wait for the lock: it stops the bot at once
(the cycle checks before every order) and sells straight away.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import penny, strategy
from .broker.base import Broker
from .config import HARD_LIMITS, Mode, Settings, load_settings
from .engine import Engine
from .readiness import live_checks, test_results
from .journal import Journal, StopState
from .models import OrderRequest, Side
from .risk import RiskManager
from .universe import sector_of

log = logging.getLogger("autotrader.server")

EDITABLE = ("budget", "approve_mode", "always_watch", "never_buy")


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
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(self.path)  # atomic, so a crash can't leave a half-written file

    def starter_active(self) -> bool:
        """True until Live has been running for the starter period."""
        first = self.get(Mode.LIVE).get("first_started")
        if not first:
            return True
        age = datetime.now(timezone.utc) - datetime.fromisoformat(first)
        return age < timedelta(days=HARD_LIMITS.live_starter_days)

    def settings_for(self, mode: Mode) -> Settings:
        saved = self.get(mode)
        overrides = {k: (tuple(v) if isinstance(v, list) else v)
                     for k, v in saved.items() if k in EDITABLE}
        if mode is Mode.LIVE and self.starter_active():
            # Start small: never more than the starter cap of real money, whatever the file says.
            cap = HARD_LIMITS.live_starter_cap
            budget = overrides.get("budget")
            overrides["budget"] = cap if budget is None else min(budget, cap)
        return load_settings(mode=mode, **overrides)


class BotRunner:
    """One bot (Test or Live) running its cycle in a background thread."""

    APPROVAL_MAX_AGE = timedelta(hours=24)

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
        try:
            self.broker: Broker | None = broker_factory(mode)
        except Exception as exc:  # e.g. Live keys not added yet
            self.broker = None
            self.error = str(exc)
        self._stop = threading.Event()
        self.halted_by_you = threading.Event()  # set by Stop or the kill switch, cleared by Start
        self._thread: threading.Thread | None = None

    @property
    def settings(self) -> Settings:
        return self.store.settings_for(self.mode)

    @property
    def running(self) -> bool:
        return bool(self.store.get(self.mode).get("running", False))

    def engine(self) -> Engine:
        return Engine(self.broker, self.settings, self.journal,
                      keep_going=lambda: not self.halted_by_you.is_set(),
                      pause_buying=self.store.get(self.mode).get("buying_paused"),
                      **self.engine_kwargs)

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

    def run_once(self) -> None:
        if self.broker is None:
            return
        with self.lock:
            report = self.engine().run_cycle()
            self.check_live_safety(self.broker.get_account().equity)
        self.halted = report.halted
        self.mood = report.mood
        self.mood_level = report.mood_level
        self.last_cycle = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.running and self.broker is not None and self.broker.is_market_open():
                    self.run_once()
                    self.error = None
                    self.failures = 0
            except Exception as exc:  # keep the thread alive; show the problem in the app
                log.exception("%s cycle failed", self.mode.value)
                self.error = str(exc)
                self.failures += 1
                if self.mode is Mode.LIVE and self.failures >= HARD_LIMITS.live_auto_stop_errors:
                    self.pause_buying(f"{self.failures} cycles failed in a row ({exc})")
            self._stop.wait(self.settings.poll_seconds)

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
        sig, problems = engine.recheck_buy(a["symbol"])
        if sig is None or problems:
            self.journal.set_approval_status(approval_id, "expired")
            raise HTTPException(409, f"Can't buy now: {'; '.join(problems)}")
        owned = self.journal.owned_symbols(self.mode.value)
        account = self.broker.get_account()
        positions = [p for p in self.broker.get_positions() if p.symbol in owned]
        for p in positions:
            p.sector = sector_of(p.symbol)
        price = sig.price
        s = engine.s
        is_penny = penny.is_penny(price, s)
        risk = RiskManager(s, account, positions, self.journal.peak_equity(self.mode.value),
                           mood_multiplier=engine.mood.size_multiplier)
        asset = engine.assets.get(a["symbol"])
        fractional = bool(getattr(asset, "fractionable", False)) and not is_penny
        size = risk.size_buy(a["symbol"], price, sector_of(a["symbol"]), is_penny, fractional)
        qty = min(float(a["qty"]), size.qty)  # re-checked: prices and the account may have moved
        if not fractional:
            qty = float(int(qty))
        if qty <= 0:
            self.journal.set_approval_status(approval_id, "expired")
            raise HTTPException(409, f"Can't buy now: {size.note}")
        limit = penny.limit_price_for_buy(price) if is_penny else None
        order_id = self.broker.submit_order(OrderRequest(a["symbol"], Side.BUY, qty, limit, "Approved in app"))
        self.journal.record_trade(self.mode.value, a["symbol"], "buy", qty, price, order_id,
                                  [*a["reasons"], "Approved by you in the app"])
        stop = strategy.initial_stop(price, sig.atr, strategy.stop_multiple(price, s))
        self.journal.save_stop(self.mode.value, a["symbol"], StopState(price, stop, price))
        self.journal.set_approval_status(approval_id, "approved")
        self.journal.add_event(self.mode.value, "buy", f"Bought {qty:g} {a['symbol']} at about ${price:.2f} (you approved)")
        return {"bought": qty, "price": price, "stop": round(stop, 2)}

    def reject(self, approval_id: int) -> None:
        with self.lock:
            a = self.journal.get_approval(approval_id)
            if a is None or a["mode"] != self.mode.value:
                raise HTTPException(404, "No such approval")
            self.journal.set_approval_status(approval_id, "rejected")

    def kill(self, sell_all: bool) -> dict:
        """Kill switch: stop the bot now, and optionally sell everything it bought.

        It doesn't wait for a running cycle: the cycle checks halted_by_you
        before every order and stops. Your own holdings are never touched.
        """
        self.halted_by_you.set()
        self.store.update(self.mode, {"running": False})
        cancelled = self.journal.cancel_pending(self.mode.value)
        sold, failed = [], []
        if sell_all and self.broker is not None:
            try:
                self.broker.cancel_open_orders()  # frees shares held by unfilled orders
            except Exception as exc:
                log.error("Kill switch couldn't cancel open orders: %s", exc)
            owned = self.journal.owned_symbols(self.mode.value)
            for p in self.broker.get_positions():
                if p.symbol not in owned:
                    continue
                limit = None
                if p.current_price < self.settings.penny_price:
                    limit = round(p.current_price * 0.98, 4 if p.current_price < 1 else 2)
                try:
                    self.broker.submit_order(OrderRequest(p.symbol, Side.SELL, p.qty, limit, "Kill switch"))
                except Exception as exc:
                    log.error("Kill switch sell %s failed: %s", p.symbol, exc)
                    failed.append(p.symbol)
                    continue
                self.journal.record_trade(self.mode.value, p.symbol, "sell", p.qty, p.current_price,
                                          None, ["Kill switch"])
                self.journal.clear_stop(self.mode.value, p.symbol)
                sold.append(p.symbol)
        self.journal.add_event(self.mode.value, "kill",
                               "Kill switch: bot stopped" + (f", sold {', '.join(sold)}" if sold else ""))
        if failed:
            self.journal.add_event(self.mode.value, "kill", f"Kill switch couldn't sell {', '.join(failed)}")
        return {"stopped": True, "sold": sold, "failed": failed, "cancelled_approvals": cancelled}


# ----- request bodies -----
class SettingsUpdate(BaseModel):
    budget: float | None = Field(default=None, ge=0)
    clear_budget: bool = False
    approve_mode: bool | None = None
    always_watch: list[str] | None = None
    never_buy: list[str] | None = None


class Decision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")


class KillRequest(BaseModel):
    sell_all: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: str = "test"


def create_app(runners: dict[Mode, BotRunner], journal: Journal, token: str,
               ask_fn: Callable[[str, dict], str] | None = None,
               live_enabled: bool = False, skip_results: bool = False) -> FastAPI:
    if not token:
        raise RuntimeError("Set AUTOTRADER_APP_TOKEN before starting the server")
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
            "mood": r.mood, "mood_level": r.mood_level, "buying_paused": r.store.get(r.mode).get("buying_paused"),
            "live_enabled": live_enabled,
            "pending_approvals": len(journal.pending_approvals(r.mode.value)),
        }
        if r.broker is not None:
            acct = r.broker.get_account()
            owned = journal.owned_symbols(r.mode.value)
            positions = [p for p in r.broker.get_positions() if p.symbol in owned]
            market_open = r.broker.is_market_open()
            out.update({
                "equity": acct.equity, "cash": acct.cash,
                "today_change": acct.equity - acct.last_equity,
                "today_change_pct": (acct.equity / acct.last_equity - 1) if acct.last_equity else 0.0,
                "invested": sum(p.market_value for p in positions),
                "positions": len(positions), "market_open": market_open,
            })
        return out

    @app.get("/api/{mode}/positions", **secured)
    def positions(mode: str):
        r = runner(mode)
        owned = journal.owned_symbols(r.mode.value)
        held = [p for p in broker_of(r).get_positions() if p.symbol in owned]
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
        return {"views": views[:200], "total": len(views), "approved": len(buys)}

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
        if r.mode is Mode.LIVE and r.store.starter_active():
            cap = HARD_LIMITS.live_starter_cap
            if body.clear_budget or (body.budget is not None and body.budget > cap):
                raise HTTPException(400, f"For the first {HARD_LIMITS.live_starter_days} days of Live, "
                                         f"the cap can't be over ${cap:,.0f}")
        if body.clear_budget:
            changes["budget"] = None
        elif body.budget is not None:
            changes["budget"] = body.budget
        if body.approve_mode is not None:
            changes["approve_mode"] = body.approve_mode
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
        return {"running": True}

    @app.post("/api/{mode}/stop", **secured)
    def stop(mode: str):
        r = runner(mode)
        r.halted_by_you.set()
        r.store.update(r.mode, {"running": False})
        journal.add_event(r.mode.value, "stop", "Bot stopped")
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

    @app.post("/api/ask", **secured)
    def ask(body: AskRequest):
        if ask_fn is None:
            raise HTTPException(503, "The AI analyst is off (add ANTHROPIC_API_KEY on the server)")
        r = runner(body.mode)
        context = {"summary": summary(body.mode), "recent_trades": trades(body.mode)[:20]}
        if r.broker is not None:
            context["positions"] = positions(body.mode)
        from .analyst import AnalystUnavailable
        try:
            return {"answer": ask_fn(body.question, context)}
        except AnalystUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    return app


def main() -> None:
    import uvicorn

    from .broker.alpaca_broker import AlpacaBroker
    from .signals import EarningsCalendar, QuiverClient, SecClient, SignalBook

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("AUTOTRADER_APP_TOKEN", "")
    live_enabled = os.environ.get("AUTOTRADER_LIVE_ENABLED", "").lower() == "true"
    base = load_settings()
    journal = Journal(base.journal_path)
    store = SettingsStore(Path(base.journal_path).with_name("app_settings.json"))

    analyst, ask_fn = None, None
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        from .analyst import Analyst, ask

        client = anthropic.Anthropic()
        analyst = Analyst(client)
        ask_fn = lambda q, ctx: ask(client, q, ctx)  # noqa: E731

    sec = SecClient()
    earnings = EarningsCalendar()
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
        }
        if mode is Mode.LIVE and not live_enabled:
            store.update(mode, {"running": False})
        r.start_thread()
        runners[mode] = r

    app = create_app(runners, journal, token, ask_fn, live_enabled, skip_results)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
