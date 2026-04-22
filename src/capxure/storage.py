"""SQLite-backed persistence for captured GitHub repo data."""
from __future__ import annotations

import hashlib
import json
import os
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

PRAGMA user_version = 1;
"""


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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
            self._conn.executescript(_SCHEMA_SQL)
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
        github_id = metadata["id"]
        readme_sha = _sha256_hex(readme_content) if readme_content is not None else None

        with self._conn:
            existing = self._fetch_internal_by_github_id(github_id)
            outcome = self._classify(existing, metadata, readme_sha)

            if outcome == UpsertOutcome.NEW:
                repo_id = self._insert_repo(metadata, readme_content, readme_sha)
                self._replace_topics(repo_id, metadata.get("topics", []))
            elif outcome in (UpsertOutcome.UPDATED, UpsertOutcome.RENAMED):
                assert existing is not None  # type-narrow: UPDATED/RENAMED imply an existing row
                self._update_repo(existing["id"], metadata, readme_content, readme_sha)
                self._replace_topics(existing["id"], metadata.get("topics", []))
            # UNCHANGED and LOCAL_IS_NEWER: no-op writes.

        return outcome

    # --- internal helpers ---

    def _classify(
        self,
        existing: sqlite3.Row | None,
        metadata: dict[str, Any],
        readme_sha: str | None,
    ) -> UpsertOutcome:
        if existing is None:
            return UpsertOutcome.NEW

        remote_push = metadata.get("pushed_at")
        local_push = existing["pushed_at"]

        # ISO-8601 YYYY-MM-DDTHH:MM:SSZ: lexicographic order == chronological order.
        # Also treat "local has timestamp, remote has none" as LOCAL_IS_NEWER so
        # we never overwrite a real timestamp with null.
        if local_push and (not remote_push or local_push > remote_push):
            return UpsertOutcome.LOCAL_IS_NEWER

        renamed = (
            existing["owner"] != metadata["owner"]["login"]
            or existing["name"] != metadata["name"]
        )

        if (
            existing["readme_sha"] == readme_sha
            and local_push == remote_push
            and not renamed
        ):
            return UpsertOutcome.UNCHANGED

        return UpsertOutcome.RENAMED if renamed else UpsertOutcome.UPDATED

    def _fetch_internal_by_github_id(self, github_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT id, owner, name, pushed_at, readme_sha "
            "FROM repos WHERE github_id = ?",
            (github_id,),
        ).fetchone()

    def _insert_repo(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
        readme_sha: str | None,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO repos (
                github_id, owner, name, full_name, url, default_branch,
                description, language, stars, forks, pushed_at,
                is_fork, is_archived, readme_content, readme_sha, metadata
            ) VALUES (
                :github_id, :owner, :name, :full_name, :url, :default_branch,
                :description, :language, :stars, :forks, :pushed_at,
                :is_fork, :is_archived, :readme_content, :readme_sha, :metadata
            )
            """,
            {
                "github_id":      metadata["id"],
                "owner":          metadata["owner"]["login"],
                "name":           metadata["name"],
                "full_name":      metadata["full_name"],
                "url":            metadata["html_url"],
                "default_branch": metadata.get("default_branch"),
                "description":    metadata.get("description"),
                "language":       metadata.get("language"),
                "stars":          metadata.get("stargazers_count") or 0,
                "forks":          metadata.get("forks_count") or 0,
                "pushed_at":      metadata.get("pushed_at"),
                "is_fork":        1 if metadata.get("fork") else 0,
                "is_archived":    1 if metadata.get("archived") else 0,
                "readme_content": readme_content,
                "readme_sha":     readme_sha,
                "metadata":       json.dumps(metadata),
            },
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def _update_repo(
        self,
        repo_id: int,
        metadata: dict[str, Any],
        readme_content: str | None,
        readme_sha: str | None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE repos SET
                owner          = :owner,
                name           = :name,
                full_name      = :full_name,
                url            = :url,
                default_branch = :default_branch,
                description    = :description,
                language       = :language,
                stars          = :stars,
                forks          = :forks,
                pushed_at      = :pushed_at,
                is_fork        = :is_fork,
                is_archived    = :is_archived,
                readme_content = :readme_content,
                readme_sha     = :readme_sha,
                metadata       = :metadata,
                last_synced_at = datetime('now')
            WHERE id = :id
            """,
            {
                "id":             repo_id,
                "owner":          metadata["owner"]["login"],
                "name":           metadata["name"],
                "full_name":      metadata["full_name"],
                "url":            metadata["html_url"],
                "default_branch": metadata.get("default_branch"),
                "description":    metadata.get("description"),
                "language":       metadata.get("language"),
                "stars":          metadata.get("stargazers_count") or 0,
                "forks":          metadata.get("forks_count") or 0,
                "pushed_at":      metadata.get("pushed_at"),
                "is_fork":        1 if metadata.get("fork") else 0,
                "is_archived":    1 if metadata.get("archived") else 0,
                "readme_content": readme_content,
                "readme_sha":     readme_sha,
                "metadata":       json.dumps(metadata),
            },
        )

    def _replace_topics(self, repo_id: int, topics: list[str]) -> None:
        self._conn.execute("DELETE FROM repo_topics WHERE repo_id = ?", (repo_id,))
        if topics:
            self._conn.executemany(
                "INSERT OR IGNORE INTO repo_topics (repo_id, topic) VALUES (?, ?)",
                [(repo_id, t) for t in topics],
            )

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
