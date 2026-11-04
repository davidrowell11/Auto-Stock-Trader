"""The bot's memory: it learns from how its own trades turned out.

Every buy records what pointed the bot at the stock (its trend score, the
daily screener, insider buying, a Congress member's purchase, the AI analyst,
penny-stock rules, after-hours buying). When the bot sells, that trade's
result joins its memory. Once a week the bot:

1. Scores each source by how its trades went. A source that keeps leading to
   winners gets a little more weight when ranking buys, and one that keeps
   losing gets a little less, always between 0.7 and 1.3. With fewer than
   five finished trades a source stays at 1.0.
2. Asks Claude for a few short lessons from the finished trades. The lessons
   go into every AI analyst review from then on.

What it learns can only change which stocks rank first and what the analyst
watches out for. The analyst can only veto a buy, and nothing here touches
stop-losses, position sizes, budgets or penny-stock limits.

Test learns by itself. Live only changes when you approve the new memory in
the app.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .journal import Journal

log = logging.getLogger("autotrader.learning")

LEARN_EVERY = timedelta(days=7)
RETRY_AFTER = timedelta(hours=6)  # after a failed or skipped attempt
MIN_TRADES_FOR_LESSONS = 3
MIN_TRADES_FOR_WEIGHT = 5
WEIGHT_MIN, WEIGHT_MAX = 0.7, 1.3
MAX_LESSONS = 6
MAX_OUTCOMES_SHOWN = 60

# Sources whose weight the bot adjusts, and how each appears in the app.
WEIGHTED = {"insider": "Insider buying", "congress": "Congress members' buys",
            "screener": "Today's most active and top gainers"}
LABELS = {"trend": "Trend rules", **WEIGHTED, "analyst": "AI analyst approval",
          "penny": "Penny stocks", "after_hours": "Bought outside regular hours"}
NOT_THE_BOTS_CALL = {"Kill switch"}  # sells you asked for don't count as the bot's results


@dataclass
class Outcome:
    mode: str
    symbol: str
    bought: str  # ISO time
    sold: str
    buy_price: float
    sell_price: float
    sources: list[str]
    sell_reason: str

    @property
    def ret(self) -> float:
        return self.sell_price / self.buy_price - 1 if self.buy_price else 0.0

    @property
    def days(self) -> int:
        return max(0, (datetime.fromisoformat(self.sold) - datetime.fromisoformat(self.bought)).days)

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "mode": self.mode, "bought": self.bought[:10],
                "sold": self.sold[:10], "days_held": self.days, "return": round(self.ret, 4),
                "sources": self.sources, "sell_reason": self.sell_reason}


@dataclass
class Memory:
    """What the engine uses: source weights for ranking and lessons for the analyst."""
    weights: dict[str, float] = field(default_factory=dict)
    lessons: list[str] = field(default_factory=list)

    def weight(self, source: str) -> float:
        return float(self.weights.get(source, 1.0))

    @classmethod
    def from_record(cls, record: dict | None) -> "Memory":
        if not record:
            return cls()
        weights = {k: min(WEIGHT_MAX, max(WEIGHT_MIN, float(v)))
                   for k, v in record.get("weights", {}).items() if k in WEIGHTED}
        return cls(weights, [str(x) for x in record.get("lessons", [])][:MAX_LESSONS])


def outcomes(journal: Journal, mode: str) -> list[Outcome]:
    """Each finished trade: a buy paired with the bot's next sell of that stock."""
    out: list[Outcome] = []
    open_buys: dict[str, dict] = {}
    for t in journal.trades(mode):
        if t["side"] == "buy":
            open_buys[t["symbol"]] = t  # the latest buy (an unfilled one is placed again)
            continue
        buy = open_buys.pop(t["symbol"], None)
        if buy is None:
            continue  # a re-priced sell, or a stock bought before the bot kept records
        reason = t["reasons"][0] if t["reasons"] else ""
        if reason in NOT_THE_BOTS_CALL:
            continue
        out.append(Outcome(mode, t["symbol"], buy["ts"], t["ts"], float(buy["price"]),
                           float(t["price"]), list(buy["sources"]), reason))
    return out


def source_stats(results: list[Outcome]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for o in results:
        for s in set(o.sources):
            st = stats.setdefault(s, {"trades": 0, "wins": 0, "total_return": 0.0})
            st["trades"] += 1
            st["wins"] += o.ret > 0
            st["total_return"] += o.ret
    for st in stats.values():
        st["win_rate"] = round(st["wins"] / st["trades"], 3)
        st["avg_return"] = round(st.pop("total_return") / st["trades"], 4)
    return stats


def weights_from(stats: dict[str, dict]) -> dict[str, float]:
    """1.0 means no change. Moves toward the source's record, more so the more trades it has."""
    out = {}
    for source in WEIGHTED:
        st = stats.get(source)
        if not st or st["trades"] < MIN_TRADES_FOR_WEIGHT:
            out[source] = 1.0
            continue
        n = st["trades"]
        confidence = n / (n + 10)  # 5 trades: a third of the way; 40 trades: most of it
        # Win rate against a coin flip, plus the average result (10% average ~ one extra win in five).
        edge = (st["win_rate"] - 0.5) + 2 * st["avg_return"]
        out[source] = round(min(WEIGHT_MAX, max(WEIGHT_MIN, 1 + edge * confidence)), 2)
    return out


# Asks Claude for lessons: (finished trades, stats per source, previous lessons) -> lessons.
LessonsFn = Callable[[list[dict], dict, list[str]], list[str]]


class LearnerBusy(RuntimeError):
    pass


def _changes_nothing(record: dict) -> bool:
    return not record.get("lessons") and all(w == 1.0 for w in record.get("weights", {}).values())


def _same(a: dict | None, b: dict) -> bool:
    return a is not None and (a.get("weights"), a.get("lessons")) == (b["weights"], b["lessons"])


class Learner:
    def __init__(self, journal: Journal, lessons_fn: LessonsFn | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.journal = journal
        self.lessons_fn = lessons_fn
        self.now = now
        self._last_try: datetime | None = None
        self._lock = threading.Lock()  # the weekly review and "Learn now" never run at once

    def memory(self, mode: str) -> Memory:
        return Memory.from_record(self.journal.memory(mode))

    def due(self) -> bool:
        now = self.now()
        if self._last_try and now - self._last_try < RETRY_AFTER:
            return False
        last = self.journal.memory("test")
        if last is None:
            return True
        # Lessons that couldn't be written last time are retried sooner than a week.
        wait = RETRY_AFTER if last.get("lessons_error") else LEARN_EVERY
        return now - datetime.fromisoformat(last.get("learned_at", last["ts"])) >= wait

    def maybe_learn(self) -> dict | None:
        """Learn if a week has passed since the last time. Returns the new memory, or None."""
        if not self.due():
            return None
        return self.learn()

    def learn(self) -> dict | None:
        if not self._lock.acquire(blocking=False):
            raise LearnerBusy("The bot is already reviewing its trades. Try again in a minute.")
        try:
            return self._learn()
        finally:
            self._lock.release()

    def _learn(self) -> dict | None:
        self._last_try = self.now()
        # Both bots' finished trades teach it; Live's real-money results are just as useful.
        results = outcomes(self.journal, "test") + outcomes(self.journal, "live")
        if len(results) < MIN_TRADES_FOR_LESSONS:
            log.info("Not enough finished trades to learn from yet (%d)", len(results))
            return None
        results.sort(key=lambda o: o.sold)
        stats = source_stats(results)
        weights = weights_from(stats)
        previous = self.memory("test").lessons
        lessons, lessons_error = previous, None
        if self.lessons_fn is not None:
            shown = [o.as_dict() for o in results[-MAX_OUTCOMES_SHOWN:]]
            try:
                lessons = [str(x).strip() for x in self.lessons_fn(shown, stats, previous)
                           if str(x).strip()][:MAX_LESSONS]
            except Exception as exc:  # keep the weights and old lessons; retry in a few hours
                log.warning("Couldn't get lessons from the analyst: %s", exc)
                lessons_error = str(exc)[:300]
        wins = sum(o.ret > 0 for o in results)
        record = {
            "learned_at": self._last_try.isoformat(timespec="seconds"),
            "weights": weights, "lessons": lessons, "trades": len(results), "wins": wins,
            "lessons_error": lessons_error,
            "stats": {s: {**st, "label": LABELS.get(s, s)} for s, st in stats.items()},
        }
        self.journal.save_memory("test", "active", record)
        self.journal.add_event("test", "learning",
                               f"The bot reviewed {len(results)} finished trades and updated what it learned")
        live = self.journal.memory("live")
        stale = self.journal.memory("live", "proposed")
        turned_down = self.journal.memory("live", "rejected")
        if _same(live, record) or (live is None and _changes_nothing(record)):
            if stale is not None:  # Live already has (or doesn't need) this
                self.journal.set_memory_status(stale["id"], "replaced")
        elif _same(stale, record) or _same(turned_down, record):
            pass  # already waiting for you, or you said no to exactly this
        else:
            self.journal.save_memory("live", "proposed", record)
            self.journal.add_event("live", "learning",
                                   "The bot learned new lessons in Test. Review them in the app "
                                   "before Live uses them.")
        return record

    def approve_live(self) -> dict:
        proposed = self.journal.memory("live", "proposed")
        if proposed is None:
            raise LookupError("Nothing is waiting for approval")
        data = {k: v for k, v in proposed.items() if k not in ("id", "ts")}
        self.journal.save_memory("live", "active", data)
        self.journal.set_memory_status(proposed["id"], "approved")
        self.journal.add_event("live", "learning", "You approved the new lessons; Live uses them now")
        return data

    def reject_live(self) -> None:
        proposed = self.journal.memory("live", "proposed")
        if proposed is None:
            raise LookupError("Nothing is waiting for approval")
        self.journal.set_memory_status(proposed["id"], "rejected")
        self.journal.add_event("live", "learning", "You turned down the new lessons; Live keeps its current ones")


def report(journal: Journal, mode: str) -> dict:
    """What the app's "What the bot learned" card shows."""
    def view(record: dict | None) -> dict | None:
        if not record:
            return None
        return {"ts": record["ts"], "lessons": record.get("lessons", []),
                "lessons_error": record.get("lessons_error"),
                "trades": record.get("trades", 0), "wins": record.get("wins", 0),
                "weights": [{"source": s, "label": WEIGHTED[s], "weight": w}
                            for s, w in record.get("weights", {}).items() if s in WEIGHTED],
                "stats": record.get("stats", {})}
    finished = {m: len(outcomes(journal, m)) for m in ("test", "live")}
    return {
        "active": view(journal.memory(mode)),
        "proposed": view(journal.memory(mode, "proposed")) if mode == "live" else None,
        "finished_trades": finished.get(mode, 0),
        "needs": max(0, MIN_TRADES_FOR_LESSONS - sum(finished.values())),
        "every_days": LEARN_EVERY.days,
    }


LESSONS_SYSTEM_PROMPT = """You review the finished trades of a personal swing-trading bot and \
write the lessons it should remember. Each trade lists what pointed the bot at the stock \
(sources), how long it was held, why it sold and the result.

Write at most six lessons. Each is one plain sentence a non-expert can follow, specific enough \
for an analyst to act on when reviewing a new buy (for example which kinds of setups or sources \
have tended to lose, or which sell reasons keep coming up). Base every lesson on the trades \
given; with only a few trades, say less rather than overfit. Keep earlier lessons that the new \
results still support, and drop ones they contradict. Never suggest loosening risk limits. \
Call submit_lessons once with the final list."""

LESSONS_TOOL = {
    "name": "submit_lessons",
    "description": "Submit the final list of lessons. Call exactly once, at the end.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"lessons": {"type": "array", "items": {"type": "string"}}},
        "required": ["lessons"],
        "additionalProperties": False,
    },
}


def claude_lessons(client, model: str | None = None) -> LessonsFn:
    """Lessons from Claude, using the analyst's API key."""
    from .analyst import MODEL, AnalystUnavailable, plain_error

    def lessons(trades: list[dict], stats: dict, previous: list[str]) -> list[str]:
        body = json.dumps({"finished_trades": trades, "results_by_source": stats,
                           "earlier_lessons": previous}, indent=2)
        try:
            response = client.messages.create(
                model=model or MODEL, max_tokens=4000, system=LESSONS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": body}],
                tools=[LESSONS_TOOL], tool_choice={"type": "tool", "name": "submit_lessons"},
            )
        except Exception as exc:
            raise AnalystUnavailable(plain_error(exc)) from exc
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_lessons":
                data = block.input if isinstance(block.input, dict) else json.loads(block.input)
                return list(data.get("lessons", []))
        raise AnalystUnavailable("The analyst didn't return lessons")

    return lessons
