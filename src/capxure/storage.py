"""SQLite-backed persistence for captured GitHub repo data."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from capxure.db import Database, UnsupportedSchemaError
from capxure.git.store import (
    DuplicateRepoNameError,
    Repo,
    RepoStore,
    UpsertOutcome,
)

# Re-exported so existing `from capxure.storage import ...` imports keep
# working during the refactor transition.
__all__ = [
    "DuplicateRepoNameError",
    "Repo",
    "Storage",
    "UnsupportedSchemaError",
    "UpsertOutcome",
]


class Storage:
    """SQLite-backed persistence for captured GitHub repos.

    The class owns a single long-lived sqlite3.Connection for its lifetime.
    Use as a context manager, or call close() explicitly.

    The .connection property is the documented escape hatch — consumers may
    run arbitrary SQL against the schema. The schema itself is a public
    contract; see docs/superpowers/specs/2026-04-22-sqlite-storage-migration-design.md.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db = Database(db_path)
        self._repos = RepoStore(self._db.connection)

    # --- lifecycle ---

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self._db.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._db.connection

    # --- repo-domain query delegates ---

    def upsert(self, *args, **kwargs):
        return self._repos.upsert(*args, **kwargs)

    def diff(self, *args, **kwargs):
        return self._repos.diff(*args, **kwargs)

    def get_repo(self, *args, **kwargs):
        return self._repos.get_repo(*args, **kwargs)

    def get_repo_by_github_id(self, *args, **kwargs):
        return self._repos.get_repo_by_github_id(*args, **kwargs)

    def list_repos(self, *args, **kwargs):
        return self._repos.list_repos(*args, **kwargs)

    def count_repos(self, *args, **kwargs):
        return self._repos.count_repos(*args, **kwargs)

    def existing_urls(self, *args, **kwargs):
        return self._repos.existing_urls(*args, **kwargs)

    def list_topic_counts(self, *args, **kwargs):
        return self._repos.list_topic_counts(*args, **kwargs)

    def get_metadata_json(self, *args, **kwargs):
        return self._repos.get_metadata_json(*args, **kwargs)
