"""SQLite-backed persistence for captured GitHub repo data."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from collections.abc import Iterable
from typing import Any, Literal, Sequence

from capxure.db import Database, UnsupportedSchemaError

# Re-exported so existing `from capxure.storage import UnsupportedSchemaError`
# keeps working during the refactor transition.
__all__ = [
    "DuplicateRepoNameError",
    "Repo",
    "Storage",
    "UnsupportedSchemaError",
    "UpsertOutcome",
]


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


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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
        self._db = Database(db_path)
        # Internal alias used by the query methods. The public escape hatch is
        # the .connection property; this attribute exists only so the existing
        # method bodies need no edits during this refactor.
        self._conn = self._db.connection

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

    # --- write path (filled in Tasks 3-6) ---

    def upsert(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
    ) -> UpsertOutcome:
        github_id = metadata["id"]
        readme_sha = _sha256_hex(readme_content) if readme_content is not None else None

        try:
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
        except sqlite3.IntegrityError as exc:
            if "repos.owner" in str(exc) or "UNIQUE constraint failed: repos.owner, repos.name" in str(exc):
                raise DuplicateRepoNameError(
                    f"(owner={metadata['owner']['login']!r}, name={metadata['name']!r}) "
                    f"already occupied by a different github_id"
                ) from exc
            raise

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
        """Classify what upsert() *would* do, without writing.

        Note: diff() cannot know about README-only changes because the caller
        has not fetched the README yet — that's the whole point of this method
        (skip the fetch when pushed_at + owner/name + existence say nothing
        has changed). When diff returns UNCHANGED, upsert() called afterward
        with the freshly-fetched README may itself decide UPDATED if the
        README content changed despite a static pushed_at; that's correct.
        """
        existing = self._fetch_internal_by_github_id(metadata["id"])
        # Pass existing readme_sha so that an UNCHANGED classification is
        # conservative: if a README-only change happened AND pushed_at didn't
        # move, diff() returns UNCHANGED (caller skips fetch), and that's the
        # acceptable outcome — we trust pushed_at.
        readme_sha = existing["readme_sha"] if existing is not None else None
        return self._classify(existing, metadata, readme_sha)

    # --- read path ---

    def get_repo(self, owner: str, name: str) -> Repo | None:
        row = self._conn.execute(
            "SELECT * FROM repos WHERE owner = ? AND name = ?",
            (owner, name),
        ).fetchone()
        return self._row_to_repo(row) if row is not None else None

    def get_repo_by_github_id(self, github_id: int) -> Repo | None:
        row = self._conn.execute(
            "SELECT * FROM repos WHERE github_id = ?",
            (github_id,),
        ).fetchone()
        return self._row_to_repo(row) if row is not None else None

    _SORT_COLUMNS: dict[str, str] = {
        "synced": "last_synced_at",
        "captured": "captured_at",
        "stars": "stars",
    }

    def list_repos(
        self,
        *,
        sort: Literal["synced", "captured", "stars"] = "synced",
        reverse: bool = False,
        topics: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[Repo]:
        """List repos with optional sort, topic filter, and limit.

        Default order is by `last_synced_at` descending, with `github_id`
        ascending as a tie-break for determinism. Topic filter matches
        case-insensitive exact topic names and combines multiple values
        with OR semantics (deduplicated).
        """
        column = self._SORT_COLUMNS[sort]
        direction = "ASC" if reverse else "DESC"

        params: list[Any] = []
        sql_parts = ["SELECT DISTINCT repos.* FROM repos"]
        if topics:
            placeholders = ",".join("?" for _ in topics)
            sql_parts.append(
                f"INNER JOIN repo_topics ON repo_topics.repo_id = repos.id "
                f"WHERE LOWER(repo_topics.topic) IN ({placeholders})"
            )
            params.extend(t.lower() for t in topics)
        sql_parts.append(f"ORDER BY {column} {direction}, repos.github_id ASC")
        if limit is not None:
            sql_parts.append("LIMIT ?")
            params.append(limit)

        rows = self._conn.execute(" ".join(sql_parts), params).fetchall()
        return [self._row_to_repo(row) for row in rows]

    def count_repos(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM repos")
        return cur.fetchone()[0]

    def existing_urls(self, urls: Iterable[str]) -> set[str]:
        """Return the subset of input URLs that already exist in the repos table.

        Single SELECT with a parameterized IN clause. Empty input is an early
        return — never sends a malformed query to SQLite.
        """
        url_list = list(urls)
        if not url_list:
            return set()
        placeholders = ",".join("?" * len(url_list))
        cur = self._conn.execute(
            f"SELECT url FROM repos WHERE url IN ({placeholders})",
            url_list,
        )
        return {row[0] for row in cur}

    def list_topic_counts(
        self,
        *,
        reverse: bool = False,
        limit: int | None = None,
    ) -> list[tuple[str, int]]:
        """Return (topic, count) pairs across all captured repos.

        Default order: count descending, then topic ascending as tie-break.
        `reverse=True` flips to count ascending, topic ascending.
        """
        direction = "ASC" if reverse else "DESC"
        sql = (
            "SELECT topic, COUNT(*) AS c FROM repo_topics "
            "GROUP BY topic "
            f"ORDER BY c {direction}, topic ASC"
        )
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [(row["topic"], row["c"]) for row in self._conn.execute(sql, params).fetchall()]

    def get_metadata_json(self, owner: str, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT metadata FROM repos WHERE owner = ? AND name = ?",
            (owner, name),
        ).fetchone()
        return json.loads(row["metadata"]) if row is not None else None

    def _row_to_repo(self, row: sqlite3.Row) -> Repo:
        topics = tuple(
            r[0] for r in self._conn.execute(
                "SELECT topic FROM repo_topics WHERE repo_id = ? ORDER BY topic",
                (row["id"],),
            ).fetchall()
        )
        return Repo(
            id=row["id"],
            github_id=row["github_id"],
            owner=row["owner"],
            name=row["name"],
            full_name=row["full_name"],
            url=row["url"],
            default_branch=row["default_branch"],
            description=row["description"],
            language=row["language"],
            stars=row["stars"],
            forks=row["forks"],
            pushed_at=row["pushed_at"],
            is_fork=bool(row["is_fork"]),
            is_archived=bool(row["is_archived"]),
            topics=topics,
            readme_content=row["readme_content"],
            readme_sha=row["readme_sha"],
            captured_at=row["captured_at"],
            last_synced_at=row["last_synced_at"],
        )
