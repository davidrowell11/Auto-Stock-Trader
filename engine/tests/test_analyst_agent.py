"""The analyst as an agent: the desk check it gets, and its look_up_stock tool loop."""

import json
import unittest
from types import SimpleNamespace

from autotrader import desk
from autotrader.analyst import MAX_LOOKUPS, Analyst, AnalystUnavailable, AnalystView
from autotrader.config import Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.market import MARKET, market_mood
from autotrader.models import Position

from helpers import downtrend, make_bars, uptrend
from test_final_check import SlowBroker


def submit(action="buy", confidence=0.7):
    return SimpleNamespace(type="tool_use", name="submit_view", id="s1",
                           input={"action": action, "confidence": confidence,
                                  "reasoning": "ok", "red_flags": []})


def look_up(symbol, id_="t1"):
    return SimpleNamespace(type="tool_use", name="look_up_stock", id=id_, input={"symbol": symbol})


def scripted_client(*responses):
    calls = []

    class Messages:
        def create(self, **kw):
            calls.append(kw)
            return responses[len(calls) - 1]

    return SimpleNamespace(beta=SimpleNamespace(messages=Messages())), calls


class DeskCheckTests(unittest.TestCase):
    def test_stock_facts_on_a_steady_uptrend(self):
        bars = make_bars(uptrend(daily=0.003))
        market = make_bars(uptrend(daily=0.001))
        f = desk.stock_facts("AAPL", bars, market)
        self.assertGreater(f["above_50_day_average"], 0)
        self.assertGreater(f["above_200_day_average"], f["above_50_day_average"])
        self.assertGreater(f["return_3_months_vs_market"], 0)  # beating the market
        self.assertAlmostEqual(f["below_52_week_high"], -0.01 / 1.01, places=3)  # at its high
        self.assertEqual(f["volume_vs_20_day_average"], 1.0)
        self.assertEqual(desk.stock_facts("X", [])["error"], "no price history")

    def test_portfolio_facts_find_the_same_sector_and_the_twin(self):
        bars = {"AAPL": make_bars(uptrend()), "MSFT": make_bars(uptrend()),
                "XOM": make_bars(downtrend())}
        held = [Position("MSFT", 10, 100, 110), Position("XOM", 10, 50, 50)]
        sectors = {"AAPL": "Technology", "MSFT": "Technology", "XOM": "Energy"}
        f = desk.portfolio_facts("AAPL", "Technology", bars, held, 10_000, sectors.get)
        self.assertEqual(f["held_in_same_sector"], ["MSFT"])
        self.assertAlmostEqual(f["share_of_budget_in_sector"], 0.11)
        self.assertEqual(f["stocks_held"], 2)

    def test_desk_check_includes_the_market(self):
        bars = {"AAPL": make_bars(uptrend()), MARKET: make_bars(uptrend())}
        check = desk.desk_check("AAPL", bars, [], 10_000, "Technology", lambda s: "Technology",
                                market_mood(bars[MARKET]))
        self.assertEqual(check["market"]["mood"], "calm")
        self.assertIn("above_200_day_average", check["market"])
        self.assertIn("units", check)


class AgentLoopTests(unittest.TestCase):
    def test_looks_up_a_stock_then_submits(self):
        client, calls = scripted_client(
            SimpleNamespace(stop_reason="tool_use", content=[look_up("xlk")]),
            SimpleNamespace(stop_reason="tool_use", content=[submit()]))
        asked = []
        view = Analyst(client=client).review({"symbol": "AAPL"},
                                             lookup=lambda s: asked.append(s) or {"symbol": s})
        self.assertTrue(view.approves)
        self.assertEqual(asked, ["XLK"])
        result = calls[1]["messages"][-1]["content"][0]
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["tool_use_id"], "t1")
        self.assertEqual(json.loads(result["content"]), {"symbol": "XLK"})
        names = [t["name"] for t in calls[0]["tools"]]
        self.assertIn("look_up_stock", names)
        self.assertEqual(calls[0]["thinking"], {"type": "adaptive"})

    def test_look_ups_are_capped_and_bad_input_is_answered_not_crashed(self):
        rounds = [SimpleNamespace(stop_reason="tool_use", content=[look_up(f"S{i}", f"t{i}")])
                  for i in range(MAX_LOOKUPS + 1)]
        client, calls = scripted_client(*rounds,
                                        SimpleNamespace(stop_reason="tool_use", content=[submit()]))
        asked = []
        Analyst(client=client).review({"symbol": "AAPL"}, lookup=lambda s: asked.append(s) or {})
        self.assertEqual(len(asked), MAX_LOOKUPS)
        self.assertIn("No more look-ups", calls[-1]["messages"][-1]["content"][0]["content"])

        bad = SimpleNamespace(stop_reason="tool_use", content=[look_up("rm -rf /", "tb")])
        client, calls = scripted_client(bad, SimpleNamespace(stop_reason="tool_use", content=[submit()]))
        asked = []
        Analyst(client=client).review({"symbol": "AAPL"}, lookup=lambda s: asked.append(s) or {})
        self.assertEqual(asked, [])
        self.assertIn("isn't a ticker", calls[-1]["messages"][-1]["content"][0]["content"])

    def test_a_failing_look_up_is_reported_to_the_analyst(self):
        client, calls = scripted_client(
            SimpleNamespace(stop_reason="tool_use", content=[look_up("ZZZZ")]),
            SimpleNamespace(stop_reason="tool_use", content=[submit("skip", 0.4)]))

        def broken(_):
            raise KeyError("no bars")

        view = Analyst(client=client).review({"symbol": "AAPL"}, lookup=broken)
        self.assertFalse(view.approves)
        self.assertEqual(calls[1]["messages"][-1]["content"][0]["content"], "No data for ZZZZ.")

    def test_quick_review_has_no_look_ups_or_thinking(self):
        client, calls = scripted_client(SimpleNamespace(stop_reason="tool_use", content=[submit()]))
        Analyst(client=client).review({"symbol": "AAPL"}, quick=True, lookup=lambda s: {})
        self.assertNotIn("look_up_stock", [t["name"] for t in calls[0]["tools"]])
        self.assertNotIn("thinking", calls[0])

    def test_carries_on_without_thinking_if_a_model_refuses_it(self):
        calls = []

        class Rejected(Exception):
            status_code = 400

        class Messages:
            def create(self, **kw):
                calls.append(kw)
                if "thinking" in kw:
                    raise Rejected("thinking type adaptive is not supported")
                return SimpleNamespace(stop_reason="tool_use", content=[submit()])

        client = SimpleNamespace(beta=SimpleNamespace(messages=Messages()))
        self.assertTrue(Analyst(client=client).review({"symbol": "AAPL"}).approves)
        self.assertEqual(len(calls), 2)

    def test_gives_up_after_too_many_turns(self):
        endless = SimpleNamespace(stop_reason="tool_use", content=[look_up("XLK")])
        client, _ = scripted_client(*[endless] * 10)
        with self.assertRaises(AnalystUnavailable):
            Analyst(client=client).review({"symbol": "AAPL"}, lookup=lambda s: {})


class EngineDeskCheckTests(unittest.TestCase):
    def test_the_analyst_gets_the_desk_check_and_a_working_look_up(self):
        seen = {}

        class AgentAnalyst:
            supports_lookup = True

            def review(self, candidate, lookup=None):
                seen["candidate"] = candidate
                seen["msft"] = lookup("MSFT")
                return AnalystView("buy", 0.9, "fine", [])

        broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend()), MARKET: make_bars(uptrend()),
                                      "MSFT": make_bars(uptrend())})
        Engine(broker, Settings().clamped(), Journal(), analyst=AgentAnalyst(),
               screener=lambda: []).run_cycle()
        check = seen["candidate"]["desk_check"]
        self.assertEqual(check["stock"]["symbol"], seen["candidate"]["symbol"])
        self.assertEqual(check["market"]["mood"], "calm")
        self.assertEqual(seen["msft"]["symbol"], "MSFT")
        self.assertIn("return_3_months_vs_market", seen["msft"])

    def test_an_older_analyst_without_look_ups_still_works(self):
        seen = []

        class OldAnalyst:
            def review(self, candidate):
                seen.append(candidate)
                return AnalystView("buy", 0.9, "fine", [])

        broker = SlowBroker(100_000, {"AAPL": make_bars(uptrend()), MARKET: make_bars(uptrend())})
        Engine(broker, Settings().clamped(), Journal(), analyst=OldAnalyst()).run_cycle()
        self.assertIn("desk_check", seen[0])


if __name__ == "__main__":
    unittest.main()
