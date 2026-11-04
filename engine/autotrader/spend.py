"""Monthly spending cap for the AI analyst (Claude API).

Every Claude call the server makes (stock reviews, the weekly lessons, the
app's chat) goes through ``MeteredClient``. It works out what each call cost
from the token counts Claude reports, adds it to this month's total, and
refuses new calls once the total reaches your cap. The month resets on the
1st (New York time does not matter here; it uses UTC).

While capped, the bot treats the analyst as switched off: penny stocks aren't
bought (they always need its approval) and other buys go ahead on the rules
alone, exactly as when Claude can't be reached. The idea comes from ruClip's
budget-gated agents: an agent stops when its budget is spent.

Before each call it also sets aside the most that call could cost (its whole
output allowance, plus the pages its web searches could add), and only starts
the call if that still fits under the cap. When the call finishes, the real
cost replaces the amount set aside. So calls running side by side can't push
the month past the cap. (The reserve-then-settle idea is from Rune's
budgeted AI proposals.)

Prices are list prices per million tokens and are an estimate: the Anthropic
Console's billing page is the real number.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone

DEFAULT_MONTHLY_CAP = 20.0

# Dollars per million tokens: (input, output). Cache reads/writes are priced from input.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
FALLBACK_PRICE = (5.0, 25.0)  # unknown model (e.g. an older Opus as a server-side fallback)


def price_for(model: str) -> tuple[float, float]:
    """List price for a model id, also matching dated snapshots such as "claude-haiku-5-5-2026...".

    Unknown models are priced high on purpose, so the cap errs toward stopping early.
    """
    for name, price in PRICES.items():
        if model == name or model.startswith(name + "-"):
            return price
    if "haiku" in model:
        return PRICES["claude-haiku-5-5"]
    return FALLBACK_PRICE
WEB_SEARCH_PER_CALL = 10.0 / 1000
SEARCH_RESULT_TOKENS = 30_000  # the most a web search is expected to add to the input
CHARS_PER_TOKEN = 3  # a cautious guess for sizing the request before it's sent
MAX_PROMPT_TOKENS = 200_000  # the context window: no request can send more than this


class SpendingCapReached(RuntimeError):
    pass


def most_it_could_cost(kwargs: dict) -> float:
    """The worst-case dollars for a request, worked out before it's sent."""
    price_in, price_out = price_for(kwargs.get("model") or "")
    searches = sum(int(t.get("max_uses", 5)) for t in kwargs.get("tools") or []
                   if isinstance(t, dict) and str(t.get("type", "")).startswith("web_search"))
    prompt = len(str(kwargs.get("system", "")) + str(kwargs.get("messages", ""))
                 + str(kwargs.get("tools", ""))) / CHARS_PER_TOKEN
    tokens_in = min(prompt, MAX_PROMPT_TOKENS) + searches * SEARCH_RESULT_TOKENS
    tokens_out = float(kwargs.get("max_tokens") or 0)
    return tokens_in * price_in / 1e6 + tokens_out * price_out / 1e6 + searches * WEB_SEARCH_PER_CALL


def cost_of(model: str, usage) -> float:
    """Estimated dollars for one response's usage."""
    if usage is None:
        return 0.0
    price_in, price_out = price_for(model or "")
    g = lambda name: float(getattr(usage, name, 0) or 0)  # noqa: E731
    tokens_in = (g("input_tokens") + 1.25 * g("cache_creation_input_tokens")
                 + 0.1 * g("cache_read_input_tokens"))
    dollars = tokens_in * price_in / 1e6 + g("output_tokens") * price_out / 1e6
    tools = getattr(usage, "server_tool_use", None)
    searches = float(getattr(tools, "web_search_requests", 0) or 0) if tools is not None else 0.0
    return round(dollars + searches * WEB_SEARCH_PER_CALL, 6)


class SpendTracker:
    """This month's Claude spending, kept in the journal so it survives restarts."""

    def __init__(self, journal, cap: Callable[[], float],
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.journal = journal
        self.cap = cap
        self.now = now
        self._lock = threading.Lock()
        self._reserved = 0.0  # set aside for calls still running

    def month(self) -> str:
        return self.now().strftime("%Y-%m")

    def spent(self) -> float:
        return self.journal.ai_spent(self.month())

    def check(self, amount: float = 0.0) -> None:
        cap = self.cap()
        if cap is None:
            return
        spent = self.spent()
        if spent >= cap:
            raise SpendingCapReached(
                f"The AI analyst reached your ${cap:,.2f} monthly spending cap, so it's paused until "
                "the 1st. Raise the cap in Settings to turn it back on sooner.")
        if spent + self._reserved + amount > cap:
            raise SpendingCapReached(
                f"The AI analyst is close to your ${cap:,.2f} monthly spending cap "
                f"(${spent:,.2f} used), and this request could cost up to ${amount:,.2f}, so it "
                "wasn't sent. Raise the cap in Settings, or it resets on the 1st.")

    def reserve(self, amount: float) -> None:
        """Set money aside for a call about to start, or raise if it might not fit under the cap."""
        with self._lock:
            self.check(amount)
            self._reserved += amount

    def release(self, amount: float) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - amount)

    def record(self, model: str, usage, what: str) -> float:
        dollars = cost_of(model, usage)
        if dollars:
            with self._lock:
                self.journal.record_ai_spend(self.month(), model, dollars, what)
        return dollars

    def report(self) -> dict:
        return {"month": self.month(), "spent": round(self.spent(), 2), "cap": self.cap()}


class _Messages:
    def __init__(self, inner, tracker: SpendTracker, what: Callable[[dict], str]) -> None:
        self.inner, self.tracker, self.what = inner, tracker, what

    def create(self, **kwargs):
        worst = most_it_could_cost(kwargs)
        self.tracker.reserve(worst)
        try:
            response = self.inner.create(**kwargs)
            model = getattr(response, "model", None) or kwargs.get("model", "")
            self.tracker.record(model, getattr(response, "usage", None), self.what(kwargs))
        finally:
            self.tracker.release(worst)
        return response


    def stream(self, **kwargs):
        """Like the SDK's messages.stream(), counted when the stream finishes."""
        worst = most_it_could_cost(kwargs)
        self.tracker.reserve(worst)
        try:
            inner = self.inner.stream(**kwargs)
        except BaseException:
            self.tracker.release(worst)
            raise
        return _MeteredStream(inner, self, kwargs, worst)


class _MeteredStream:
    def __init__(self, inner, messages: _Messages, kwargs: dict, worst: float) -> None:
        self.inner, self.messages, self.kwargs, self.worst = inner, messages, kwargs, worst
        self.stream = None

    def __enter__(self):
        try:
            self.stream = self.inner.__enter__()
        except BaseException:
            self.messages.tracker.release(self.worst)
            raise
        return self.stream

    def __exit__(self, *exc):
        try:
            final = None
            try:
                final = (self.stream.get_final_message() if exc[0] is None
                         else getattr(self.stream, "current_message_snapshot", None))
            except Exception:  # a stream that broke part way: count what it reported, if anything
                final = getattr(self.stream, "current_message_snapshot", None)
            if final is not None:
                model = getattr(final, "model", None) or self.kwargs.get("model", "")
                self.messages.tracker.record(model, getattr(final, "usage", None),
                                             self.messages.what(self.kwargs))
        finally:
            self.messages.tracker.release(self.worst)
        return self.inner.__exit__(*exc)


class MeteredClient:
    """Wraps an Anthropic client so every messages.create call is checked and counted."""

    def __init__(self, client, tracker: SpendTracker) -> None:
        self._client = client
        self.tracker = tracker
        self.messages = _Messages(client.messages, tracker, _what)
        self.beta = type("Beta", (), {})()
        self.beta.messages = _Messages(client.beta.messages, tracker, _what)


def _what(kwargs: dict) -> str:
    system = str(kwargs.get("system", ""))
    if "submit_lessons" in str(kwargs.get("tools", "")):
        return "lessons"
    if "submit_view" in str(kwargs.get("tools", "")):
        return "review"
    return "chat" if "Answer" in system else "other"
