"""Pure tool handler functions. Each takes a Database and returns a JSON-serializable dict/list.

Handlers are deliberately thin wrappers over RepoStore / NoteStore so they can
be tested in isolation without spinning up the MCP runtime.
"""
from __future__ import annotations

from typing import Any

from capxure.db import Database


def get_repo(db: Database, *, owner: str, name: str) -> dict[str, Any] | None:
    """Return structured metadata for a captured repo, or None if missing.

    Excludes readme_content; use get_readme for that.
    """
    repo = db.repos.get_repo(owner, name)
    if repo is None:
        return None
    return {
        "owner": repo.owner,
        "name": repo.name,
        "full_name": repo.full_name,
        "url": repo.url,
        "default_branch": repo.default_branch,
        "description": repo.description,
        "language": repo.language,
        "stars": repo.stars,
        "forks": repo.forks,
        "pushed_at": repo.pushed_at,
        "is_fork": repo.is_fork,
        "is_archived": repo.is_archived,
        "topics": list(repo.topics),
        "captured_at": repo.captured_at,
        "last_synced_at": repo.last_synced_at,
    }


def get_readme(
    db: Database, *, owner: str, name: str
) -> dict[str, Any] | None:
    """Return the full README for a repo. None if the repo isn't captured.

    `readme_content` may be None for repos that genuinely have no README.
    """
    repo = db.repos.get_repo(owner, name)
    if repo is None:
        return None
    return {
        "owner": repo.owner,
        "name": repo.name,
        "readme_content": repo.readme_content,
    }
