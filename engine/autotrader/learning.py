"""The bot's memory: it learns from how its own trades turned out.

Every buy records what pointed the bot at the stock (its trend score, the
daily screener, insider buying, a Congress member's purchase, the AI analyst,
penny-stock rules, after-hours buying). When the bot sells, that trade's
result joins its memory. Once a week the bot:

1. Scores each source by how its trades went. A source that keeps leading to
   winners gets a little more weight when ranking buys, and one that keeps
   losing gets a little less, always between 0.7 and 1.3. With fewer than
   five finished trades a source stays at 1.0.
2. Has the analyst review its own finished trades (notebook.py): a short
   post-mortem of each new one, and a notebook of lessons, each with the
   trades that back it and those against it. The code, not Claude, decides
   from that evidence which lessons are proven (5+ trades, twice as many for
   as against), which are still unproven, and which are retired. Proven
   lessons go into every analyst review, plus a few unproven ones marked as
   such. (Without the notebook, Claude writes a short flat list instead.)
   It also checks how sure the analyst said it was against how its calls
   turned out a month later (calibration.py), and corrects its confidence
   by a small, capped amount once there are enough graded calls.
3. Keeps its recent finished trades as cases. Before a buy, the most similar
   past cases (same kind of sources, a similar trend score, penny or not) are
   shown with the buy's reasons and given to the analyst, so it can see "the
   last 4 buys like this: 1 made money". (An idea borrowed from case-based
   agent memories such as ReasoningBank, without the vector database: a
   handful of trade features is enough here.) Recent cases count as a bit
   more alike than old ones, since what works in the market drifts over time
   (the "temporal decay" idea from RuVector).

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
from datetime import date, datetime, timedelta, timezone

from . import calibration, scorecard
from .journal import TRIM, Journal
from .notebook import (MAX_REVIEWS, ReflectFn, clean_reviews, lessons_for_analyst, settle,
                       trade_id)

log = logging.getLogger("autotrader.learning")

LEARN_EVERY = timedelta(days=7)
RETRY_AFTER = timedelta(hours=6)  # after a failed or skipped attempt
MIN_TRADES_FOR_LESSONS = 3
MIN_TRADES_FOR_WEIGHT = 5
WEIGHT_MIN, WEIGHT_MAX = 0.7, 1.3
FLAT_LESSONS = 6  # the older one-list lessons
MAX_LESSONS = 16  # what the analyst is given: 12 proven notebook lessons and 4 unproven ones
MAX_OUTCOMES_SHOWN = 60
MAX_CASES = 200  # finished trades kept for "similar past trades"
SIMILAR_K = 5
SIMILAR_MAX_DISTANCE = 0.8
RECENCY_PENALTY = 0.3  # a year-old case counts as this much less alike (markets change)

# Sources whose weight the bot adjusts, and how each appears in the app.
WEIGHTED = {"insider": "Insider buying", "congress": "Congress members' buys",
            "screener": "Today's most active stocks"}
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
    score: float | None = None  # the trend score when it was bought
    facts: dict = field(default_factory=dict)  # the desk check's numbers when it was bought

    @property
    def ret(self) -> float:
        return self.sell_price / self.buy_price - 1 if self.buy_price else 0.0

    @property
    def days(self) -> int:
        return max(0, (datetime.fromisoformat(self.sold) - datetime.fromisoformat(self.bought)).days)

    def as_dict(self) -> dict:
        out = {"symbol": self.symbol, "mode": self.mode, "bought": self.bought[:10],
               "sold": self.sold[:10], "days_held": self.days, "return": round(self.ret, 4),
               "sources": self.sources, "sell_reason": self.sell_reason, "score": self.score}
        out["id"] = trade_id(out)
        if self.facts:
            out["facts"] = self.facts
        return out


def _score_of(reasons: list[str]) -> float | None:
    for r in reasons:
        if r.startswith("Score "):
            try:
                return float(r.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def distance(a_sources, a_score, b_sources, b_score) -> float:
    """0 for the same kind of buy; about 1 or more for unrelated ones."""
    a, b = set(a_sources) - {"trend"}, set(b_sources) - {"trend"}
    penny = ("penny" in a) != ("penny" in b)
    union = a | b
    overlap = len(a & b) / len(union) if union else 1.0
    gap = abs(a_score - b_score) if a_score is not None and b_score is not None else 0.25
    return round((1 - overlap) * 0.6 + gap * 2 + penny * 1.0, 4)


@dataclass
class Memory:
    """What the engine uses: source weights for ranking and lessons for the analyst."""
    weights: dict[str, float] = field(default_factory=dict)
    lessons: list[str] = field(default_factory=list)
    cases: list[dict] = field(default_factory=list)  # finished trades, as Outcome.as_dict()
    calibration_shift: float = 0.0  # added to the analyst's confidence (calibration.py)

    def adjust(self, confidence: float) -> float:
        """The analyst's confidence, corrected by its track record."""
        return calibration.adjust(confidence, self.calibration_shift)

    def similar(self, sources: list[str], score: float,
                today: date | None = None) -> list[dict]:
        """The closest past finished trades to a buy like this one, nearest first."""
        today = today or datetime.now(timezone.utc).date()

        def age_penalty(c: dict) -> float:
            try:
                days = (today - date.fromisoformat(str(c.get("sold", ""))[:10])).days
            except ValueError:
                return RECENCY_PENALTY
            return RECENCY_PENALTY * min(max(days, 0), 365) / 365

        scored = sorted(((distance(sources, score, c.get("sources", []), c.get("score"))
                          + age_penalty(c), c) for c in self.cases), key=lambda x: x[0])
        return [c for d, c in scored if d <= SIMILAR_MAX_DISTANCE][:SIMILAR_K]

    def similar_note(self, sources: list[str], score: float) -> tuple[str | None, list[dict]]:
        """("Similar past trades: 1 of 4 made money (avg -2.1%)", the cases), or (None, []) with too few."""
        cases = self.similar(sources, score)
        if len(cases) < 2:
            return None, []
        wins = sum(c["return"] > 0 for c in cases)
        avg = sum(c["return"] for c in cases) / len(cases)
        return f"Similar past trades: {wins} of {len(cases)} made money (avg {avg:+.1%})", cases

    def weight(self, source: str) -> float:
        return float(self.weights.get(source, 1.0))

    @classmethod
    def from_record(cls, record: dict | None) -> "Memory":
        if not record:
            return cls()
        weights = {k: min(WEIGHT_MAX, max(WEIGHT_MIN, float(v)))
                   for k, v in record.get("weights", {}).items() if k in WEIGHTED}
        try:
            shift = float((record.get("calibration") or {}).get("shift") or 0.0)
        except (TypeError, ValueError):
            shift = 0.0
        return cls(weights, [str(x) for x in record.get("lessons", [])][:MAX_LESSONS],
                   [c for c in record.get("cases", []) if isinstance(c, dict) and "return" in c],
                   calibration.adjust(0.5, shift) - 0.5)


def outcomes(journal: Journal, mode: str) -> list[Outcome]:
    """Each finished trade: a buy paired with the bot's next sell of that stock."""
    out: list[Outcome] = []
    open_buys: dict[str, dict] = {}
    for t in journal.trades(mode):
        if t["side"] == "buy":
            open_buys[t["symbol"]] = t  # the latest buy (an unfilled one is placed again)
            continue
        reason = t["reasons"][0] if t["reasons"] else ""
        if reason.startswith(TRIM):
            continue  # a trim sells part of it; the trade isn't finished yet
        buy = open_buys.pop(t["symbol"], None)
        if buy is None:
            continue  # a re-priced sell, or a stock bought before the bot kept records
        if reason in NOT_THE_BOTS_CALL:
            continue
        out.append(Outcome(mode, t["symbol"], buy["ts"], t["ts"], float(buy["price"]),
                           float(t["price"]), list(buy["sources"]), reason,
                           _score_of(buy["reasons"]), dict(buy.get("facts") or {})))
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


# Asks Claude for lessons: (finished trades, stats per source, previous lessons, analyst
# scorecard or None) -> lessons.
LessonsFn = Callable[[list[dict], dict, list[str], dict | None], list[str]]
# Daily prices for the scorecard: (symbols, trading days) -> bars by symbol.
PricesFn = Callable[[list[str], int], dict]


class LearnerBusy(RuntimeError):
    pass


def _shift(record: dict | None) -> float:
    return float(((record or {}).get("calibration") or {}).get("shift") or 0.0)


def _changes_nothing(record: dict) -> bool:
    return (not record.get("lessons") and all(w == 1.0 for w in record.get("weights", {}).values())
            and _shift(record) == 0.0)


def _same(a: dict | None, b: dict) -> bool:
    return a is not None and ((a.get("weights"), a.get("lessons"), _shift(a))
                              == (b["weights"], b["lessons"], _shift(b)))


class Learner:
    def __init__(self, journal: Journal, lessons_fn: LessonsFn | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 prices_fn: PricesFn | None = None, reflect_fn: ReflectFn | None = None) -> None:
        self.journal = journal
        self.lessons_fn = lessons_fn  # the older flat lessons; reflect_fn (the notebook) wins
        self.reflect_fn = reflect_fn
        self.prices_fn = prices_fn  # for grading the analyst's calls; None skips the scorecard
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

    def grade_analyst(self) -> dict | None:
        """Grade the analyst's month-old calls (Test and Live together) and keep the result."""
        if self.prices_fn is None:
            return None
        today = self.now().date()
        try:
            views = self.journal.views("test") + self.journal.views("live")
            if not scorecard.any_ready(views, today):
                return None  # nothing is a month old yet: no need to fetch prices
            bars = self.prices_fn(scorecard.symbols_of(views, today),
                                  scorecard.bars_needed(views, today))
            card = scorecard.grade(views, bars, today)
        except Exception as exc:  # the scorecard can wait; it must never stop the weekly review
            log.warning("Couldn't grade the analyst's calls: %s", exc)
            return None
        self.journal.save_memory("test", "scorecard", card)
        return card

    def _learn(self) -> dict | None:
        self._last_try = self.now()
        card = self.grade_analyst()
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
        last = self.journal.memory("test") or {}
        notebook, reviews = last.get("notebook", []), last.get("reviews", [])
        reviewed = set(last.get("reviewed", []))
        if self.reflect_fn is not None:
            shown = [o.as_dict() for o in results[-MAX_OUTCOMES_SHOWN:]]
            new = [t for t in shown if t["id"] not in reviewed]
            if new or not notebook:
                try:
                    out = self.reflect_fn(new, shown, notebook, stats, card)
                    known = {t["id"] for t in shown} | {
                        x for e in notebook for x in (*e.get("support", []), *e.get("against", []))}
                    notebook = settle(out.get("notebook", []), known, notebook, self.now().date())
                    reviews = (reviews + clean_reviews(out.get("reviews", []), {t["id"] for t in new})
                               )[-MAX_REVIEWS:]
                    reviewed |= {t["id"] for t in new}
                except Exception as exc:  # keep the old notebook; retry in a few hours
                    log.warning("Couldn't get the analyst's review of its trades: %s", exc)
                    lessons_error = str(exc)[:300]
            lessons = lessons_for_analyst(notebook) if notebook else previous
        elif self.lessons_fn is not None:
            shown = [o.as_dict() for o in results[-MAX_OUTCOMES_SHOWN:]]
            try:
                lessons = [str(x).strip() for x in self.lessons_fn(shown, stats, previous, card)
                           if str(x).strip()][:FLAT_LESSONS]
            except Exception as exc:  # keep the weights and old lessons; retry in a few hours
                log.warning("Couldn't get lessons from the analyst: %s", exc)
                lessons_error = str(exc)[:300]
        wins = sum(o.ret > 0 for o in results)
        record = {
            "learned_at": self._last_try.isoformat(timespec="seconds"),
            "weights": weights, "lessons": lessons, "trades": len(results), "wins": wins,
            "lessons_error": lessons_error,
            "notebook": notebook, "reviews": reviews, "reviewed": sorted(reviewed)[-1000:],
            # How sure the analyst should be, from its graded calls (kept until there's a new grade).
            "calibration": (card or {}).get("calibration") or last.get("calibration"),
            "cases": [o.as_dict() for o in results[-MAX_CASES:]],
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
            if live is not None:  # same rules; just bring its record of past trades up to date
                kept = {k: v for k, v in live.items() if k not in ("id", "ts")}
                self.journal.save_memory("live", "active", {**kept, "cases": record["cases"]})
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
                "stats": record.get("stats", {}),
                "notebook": record.get("notebook", []),
                "reviews": list(reversed(record.get("reviews", [])))[:10],
                "calibration": record.get("calibration")}
    finished = {m: len(outcomes(journal, m)) for m in ("test", "live")}
    return {
        "active": view(journal.memory(mode)),
        "proposed": view(journal.memory(mode, "proposed")) if mode == "live" else None,
        "finished_trades": finished.get(mode, 0),
        "needs": max(0, MIN_TRADES_FOR_LESSONS - sum(finished.values())),
        "every_days": LEARN_EVERY.days,
        "scorecard": _scorecard(journal),
    }


def _scorecard(journal: Journal) -> dict | None:
    card = journal.memory("test", "scorecard")  # one scorecard: the analyst is the same in both
    return {k: v for k, v in card.items() if k != "id"} if card else None


LESSONS_SYSTEM_PROMPT = """You review the finished trades of a personal swing-trading bot and \
write the lessons it should remember. Each trade lists what pointed the bot at the stock \
(sources), how long it was held, why it sold and the result.

Write at most six lessons. Each is one plain sentence a non-expert can follow, specific enough \
for an analyst to act on when reviewing a new buy (for example which kinds of setups or sources \
have tended to lose, or which sell reasons keep coming up). Base every lesson on the trades \
given; with only a few trades, say less rather than overfit. Keep earlier lessons that the new \
results still support, and drop ones they contradict. Never suggest loosening risk limits. \
When an analyst_scorecard is given, it shows how the stocks the analyst approved and blocked \
did over the month after each review, compared with the market. If blocked stocks keep doing \
better than approved ones, write a lesson about what kind of setup it has been too strict on; \
if approved ones keep lagging, say what it let through. Call submit_lessons once with the final \
list."""

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

    def lessons(trades: list[dict], stats: dict, previous: list[str],
                card: dict | None = None) -> list[str]:
        payload = {"finished_trades": trades, "results_by_source": stats,
                   "earlier_lessons": previous}
        if card and (card["approved"]["calls"] or card["blocked"]["calls"]):
            payload["analyst_scorecard"] = card
        body = json.dumps(payload, indent=2)
        try:
            response = client.messages.create(
                model=model or MODEL, max_tokens=4000, system=LESSONS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": body}],
                tools=[LESSONS_TOOL],  # forced tool_choice isn't allowed on this model; the prompt asks
            )
        except Exception as exc:
            raise AnalystUnavailable(plain_error(exc)) from exc
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_lessons":
                data = block.input if isinstance(block.input, dict) else json.loads(block.input)
                return list(data.get("lessons", []))
        if response.stop_reason == "refusal":
            raise AnalystUnavailable("The analyst declined to write lessons")
        raise AnalystUnavailable("The analyst didn't return lessons")

    return lessons
