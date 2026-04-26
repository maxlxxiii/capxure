"""Tests for the cap git dispatcher: smart-dispatch on '/', subcommand routing."""

import pytest

from capxure.cli.git import build_parser, main


def test_help_lists_capture_ls_stars(capsys):
    """`cap git --help` mentions all three subcommands."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "capture" in out
    assert "ls" in out
    assert "stars" in out


def test_smart_dispatch_owner_slash_repo_routes_to_capture(monkeypatch):
    """`cap git owner/repo` triggers the capture handler with the target arg."""
    captured = {}

    def fake_capture(args):
        captured["target"] = args.target
        return 0

    monkeypatch.setattr("capxure.cli.git.capture.command", fake_capture)
    code = main(["owner/repo"])
    assert code == 0
    assert captured["target"] == "owner/repo"


def test_explicit_capture_subcommand_works(monkeypatch):
    """`cap git capture owner/repo` (without smart-dispatch) routes the same."""
    captured = {}

    def fake_capture(args):
        captured["target"] = args.target
        return 0

    monkeypatch.setattr("capxure.cli.git.capture.command", fake_capture)
    code = main(["capture", "owner/repo"])
    assert code == 0
    assert captured["target"] == "owner/repo"


def test_ls_subcommand_routes_to_ls_handler(monkeypatch):
    called = {"ls": False}

    def fake_ls(args):
        called["ls"] = True
        return 0

    monkeypatch.setattr("capxure.cli.git.ls.command", fake_ls)
    code = main(["ls"])
    assert code == 0
    assert called["ls"]


def test_stars_subcommand_routes_to_stars_handler(monkeypatch):
    called = {"stars": False}

    def fake_stars(args):
        called["stars"] = True
        return 0

    monkeypatch.setattr("capxure.cli.git.stars.command", fake_stars)
    code = main(["stars"])
    assert code == 0
    assert called["stars"]


def test_no_args_returns_2_and_prints_usage(capsys):
    """`cap git` with no subcommand prints usage to stderr and returns 2."""
    code = main([])
    assert code == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_unknown_subcommand_returns_2():
    """`cap git wat` is rejected by argparse with exit 2."""
    with pytest.raises(SystemExit) as exc:
        main(["wat"])
    assert exc.value.code == 2
