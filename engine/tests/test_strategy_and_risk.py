import unittest

from autotrader import penny, strategy
from autotrader.config import HARD_LIMITS, Settings, load_settings
from autotrader.models import Account, Asset, Position
from autotrader.risk import RiskManager

from helpers import downtrend, make_bars, uptrend


class StrategyTests(unittest.TestCase):
    def test_uptrend_gives_signal(self):
        sig = strategy.score_trend("ABC", make_bars(uptrend()))
        self.assertIsNotNone(sig)
        self.assertGreater(sig.score, 0.5)
        self.assertTrue(sig.reasons)

    def test_downtrend_gives_no_signal(self):
        self.assertIsNone(strategy.score_trend("ABC", make_bars(downtrend())))

    def test_not_enough_history(self):
        self.assertIsNone(strategy.score_trend("ABC", make_bars(uptrend(100))))

    def test_initial_stop_never_more_than_hard_limit(self):
        stop = strategy.initial_stop(100.0, atr=20.0, multiple=2.0)
        self.assertAlmostEqual(stop, 100.0 * (1 - HARD_LIMITS.max_stop_loss_pct))

    def test_trailing_stop_only_moves_up(self):
        self.assertEqual(strategy.trail_stop(95.0, 110.0, 2.0, 2.0), 106.0)
        self.assertEqual(strategy.trail_stop(106.0, 100.0, 2.0, 2.0), 106.0)

    def test_exit_when_trend_breaks(self):
        closes = uptrend(200) + [uptrend(200)[-1] * 0.85]
        reason = strategy.exit_reason(make_bars(closes), stop_price=1.0)
        self.assertIn("Trend broke", reason)


class SettingsTests(unittest.TestCase):
    def test_hard_limits_cannot_be_loosened(self):
        s = load_settings(env={
            "AUTOTRADER_MAX_POSITION_PCT": "0.5",
            "AUTOTRADER_DAILY_LOSS_PCT": "0.2",
            "AUTOTRADER_CASH_RESERVE_PCT": "0",
            "AUTOTRADER_PENNY_MAX_TOTAL_PCT": "0.9",
        })
        self.assertEqual(s.max_position_pct, HARD_LIMITS.max_position_pct)
        self.assertEqual(s.daily_loss_pct, HARD_LIMITS.max_daily_loss_pct)
        self.assertEqual(s.cash_reserve_pct, HARD_LIMITS.min_cash_reserve_pct)
        self.assertEqual(s.penny_max_total_pct, HARD_LIMITS.penny_max_total_pct)

    def test_lists_and_budget_from_env(self):
        s = load_settings(env={"AUTOTRADER_BUDGET": "5000", "AUTOTRADER_NEVER_BUY": "gme, amc"})
        self.assertEqual(s.budget, 5000.0)
        self.assertEqual(s.never_buy, ("GME", "AMC"))


class RiskTests(unittest.TestCase):
    def rm(self, equity=100_000, cash=100_000, last=None, positions=(), peak=0, **kw):
        acct = Account(equity, cash, last if last is not None else equity)
        return RiskManager(Settings(**kw).clamped(), acct, positions, peak)

    def test_position_capped_at_5_percent(self):
        d = self.rm().size_buy("ABC", 100.0, "Technology", penny=False)
        self.assertEqual(d.qty, 50)  # 5% of $100k / $100

    def test_budget_limits_sizing(self):
        d = self.rm(budget=10_000).size_buy("ABC", 10.0, "Technology", penny=False)
        self.assertEqual(d.qty, 50)  # 5% of $10k / $10

    def test_penny_position_capped_at_2_percent(self):
        d = self.rm().size_buy("PNY", 2.0, "Unknown", penny=True)
        self.assertEqual(d.qty, 1000)  # 2% of $100k / $2

    def test_daily_loss_cap_blocks_buys(self):
        r = self.rm(equity=97_000, cash=97_000, last=100_000)
        self.assertIn("Daily loss", r.buying_halted())
        self.assertEqual(r.size_buy("ABC", 10.0, "Tech", False).qty, 0)

    def test_drawdown_brake_halves_size_then_pauses(self):
        self.assertEqual(self.rm(equity=89_000, cash=89_000, peak=100_000).size_multiplier, 0.5)
        self.assertIn("Paused", self.rm(equity=84_000, cash=84_000, peak=100_000).buying_halted())

    def test_sector_limit(self):
        held = [Position(f"T{i}", 100, 50, 50, "Technology") for i in range(5)]  # $25k tech
        d = self.rm(cash=75_000, positions=held).size_buy("NEW", 50.0, "Technology", False)
        self.assertEqual(d.qty, 0)
        self.assertIn("sector", d.note)

    def test_cash_reserve_kept(self):
        d = self.rm(cash=12_000).size_buy("ABC", 100.0, "Technology", False)
        self.assertEqual(d.qty, 20)  # $12k cash - $10k reserve = $2k


class PennyTests(unittest.TestCase):
    def setUp(self):
        self.s = Settings()

    def test_pump_is_rejected(self):
        closes = uptrend(255, start=1.0) + [2.0, 2.4, 2.8, 3.2, 3.6]
        bars = make_bars(closes, volume=5_000_000)
        sig = strategy.score_trend("PMP", bars)
        problems = penny.penny_buy_problems(Asset("PMP", "NASDAQ"), bars, sig, self.s)
        self.assertTrue(any("pump" in p for p in problems))

    def test_low_volume_and_otc_rejected(self):
        bars = make_bars(uptrend(260, start=1.0), volume=1_000)
        sig = strategy.score_trend("LOW", bars)
        problems = penny.penny_buy_problems(Asset("LOW", "OTC"), bars, sig, self.s)
        self.assertTrue(any("OTC" in p for p in problems))
        self.assertTrue(any("trading" in p for p in problems))

    def test_volume_drying_up(self):
        bars = make_bars([2.0] * 55, volume=1_000_000)
        bars[-5:] = make_bars([2.0] * 5, volume=100_000)
        self.assertTrue(penny.volume_drying_up(bars))


if __name__ == "__main__":
    unittest.main()
