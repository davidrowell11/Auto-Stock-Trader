import tempfile
import threading
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # server extras not installed
    TestClient = None

from autotrader.broker.sim import SimBroker
from autotrader.config import Mode
from autotrader.journal import Journal
from autotrader.models import Asset

from helpers import make_bars, uptrend

FRACTIONAL = {"AAPL": Asset("AAPL", exchange="NASDAQ", fractionable=True)}
TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@unittest.skipIf(TestClient is None, "fastapi not installed")
class ServerTests(unittest.TestCase):
    def setUp(self):
        from autotrader.server import BotRunner, SettingsStore, create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Journal()
        self.store = SettingsStore(f"{self.tmp.name}/app_settings.json")
        lock = threading.RLock()
        self.brokers = {m: SimBroker(100_000, {"AAPL": make_bars(uptrend())}, assets=FRACTIONAL) for m in Mode}
        self.runners = {m: BotRunner(m, self.store, self.journal, lock, lambda mode: self.brokers[mode])
                        for m in Mode}
        self.app = create_app(self.runners, self.journal, TOKEN,
                              ask_fn=lambda q, ctx: f"echo: {q}", live_enabled=True)
        self.c = TestClient(self.app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_auth_required(self):
        self.assertEqual(self.c.get("/health").status_code, 200)
        self.assertEqual(self.c.get("/api/test/summary").status_code, 401)
        self.assertEqual(self.c.get("/api/test/summary", headers={"Authorization": "Bearer nope"}).status_code, 401)
        self.assertEqual(self.c.get("/api/test/summary", headers=AUTH).status_code, 200)

    def test_bad_mode(self):
        self.assertEqual(self.c.get("/api/paper/summary", headers=AUTH).status_code, 404)

    def test_budget_setting_flows_into_bot(self):
        r = self.c.put("/api/test/settings", headers=AUTH, json={"budget": 2000, "never_buy": ["gme"]})
        self.assertEqual(r.json()["budget"], 2000)
        self.assertEqual(r.json()["never_buy"], ["GME"])
        self.assertEqual(self.runners[Mode.TEST].settings.budget, 2000)
        self.assertEqual(self.runners[Mode.LIVE].settings.budget, 1000)  # Live starts small, separate from Test
        self.c.put("/api/test/settings", headers=AUTH, json={"clear_budget": True})
        self.assertIsNone(self.runners[Mode.TEST].settings.budget)

    def test_cycle_then_positions_and_summary(self):
        self.runners[Mode.TEST].run_once()
        pos = self.c.get("/api/test/positions", headers=AUTH).json()
        self.assertEqual(pos[0]["symbol"], "AAPL")
        self.assertIsNotNone(pos[0]["stop"])
        self.assertTrue(pos[0]["why_bought"])
        s = self.c.get("/api/test/summary", headers=AUTH).json()
        self.assertEqual(s["positions"], 1)
        self.assertTrue(self.c.get("/api/test/events", headers=AUTH).json())

    def test_live_approval_flow(self):
        self.runners[Mode.LIVE].run_once()  # approve mode is on by default in Live
        self.assertEqual(self.brokers[Mode.LIVE].orders, [])
        pending = self.c.get("/api/live/approvals", headers=AUTH).json()
        self.assertEqual(len(pending), 1)
        r = self.c.post(f"/api/live/approvals/{pending[0]['id']}", headers=AUTH, json={"decision": "approve"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.brokers[Mode.LIVE].orders), 1)
        again = self.c.post(f"/api/live/approvals/{pending[0]['id']}", headers=AUTH, json={"decision": "approve"})
        self.assertEqual(again.status_code, 409)
        # A Test-mode request can't touch a Live approval.
        self.assertEqual(self.c.post(f"/api/test/approvals/{pending[0]['id']}", headers=AUTH,
                                     json={"decision": "reject"}).status_code, 404)

    def test_kill_switch_stops_and_sells(self):
        self.c.post("/api/test/start", headers=AUTH)
        self.runners[Mode.TEST].run_once()
        r = self.c.post("/api/test/kill", headers=AUTH, json={"sell_all": True}).json()
        self.assertEqual(r["sold"], ["AAPL"])
        self.assertFalse(self.runners[Mode.TEST].running)
        self.assertEqual(self.brokers[Mode.TEST].holdings, {})

    def test_live_start_blocked_when_disabled(self):
        from autotrader.server import create_app

        app = create_app(self.runners, self.journal, TOKEN, live_enabled=False)
        self.assertEqual(TestClient(app).post("/api/live/start", headers=AUTH).status_code, 403)

    def test_ask_and_bars(self):
        self.assertEqual(self.c.post("/api/ask", headers=AUTH, json={"question": "hi"}).json()["answer"], "echo: hi")
        self.assertEqual(len(self.c.get("/api/bars/AAPL?days=30", headers=AUTH).json()), 30)

    def test_ask_streams_the_answer_line_by_line(self):
        import json

        from autotrader.analyst import AnalystUnavailable
        from autotrader.server import create_app

        def lines(app):
            r = TestClient(app).post("/api/ask/stream", headers=AUTH, json={"question": "hi"})
            self.assertEqual(r.status_code, 200)
            return [json.loads(x) for x in r.text.splitlines() if x]

        # Without a streaming analyst, the whole answer comes as one piece.
        self.assertEqual(lines(self.app), [{"text": "echo: hi"}, {"done": True}])

        def pieces(q, ctx):
            yield "Hello "
            yield "there."
            raise AnalystUnavailable("The analyst reached your monthly spending cap")

        app = create_app(self.runners, self.journal, TOKEN, ask_fn=lambda q, c: "",
                         ask_stream_fn=pieces, live_enabled=True)
        self.assertEqual(lines(app), [{"text": "Hello "}, {"text": "there."},
                                      {"error": "The analyst reached your monthly spending cap"},
                                      {"done": True}])

    def test_weekly_report(self):
        self.runners[Mode.TEST].run_once()
        rep = self.c.get("/api/test/report/weekly", headers=AUTH).json()
        self.assertEqual(rep["buys"], 1)


if __name__ == "__main__":
    unittest.main()
