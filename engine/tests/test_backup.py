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

    def test_a_backup_problem_never_stops_the_bot(self):
        holder = [Journal()]
        _, _, b = self.make(holder)
        self.store.put = mock.Mock(side_effect=RuntimeError("database down"))
        self.assertEqual(b.save(), [])  # logged, not raised

    def test_no_database_means_no_backup(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(backup.from_environment(Path("j"), Path("s"), lambda: b""))


if __name__ == "__main__":
    unittest.main()
