"""Tests for the `cap stars` subcommand."""
from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import patch

import pytest

from capxure import ProcessResult, UpsertOutcome
from capxure.cli import build_parser
from capxure.cli.stars import _confirm
from capxure.github import (
    AuthenticationError,
    NotFoundError,
    RateLimitExceededError,
)


class TestStarsParser:
    def test_no_user_arg_defaults_to_none(self):
        parser = build_parser()
        args = parser.parse_args(["stars"])
        assert args.subcommand == "stars"
        assert args.user is None
        assert args.limit is None
        assert args.quiet is False
        assert args.yes is False
        assert args.data_dir is None

    def test_accepts_user_argument(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "max-freeman"])
        assert args.user == "max-freeman"

    def test_limit_flag(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "--limit", "20"])
        assert args.limit == 20

    def test_limit_rejects_zero(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["stars", "--limit", "0"])
        err = capsys.readouterr().err
        assert "positive integer" in err

    def test_limit_rejects_negative(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["stars", "--limit", "-3"])
        err = capsys.readouterr().err
        assert "positive integer" in err

    def test_quiet_flag(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "--quiet"])
        assert args.quiet is True

    def test_yes_flag_long(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "--yes"])
        assert args.yes is True

    def test_yes_flag_short(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "-y"])
        assert args.yes is True

    def test_data_dir_flag(self, tmp_path):
        parser = build_parser()
        args = parser.parse_args(["stars", f"--data-dir={tmp_path}"])
        assert args.data_dir == str(tmp_path)

    def test_combination_of_user_and_flags(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "max-freeman", "--limit", "5", "-y", "--quiet"])
        assert args.user == "max-freeman"
        assert args.limit == 5
        assert args.yes is True
        assert args.quiet is True


class TestConfirm:
    def test_yes_flag_skips_prompt_and_logs_acknowledgement(self, capsys):
        result = _confirm(
            total=10, already=2, new=8,
            yes=True, quiet=False,
            user_label="alice", limit=None,
        )
        assert result is True
        err = capsys.readouterr().err
        assert "running with --yes" in err
        assert "8 to capture" in err

    def test_yes_with_quiet_suppresses_acknowledgement(self, capsys):
        result = _confirm(
            total=10, already=2, new=8,
            yes=True, quiet=True,
            user_label="alice", limit=None,
        )
        assert result is True
        assert capsys.readouterr().err == ""

    def test_non_tty_without_yes_aborts(self, capsys):
        with patch("sys.stdin.isatty", return_value=False):
            result = _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            )
        assert result is False
        err = capsys.readouterr().err
        assert "no TTY" in err

    def test_tty_accepts_lowercase_y(self, capsys):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="y"):
            result = _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            )
        assert result is True

    def test_tty_accepts_yes_full_word(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="yes"):
            assert _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            ) is True

    def test_tty_accepts_uppercase_Y(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="Y"):
            assert _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            ) is True

    def test_tty_rejects_anything_else(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value=""):
            assert _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            ) is False

    def test_tty_rejects_n(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n"):
            assert _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            ) is False

    def test_breakdown_includes_counts_and_user_label(self, capsys):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n"):
            _confirm(
                total=213, already=47, new=166,
                yes=False, quiet=False,
                user_label="max-freeman", limit=None,
            )
        err = capsys.readouterr().err
        assert "213 starred repos for max-freeman" in err
        assert "47 already captured" in err
        assert "166 new" in err

    def test_breakdown_with_limit_includes_limit_note(self, capsys):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n"):
            _confirm(
                total=213, already=5, new=15,
                yes=False, quiet=False,
                user_label="max-freeman", limit=20,
            )
        err = capsys.readouterr().err
        assert "limited to 20" in err


def _make_args(
    *,
    user: str | None = None,
    limit: int | None = None,
    quiet: bool = False,
    yes: bool = True,
    data_dir: Any = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        subcommand="stars",
        user=user,
        limit=limit,
        quiet=quiet,
        yes=yes,
        data_dir=data_dir,
        handler=None,
    )


class TestCommandHappyPath:
    def test_missing_token_returns_1_with_error(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)

        from capxure.cli.stars import command

        rc = command(_make_args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "GITHUB_TOKEN" in err

    def test_full_flow_yes_with_two_new_one_dupe_all_succeed(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        starred = [
            ("alice", "repo1", "https://github.com/alice/repo1"),
            ("bob", "repo2", "https://github.com/bob/repo2"),
            ("carol", "repo3", "https://github.com/carol/repo3"),
        ]
        # repo2 is already in the DB
        existing = {"https://github.com/bob/repo2"}

        async def fake_list_starred(self, user, limit=None):
            return starred

        async def fake_process_repo(url, *, github, storage, on_status):
            owner, repo = url.removeprefix("https://github.com/").split("/")
            return ProcessResult(owner=owner, repo=repo, outcome=UpsertOutcome.NEW)

        with patch("capxure.cli.stars.GitHubClient.list_starred", new=fake_list_starred), \
             patch("capxure.cli.stars.process_repo", new=fake_process_repo), \
             patch("capxure.cli.stars.Storage.existing_urls", return_value=existing):
            from capxure.cli.stars import command

            rc = command(_make_args(yes=True, data_dir=str(tmp_path)))

        assert rc == 0
        out = capsys.readouterr()
        # Per-repo lines on stderr
        assert "alice/repo1" in out.err
        assert "carol/repo3" in out.err
        # bob/repo2 is a dupe — must NOT be passed to process_repo (no per-repo line)
        assert "bob/repo2" not in out.err
        # Summary line
        assert "captured: 2" in out.err
        assert "already had: 1" in out.err
        assert "failed: 0" in out.err

    def test_quiet_suppresses_per_repo_lines_but_keeps_summary(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        starred = [("alice", "repo1", "https://github.com/alice/repo1")]

        async def fake_list_starred(self, user, limit=None):
            return starred

        async def fake_process_repo(url, *, github, storage, on_status):
            return ProcessResult(owner="alice", repo="repo1", outcome=UpsertOutcome.NEW)

        with patch("capxure.cli.stars.GitHubClient.list_starred", new=fake_list_starred), \
             patch("capxure.cli.stars.process_repo", new=fake_process_repo), \
             patch("capxure.cli.stars.Storage.existing_urls", return_value=set()):
            from capxure.cli.stars import command

            rc = command(_make_args(yes=True, quiet=True, data_dir=str(tmp_path)))

        assert rc == 0
        err = capsys.readouterr().err
        # No per-repo log line under --quiet
        assert "✓ alice/repo1" not in err
        # Summary still prints
        assert "captured: 1" in err

    def test_per_repo_failure_continues_loop_and_increments_failed(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        starred = [
            ("alice", "repo1", "https://github.com/alice/repo1"),
            ("bob", "repo2", "https://github.com/bob/repo2"),
            ("carol", "repo3", "https://github.com/carol/repo3"),
        ]

        async def fake_list_starred(self, user, limit=None):
            return starred

        async def fake_process_repo(url, *, github, storage, on_status):
            owner, repo = url.removeprefix("https://github.com/").split("/")
            if repo == "repo2":
                # process_repo signals failure via outcome=None, error="..."
                return ProcessResult(owner=owner, repo=repo, outcome=None, error="404")
            return ProcessResult(owner=owner, repo=repo, outcome=UpsertOutcome.NEW)

        with patch("capxure.cli.stars.GitHubClient.list_starred", new=fake_list_starred), \
             patch("capxure.cli.stars.process_repo", new=fake_process_repo), \
             patch("capxure.cli.stars.Storage.existing_urls", return_value=set()):
            from capxure.cli.stars import command

            rc = command(_make_args(yes=True, data_dir=str(tmp_path)))

        # failed > 0 → exit 1
        assert rc == 1
        err = capsys.readouterr().err
        assert "✓ alice/repo1" in err
        assert "✗ bob/repo2" in err
        assert "✓ carol/repo3" in err
        assert "captured: 2" in err
        assert "failed: 1" in err

    def test_no_new_repos_skips_loop_with_zero_summary(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        starred = [("alice", "repo1", "https://github.com/alice/repo1")]

        async def fake_list_starred(self, user, limit=None):
            return starred

        process_repo_called = False

        async def fake_process_repo(url, *, github, storage, on_status):
            nonlocal process_repo_called
            process_repo_called = True
            return ProcessResult(owner="alice", repo="repo1", outcome=UpsertOutcome.NEW)

        with patch("capxure.cli.stars.GitHubClient.list_starred", new=fake_list_starred), \
             patch("capxure.cli.stars.process_repo", new=fake_process_repo), \
             patch(
                 "capxure.cli.stars.Storage.existing_urls",
                 return_value={"https://github.com/alice/repo1"},
             ):
            from capxure.cli.stars import command

            rc = command(_make_args(yes=True, data_dir=str(tmp_path)))

        assert rc == 0
        assert process_repo_called is False
        err = capsys.readouterr().err
        assert "captured: 0" in err
        assert "already had: 1" in err


class TestCommandErrorHandling:
    def test_list_phase_authentication_error_aborts(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        async def fake_list_starred(self, user, limit=None):
            raise AuthenticationError("Invalid or expired GITHUB_TOKEN")

        with patch("capxure.cli.stars.GitHubClient.list_starred", new=fake_list_starred):
            from capxure.cli.stars import command

            rc = command(_make_args(yes=True, data_dir=str(tmp_path)))

        assert rc == 1
        err = capsys.readouterr().err
        assert "GITHUB_TOKEN" in err or "authentication" in err.lower()

    def test_list_phase_not_found_for_named_user(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        async def fake_list_starred(self, user, limit=None):
            raise NotFoundError(f"User 'ghost' not found")

        with patch("capxure.cli.stars.GitHubClient.list_starred", new=fake_list_starred):
            from capxure.cli.stars import command

            rc = command(_make_args(user="ghost", yes=True, data_dir=str(tmp_path)))

        assert rc == 1
        err = capsys.readouterr().err
        assert "ghost" in err
        assert "not found" in err

    def test_list_phase_rate_limit_aborts_with_reset_message(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        async def fake_list_starred(self, user, limit=None):
            raise RateLimitExceededError("rate limit", reset_timestamp=1700000000)

        with patch("capxure.cli.stars.GitHubClient.list_starred", new=fake_list_starred):
            from capxure.cli.stars import command

            rc = command(_make_args(yes=True, data_dir=str(tmp_path)))

        assert rc == 1
        err = capsys.readouterr().err
        assert "rate limit" in err.lower()
        # Reset timestamp surfaced (in some readable form)
        assert "1700000000" in err or "reset" in err.lower()

    def test_user_rejects_prompt_runs_no_captures_returns_zero(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        starred = [("alice", "repo1", "https://github.com/alice/repo1")]

        async def fake_list_starred(self, user, limit=None):
            return starred

        process_repo_called = False

        async def fake_process_repo(url, *, github, storage, on_status):
            nonlocal process_repo_called
            process_repo_called = True
            return ProcessResult(owner="alice", repo="repo1", outcome=UpsertOutcome.NEW)

        # No --yes, but stdin is a TTY and user rejects with empty input.
        with patch("capxure.cli.stars.GitHubClient.list_starred", new=fake_list_starred), \
             patch("capxure.cli.stars.process_repo", new=fake_process_repo), \
             patch("capxure.cli.stars.Storage.existing_urls", return_value=set()), \
             patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value=""):
            from capxure.cli.stars import command

            rc = command(_make_args(yes=False, data_dir=str(tmp_path)))

        assert rc == 0
        assert process_repo_called is False
        err = capsys.readouterr().err
        assert "captured: 0" in err

    def test_non_tty_without_yes_aborts_before_listing(
        self, monkeypatch, tmp_path, capsys
    ):
        """The current implementation lists first, then prompts. Confirm the prompt's
        non-TTY rejection still produces a clean zero-capture run with the right output."""
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        starred = [("alice", "repo1", "https://github.com/alice/repo1")]

        async def fake_list_starred(self, user, limit=None):
            return starred

        with patch("capxure.cli.stars.GitHubClient.list_starred", new=fake_list_starred), \
             patch("capxure.cli.stars.Storage.existing_urls", return_value=set()), \
             patch("sys.stdin.isatty", return_value=False):
            from capxure.cli.stars import command

            rc = command(_make_args(yes=False, data_dir=str(tmp_path)))

        # Aborted before any captures; failed=0 → exit 0
        assert rc == 0
        err = capsys.readouterr().err
        assert "no TTY" in err
        assert "captured: 0" in err
