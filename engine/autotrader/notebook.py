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


def settle(proposed: list[dict], known_trades: set[str], previous: list[dict],
           today: date) -> list[dict]:
    """The new notebook: Claude's entries with their evidence checked and a status from the code."""
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
        out.append({"text": text, "support": support, "against": against,
                    "status": status_of(len(support), len(against)),
                    "since": since.get(text) or today.isoformat()})
    out.sort(key=lambda e: (len(e["against"]) - len(e["support"]), e["text"]))
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
    active = [e["text"] for e in entries if e["status"] == "active"]
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
mood), never a vague truth. Never suggest loosening risk limits. The bot decides from the \
evidence which lessons are proven, so don't overstate support.

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
                               "support": {"type": "array", "items": {"type": "string"}},
                               "against": {"type": "array", "items": {"type": "string"}}},
                "required": ["text", "support", "against"],
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
                   "notebook": [{k: e[k] for k in ("text", "support", "against", "status")}
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
