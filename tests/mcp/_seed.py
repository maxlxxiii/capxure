"""Shared test seeding helpers for MCP / search tests."""
from __future__ import annotations


def insert_repo(
    db,
    *,
    github_id: int,
    owner: str,
    name: str,
    language: str | None = None,
    description: str | None = None,
    readme: str | None = None,
    topics: list[str] | None = None,
    stars: int = 0,
    forks: int = 0,
    pushed_at: str | None = None,
    is_fork: bool = False,
    is_archived: bool = False,
) -> int:
    """Insert a repo directly (bypassing upsert) for deterministic test data.

    Returns the inserted repo's primary-key id so callers can attach
    related rows (topics, etc.) without an extra SELECT.
    """
    cur = db.connection.execute(
        "INSERT INTO repos "
        "(github_id, owner, name, full_name, url, language, description, "
        " stars, forks, pushed_at, is_fork, is_archived, "
        " readme_content, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            github_id, owner, name, f"{owner}/{name}",
            f"https://github.com/{owner}/{name}",
            language, description,
            stars, forks, pushed_at,
            1 if is_fork else 0, 1 if is_archived else 0,
            readme, "{}",
        ),
    )
    repo_id = cur.lastrowid
    for topic in topics or []:
        db.connection.execute(
            "INSERT INTO repo_topics (repo_id, topic) VALUES (?, ?)",
            (repo_id, topic),
        )
    return repo_id
