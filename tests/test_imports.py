"""Smoke test: every symbol in capxure.__all__ is importable from the package root."""
from __future__ import annotations

import capxure


EXPECTED_EXPORTS = {
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
}


def test_all_matches_expected():
    assert set(capxure.__all__) == EXPECTED_EXPORTS


def test_every_name_in_all_is_resolvable():
    for name in capxure.__all__:
        assert hasattr(capxure, name), f"capxure.__all__ lists {name!r} but attribute is missing"


def test_removed_symbols_are_gone():
    assert not hasattr(capxure, "DeduplicationResult"), (
        "DeduplicationResult was removed in the SQLite migration; do not re-add"
    )
