import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autotrader import backup
from autotrader.journal import Journal, StopState


class DictStore:
    def __init__(self):
        self.files, self.puts = {}, 0

    def get(self, name):
        return self.files.get(name)

    def put(self, name, data):
        self.files[name] = data
        self.puts += 1


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = DictStore()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, journal_holder):
        path = self.dir / "data" / "journal.sqlite3"
        settings = self.dir / "data" / "app_settings.json"
        return path, settings, backup.Backup(self.store, {
            "journal.sqlite3": (path, lambda: journal_holder[0].snapshot()),
            "app_settings.json": (settings, lambda: settings.read_bytes() if settings.exists() else None),
        })

    def test_a_publish_no_longer_wipes_the_bots_memory(self):
        holder = [None]
        path, settings, b = self.make(holder)
        self.assertEqual(b.restore(), [])  # first start: nothing saved yet
        holder[0] = Journal(path)
        holder[0].save_stop("test", "KO", StopState(96.0, 90.0, 96.0, qty=52))
        settings.write_text('{"test": {"budget": 5000}}')
        self.assertEqual(sorted(b.save()), ["app_settings.json", "journal.sqlite3"])
        self.assertEqual(b.save(), [])  # nothing changed: nothing copied

        holder[0].db.close()
        for f in (path, settings):  # the publish wipes the files
            f.unlink()
        _, _, fresh = self.make(holder)
        self.assertEqual(sorted(fresh.restore()), ["app_settings.json", "journal.sqlite3"])
        holder[0] = Journal(path)
        self.assertEqual(holder[0].get_stop("test", "KO").qty, 52)
        self.assertIn("5000", settings.read_text())

    def test_files_already_there_are_kept(self):
        holder = [None]
        path, settings, b = self.make(holder)
        settings.parent.mkdir(parents=True)
        settings.write_text("local")
        self.store.files["app_settings.json"] = b"older copy"
        b.restore()
        self.assertEqual(settings.read_text(), "local")

    def test_a_database_error_at_start_never_stops_the_bot(self):
        _, _, b = self.make([None])
        self.store.get = mock.Mock(side_effect=RuntimeError("timeout"))
        self.assertEqual(b.restore(), [])  # logged; the bot starts with empty files

    def test_a_backup_problem_never_stops_the_bot(self):
        holder = [Journal()]
        _, _, b = self.make(holder)
        self.store.put = mock.Mock(side_effect=RuntimeError("database down"))
        self.assertEqual(b.save(), [])  # logged, not raised

    def test_published_and_workspace_copies_stay_apart(self):
        with mock.patch.object(backup, "PostgresStore", lambda url: self.store):
            with mock.patch.dict("os.environ", {"DATABASE_URL": "x", "REPLIT_DEPLOYMENT": "1"}):
                published = backup.from_environment(Path("j"), Path("s"), lambda: b"")
            with mock.patch.dict("os.environ", {"DATABASE_URL": "x"}, clear=True):
                workspace = backup.from_environment(Path("j"), Path("s"), lambda: b"")
        self.assertEqual(sorted(published.sources), ["published/app_settings.json",
                                                     "published/journal.sqlite3"])
        self.assertTrue(all(n.startswith("workspace/") for n in workspace.sources))

    def test_no_database_means_no_backup(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(backup.from_environment(Path("j"), Path("s"), lambda: b""))


class SeedEquityTests(unittest.TestCase):
    def test_a_fresh_history_starts_from_the_brokers(self):
        from datetime import datetime, timezone

        j = Journal()
        days = [(datetime(2026, 11, d, 21, tzinfo=timezone.utc), 100_000.0 + d) for d in (2, 3)]
        self.assertEqual(j.seed_equity("test", days), 2)
        self.assertEqual(j.peak_equity("test"), 100_003)
        self.assertEqual(j.seed_equity("test", days), 0)  # only ever once
        self.assertEqual(len(j.equity_history("test")), 2)


if __name__ == "__main__":
    unittest.main()
