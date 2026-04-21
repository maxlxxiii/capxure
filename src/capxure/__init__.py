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
from capxure.storage import DeduplicationResult, Storage

__version__ = "0.1.0"

__all__ = [
    "AuthenticationError",
    "DeduplicationResult",
    "GitHubClient",
    "GitHubError",
    "NotFoundError",
    "ProcessResult",
    "RateLimitExceededError",
    "RateLimitInfo",
    "Severity",
    "StatusCallback",
    "Storage",
    "__version__",
    "parse_github_url",
    "process_repo",
]
