import unittest

from autotrader.broker.sim import SimBroker
from autotrader.config import Mode, Settings
from autotrader.engine import Engine
from autotrader.journal import Journal

from helpers import downtrend, make_bars, uptrend


def engine_with(bars, cash=100_000, **kw):
    broker = SimBroker(cash, bars)
    settings = Settings(**kw).clamped()
    return Engine(broker, settings, Journal()), broker


class EngineTests(unittest.TestCase):
    def test_buys_uptrend_and_skips_downtrend(self):
        eng, broker = engine_with(
            {"AAPL": make_bars(uptrend()), "MSFT": make_bars(downtrend())},
        )
        report = eng.run_cycle()
        self.assertEqual([o.symbol for o in broker.orders], ["AAPL"])
        self.assertTrue(report.buys)
        self.assertEqual(len(eng.journal.trades("test")), 1)
        self.assertIsNotNone(eng.journal.get_stop("test", "AAPL"))

    def test_sells_when_trend_breaks(self):
        eng, broker = engine_with({"AAPL": make_bars(uptrend())})
        eng.run_cycle()
        crashed = uptrend()
        crashed[-1] = crashed[-1] * 0.80
        broker.bars["AAPL"] = make_bars(crashed)
        report = eng.run_cycle()
        self.assertTrue(report.sells)
        self.assertNotIn("AAPL", broker.holdings)
        self.assertIsNone(eng.journal.get_stop("test", "AAPL"))

    def test_never_buy_list(self):
        eng, broker = engine_with({"AAPL": make_bars(uptrend())}, never_buy=("AAPL",))
        eng.run_cycle()
        self.assertEqual(broker.orders, [])

    def test_live_approve_mode_queues_instead_of_buying(self):
        eng, broker = engine_with({"AAPL": make_bars(uptrend())}, mode=Mode.LIVE)
        report = eng.run_cycle()
        self.assertEqual(broker.orders, [])
        self.assertEqual(len(report.queued), 1)
        self.assertEqual(len(eng.journal.pending_approvals("live")), 1)
        # A second cycle must not queue the same stock again.
        eng.run_cycle()
        self.assertEqual(len(eng.journal.pending_approvals("live")), 1)

    def test_test_and_live_journals_stay_separate(self):
        eng, _ = engine_with({"AAPL": make_bars(uptrend())})
        eng.run_cycle()
        self.assertEqual(eng.journal.trades("live"), [])

    def test_budget_respected(self):
        eng, broker = engine_with({"AAPL": make_bars(uptrend())}, budget=2_000)
        eng.run_cycle()
        spent = sum(o.qty for o in broker.orders) * broker.price("AAPL")
        self.assertLessEqual(spent, 2_000 * 0.05 + 1e-6)


if __name__ == "__main__":
    unittest.main()
