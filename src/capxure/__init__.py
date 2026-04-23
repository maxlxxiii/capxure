"""capxure - Capture GitHub repos locally."""

from capxure.github import (
    AuthenticationError,
    GitHubClient,
    GitHubError,
    NotFoundError,
    RateLimitExceededError,
    RateLimitInfo,
    parse_github_url,
)
from capxure.processor import (
    ProcessResult,
    Severity,
    StatusCallback,
    process_repo,
)
from capxure.storage import (
    DuplicateRepoNameError,
    Repo,
    Storage,
    UnsupportedSchemaError,
    UpsertOutcome,
)

__version__ = "0.3.0"

__all__ = [
    "AuthenticationError",
    "DuplicateRepoNameError",
    "GitHubClient",
    "GitHubError",
    "NotFoundError",
    "ProcessResult",
    "RateLimitExceededError",
    "RateLimitInfo",
    "Repo",
    "Severity",
    "StatusCallback",
    "Storage",
    "UnsupportedSchemaError",
    "UpsertOutcome",
    "__version__",
    "parse_github_url",
    "process_repo",
]
