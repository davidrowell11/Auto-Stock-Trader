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

    @property
    def approves(self) -> bool:
        return self.action == "buy"


class AnalystUnavailable(RuntimeError):
    pass


class Analyst:
    def __init__(self, client=None, model: str = MODEL, effort: str = "medium") -> None:
        if client is None:
            import anthropic  # imported here so tests don't need the package

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def review(self, candidate: dict) -> AnalystView:
        """Ask Claude for a view on one buy candidate. Raises AnalystUnavailable on failure."""
        messages = [{
            "role": "user",
            "content": "Review this buy candidate:\n\n" + json.dumps(candidate, indent=2, default=str),
        }]
        for _ in range(3):  # allow resuming if the server pauses a long search turn
            try:
                response = self.client.beta.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=[WEB_SEARCH_TOOL, SUBMIT_TOOL],
                    output_config={"effort": self.effort},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except Exception as exc:
                raise AnalystUnavailable(f"Claude API error: {exc}") from exc

            if response.stop_reason == "refusal":
                raise AnalystUnavailable("The analyst declined to review this stock")
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "submit_view":
                    return _to_view(block.input)
            if response.stop_reason == "pause_turn":
                messages = [*messages, {"role": "assistant", "content": response.content}]
                continue
            break
        raise AnalystUnavailable("The analyst didn't return a view")


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
            raise AnalystUnavailable(f"Claude API error: {exc}") from exc
        if response.stop_reason == "refusal":
            raise AnalystUnavailable("The analyst declined to answer that")
        if response.stop_reason != "pause_turn":
            break
        messages = [*messages, {"role": "assistant", "content": response.content}]
    text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text")
    return text.strip() or "Sorry, I couldn't come up with an answer. Try asking another way."
