import random
import unittest

from autotrader.broker.sim import SimBroker
from autotrader.config import Settings
from autotrader.engine import Engine
from autotrader.journal import Journal
from autotrader.risk import correlation

from helpers import make_bars


def wiggly(seed, n=260, drift=0.003):
    rng, price, out = random.Random(seed), 50.0, []
    for _ in range(n):
        price *= 1 + drift + rng.uniform(-0.01, 0.01)
        out.append(price)
    return out


class CorrelationTests(unittest.TestCase):
    def test_correlation(self):
        a = [0.01, -0.02, 0.03, 0.0, 0.01]
        self.assertAlmostEqual(correlation(a, a), 1.0)
        self.assertAlmostEqual(correlation(a, [-x for x in a]), -1.0)
        self.assertIsNone(correlation(a, [0.01] * 5))  # never moves

    def test_twin_of_a_holding_shares_its_sector_limit(self):
        aapl = wiggly(1)
        twin = [c * 0.5 for c in aapl]  # an unlisted stock that moves exactly like AAPL
        broker = SimBroker(100_000, {"AAPL": make_bars(aapl)})
        eng = Engine(broker, Settings(max_sector_pct=0.03).clamped(), Journal())
        eng.run_cycle()
        self.assertEqual([o.symbol for o in broker.orders], ["AAPL"])

        broker.bars["TWIN"] = make_bars(twin)
        eng.s = Settings(max_sector_pct=0.03, always_watch=("TWIN",)).clamped()
        report = eng.run_cycle()
        self.assertEqual([o.symbol for o in broker.orders], ["AAPL", "TWIN"])
        buy = eng.journal.trades("test")[-1]
        self.assertIn("Moves closely with AAPL", " ".join(buy["reasons"]))
        # AAPL took about 2.4% of the 3% Technology limit, so its twin only gets what's left.
        aapl_value = broker.orders[0].qty * aapl[-1]
        self.assertLessEqual(broker.orders[-1].qty * twin[-1], 100_000 * 0.03 - aapl_value + 1)
        self.assertIn("limited by limit it shares with AAPL", " ".join(buy["reasons"]))
        self.assertFalse(report.skipped and any("TWIN" in s for s in report.skipped))

    def test_unrelated_stock_keeps_its_own_limit(self):
        broker = SimBroker(100_000, {"AAPL": make_bars(wiggly(1))})
        eng = Engine(broker, Settings().clamped(), Journal())
        eng.run_cycle()
        broker.bars["OTHER"] = make_bars(wiggly(2))
        eng.s = Settings(always_watch=("OTHER",)).clamped()
        eng.run_cycle()
        buy = eng.journal.trades("test")[-1]
        self.assertEqual(buy["symbol"], "OTHER")
        self.assertNotIn("Moves closely", " ".join(buy["reasons"]))


class SharedLimitTests(unittest.TestCase):
    def test_its_own_sector_limit_still_applies(self):
        from autotrader.models import Account, Position
        from autotrader.risk import RiskManager

        moves = make_bars(wiggly(1))
        bars = {"XOM": moves, "MSFT": make_bars([c * 2 for c in wiggly(1)])}
        held = [Position("XOM", 10, 100, 100, "Energy"),  # small energy holding
                Position("AAPL", 25, 100, 100, "Technology")]  # tech already at 2.5%
        risk = RiskManager(Settings(max_sector_pct=0.025).clamped(),
                           Account(equity=100_000, cash=90_000, last_equity=100_000), held, 100_000)
        size = risk.size_buy("MSFT", 200, "Technology", False, bars=bars)
        self.assertEqual(size.qty, 0)  # moves with XOM, but Technology is full
        self.assertIn("Moves closely with XOM", size.twin)


class StaleDataTests(unittest.TestCase):
    def test_stock_with_old_prices_isnt_bought(self):
        spy = make_bars(wiggly(3))
        halted = make_bars(wiggly(1))[:-5]  # stopped trading five days ago
        broker = SimBroker(100_000, {"SPY": spy, "HALT": halted})
        eng = Engine(broker, Settings(always_watch=("HALT",)).clamped(), Journal())
        report = eng.run_cycle()
        self.assertNotIn("HALT", [o.symbol for o in broker.orders])
        self.assertTrue(any("HALT: its price data is out of date" in s for s in report.skipped))


if __name__ == "__main__":
    unittest.main()
