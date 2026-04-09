"""Core orchestrator. No TUI dependencies.

Coordinates GitHub API calls and local storage operations.
Accepts a callback for status reporting so the TUI (or any other
consumer) can display progress.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from capxure.github import (
    AuthenticationError,
    GitHubClient,
    GitHubError,
    NotFoundError,
    RateLimitExceededError,
    RateLimitInfo,
    parse_github_url,
)
from capxure.storage import DeduplicationResult, Storage


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
    outcome: DeduplicationResult | None  # None if error
    error: str | None = None


# Lock to prevent concurrent metadata.json writes from racing
_storage_lock = asyncio.Lock()


async def process_repo(
    url: str,
    *,
    github: GitHubClient,
    storage: Storage,
    on_status: StatusCallback,
) -> ProcessResult:
    """Process a single GitHub repo URL end-to-end.

    1. Parse URL
    2. Fetch metadata
    3. Check dedup
    4. Fetch README (only for NEW/UPDATED)
    5. Save via storage.upsert_entry()
    """
    # Parse URL
    try:
        owner, repo = parse_github_url(url)
    except ValueError as exc:
        on_status(str(exc), Severity.ERROR)
        return ProcessResult(owner="", repo="", outcome=None, error=str(exc))

    on_status(f"Fetching metadata for {owner}/{repo}...", Severity.INFO)

    # Fetch metadata
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
        msg = "Authentication failed — check GITHUB_TOKEN in .env"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)
    except (GitHubError, Exception) as exc:
        msg = f"Error fetching {owner}/{repo}: {exc}"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)

    # Check dedup
    all_metadata = storage.load_metadata()
    dedup_result = storage.check_dedup(all_metadata, metadata_entry)

    if dedup_result == DeduplicationResult.ALREADY_UP_TO_DATE:
        on_status(f"{owner}/{repo}: already up to date", Severity.INFO)
        return ProcessResult(owner=owner, repo=repo, outcome=dedup_result)

    if dedup_result == DeduplicationResult.LOCAL_IS_NEWER:
        on_status(f"{owner}/{repo}: local copy is newer, skipping", Severity.INFO)
        return ProcessResult(owner=owner, repo=repo, outcome=dedup_result)

    # Fetch README
    default_branch = metadata_entry.get("default_branch", "main")
    on_status(f"Downloading README for {owner}/{repo}...", Severity.INFO)

    try:
        readme_content = await github.fetch_readme(owner, repo, default_branch)
    except NotFoundError:
        readme_content = "*No README.md found in this repository.*"
        on_status(f"{owner}/{repo}: no README.md found, using placeholder", Severity.INFO)

    # Save (with lock to prevent concurrent write races)
    async with _storage_lock:
        result = storage.upsert_entry(owner, repo, metadata_entry, readme_content)

    if result == DeduplicationResult.NEW:
        on_status(f"{owner}/{repo}: captured successfully", Severity.SUCCESS)
    elif result == DeduplicationResult.UPDATED:
        on_status(f"{owner}/{repo}: updated to latest version", Severity.SUCCESS)

    return ProcessResult(owner=owner, repo=repo, outcome=result)


async def fetch_rate_limit(github: GitHubClient) -> RateLimitInfo:
    """Fetch current rate limit info."""
    return await github.fetch_rate_limit()
