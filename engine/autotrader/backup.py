"""Keep the bot's memory safe when the server is republished.

Replit wipes a published app's files every time it is published. The bot's
journal (its trades, safety stops, analyst reviews, lessons and AI spending)
and the app settings live in files, so each publish used to erase them, and
the bot lost track of stocks it had bought.

This copies those files into Replit's database (Postgres, found through
DATABASE_URL) every minute when something changed, and once more when the
server shuts down. On start, any file that is missing is put back from the
database before the bot opens it.

Without DATABASE_URL nothing is copied, and the files work as before.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

EVERY = 60  # seconds between checks for changes


class Store(Protocol):
    def get(self, name: str) -> bytes | None: ...

    def put(self, name: str, data: bytes) -> None: ...


class PostgresStore:
    """Files kept as rows in one table of the app's Postgres database."""

    def __init__(self, url: str) -> None:
        import psycopg

        self._connect = lambda: psycopg.connect(url, connect_timeout=10)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS autotrader_files ("
                         " name TEXT PRIMARY KEY, data BYTEA NOT NULL,"
                         " saved_at TIMESTAMPTZ NOT NULL DEFAULT now())")

    def get(self, name: str) -> bytes | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM autotrader_files WHERE name = %s",
                               (name,)).fetchone()
        return bytes(row[0]) if row else None

    def put(self, name: str, data: bytes) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO autotrader_files (name, data) VALUES (%s, %s)"
                         " ON CONFLICT (name) DO UPDATE SET data = excluded.data, saved_at = now()",
                         (name, data))


class Backup:
    """Copies named files to a store, and puts missing ones back.

    ``sources`` maps each name to the local path and a function that reads the
    file's current contents (for the journal, a consistent copy taken from the
    open database rather than the file on disk).
    """

    def __init__(self, store: Store, sources: dict[str, tuple[Path, Callable[[], bytes | None]]]):
        self.store = store
        self.sources = sources
        self._saved: dict[str, str] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def restore(self) -> list[str]:
        """Put back every file that is missing locally. Returns the names restored."""
        restored = []
        for name, (path, _) in self.sources.items():
            if path.exists() and path.stat().st_size > 0:
                continue
            data = self.store.get(name)
            if data:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(path.suffix + ".restoring")
                tmp.write_bytes(data)
                tmp.replace(path)
                self._saved[name] = hashlib.sha256(data).hexdigest()
                restored.append(name)
        return restored

    def save(self) -> list[str]:
        """Copy every file that changed since the last copy. Returns the names saved."""
        saved = []
        with self._lock:
            for name, (_, read) in self.sources.items():
                try:
                    data = read()
                    if not data:
                        continue
                    digest = hashlib.sha256(data).hexdigest()
                    if self._saved.get(name) == digest:
                        continue
                    self.store.put(name, data)
                    self._saved[name] = digest
                    saved.append(name)
                except Exception as exc:  # never let a backup problem stop trading
                    log.warning("Couldn't back up %s: %s", name, exc)
        return saved

    def run(self) -> None:
        while not self._stop.wait(EVERY):
            self.save()

    def start(self) -> None:
        threading.Thread(target=self.run, name="backup", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.save()


def from_environment(journal_path: Path, settings_path: Path,
                     journal_snapshot: Callable[[], bytes]) -> Backup | None:
    """The backup for this server, or None when there's no database to copy to."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        log.warning("No DATABASE_URL: the bot's memory will be lost the next time it's published")
        return None

    def read_file(path: Path) -> Callable[[], bytes | None]:
        return lambda: path.read_bytes() if path.exists() else None

    try:
        store = PostgresStore(url)
    except Exception as exc:
        log.warning("Couldn't reach the database for backups: %s", exc)
        return None
    # The published bot and a copy running in the Replit workspace may share one database:
    # each keeps its own files so neither overwrites the other's memory.
    where = "published" if os.environ.get("REPLIT_DEPLOYMENT") else "workspace"
    return Backup(store, {
        f"{where}/journal.sqlite3": (journal_path, journal_snapshot),
        f"{where}/app_settings.json": (settings_path, read_file(settings_path)),
    })
