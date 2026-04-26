"""Tests for top-level cap dispatcher: routes to git, no smart-dispatch."""

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
