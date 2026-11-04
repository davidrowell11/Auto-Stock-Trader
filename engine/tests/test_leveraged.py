import unittest

from autotrader.models import Asset


class LeveragedFundTests(unittest.TestCase):
    def test_leveraged_and_inverse_funds_are_spotted(self):
        for name in ("Direxion Daily Semiconductor Bear 3X Shares", "ProShares UltraPro QQQ",
                     "ProShares Short S&P500", "Direxion Daily TSLA Bull 2X Shares",
                     "T-Rex 2X Long MSTR Daily Target ETF", "ProShares UltraShort Bloomberg Crude Oil",
                     "MicroSectors FANG+ Index 3X Leveraged ETNs", "ProShares Ultra S&P500"):
            self.assertTrue(Asset("X", name=name).is_leveraged_fund, name)

    def test_ordinary_stocks_and_funds_are_not(self):
        for name in ("SPDR S&P 500 ETF Trust", "Invesco QQQ Trust, Series 1", "Apple Inc. Common Stock",
                     "iShares Russell 2000 ETF", "Bull Horn Holdings Inc.", "",
                     "Vanguard Short-Term Bond ETF", "iShares Short Treasury Bond ETF",
                     "Ultrapar Participacoes S.A. American Depositary Shares"):
            self.assertFalse(Asset("X", name=name).is_leveraged_fund, name)


if __name__ == "__main__":
    unittest.main()
