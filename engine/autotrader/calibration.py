"""Is the analyst as sure as it should be? Its stated confidence against what really happened.

The analyst gives each review a confidence: how likely the stock is to be up a few weeks later.
Once a call is a month old the scorecard knows the answer. If its 70% calls only come good half
the time, it is overconfident, and the bot nudges its numbers down before comparing them with
the 55% (Test) or 60% (Live) bar; if it's too modest, up.

The nudge is one shift for all calls, shrunk toward zero when there are few graded calls (with
20 calls it moves half way to what the record says) and never more than 15 points either way,
so a handful of trades can't swing it. Below 10 graded calls there is no nudge at all.
"""

from __future__ import annotations

MIN_CALLS = 10  # graded calls before any adjustment
SHRINK = 20  # with this many calls, move half way to the record
MAX_SHIFT = 0.15
BINS = ((0.0, 0.5, "under 50%"), (0.5, 0.6, "50-60%"), (0.6, 0.7, "60-70%"), (0.7, 1.01, "70% or more"))


def calibrate(calls: list[dict]) -> dict:
    """calls: graded reviews, each with "confidence" (0-1) and "return" (the stock's, a month on)."""
    calls = [c for c in calls if c.get("confidence") is not None and c.get("return") is not None]
    n = len(calls)
    if not n:
        return {"calls": 0, "said": None, "right": None, "shift": 0.0, "brier": None, "bins": [],
                "note": "No graded calls yet."}
    said = sum(float(c["confidence"]) for c in calls) / n
    right = sum(c["return"] > 0 for c in calls) / n
    brier = sum((float(c["confidence"]) - (c["return"] > 0)) ** 2 for c in calls) / n
    weight = n / (n + SHRINK) if n >= MIN_CALLS else 0.0
    shift = max(-MAX_SHIFT, min(MAX_SHIFT, weight * (right - said)))
    bins = []
    for lo, hi, label in BINS:
        inside = [c for c in calls if lo <= float(c["confidence"]) < hi]
        if inside:
            bins.append({"label": label, "calls": len(inside),
                         "said": round(sum(float(c["confidence"]) for c in inside) / len(inside), 3),
                         "right": round(sum(c["return"] > 0 for c in inside) / len(inside), 3)})
    return {"calls": n, "said": round(said, 3), "right": round(right, 3), "shift": round(shift, 3),
            "brier": round(brier, 4), "bins": bins, "note": note(n, said, right, shift)}


def note(n: int, said: float, right: float, shift: float) -> str:
    if n < MIN_CALLS:
        return (f"Its calls average {said:.0%} sure and {right:.0%} came good, but {n} graded calls "
                f"are too few to adjust anything (needs {MIN_CALLS}).")
    if abs(shift) < 0.01:
        return f"Well calibrated: its calls average {said:.0%} sure and {right:.0%} came good."
    way = "overconfident" if shift < 0 else "too modest"
    return (f"A bit {way}: its calls average {said:.0%} sure but {right:.0%} came good, so its "
            f"confidence is adjusted by {shift * 100:+.0f} points before the bar is applied.")


def adjust(confidence: float, shift: float) -> float:
    return max(0.0, min(1.0, confidence + max(-MAX_SHIFT, min(MAX_SHIFT, shift))))
