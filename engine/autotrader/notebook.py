"""The analyst's notebook: lessons it has to prove before it relies on them.

Each week, for every trade that finished since the last review, the analyst writes a short
post-mortem of its own call: what it expected, what happened, and whether the result came from
its reasoning, the bot's rules, the whole market, or luck. Then it updates its notebook: each
lesson lists the finished trades that back it and the ones that go against it.

Claude writes the words; the code decides what counts, from the evidence alone:

* A lesson is **active** (the analyst relies on it) once at least 5 trades back it and at least
  twice as many back it as go against it.
* It is **retired** once 3 or more trades go against it and they outnumber the ones for it.
* Anything else is a **candidate**: the analyst sees it, marked as unproven.

Trades named as evidence must be real finished trades, so a lesson can't invent its support.
(Ideas from Reflexion's self-review and from keeping a "playbook" of rules with their evidence.)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date

log = logging.getLogger("autotrader.notebook")

ACTIVE_SUPPORT = 5
RETIRE_AGAINST = 3
MAX_ACTIVE = 12
MAX_CANDIDATES = 12
MAX_RETIRED = 12
MAX_REVIEWS = 60  # post-mortems kept
MAX_TEXT = 300
CANDIDATES_FOR_ANALYST = 4
CAUSES = ("reasoning", "rules", "market", "luck")

EXAM_MIN_CASES = 8  # finished trades (real or practice) with their desk facts, before any exam
EXAM_CASES = 12
EXAM_TOLERANCE = 0.01  # a new notebook may score this much worse (noise) and still pass
PRACTICE = "practice"  # the id prefix of trades replayed from the backtest

# A past setup -> the analyst's confidence it would be up a few weeks later (fast model, no search).
ExamFn = Callable[[dict], float]
# (new trades to review, all recent trades, notebook entries, stats, scorecard or None)
#   -> {"reviews": [...], "notebook": [...]}
ReflectFn = Callable[[list[dict], list[dict], list[dict], dict, dict | None], dict]


def trade_id(t: dict) -> str:
    """A finished trade's name in the notebook: symbol and the day it was bought."""
    return f"{t['symbol']} {str(t['bought'])[:10]}"


def status_of(support: int, against: int) -> str:
    if against >= RETIRE_AGAINST and against > support:
        return "retired"
    if support >= ACTIVE_SUPPORT and support >= 2 * against:
        return "active"
    return "candidate"


ACTIONS = ("avoid", "favour", "note")  # what a lesson tells the analyst to do


def money(ids: list[str], returns: dict[str, float]) -> dict:
    """What the trades behind a lesson made or lost: count, average and total return."""
    got = [returns[i] for i in ids if i in returns]
    if not got:
        return {"trades": 0, "avg_return": None, "total_return": 0.0}
    return {"trades": len(got), "avg_return": round(sum(got) / len(got), 4),
            "total_return": round(sum(got), 4)}


def money_agrees(action: str, avg: float | None) -> bool:
    """A lesson to avoid a setup must be backed by trades that lost money on average, and one to
    favour a setup by trades that made money. (A "note" has no money direction to check.)"""
    if avg is None or action == "note":
        return True
    return avg < 0 if action == "avoid" else avg > 0


def settle(proposed: list[dict], known_trades: set[str], previous: list[dict],
           today: date, returns: dict[str, float] | None = None) -> list[dict]:
    """The new notebook: Claude's entries with their evidence checked and a status from the code.

    returns (trade id -> its return) lets the money check run: a lesson whose own trades say the
    opposite of what it advises can't become proven, and proven lessons are ranked by the money
    behind them, so the analyst's most valuable lessons come first.
    """
    returns = returns or {}
    since = {e["text"]: e.get("since") for e in previous}
    out, seen = [], set()
    for e in proposed:
        text = " ".join(str(e.get("text", "")).split())[:MAX_TEXT]
        if not text or text.lower() in seen:
            continue
        support = sorted({str(x) for x in e.get("support", []) if str(x) in known_trades})
        against = sorted({str(x) for x in e.get("against", []) if str(x) in known_trades} - set(support))
        if not support and not against:
            continue  # a lesson with no real evidence isn't a lesson
        seen.add(text.lower())
        action = str(e.get("action", "note")).lower()
        action = action if action in ACTIONS else "note"
        backed = money(support, returns)
        status = status_of(len(support), len(against))
        if status == "active" and returns and not money_agrees(action, backed["avg_return"]):
            status = "candidate"  # the evidence counts, but the money says otherwise
        out.append({"text": text, "action": action, "support": support, "against": against,
                    "status": status, "money": backed,
                    "since": since.get(text) or today.isoformat()})
    # Most valuable first: the money at stake in the trades behind it, then the evidence balance.
    out.sort(key=lambda e: (-abs(e["money"]["total_return"]),
                            len(e["against"]) - len(e["support"]), e["text"]))
    keep = {"active": MAX_ACTIVE, "candidate": MAX_CANDIDATES, "retired": MAX_RETIRED}
    counts: dict[str, int] = {}
    kept = []
    for e in out:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
        if counts[e["status"]] <= keep[e["status"]]:
            kept.append(e)
    return kept


def clean_reviews(reviews: list, known_trades: set[str]) -> list[dict]:
    out = []
    for r in reviews or []:
        if not isinstance(r, dict) or str(r.get("trade")) not in known_trades:
            continue
        cause = str(r.get("cause", "")).lower()
        out.append({"trade": str(r["trade"]),
                    "expected": str(r.get("expected", ""))[:MAX_TEXT],
                    "happened": str(r.get("happened", ""))[:MAX_TEXT],
                    "cause": cause if cause in CAUSES else "luck",
                    "lesson": str(r.get("lesson", ""))[:MAX_TEXT]})
    return out


def lessons_for_analyst(entries: list[dict]) -> list[str]:
    """Active lessons as they are, then a few candidates marked as unproven."""
    def worth(e: dict) -> str:
        m = e.get("money") or {}
        if not m.get("trades") or m.get("avg_return") is None:
            return ""
        return f" [{m['trades']} trades behind it averaged {m['avg_return']:+.1%}]"

    active = [e["text"] + worth(e) for e in entries if e["status"] == "active"]
    hunches = [f"Unproven ({len(e['support'])} trades for, {len(e['against'])} against), "
               f"weigh lightly: {e['text']}"
               for e in entries if e["status"] == "candidate"][:CANDIDATES_FOR_ANALYST]
    return active + hunches


REFLECT_SYSTEM_PROMPT = """You are the analyst inside a personal swing-trading bot, reviewing \
your own past calls so you get better. You are given the bot's recently finished trades \
(each with an id, what pointed the bot at it, the desk-check numbers when it was bought such \
as how far above its averages it was and the market's mood, how long it was held, why it sold \
and the result), which of them are new since your last review, your current notebook of \
lessons with the trades that back or contradict each, results by source, and a scorecard of \
how the stocks you approved and blocked did a month later.

1. For each new trade, write a short post-mortem: what the buy expected, what happened, and \
the main cause of the result: "reasoning" (the call itself was right or wrong for a reason \
you can name), "rules" (the bot's own rules, such as a stop or trend exit, decided it), \
"market" (the whole market moved), or "luck" (nothing in the data explains it). Add at most \
one lesson, or leave it empty. Be honest: one loss is not a pattern.
2. Update the notebook. Keep, reword, merge or drop existing lessons, and add new ones. For \
each lesson list the ids of the trades that support it and those that go against it, using \
only ids from the trades given. A lesson must be specific enough to act on when reviewing a \
new buy (for example a range of a desk-check number, a source, a sell reason, or a market \
mood), never a vague truth. Mark each lesson "avoid" (setups like this lose money), "favour" \
(they make money) or "note". Aim at money, not tidiness: the bot ranks lessons by the money \
in the trades behind them, and a lesson can only be proven when its trades' results agree with \
it (an "avoid" lesson needs trades that lost on average). Prefer lessons that would have \
avoided the biggest losses or kept the biggest winners. Never suggest loosening risk limits. \
The bot decides from the evidence which lessons are proven, so don't overstate support.

Call submit_reflection once with the post-mortems and the whole notebook."""

REFLECT_TOOL = {
    "name": "submit_reflection",
    "description": "Submit the post-mortems and the updated notebook. Call exactly once, at the end.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "reviews": {"type": "array", "items": {
                "type": "object",
                "properties": {"trade": {"type": "string"}, "expected": {"type": "string"},
                               "happened": {"type": "string"},
                               "cause": {"type": "string", "enum": list(CAUSES)},
                               "lesson": {"type": "string"}},
                "required": ["trade", "expected", "happened", "cause", "lesson"],
                "additionalProperties": False}},
            "notebook": {"type": "array", "items": {
                "type": "object",
                "properties": {"text": {"type": "string"},
                               "action": {"type": "string", "enum": list(ACTIONS)},
                               "support": {"type": "array", "items": {"type": "string"}},
                               "against": {"type": "array", "items": {"type": "string"}}},
                "required": ["text", "action", "support", "against"],
                "additionalProperties": False}},
        },
        "required": ["reviews", "notebook"],
        "additionalProperties": False,
    },
}


def claude_reflect(client, model: str | None = None) -> ReflectFn:
    """Post-mortems and notebook updates from Claude, using the analyst's API key."""
    from .analyst import MODEL, AnalystUnavailable, plain_error

    def reflect(new: list[dict], trades: list[dict], notebook: list[dict], stats: dict,
                card: dict | None = None) -> dict:
        payload = {"new_trades_to_review": [trade_id(t) for t in new], "finished_trades": trades,
                   "notebook": [{k: e.get(k) for k in ("text", "action", "support", "against",
                                                       "status", "money")}
                                for e in notebook],
                   "results_by_source": stats}
        if card and (card["approved"]["calls"] or card["blocked"]["calls"]):
            payload["analyst_scorecard"] = {k: v for k, v in card.items() if k != "calibration"}
        messages = [{"role": "user", "content": json.dumps(payload, indent=2, default=str)}]
        try:
            with client.messages.stream(
                model=model or MODEL, max_tokens=16000, system=REFLECT_SYSTEM_PROMPT,
                messages=messages, tools=[REFLECT_TOOL],
                thinking={"type": "adaptive"}, output_config={"effort": "medium"},
            ) as stream:
                response = stream.get_final_message()
        except Exception as exc:
            raise AnalystUnavailable(plain_error(exc)) from exc
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_reflection":
                data = block.input if isinstance(block.input, dict) else json.loads(block.input)
                return {"reviews": list(data.get("reviews", [])),
                        "notebook": list(data.get("notebook", []))}
        if response.stop_reason == "refusal":
            raise AnalystUnavailable("The analyst declined to review its trades")
        raise AnalystUnavailable("The analyst didn't return its review")

    return reflect


# ----- the exam: does a new notebook make the analyst's calls better or worse? -----
def exam_cases(trades: list[dict]) -> list[dict]:
    """The most recent finished trades whose setup is known (their desk facts), newest last."""
    return [t for t in trades if t.get("facts")][-EXAM_CASES:]


def sit_exam(exam_fn: ExamFn, cases: list[dict], lessons: list[str],
             bar: float = 0.55) -> dict | None:
    """How the analyst does on past setups with these lessons.

    money: the total return of the trades it would have let through (confidence at or over the
    bar), which is what counts. brier: how close its confidence was to what happened (0 perfect,
    0.25 a coin flip), the tie-breaker. The stock's name and dates are hidden, so it judges the
    setup, not what it may know happened. None if too few answers came back to judge.
    """
    scores, made = [], 0.0
    for t in cases:
        candidate = {"symbol": "a past buy (name hidden)", "sources": t.get("sources", []),
                     "score": t.get("score"),
                     # Without its own earlier confidence, so it judges the setup afresh.
                     "desk_check": {k: v for k, v in t["facts"].items()
                                    if k != "analyst_confidence"},
                     "lessons_from_past_trades": lessons}
        try:
            p = float(exam_fn(candidate))
        except Exception as exc:  # one failed question doesn't fail the exam
            log.info("Exam question failed: %s", exc)
            continue
        p = min(1.0, max(0.0, p))
        scores.append((p - (t["return"] > 0)) ** 2)
        if p >= bar:
            made += float(t["return"])
    if len(scores) < max(1, len(cases) // 2):
        return None
    return {"brier": round(sum(scores) / len(scores), 4), "money": round(made, 4)}


def exam_passed(old: dict, new: dict) -> bool:
    """More money passes. The same money passes if its confidence isn't clearly worse."""
    if new["money"] > old["money"] + 1e-9:
        return True
    if new["money"] < old["money"] - 1e-9:
        return False
    return new["brier"] <= old["brier"] + EXAM_TOLERANCE


def practice_cases(results: list[dict]) -> list[dict]:
    """Backtest trades as practice cases, marked so they're never mixed up with real ones."""
    return [{**r, "id": f"{PRACTICE} {r['id']}", "practice": True} for r in results]
