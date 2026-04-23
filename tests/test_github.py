"""Tests for capxure.github URL parsing."""
from __future__ import annotations

import pytest

from capxure.github import parse_github_url


class TestParseGithubUrl:
    def test_accepts_full_https_url(self):
        assert parse_github_url("https://github.com/owner/repo") == ("owner", "repo")

    def test_accepts_http_url(self):
        assert parse_github_url("http://github.com/owner/repo") == ("owner", "repo")

    def test_accepts_schemeless_url(self):
        assert parse_github_url("github.com/owner/repo") == ("owner", "repo")

    def test_accepts_bare_shorthand(self):
        assert parse_github_url("owner/repo") == ("owner", "repo")

    def test_accepts_dot_git_suffix(self):
        assert parse_github_url("https://github.com/owner/repo.git") == ("owner", "repo")

    def test_accepts_trailing_slash(self):
        assert parse_github_url("https://github.com/owner/repo/") == ("owner", "repo")

    def test_rejects_single_token(self):
        with pytest.raises(ValueError):
            parse_github_url("owner")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            parse_github_url("")

    def test_rejects_non_github_domain(self):
        # The relaxed regex still rejects gitlab.com URLs — the `github.com/` prefix
        # is only optional, not replaceable.
        with pytest.raises(ValueError):
            parse_github_url("https://gitlab.com/owner/repo")
