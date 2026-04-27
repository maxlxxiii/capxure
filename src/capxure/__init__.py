"""capxure - Capture GitHub repos locally."""

from capxure.db import Database, UnsupportedSchemaError
from capxure.git.client import (
    AuthenticationError,
    GitHubClient,
    GitHubError,
    NotFoundError,
    RateLimitExceededError,
    RateLimitInfo,
    parse_github_url,
)
from capxure.git.processor import (
    ProcessResult,
    Severity,
    StatusCallback,
    process_repo,
)
from capxure.git.store import (
    DuplicateRepoNameError,
    Repo,
    RepoHit,
    RepoStore,
    UpsertOutcome,
)
from capxure.note import Note, NoteStore

__version__ = "0.5.0"

__all__ = [
    "AuthenticationError",
    "Database",
    "DuplicateRepoNameError",
    "GitHubClient",
    "GitHubError",
    "Note",
    "NoteStore",
    "NotFoundError",
    "ProcessResult",
    "RateLimitExceededError",
    "RateLimitInfo",
    "Repo",
    "RepoHit",
    "RepoStore",
    "Severity",
    "StatusCallback",
    "UnsupportedSchemaError",
    "UpsertOutcome",
    "__version__",
    "parse_github_url",
    "process_repo",
]
