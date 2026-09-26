"""SQLite cache of facts per file, keyed by content hash and extractor version."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from archdiag.facts.model import Fact

STORE_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, cache_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    line INTEGER NOT NULL,
    source TEXT NOT NULL,
    confidence INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_path ON facts(path);
CREATE TABLE IF NOT EXISTS annotations (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class FactStore:
    """One writer (the main process). Corrupt or outdated databases are rebuilt."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.warnings: list[str] = []
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._db = self._open()
        except sqlite3.DatabaseError:
            self.warnings.append(f"The facts cache {path} was unreadable and has been rebuilt.")
            path.unlink(missing_ok=True)
            self._db = self._open()

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.executescript(_SCHEMA)
        row = db.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        if row is None or row[0] != STORE_VERSION:
            db.executescript("DELETE FROM files; DELETE FROM facts; DELETE FROM annotations;")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('version', ?)", (STORE_VERSION,))
            db.commit()
        return db

    def cached(self, path: str, cache_key: str) -> list[Fact] | None:
        row = self._db.execute("SELECT cache_key FROM files WHERE path = ?", (path,)).fetchone()
        if row is None or row[0] != cache_key:
            return None
        rows = self._db.execute(
            "SELECT path, kind, line, source, confidence, data FROM facts WHERE path = ? "
            "ORDER BY id",
            (path,),
        ).fetchall()
        return [Fact.from_row(r) for r in rows]

    def replace(self, path: str, cache_key: str, facts: Iterable[Fact]) -> None:
        with self._db:
            self._db.execute("DELETE FROM facts WHERE path = ?", (path,))
            self._db.executemany(
                "INSERT INTO facts (path, kind, line, source, confidence, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [fact.to_row() for fact in facts],
            )
            self._db.execute("INSERT OR REPLACE INTO files VALUES (?, ?)", (path, cache_key))

    def prune(self, keep: set[str]) -> None:
        stale = [row[0] for row in self._db.execute("SELECT path FROM files") if row[0] not in keep]
        with self._db:
            for path in stale:
                self._db.execute("DELETE FROM facts WHERE path = ?", (path,))
                self._db.execute("DELETE FROM files WHERE path = ?", (path,))

    def clear(self) -> None:
        with self._db:
            self._db.executescript("DELETE FROM files; DELETE FROM facts;")

    def get_annotation(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM annotations WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def put_annotation(self, key: str, value: str) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO annotations VALUES (?, ?)", (key, value))

    def close(self) -> None:
        self._db.close()
