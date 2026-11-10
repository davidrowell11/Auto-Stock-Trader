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
from collections.abc import Callable, Iterator
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

A desk check comes with each candidate: the stock's trend and momentum against its own \
averages and the market, its volume, the market's mood, and how it would sit with what the bot \
already holds. Read it the way a professional swing trader would:
- Strength that lasts: stocks beating the market over 3 months, above a rising 50- and 200-day \
average, near their 52-week high, tend to keep going. A stock far below its 52-week high is \
more often a recovery bet than a trend.
- Extended: well above its 20-day average (several average daily moves) often pulls back \
soon after buying; that is a reason for lower confidence, not on its own a skip.
- Volume: a breakout on heavy volume is more trustworthy; a jump on thin volume less so.
- Market: in a cautious or weak market, fewer trends hold; be stricter.
- Concentration: another stock in the same sector, or one that moves almost the same way as a \
holding, adds little and doubles the risk of one bad day. Prefer to skip it unless it is \
clearly the stronger of the two.
If comparing helps (a close competitor, or the sector's fund such as XLK, XLV or XLF), call \
look_up_stock for the same summary on that symbol, at most twice.

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
LOOKUP_TOOL = {
    "name": "look_up_stock",
    "description": ("The same trend, momentum and volume summary as the desk check, for another "
                    "stock or fund (for example a competitor or the sector's fund). Use at most twice."),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"symbol": {"type": "string", "description": "Ticker, like MSFT or XLK"}},
        "required": ["symbol"],
        "additionalProperties": False,
    },
}
MAX_LOOKUPS = 2
MAX_TURNS = 6  # model turns per review: searches, look-ups, then the view

# The basic search tool. The newer web_search_20260209 (with dynamic filtering) failed on most
# Opus reviews on 2026-11-04 ("web search failed"), which left the analyst unsure and its
# confidence low, while Haiku's basic search worked every time.
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}


def search_problems(response) -> list[str]:
    """Error codes from any web searches in this response that failed."""
    out = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        content = getattr(block, "content", None)
        code = getattr(content, "error_code", None)
        if code is None and isinstance(content, dict):
            code = content.get("error_code")
        if code:
            out.append(str(code))
    return out


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
    supports_lookup = True  # the full analyst may look up other stocks (look_up_stock)

    def __init__(self, client=None, model: str = MODEL, effort: str = "medium",
                 quick_model: str = QUICK_MODEL) -> None:
        if client is None:
            import anthropic  # imported here so tests don't need the package

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort
        self.quick_model = quick_model

    def review(self, candidate: dict, quick: bool = False,
               lookup: Callable[[str], dict] | None = None) -> AnalystView:
        """Ask Claude for a view on one buy candidate. Raises AnalystUnavailable on failure.

        quick uses the fast, low-cost model; the engine only trusts its clear-cut answers.
        lookup, if given, lets the full analyst pull the desk-check summary for other symbols.
        """
        if quick:
            extra = {"tools": [QUICK_SEARCH_TOOL, SUBMIT_TOOL]}  # no server-side fallback on this model
            model = self.quick_model
            lookup = None  # the quick look stays a single cheap pass
        else:
            tools = [WEB_SEARCH_TOOL, *([LOOKUP_TOOL] if lookup else []), SUBMIT_TOOL]
            extra = {"tools": tools, "thinking": {"type": "adaptive"},
                     "output_config": {"effort": self.effort},
                     "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
            model = self.model
        messages = [{
            "role": "user",
            "content": "Review this buy candidate:\n\n" + json.dumps(candidate, indent=2, default=str),
        }]
        lookups = 0
        for _ in range(MAX_TURNS):
            try:
                response = self.client.beta.messages.create(
                    model=model, max_tokens=16000, system=SYSTEM_PROMPT, messages=messages, **extra)
            except Exception as exc:
                if "thinking" in extra and getattr(exc, "status_code", None) == 400 \
                        and "thinking" in str(exc).lower():
                    # A model (or fallback) that won't take adaptive thinking: carry on without it.
                    log.warning("Analyst thinking not accepted (%s); reviewing without it", exc)
                    extra = {k: v for k, v in extra.items() if k != "thinking"}
                    continue
                raise AnalystUnavailable(plain_error(exc)) from exc

            problems = search_problems(response)
            if problems:  # so the logs show why, if the analyst says it couldn't search
                log.warning("Web search failed for %s (%s): %s", candidate.get("symbol"), model,
                            ", ".join(problems))
            if response.stop_reason == "refusal":
                raise AnalystUnavailable("The analyst declined to review this stock")
            calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            for block in calls:
                if block.name == "submit_view":
                    view = _to_view(block.input)
                    view.model = model
                    return view
            if response.stop_reason == "pause_turn":
                messages = [*messages, {"role": "assistant", "content": response.content}]
                continue
            if response.stop_reason == "tool_use" and calls:
                results = []
                for block in calls:
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": self._run_tool(block, lookup, lookups)})
                    lookups += block.name == "look_up_stock"
                messages = [*messages, {"role": "assistant", "content": response.content},
                            {"role": "user", "content": results}]
                continue
            break
        raise AnalystUnavailable("The analyst didn't return a view")

    def exam_view(self, candidate: dict) -> float:
        """For the learning exam: the fast model's confidence on a past setup, from the numbers
        and lessons alone (no news search, so it can't look up what happened)."""
        messages = [{"role": "user", "content": (
            "Exam question: judge this past setup from the numbers and lessons only. No news "
            "search is available. Review this buy candidate:\n\n"
            + json.dumps(candidate, indent=2, default=str))}]
        try:
            response = self.client.beta.messages.create(
                model=self.quick_model, max_tokens=2000, system=SYSTEM_PROMPT, messages=messages,
                tools=[SUBMIT_TOOL])
        except Exception as exc:
            raise AnalystUnavailable(plain_error(exc)) from exc
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_view":
                return _to_view(block.input).confidence
        raise AnalystUnavailable("No answer to the exam question")

    @staticmethod
    def _run_tool(block, lookup: Callable[[str], dict] | None, used: int) -> str:
        """Our side of a tool call. Bad input or a failure comes back as text, never a crash."""
        if block.name != "look_up_stock" or lookup is None:
            return f"Unknown tool {block.name}. Call submit_view with your view."
        if used >= MAX_LOOKUPS:
            return "No more look-ups for this review. Call submit_view with your view."
        symbol = str((block.input or {}).get("symbol", "")).strip().upper()
        if not symbol or len(symbol) > 10 or not symbol.replace(".", "").replace("-", "").isalnum():
            return "That isn't a ticker symbol."
        try:
            return json.dumps(lookup(symbol), default=str)
        except Exception as exc:  # no data for it, broker hiccup, ...
            log.info("look_up_stock %s failed: %s", symbol, exc)
            return f"No data for {symbol}."


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
    text = "".join(ask_stream(client, question, context, model)).strip()
    return text or "Sorry, I couldn't come up with an answer. Try asking another way."


def ask_stream(client, question: str, context: dict, model: str = MODEL) -> Iterator[str]:
    """The same answer, yielded piece by piece as it's written, so the app can show it growing."""
    messages = [{
        "role": "user",
        "content": f"Bot state:\n{json.dumps(context, default=str)}\n\nQuestion: {question}",
    }]
    wrote = False
    for _ in range(3):  # a long web search can pause the turn; carry on where it left off
        try:
            with client.beta.messages.stream(
                model=model,
                max_tokens=16000,
                system=ASK_SYSTEM_PROMPT,
                messages=messages,
                tools=[WEB_SEARCH_TOOL],
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        wrote = True
                        yield text
                response = stream.get_final_message()
        except AnalystUnavailable:
            raise
        except Exception as exc:
            raise AnalystUnavailable(plain_error(exc)) from exc
        if response.stop_reason == "refusal":
            raise AnalystUnavailable("The analyst declined to answer that")
        if response.stop_reason != "pause_turn":
            break
        messages = [*messages, {"role": "assistant", "content": response.content}]
    if not wrote:
        yield "Sorry, I couldn't come up with an answer. Try asking another way."
