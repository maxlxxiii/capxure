"""SQLite-backed persistence for captured GitHub repo data."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class UpsertOutcome(StrEnum):
    NEW = "new"                          # not present before
    UPDATED = "updated"                  # existed, content or metadata changed
    RENAMED = "renamed"                  # same github_id, owner/name changed
    UNCHANGED = "unchanged"              # existed, nothing to persist
    LOCAL_IS_NEWER = "local_is_newer"    # local pushed_at > remote pushed_at


class DuplicateRepoNameError(Exception):
    """Another GitHub repo already occupies this (owner, name)."""


class UnsupportedSchemaError(Exception):
    """DB on disk uses a schema version this library doesn't know."""


@dataclass(frozen=True)
class Repo:
    id: int
    github_id: int
    owner: str
    name: str
    full_name: str
    url: str
    default_branch: str | None
    description: str | None
    language: str | None
    stars: int
    forks: int
    pushed_at: str | None
    is_fork: bool
    is_archived: bool
    topics: tuple[str, ...]
    readme_content: str | None
    readme_sha: str | None
    captured_at: str
    last_synced_at: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_SCHEMA_VERSION = 1

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
"""


def _resolve_default_db_path() -> Path:
    """Resolve the default SQLite db location.

    Uses platformdirs.user_data_dir when the library is embedded; falls back to
    package-relative `data/` when running in-repo (useful for development).
    """
    try:
        package_data = Path(__file__).resolve().parent.parent.parent / "data"
        if package_data.is_dir():
            return package_data / "capxure.db"
    except Exception:
        pass
    return Path(user_data_dir("capxure")) / "capxure.db"


# ---------------------------------------------------------------------------
# Storage facade
# ---------------------------------------------------------------------------


class Storage:
    """SQLite-backed persistence for captured GitHub repos.

    The class owns a single long-lived sqlite3.Connection for its lifetime.
    Use as a context manager, or call close() explicitly.

    The .connection property is the documented escape hatch — consumers may
    run arbitrary SQL against the schema. The schema itself is a public
    contract; see docs/superpowers/specs/2026-04-22-sqlite-storage-migration-design.md.
    """

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

    # --- lifecycle ---

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    # --- schema management ---

    def _ensure_schema(self) -> None:
        cur = self._conn.execute("PRAGMA user_version")
        current = cur.fetchone()[0]
        if current == 0:
            with self._conn:
                self._conn.executescript(_SCHEMA_SQL)
                self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        elif current != _SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"DB at {self._db_path} uses schema version {current}, "
                f"but this library knows version {_SCHEMA_VERSION}."
            )

    # --- write path (filled in Tasks 3-6) ---

    def upsert(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
    ) -> UpsertOutcome:
        raise NotImplementedError("upsert() is implemented in Task 3+")

    def diff(self, metadata: dict[str, Any]) -> UpsertOutcome:
        raise NotImplementedError("diff() is implemented in Task 5")

    # --- read path (filled in Task 7) ---

    def get_repo(self, owner: str, name: str) -> Repo | None:
        raise NotImplementedError("get_repo() is implemented in Task 7")

    def get_repo_by_github_id(self, github_id: int) -> Repo | None:
        raise NotImplementedError("get_repo_by_github_id() is implemented in Task 7")

    def list_repos(self) -> list[Repo]:
        raise NotImplementedError("list_repos() is implemented in Task 7")

    def count_repos(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM repos")
        return cur.fetchone()[0]

    def get_metadata_json(self, owner: str, name: str) -> dict[str, Any] | None:
        raise NotImplementedError("get_metadata_json() is implemented in Task 7")
