"""Monthly spending cap for the AI analyst (Claude API).

Every Claude call the server makes (stock reviews, the weekly lessons, the
app's chat) goes through ``MeteredClient``. It works out what each call cost
from the token counts Claude reports, adds it to this month's total, and
refuses new calls once the total reaches your cap. The month resets on the
1st (New York time does not matter here; it uses UTC).

While capped, the bot treats the analyst as switched off: penny stocks aren't
bought (they always need its approval) and other buys go ahead on the rules
alone, exactly as when Claude can't be reached. The idea comes from ruClip's
budget-gated agents: an agent stops when its budget is spent. Calls already
running when the cap is reached still finish, so a month can end a few
cents over.

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


class SpendingCapReached(RuntimeError):
    pass


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

    def month(self) -> str:
        return self.now().strftime("%Y-%m")

    def spent(self) -> float:
        return self.journal.ai_spent(self.month())

    def check(self) -> None:
        cap = self.cap()
        if cap is not None and self.spent() >= cap:
            raise SpendingCapReached(
                f"The AI analyst reached your ${cap:,.2f} monthly spending cap, so it's paused until "
                "the 1st. Raise the cap in Settings to turn it back on sooner.")

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
        self.tracker.check()
        response = self.inner.create(**kwargs)
        model = getattr(response, "model", None) or kwargs.get("model", "")
        self.tracker.record(model, getattr(response, "usage", None), self.what(kwargs))
        return response


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
