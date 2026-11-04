"""Strategy lab: once a week, test one small rule change before trusting it.

Each week the lab tries one idea from a short, fixed list of tweaks (a wider
or tighter safety stop, a higher minimum trend score). Every tweak stays
inside the hard limits, and none touches position sizes, budgets or penny
rules. The idea has to pass every gate to be kept:

1. **Tuning stretch.** Replayed on the older two thirds of the price history,
   it must do better than the current rules (higher Sharpe ratio, a drop no
   more than 1 point worse).
2. **Unseen stretch.** Replayed on the most recent third, which played no
   part in choosing it, it must beat the current rules again: Sharpe higher
   by at least 0.10, a higher return, and a drop no more than 1 point worse.
3. **Evidence.** It must turn at least one of the current rules' losing
   trades in the unseen stretch into a smaller loss or no trade at all, and
   the result names them.

A tweak that passes goes into Test straight away. Live gets it only when you
approve it in the app, and either can be undone back to the standard rules.
Rejected ideas are recorded too, so the same idea isn't retried for a month.

(The discipline here follows MetaHarness and Dream Machine: one testable
change at a time, judged on data it wasn't tuned on, kept only with evidence.)
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from .backtest import BacktestResult, run_backtest
from .config import HARD_LIMITS, Settings
from .journal import Journal
from .learning import outcomes
from .strategy import MIN_HISTORY

log = logging.getLogger("autotrader.lab")

RUN_EVERY = timedelta(days=7)
RETRY_AFTER = timedelta(hours=6)
RETRY_IDEA_AFTER = timedelta(days=30)
HISTORY_DAYS = MIN_HISTORY + 3 * 252  # a year to warm up plus three years to test on
MIN_SHARPE_GAIN = 0.10
MAX_EXTRA_DROP = 0.01

# The settings the lab may change, with the values it may try. Kept inside the hard limits.
KNOBS: dict[str, tuple[float, ...]] = {
    "atr_stop_multiple": (1.5, 2.0, 2.5, 3.0),
    "min_score": (0.0, 0.2, 0.35, 0.5),
}
KNOB_LABELS = {"atr_stop_multiple": "Safety stop distance (x average daily move)",
               "min_score": "Lowest trend score it will buy"}


def clamp_rules(rules: dict) -> dict:
    """Only known knobs, only values from their list (so nothing can drift outside the limits)."""
    out = {}
    for k, v in (rules or {}).items():
        if k in KNOBS:
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v in KNOBS[k]:
                out[k] = v
    return out


def apply_rules(settings: Settings, rules: dict) -> Settings:
    return replace(settings, **clamp_rules(rules)).clamped()


@dataclass
class Idea:
    knob: str
    value: float

    @property
    def key(self) -> str:
        return f"{self.knob}={self.value:g}"

    def describe(self, current: float) -> str:
        label = KNOB_LABELS[self.knob]
        return f"{label}: {current:g} → {self.value:g}"


def next_idea(rules: dict, base: Settings, tried: dict[str, str], now: datetime) -> Idea | None:
    """The next untried neighbour of the current rules (one step on one knob)."""
    current = apply_rules(base, rules)
    for knob, values in KNOBS.items():
        here = getattr(current, knob)
        i = values.index(here) if here in values else 0
        for j in (i + 1, i - 1):
            if 0 <= j < len(values):
                idea = Idea(knob, values[j])
                last = tried.get(idea.key)
                if last is None or now - datetime.fromisoformat(last) >= RETRY_IDEA_AFTER:
                    return idea
    return None


def split_history(history: dict, tune_share: float = 2 / 3):
    """(tuning stretch, unseen stretch), each with a year of warm-up bars in front."""
    dates = sorted({b.time for bars in history.values() for b in bars})
    if len(dates) < MIN_HISTORY + 120:
        raise ValueError("not enough price history for the lab")
    test_days = dates[MIN_HISTORY:]
    cut = test_days[int(len(test_days) * tune_share)]
    tune = {s: [b for b in bars if b.time < cut] for s, bars in history.items()}
    # The unseen stretch needs its own warm-up: the year of bars just before it.
    start = dates[max(0, dates.index(cut) - MIN_HISTORY)]
    unseen = {s: [b for b in bars if b.time >= start] for s, bars in history.items()}
    return tune, unseen


def _summary(r: BacktestResult) -> dict:
    return {"return": round(r.overall.total_return, 4), "sharpe": round(r.overall.sharpe, 2),
            "max_drop": round(r.overall.max_drawdown, 4), "trades": r.trades,
            "win_rate": None if r.win_rate is None else round(r.win_rate, 3)}


def _trades(journal: Journal) -> dict[str, float]:
    """Every finished trade in a replay, by "SYMBOL bought at $PRICE", with its result.

    Both replays see the same prices, so the same entry has the same buy price in each.
    (Replayed trades are stamped with today's date, so the date can't be the key.)
    """
    out: dict[str, float] = {}
    for o in outcomes(journal, "test"):
        key = f"{o.symbol} bought at ${o.buy_price:,.2f}"
        out[key if key not in out else f"{key} (again)"] = o.ret
    return out


Backtest = Callable[..., BacktestResult]


class Lab:
    def __init__(self, store, history_fn: Callable[[], dict] | None,
                 base: Callable[[], Settings], backtest: Backtest = run_backtest,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 events: Callable[[str, str], None] = lambda mode, msg: None) -> None:
        self.store = store  # SettingsStore: per-mode "lab_rules", "lab_proposed", "lab_log"
        self.history_fn = history_fn
        self.base = base  # the standard rules (Test settings without lab changes)
        self.backtest = backtest
        self.now = now
        self.events = events
        self._lock = threading.Lock()
        self._last_try: datetime | None = None

    # ----- state -----
    def rules(self, mode: str) -> dict:
        return clamp_rules(self._get(mode).get("lab_rules", {}))

    def _get(self, mode: str) -> dict:
        from .config import Mode
        return self.store.get(Mode(mode))

    def _update(self, mode: str, changes: dict) -> None:
        from .config import Mode
        self.store.update(Mode(mode), changes)

    def log(self) -> list[dict]:
        return list(self._get("test").get("lab_log", []))

    def due(self) -> bool:
        now = self.now()
        if self.history_fn is None or (self._last_try and now - self._last_try < RETRY_AFTER):
            return False
        last = self.log()
        return not last or now - datetime.fromisoformat(last[-1]["ts"]) >= RUN_EVERY

    # ----- the weekly experiment -----
    def run(self) -> dict | None:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("The strategy lab is already running")
        try:
            return self._run()
        finally:
            self._lock.release()

    def _run(self) -> dict | None:
        now = self._last_try = self.now()
        rules = self.rules("test")
        tried = {e["idea"]: e["ts"] for e in self.log() if not e.get("kept")}
        idea = next_idea(rules, self.base(), tried, now)
        if idea is None:
            return None
        current = apply_rules(self.base(), rules)
        candidate = apply_rules(self.base(), {**rules, idea.knob: idea.value})
        tune, unseen = split_history(self.history_fn())

        entry = {"ts": now.isoformat(timespec="seconds"), "idea": idea.key,
                 "change": idea.describe(getattr(current, idea.knob)), "kept": False}
        a, b = self.backtest(tune, current), self.backtest(tune, candidate)
        entry["tuning"] = {"current": _summary(a), "new": _summary(b)}
        if not (b.overall.sharpe > a.overall.sharpe
                and b.overall.max_drawdown <= a.overall.max_drawdown + MAX_EXTRA_DROP):
            entry["verdict"] = "Not better on the older prices, so it wasn't tried on newer ones"
            return self._record(entry)

        ja, jb = Journal(), Journal()
        a, b = self.backtest(unseen, current, journal=ja), self.backtest(unseen, candidate, journal=jb)
        entry["unseen"] = {"current": _summary(a), "new": _summary(b)}
        if not (b.overall.sharpe >= a.overall.sharpe + MIN_SHARPE_GAIN
                and b.overall.total_return > a.overall.total_return
                and b.overall.max_drawdown <= a.overall.max_drawdown + MAX_EXTRA_DROP):
            entry["verdict"] = "Better on older prices but not on the newer, unseen ones"
            return self._record(entry)

        before, after = _trades(ja), _trades(jb)
        losers = {k: r for k, r in before.items() if r < 0}
        fixed = sorted((k for k, r in losers.items() if k not in after or after[k] > r),
                       key=lambda k: losers[k])[:5]
        if not fixed:
            entry["verdict"] = "Better overall, but it didn't fix any of the current rules' losing trades"
            return self._record(entry)
        entry["evidence"] = [f"{k}: {losers[k]:+.1%} → "
                             + (f"{after[k]:+.1%}" if k in after else "not bought") for k in fixed]
        entry["kept"] = True
        entry["verdict"] = "Passed every check; Test uses it now"
        new_rules = {**rules, idea.knob: idea.value}
        self._update("test", {"lab_rules": new_rules})
        # Live is offered just this one change on top of its own rules, never a bundle that
        # sneaks back a change you turned down before.
        live_rules = {**self.rules("live"), idea.knob: idea.value}
        self._update("live", {"lab_proposed": {"rules": live_rules, "entry": entry}})
        self.events("test", f"Strategy lab kept a rule change: {entry['change']}")
        self.events("live", f"The strategy lab found a better rule in Test ({entry['change']}). "
                            "Review it in the app before Live uses it.")
        return self._record(entry)

    def _record(self, entry: dict) -> dict:
        log = (self.log() + [entry])[-26:]  # about six months of weekly experiments
        self._update("test", {"lab_log": log})
        if not entry["kept"]:
            self.events("test", f"Strategy lab tried {entry['change']}: {entry['verdict'].lower()}")
        return entry

    # ----- your decisions -----
    def approve_live(self) -> dict:
        proposed = self._get("live").get("lab_proposed")
        if not proposed:
            raise LookupError("Nothing is waiting for approval")
        self._update("live", {"lab_rules": clamp_rules(proposed["rules"]), "lab_proposed": None})
        self.events("live", "You approved the strategy lab's rule change; Live uses it now")
        return self.rules("live")

    def reject_live(self) -> None:
        if not self._get("live").get("lab_proposed"):
            raise LookupError("Nothing is waiting for approval")
        self._update("live", {"lab_proposed": None})
        self.events("live", "You turned down the strategy lab's rule change")

    def undo(self, mode: str) -> None:
        """Back to the standard rules."""
        self._update(mode, {"lab_rules": {}})
        if mode == "test":
            self._update("live", {"lab_proposed": None})
        self.events(mode, "Strategy lab changes undone; back to the standard rules")

    def report(self, mode: str) -> dict:
        base = self.base()
        rules = self.rules(mode)
        proposed = self._get("live").get("lab_proposed") if mode == "live" else None

        def described(r: dict) -> list[dict]:
            return [{"setting": k, "label": KNOB_LABELS[k], "standard": getattr(base, k), "now": v}
                    for k, v in clamp_rules(r).items() if v != getattr(base, k)]

        return {
            "changes": described(rules),
            "proposed": ({"changes": described(proposed["rules"]), "entry": proposed["entry"]}
                         if proposed else None),
            "experiments": list(reversed(self.log()))[:8],
            "every_days": RUN_EVERY.days,
            "enabled": self.history_fn is not None,
            "limits_note": f"Stops stay within {HARD_LIMITS.max_stop_loss_pct:.0%} of the buy price; "
                           "sizes, budgets and penny rules never change.",
        }
