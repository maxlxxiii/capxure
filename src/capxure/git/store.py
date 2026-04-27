"""Repo-domain queries against a SQLite connection."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Sequence

__all__ = [
    "DuplicateRepoNameError",
    "Repo",
    "RepoHit",
    "RepoStore",
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


@dataclass(frozen=True)
class RepoHit:
    """A single search hit. Lean shape — fetch full README via get_readme."""
    owner: str
    name: str
    full_name: str
    url: str
    language: str | None
    stars: int
    description: str | None
    snippet: str
    score: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# RepoStore
# ---------------------------------------------------------------------------


class RepoStore:
    """Repo-domain queries. Construct over a connection from `Database`."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    # --- write path ---

    def upsert(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
    ) -> UpsertOutcome:
        github_id = metadata["id"]
        readme_sha = _sha256_hex(readme_content) if readme_content is not None else None

        try:
            with self.connection:
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
        return self.connection.execute(
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
        cur = self.connection.execute(
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
        self.connection.execute(
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
        self.connection.execute("DELETE FROM repo_topics WHERE repo_id = ?", (repo_id,))
        if topics:
            self.connection.executemany(
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
        row = self.connection.execute(
            "SELECT * FROM repos WHERE owner = ? AND name = ?",
            (owner, name),
        ).fetchone()
        return self._row_to_repo(row) if row is not None else None

    def get_repo_by_github_id(self, github_id: int) -> Repo | None:
        row = self.connection.execute(
            "SELECT * FROM repos WHERE github_id = ?",
            (github_id,),
        ).fetchone()
        return self._row_to_repo(row) if row is not None else None

    _SORT_COLUMNS: dict[str, str] = {
        "synced": "last_synced_at",
        "captured": "captured_at",
        "stars": "stars",
    }

    _VALID_TOPIC_ORDERS: dict[str, str] = {
        "count_desc": "c DESC, topic ASC",
        "count_asc":  "c ASC, topic ASC",
        "topic_asc": "topic ASC",
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

        rows = self.connection.execute(" ".join(sql_parts), params).fetchall()
        return [self._row_to_repo(row) for row in rows]

    def count_repos(self) -> int:
        cur = self.connection.execute("SELECT COUNT(*) FROM repos")
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
        cur = self.connection.execute(
            f"SELECT url FROM repos WHERE url IN ({placeholders})",
            url_list,
        )
        return {row[0] for row in cur}

    def search(
        self,
        query: str,
        *,
        topics: Sequence[str] | None = None,
        language: str | None = None,
        k: int = 20,
    ) -> list[RepoHit]:
        """FTS5-backed search across full_name + description + readme_content.

        BM25 weights: full_name 10x, description 5x, readme 1x.
        Snippets are taken from readme_content (col 2) only — when the match
        is on a shorter column, snippet is empty.
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        k = max(1, min(k, 100))

        sql_parts = [
            "SELECT repos.owner, repos.name, repos.full_name, repos.url,",
            "       repos.language, repos.stars, repos.description,",
            "       COALESCE(snippet(repos_fts, 2, '<<', '>>', '...', 32), '') AS snippet,",
            "       bm25(repos_fts, 10.0, 5.0, 1.0) AS score",
            "FROM repos_fts",
            "JOIN repos ON repos.id = repos_fts.rowid",
            "WHERE repos_fts MATCH ?",
        ]
        params: list[Any] = [query]

        if topics:
            placeholders = ",".join("?" for _ in topics)
            sql_parts.append(
                f"AND repos.id IN ("
                f"  SELECT repo_id FROM repo_topics "
                f"  WHERE LOWER(topic) IN ({placeholders})"
                f")"
            )
            params.extend(t.lower() for t in topics)

        if language is not None:
            sql_parts.append("AND repos.language = ?")
            params.append(language)

        sql_parts.append("ORDER BY score ASC LIMIT ?")
        params.append(k)

        rows = self.connection.execute(" ".join(sql_parts), params).fetchall()
        return [
            RepoHit(
                owner=row["owner"],
                name=row["name"],
                full_name=row["full_name"],
                url=row["url"],
                language=row["language"],
                stars=row["stars"],
                description=row["description"],
                snippet=row["snippet"],
                score=row["score"],
            )
            for row in rows
        ]

    def list_topic_counts(
        self,
        *,
        prefix: str | None = None,
        min_count: int | None = None,
        max_count: int | None = None,
        order: str = "count_desc",
        limit: int | None = None,
    ) -> list[tuple[str, int]]:
        """Return (topic, count) pairs across all captured repos.

        Filters compose: WHERE prefix, HAVING min/max_count, ORDER BY order.
        """
        if order not in self._VALID_TOPIC_ORDERS:
            raise ValueError(
                f"order must be one of {sorted(self._VALID_TOPIC_ORDERS)}, got {order!r}"
            )
        order_clause = self._VALID_TOPIC_ORDERS[order]

        where_parts: list[str] = []
        params: list[Any] = []
        if prefix is not None:
            where_parts.append("LOWER(topic) LIKE ?")
            params.append(prefix.lower() + "%")

        having_parts: list[str] = []
        if min_count is not None:
            having_parts.append("c >= ?")
            params.append(min_count)
        if max_count is not None:
            having_parts.append("c <= ?")
            params.append(max_count)

        sql = "SELECT topic, COUNT(*) AS c FROM repo_topics"
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        sql += " GROUP BY topic"
        if having_parts:
            sql += " HAVING " + " AND ".join(having_parts)
        sql += f" ORDER BY {order_clause}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        return [(row["topic"], row["c"])
                for row in self.connection.execute(sql, params).fetchall()]

    def get_metadata_json(self, owner: str, name: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT metadata FROM repos WHERE owner = ? AND name = ?",
            (owner, name),
        ).fetchone()
        return json.loads(row["metadata"]) if row is not None else None

    def _row_to_repo(self, row: sqlite3.Row) -> Repo:
        topics = tuple(
            r[0] for r in self.connection.execute(
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
