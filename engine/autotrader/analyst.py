"""The AI analyst: Claude reviews each buy the rules want to make.

For every candidate it reads the trend numbers, the expert signals
(insiders, Congress), and searches recent news, then returns buy or skip, a
confidence score and a plain-English reason. It can only veto a buy, never
force one or loosen a risk rule.

Needs ``ANTHROPIC_API_KEY``. Every view is saved to the journal so the app
can show a scorecard of how accurate the analyst has been.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger("autotrader.analyst")

MODEL = "claude-opus-5-5"
# First look for the cheaper reviews: about 40x cheaper per token than Opus.
QUICK_MODEL = "claude-haiku-5-5"
QUICK_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 2}

SYSTEM_PROMPT = """You are the risk-aware equity analyst inside an automated swing-trading bot \
(holding periods of days to weeks). The bot's rules have already found a stock in a strong \
uptrend and sized a position within strict risk limits. Your job is to catch what the numbers \
miss before money is committed.

Start from the recent headlines provided (from the last two days), then use web search to \
check the last two weeks of news for the company. Look for red flags: an \
earnings report in the next 5 trading days, an announced share offering or dilution, fraud or \
accounting questions, regulatory action, a pending acquisition that caps the price, or a move \
driven only by hype or social-media promotion. For penny stocks (under $5) be especially \
skeptical: promotion campaigns and dilution are common.

If lessons from the bot's past trades are provided, apply them: they come from how its own \
earlier buys turned out. Similar past trades, if given, are its closest earlier buys of the same \
kind and how each ended; a poor record there is a reason for more caution, not an automatic skip.

Weigh the expert signals provided: open-market insider buying and purchases by members of \
Congress with strong track records are positives, but they are reported with a delay.

Recommend "skip" when there is a concrete red flag or the evidence is weak; otherwise "buy". \
Set confidence between 0 and 1 for how likely the position is to be profitable over the next \
few weeks. Keep the reasoning to 2-4 plain sentences a non-expert can follow. When you are \
done, call submit_view exactly once with your final view."""

SUBMIT_TOOL = {
    "name": "submit_view",
    "description": "Submit the final buy-or-skip view for this stock. Call exactly once, at the end.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["buy", "skip"]},
            "confidence": {"type": "number", "description": "0 to 1"},
            "reasoning": {"type": "string"},
            "red_flags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["action", "confidence", "reasoning", "red_flags"],
        "additionalProperties": False,
    },
}
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


@dataclass
class AnalystView:
    action: str  # "buy" or "skip"
    confidence: float
    reasoning: str
    red_flags: list[str] = field(default_factory=list)
    model: str | None = None  # which Claude model gave this view

    @property
    def approves(self) -> bool:
        return self.action == "buy"


class AnalystUnavailable(RuntimeError):
    pass


class Analyst:
    supports_quick = True  # the engine may ask the fast model first (Settings.quick_reviews)

    def __init__(self, client=None, model: str = MODEL, effort: str = "medium",
                 quick_model: str = QUICK_MODEL) -> None:
        if client is None:
            import anthropic  # imported here so tests don't need the package

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort
        self.quick_model = quick_model

    def review(self, candidate: dict, quick: bool = False) -> AnalystView:
        """Ask Claude for a view on one buy candidate. Raises AnalystUnavailable on failure.

        quick uses the fast, low-cost model; the engine only trusts its clear-cut answers.
        """
        if quick:
            extra = {"tools": [QUICK_SEARCH_TOOL, SUBMIT_TOOL]}  # no server-side fallback on this model
            model = self.quick_model
        else:
            extra = {"tools": [WEB_SEARCH_TOOL, SUBMIT_TOOL], "output_config": {"effort": self.effort},
                     "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
            model = self.model
        messages = [{
            "role": "user",
            "content": "Review this buy candidate:\n\n" + json.dumps(candidate, indent=2, default=str),
        }]
        for _ in range(3):  # allow resuming if the server pauses a long search turn
            try:
                response = self.client.beta.messages.create(
                    model=model, max_tokens=16000, system=SYSTEM_PROMPT, messages=messages, **extra)
            except Exception as exc:
                raise AnalystUnavailable(plain_error(exc)) from exc

            if response.stop_reason == "refusal":
                raise AnalystUnavailable("The analyst declined to review this stock")
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "submit_view":
                    view = _to_view(block.input)
                    view.model = model
                    return view
            if response.stop_reason == "pause_turn":
                messages = [*messages, {"role": "assistant", "content": response.content}]
                continue
            break
        raise AnalystUnavailable("The analyst didn't return a view")


def plain_error(exc: Exception) -> str:
    """A Claude API failure in words the app can show, with what to do about it."""
    from .spend import SpendingCapReached

    if isinstance(exc, SpendingCapReached):
        return str(exc)
    text = str(exc)
    status = getattr(exc, "status_code", None)
    lower = text.lower()
    if "credit balance" in lower:
        return ("The AI analyst's Anthropic account is out of API credits. Add credits at "
                "console.anthropic.com under Plans & Billing (separate from a Claude.ai plan).")
    if status in (401, 403) or "authentication_error" in lower or "permission_error" in lower:
        return ("The server's ANTHROPIC_API_KEY isn't working. Make a new key at "
                "console.anthropic.com and update it in Replit Secrets.")
    if status == 429 or "rate_limit" in lower:
        return "The AI analyst is getting too many requests. Try again in a minute."
    if status in (500, 502, 503, 529) or "overloaded" in lower:
        return "The AI analyst is busy right now. Try again in a minute."
    if "connection" in lower or "timed out" in lower or "timeout" in lower:
        return "Couldn't reach the AI analyst. Try again in a minute."
    return f"Claude API error: {text[:200]}"


def _to_view(data) -> AnalystView:
    if isinstance(data, str):
        data = json.loads(data)
    try:
        confidence = min(1.0, max(0.0, float(data["confidence"])))
        action = data["action"] if data["action"] in ("buy", "skip") else "skip"
        return AnalystView(action, confidence, str(data["reasoning"]),
                           [str(f) for f in data.get("red_flags", [])])
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalystUnavailable(f"Malformed analyst view: {exc}") from exc


ASK_SYSTEM_PROMPT = """You are the analyst inside the user's personal automated trading bot. Answer \
the user's question about their bot, its trades or a stock in plain, friendly language a \
non-expert can follow, in a few short paragraphs at most. The bot's current state is given as \
JSON. Use web search when the question needs recent news or prices. Be honest about \
uncertainty, and never promise returns."""


def ask(client, question: str, context: dict, model: str = MODEL) -> str:
    """Answer a free-form question from the app's chat box."""
    messages = [{
        "role": "user",
        "content": f"Bot state:\n{json.dumps(context, default=str)}\n\nQuestion: {question}",
    }]
    for _ in range(3):  # a long web search can pause the turn; carry on where it left off
        try:
            response = client.beta.messages.create(
                model=model,
                max_tokens=16000,
                system=ASK_SYSTEM_PROMPT,
                messages=messages,
                tools=[WEB_SEARCH_TOOL],
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except Exception as exc:
            raise AnalystUnavailable(plain_error(exc)) from exc
        if response.stop_reason == "refusal":
            raise AnalystUnavailable("The analyst declined to answer that")
        if response.stop_reason != "pause_turn":
            break
        messages = [*messages, {"role": "assistant", "content": response.content}]
    text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text")
    return text.strip() or "Sorry, I couldn't come up with an answer. Try asking another way."
