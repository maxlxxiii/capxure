"""Connection lifecycle, schema management, and migrations for capxure's SQLite store."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_data_dir

if TYPE_CHECKING:
    from capxure.git.store import RepoStore
    from capxure.note import NoteStore

__all__ = ["Database", "UnsupportedSchemaError"]


_SCHEMA_VERSION = 2

_SCHEMA_SQL = """
CREATE TABLE repos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id         INTEGER UNIQUE NOT NULL,
    owner             TEXT NOT NULL,
    name              TEXT NOT NULL,
    full_name         TEXT NOT NULL,
    url               TEXT NOT NULL,
    default_branch    TEXT,
    description       TEXT,
    language          TEXT,
    stars             INTEGER NOT NULL DEFAULT 0,
    forks             INTEGER NOT NULL DEFAULT 0,
    pushed_at         TEXT,
    is_fork           INTEGER NOT NULL DEFAULT 0,
    is_archived       INTEGER NOT NULL DEFAULT 0,
    readme_content    TEXT,
    readme_sha        TEXT,
    metadata          TEXT NOT NULL,
    captured_at       TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (owner, name)
);

CREATE INDEX idx_repos_language ON repos(language);
CREATE INDEX idx_repos_stars    ON repos(stars);
CREATE INDEX idx_repos_pushed   ON repos(pushed_at);

CREATE TABLE repo_topics (
    repo_id  INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    topic    TEXT NOT NULL,
    PRIMARY KEY (repo_id, topic)
);

CREATE INDEX idx_repo_topics_topic ON repo_topics(topic);

CREATE TABLE notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    annotation      TEXT,
    source          TEXT,
    source_locator  TEXT,
    kind_hint       TEXT,
    captured_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

PRAGMA user_version = 2;
"""

# Each _MIGRATIONS[v] body is intentionally duplicated into _SCHEMA_SQL.
# Fresh installs run _SCHEMA_SQL once; existing dbs run only the relevant
# _MIGRATIONS[v] entries. Future migrations that aren't pure CREATE TABLE
# (e.g., ALTER, data backfills) will diverge from _SCHEMA_SQL on purpose.
_MIGRATIONS = {
    2: """
        CREATE TABLE notes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            content         TEXT NOT NULL,
            annotation      TEXT,
            source          TEXT,
            source_locator  TEXT,
            kind_hint       TEXT,
            captured_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        PRAGMA user_version = 2;
    """,
}


class UnsupportedSchemaError(Exception):
    """DB on disk uses a schema version this library doesn't know."""


def _resolve_default_db_path() -> Path:
    """Resolve the default SQLite db location.

    Priority:
      1. $CAPXURE_DATA_DIR environment variable, if set and non-empty
      2. platformdirs.user_data_dir("capxure")
      3. RuntimeError if neither yields a usable path

    The result has `capxure.db` appended.
    """
    env = os.environ.get("CAPXURE_DATA_DIR", "").strip()
    if env:
        return Path(env) / "capxure.db"
    resolved = user_data_dir("capxure")
    if resolved:
        return Path(resolved) / "capxure.db"
    raise RuntimeError(
        "Cannot resolve default db path: set $CAPXURE_DATA_DIR "
        "or ensure platformdirs is working"
    )


class Database:
    """Owns the SQLite connection, schema lifecycle, and context-manager protocol."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else _resolve_default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self._db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level="",
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema()
        self._repos: "RepoStore | None" = None
        self._notes: "NoteStore | None" = None

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def repos(self) -> "RepoStore":
        """Lazy accessor — constructs a RepoStore over self.connection on first use."""
        if self._repos is None:
            from capxure.git.store import RepoStore
            self._repos = RepoStore(self._conn)
        return self._repos

    @property
    def notes(self) -> "NoteStore":
        """Lazy accessor — constructs a NoteStore over self.connection on first use."""
        if self._notes is None:
            from capxure.note import NoteStore
            self._notes = NoteStore(self._conn)
        return self._notes

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        cur = self._conn.execute("PRAGMA user_version")
        current = cur.fetchone()[0]
        if current == 0:
            self._conn.executescript(_SCHEMA_SQL)
            return
        if current > _SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"DB at {self._db_path} uses schema version {current}, "
                f"but this library knows version {_SCHEMA_VERSION}."
            )
        for v in range(current + 1, _SCHEMA_VERSION + 1):
            self._conn.executescript(_MIGRATIONS[v])
