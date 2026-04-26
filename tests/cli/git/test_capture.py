"""Capture-handler tests (cap git capture). Top-level dispatch tests live in tests/cli/test_dispatcher.py."""
from __future__ import annotations

import argparse

import pytest

from pathlib import Path

from capxure import ProcessResult, Severity, UpsertOutcome
from capxure.cli.git.capture import (
    _exit_code_for,
    _print_status,
    _resolve_db_path,
    _resolve_token,
)


class TestResolveToken:
    def test_prefers_github_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "primary")
        monkeypatch.setenv("GH_TOKEN", "secondary")
        assert _resolve_token() == "primary"

    def test_falls_back_to_gh_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GH_TOKEN", "secondary")
        assert _resolve_token() == "secondary"

    def test_returns_none_if_neither_set(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert _resolve_token() is None

    def test_treats_empty_string_as_unset(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "")
        monkeypatch.setenv("GH_TOKEN", "")
        assert _resolve_token() is None


class TestResolveDbPath:
    def test_returns_none_when_no_flag(self):
        assert _resolve_db_path(None) is None

    def test_composes_capxure_db_filename(self, tmp_path):
        # The function runs .resolve() on the directory before appending — match that order.
        expected = tmp_path.resolve() / "capxure.db"
        assert _resolve_db_path(str(tmp_path)) == expected

    def test_expands_user_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = _resolve_db_path("~/capxure-data")
        expected = (tmp_path / "capxure-data").resolve() / "capxure.db"
        assert result == expected


class TestPrintStatus:
    def test_writes_severity_colon_message_to_stderr(self, capsys):
        _print_status("hello world", Severity.INFO)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "info: hello world\n"

    def test_each_severity_lowercased(self, capsys):
        _print_status("done", Severity.SUCCESS)
        _print_status("bad", Severity.ERROR)
        captured = capsys.readouterr()
        assert "success: done" in captured.err
        assert "error: bad" in captured.err


class TestExitCodeFor:
    def test_zero_when_outcome_populated(self):
        result = ProcessResult(owner="a", repo="b", outcome=UpsertOutcome.NEW)
        assert _exit_code_for(result) == 0

    def test_zero_when_dedup_skip(self):
        result = ProcessResult(owner="a", repo="b", outcome=UpsertOutcome.UNCHANGED)
        assert _exit_code_for(result) == 0

    def test_one_when_outcome_none(self):
        result = ProcessResult(owner="a", repo="b", outcome=None, error="network dead")
        assert _exit_code_for(result) == 1


import asyncio
from unittest.mock import AsyncMock, MagicMock

from capxure.cli.git.capture import command


@pytest.fixture
def args_ok(tmp_path):
    """A plausible argparse namespace: valid target + data-dir under tmp."""
    ns = argparse.Namespace()
    ns.target = "owner/repo"
    ns.data_dir = str(tmp_path)
    ns.handler = command
    ns.subcommand = "capture"
    return ns


class _AsyncCM:
    """Minimal async context manager for mocking GitHubClient."""
    def __init__(self, value):
        self._value = value
    async def __aenter__(self):
        return self._value
    async def __aexit__(self, *exc):
        return False


def _patch_client_and_database(monkeypatch):
    """Replace GitHubClient (async CM) and Database with test doubles. Returns (client, database, process_repo_mock)."""
    client_instance = MagicMock(name="GitHubClient.instance")
    client_cls = MagicMock(name="GitHubClient", return_value=_AsyncCM(client_instance))
    database_instance = MagicMock(name="Database.instance")
    database_cls = MagicMock(name="Database", return_value=database_instance)
    process_repo_mock = AsyncMock(name="process_repo")

    monkeypatch.setattr("capxure.cli.git.capture.GitHubClient", client_cls)
    monkeypatch.setattr("capxure.cli.git.capture.Database", database_cls)
    monkeypatch.setattr("capxure.cli.git.capture.process_repo", process_repo_mock)
    return client_cls, database_cls, process_repo_mock


class TestCommandHappyPath:
    def test_returns_zero_on_successful_capture(self, monkeypatch, args_ok):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, _, process_repo_mock = _patch_client_and_database(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="owner", repo="repo", outcome=UpsertOutcome.NEW
        )

        assert command(args_ok) == 0

    def test_passes_token_to_github_client(self, monkeypatch, args_ok):
        monkeypatch.setenv("GITHUB_TOKEN", "secret123")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        client_cls, _, process_repo_mock = _patch_client_and_database(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=UpsertOutcome.NEW
        )

        command(args_ok)
        client_cls.assert_called_once_with(token="secret123")

    def test_passes_composed_db_path_to_database(self, monkeypatch, args_ok, tmp_path):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, database_cls, process_repo_mock = _patch_client_and_database(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=UpsertOutcome.NEW
        )

        command(args_ok)
        # _resolve_db_path calls .expanduser().resolve() on the parent dir, so
        # compare against the same canonicalization to survive macOS's /private symlink.
        expected = tmp_path.expanduser().resolve() / "capxure.db"
        database_cls.assert_called_once_with(db_path=expected)

    def test_omits_db_path_when_no_data_dir_flag(self, monkeypatch, args_ok):
        args_ok.data_dir = None
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, database_cls, process_repo_mock = _patch_client_and_database(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=UpsertOutcome.NEW
        )

        command(args_ok)
        database_cls.assert_called_once_with()

    def test_passes_keyword_args_to_process_repo(self, monkeypatch, args_ok):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        # Rebuild patches locally so we can refer to the instance objects.
        client_instance = MagicMock(name="GitHubClient.instance")
        client_cls = MagicMock(name="GitHubClient", return_value=_AsyncCM(client_instance))
        database_instance = MagicMock(name="Database.instance")
        # Real Database.__enter__ returns self; mirror that so `with Database() as db: db.repos`
        # hands back database_instance.repos rather than a fresh child mock.
        database_instance.__enter__.return_value = database_instance
        database_cls = MagicMock(name="Database", return_value=database_instance)
        process_repo_mock = AsyncMock(name="process_repo")
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=UpsertOutcome.NEW
        )
        monkeypatch.setattr("capxure.cli.git.capture.GitHubClient", client_cls)
        monkeypatch.setattr("capxure.cli.git.capture.Database", database_cls)
        monkeypatch.setattr("capxure.cli.git.capture.process_repo", process_repo_mock)

        command(args_ok)

        process_repo_mock.assert_called_once()
        call = process_repo_mock.call_args
        assert call.args == ("owner/repo",)
        # Identity checks: verify the CLI passed the actual yielded / constructed objects.
        assert call.kwargs["github"] is client_instance
        # CLI hands process_repo the RepoStore via db.repos.
        assert call.kwargs["repos"] is database_instance.repos
        # on_status should be _print_status itself — not a wrapper, not a lambda.
        assert call.kwargs["on_status"] is _print_status

    def test_integration_real_asyncio_and_database(self, monkeypatch, args_ok, tmp_path):
        """Real asyncio.run + real Database + mocked network — catches integration seams."""
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        # Patch network only. Database stays real and writes to tmp_path/capxure.db.
        client_instance = MagicMock(name="GitHubClient.instance")
        client_cls = MagicMock(name="GitHubClient", return_value=_AsyncCM(client_instance))
        process_repo_mock = AsyncMock(name="process_repo")
        process_repo_mock.return_value = ProcessResult(
            owner="owner", repo="repo", outcome=UpsertOutcome.NEW
        )
        monkeypatch.setattr("capxure.cli.git.capture.GitHubClient", client_cls)
        monkeypatch.setattr("capxure.cli.git.capture.process_repo", process_repo_mock)
        # Note: Database is NOT patched — real SQLite DB created on tmp_path.

        assert command(args_ok) == 0
        # Confirm Database actually built a DB file on disk.
        assert (tmp_path / "capxure.db").exists()


class TestCommandErrorPaths:
    def test_returns_one_when_process_repo_reports_failure(
        self, monkeypatch, args_ok, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, _, process_repo_mock = _patch_client_and_database(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=None, error="rate limited"
        )

        assert command(args_ok) == 1

    def test_returns_one_and_stderr_message_when_token_missing(
        self, monkeypatch, args_ok, capsys
    ):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        # GitHubClient/Database should NOT be constructed.
        _, _, process_repo_mock = _patch_client_and_database(monkeypatch)

        assert command(args_ok) == 1
        err = capsys.readouterr().err
        assert "GITHUB_TOKEN" in err and "GH_TOKEN" in err
        process_repo_mock.assert_not_called()

    def test_returns_three_on_parse_error(self, monkeypatch, args_ok, capsys):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        args_ok.target = "not-a-valid-url-at-all-no-slashes-here"
        _, _, process_repo_mock = _patch_client_and_database(monkeypatch)

        assert command(args_ok) == 3
        process_repo_mock.assert_not_called()
        assert "error:" in capsys.readouterr().err.lower()

    def test_returns_130_on_keyboard_interrupt(self, monkeypatch, args_ok, capsys):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, _, process_repo_mock = _patch_client_and_database(monkeypatch)
        # Let the real asyncio.run execute; KeyboardInterrupt propagates out of
        # the awaited process_repo call, hitting the try/except in command().
        process_repo_mock.side_effect = KeyboardInterrupt

        assert command(args_ok) == 130
        assert "interrupted" in capsys.readouterr().err


