"""Core orchestrator.

Coordinates GitHub API calls and local storage operations.
Accepts a StatusCallback so consumers can surface progress.

NOTE: this module is temporarily stubbed during the SQLite storage migration.
The full implementation is restored in Task 8 of
docs/superpowers/plans/2026-04-22-sqlite-storage-migration.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from capxure.github import GitHubClient
from capxure.storage import Storage, UpsertOutcome


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
    outcome: UpsertOutcome | None   # None if error
    error: str | None = None


async def process_repo(
    url: str,
    *,
    github: GitHubClient,
    storage: Storage,
    on_status: StatusCallback,
) -> ProcessResult:
    raise NotImplementedError(
        "process_repo is temporarily stubbed during the SQLite storage "
        "migration; see docs/superpowers/plans/2026-04-22-sqlite-storage-migration.md "
        "Task 8."
    )
