"""Tests for top-level cap dispatcher: routes to git, no smart-dispatch."""

import subprocess
import sys

import pytest

from capxure.cli import build_parser, main


def test_no_args_returns_2(capsys):
    """`cap` with no subcommand prints usage and returns 2."""
    code = main([])
    assert code == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_unknown_subcommand_returns_2():
    """`cap wat` → argparse 'invalid choice', exit 2."""
    with pytest.raises(SystemExit) as exc:
        main(["wat"])
    assert exc.value.code == 2


def test_owner_slash_repo_at_top_level_returns_2():
    """`cap owner/repo` no longer works — pinned regression test for the
    breaking change. If a future refactor reintroduces top-level smart-dispatch,
    this test fails loudly."""
    with pytest.raises(SystemExit) as exc:
        main(["owner/repo"])
    assert exc.value.code == 2


def test_help_mentions_git():
    """`cap --help` lists `git` as an available subcommand."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0


def test_git_subcommand_dispatches(monkeypatch):
    """`cap git ls` reaches the git-level dispatcher's ls handler."""
    called = {"ls": False}

    def fake_ls(args):
        called["ls"] = True
        return 0

    monkeypatch.setattr("capxure.cli.git.ls.command", fake_ls)
    code = main(["git", "ls"])
    assert code == 0
    assert called["ls"]


def test_cli_runs_as_module_with_no_args_exits_2():
    """`python -m capxure.cli` with no args → argparse complains about missing target."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_main_help_flag_exits_zero(capsys):
    """`cap --help` goes through argparse which raises SystemExit(0)."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    # argparse prints usage + description to stdout on --help
    assert "cap" in out.lower()


class TestMainDispatch:
    """Top-level `main` rejects unknown subcommands; routing tests live in tests/cli/test_dispatcher.py."""

    def test_unknown_subcommand_exits_2(self, capsys):
        """A bare word isn't a known subcommand; argparse rejects it and exits 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(["gibberish"])
        assert exc_info.value.code == 2
        # argparse writes its "invalid choice" message to stderr
        assert "gibberish" in capsys.readouterr().err


def test_cli_help_exits_zero_and_mentions_cap():
    """`python -m capxure.cli --help` → exit 0, usage on stdout."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "cap" in result.stdout.lower()
    assert "capture" in result.stdout.lower()
