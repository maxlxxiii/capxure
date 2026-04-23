"""CLI tests. Kept layered: parser-level, handler-level, and an end-to-end smoke test."""
from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from capxure.cli import build_parser, main


def test_cli_runs_as_module_with_no_args_exits_2():
    """`python -m capxure.cli` with no args → argparse complains about missing target."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_parser_accepts_capture_subcommand_with_target():
    """`cap capture owner/repo` parses cleanly via the capture subparser."""
    parser = build_parser()
    args = parser.parse_args(["capture", "owner/repo"])
    assert args.subcommand == "capture"
    assert args.target == "owner/repo"
    assert args.data_dir is None


def test_parser_accepts_capture_with_data_dir_flag(tmp_path):
    parser = build_parser()
    args = parser.parse_args(["capture", f"--data-dir={tmp_path}", "owner/repo"])
    assert args.data_dir == str(tmp_path)
    assert args.target == "owner/repo"


def test_main_with_no_args_returns_2(capsys):
    """`cap` (no args) prints help to stderr and returns 2."""
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_main_help_flag_exits_zero(capsys):
    """`cap --help` goes through argparse which raises SystemExit(0)."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    # argparse prints usage + description to stdout on --help
    assert "cap" in out.lower()


from pathlib import Path

from capxure import ProcessResult, Severity, UpsertOutcome
from capxure.cli.capture import (
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
