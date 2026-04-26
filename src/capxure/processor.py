"""Core orchestrator.

Coordinates GitHub API calls and local storage operations.
Accepts a StatusCallback so consumers can surface progress.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from capxure.github import (
    AuthenticationError,
    GitHubClient,
    NotFoundError,
    RateLimitExceededError,
    parse_github_url,
)
from capxure.git.store import RepoStore, UpsertOutcome


class Severity(StrEnum):
    SUCCESS = "success"
    INFO = "info"
    ERROR = "error"


class StatusCallback(Protocol):
    def __call__(self, message: str, severity: Severity) -> None: ...


@dataclass(frozen=True)
class ProcessResult:
    owner: str
    repo: str
    outcome: UpsertOutcome | None  # None if error
    error: str | None = None


async def process_repo(
    url: str,
    *,
    github: GitHubClient,
    repos: RepoStore,
    on_status: StatusCallback,
) -> ProcessResult:
    """Process a single GitHub repo URL end-to-end.

    1. Parse URL
    2. Fetch metadata
    3. diff() against local — skip README fetch if UNCHANGED/LOCAL_IS_NEWER
    4. Fetch README (only for NEW/UPDATED/RENAMED outcomes)
    5. upsert() atomically
    """
    try:
        owner, repo = parse_github_url(url)
    except ValueError as exc:
        on_status(str(exc), Severity.ERROR)
        return ProcessResult(owner="", repo="", outcome=None, error=str(exc))

    on_status(f"Fetching metadata for {owner}/{repo}...", Severity.INFO)

    try:
        metadata_entry = await github.fetch_metadata(owner, repo)
    except NotFoundError:
        msg = f"Repository {owner}/{repo} not found on GitHub"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)
    except RateLimitExceededError:
        msg = "GitHub API rate limit exceeded — wait and retry"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)
    except AuthenticationError:
        msg = "Authentication failed — invalid or missing GITHUB_TOKEN"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)
    except Exception as exc:
        msg = f"Error fetching {owner}/{repo}: {exc}"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)

    # Pre-fetch dedup check: skip README download when nothing has changed.
    outcome = repos.diff(metadata_entry)

    if outcome == UpsertOutcome.UNCHANGED:
        on_status(f"{owner}/{repo}: already up to date", Severity.INFO)
        return ProcessResult(owner=owner, repo=repo, outcome=outcome)

    if outcome == UpsertOutcome.LOCAL_IS_NEWER:
        on_status(f"{owner}/{repo}: local copy is newer, skipping", Severity.INFO)
        return ProcessResult(owner=owner, repo=repo, outcome=outcome)

    # Fetch README.
    on_status(f"Downloading README for {owner}/{repo}...", Severity.INFO)

    try:
        readme_content = await github.fetch_readme(owner, repo)
    except NotFoundError:
        readme_content = None
        on_status(
            f"{owner}/{repo}: no README found, storing NULL readme",
            Severity.INFO,
        )

    # Atomic upsert. SQLite's WAL + single-writer replaces the old asyncio lock.
    outcome = repos.upsert(metadata_entry, readme_content)

    if outcome == UpsertOutcome.NEW:
        on_status(f"{owner}/{repo}: captured successfully", Severity.SUCCESS)
    elif outcome in (UpsertOutcome.UPDATED, UpsertOutcome.RENAMED):
        on_status(f"{owner}/{repo}: updated to latest version", Severity.SUCCESS)

    return ProcessResult(owner=owner, repo=repo, outcome=outcome)
