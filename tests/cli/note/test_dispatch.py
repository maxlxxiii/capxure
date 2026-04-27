"""Tests for the cap note dispatcher: smart-dispatch + verb routing."""

import pytest

from capxure.cli.note import build_parser, main


def test_help_lists_add_and_ls(capsys):
    """`cap note --help` mentions both subcommands."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "add" in out
    assert "ls" in out


def test_smart_dispatch_bare_string_routes_to_add(monkeypatch):
    """`cap note "thought"` routes to the add handler with content set."""
    captured = {}

    def fake_command(args):
        captured["content"] = args.content
        return 0

    monkeypatch.setattr("capxure.cli.note.capture.command", fake_command)
    code = main(["a fleeting thought"])
    assert code == 0
    assert captured["content"] == "a fleeting thought"


def test_explicit_add_subcommand_works(monkeypatch):
    """`cap note add x` routes the same as smart-dispatch."""
    captured = {}

    def fake_command(args):
        captured["content"] = args.content
        return 0

    monkeypatch.setattr("capxure.cli.note.capture.command", fake_command)
    code = main(["add", "explicit"])
    assert code == 0
    assert captured["content"] == "explicit"


def test_ls_subcommand_routes_to_ls_handler(monkeypatch):
    called = {"ls": False}

    def fake_command(args):
        called["ls"] = True
        return 0

    monkeypatch.setattr("capxure.cli.note.ls.command", fake_command)
    code = main(["ls"])
    assert code == 0
    assert called["ls"]


def test_documented_quirk_cap_note_ls_string_routes_to_ls(monkeypatch):
    """`cap note "ls"` (literal string 'ls') is eaten by the ls verb. Documented
    trade-off; pinned by this test so future heuristic changes are intentional."""
    called = {"ls": False, "add": False}

    def fake_ls(args):
        called["ls"] = True
        return 0

    def fake_add(args):
        called["add"] = True
        return 0

    monkeypatch.setattr("capxure.cli.note.ls.command", fake_ls)
    monkeypatch.setattr("capxure.cli.note.capture.command", fake_add)
    code = main(["ls"])
    assert code == 0
    assert called["ls"] is True
    assert called["add"] is False


def test_escape_hatch_cap_note_add_ls_captures_literal(monkeypatch):
    """`cap note add ls` captures the literal string 'ls' as content."""
    captured = {}

    def fake_command(args):
        captured["content"] = args.content
        return 0

    monkeypatch.setattr("capxure.cli.note.capture.command", fake_command)
    code = main(["add", "ls"])
    assert code == 0
    assert captured["content"] == "ls"


def test_no_args_returns_2_and_prints_usage(capsys):
    """`cap note` with no args prints usage and returns 2."""
    code = main([])
    assert code == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_explicit_add_with_kind_flag(monkeypatch):
    """`cap note add --kind quote "stoic wisdom"` routes via the add subparser."""
    captured = {}

    def fake_command(args):
        captured["content"] = args.content
        captured["kind_hint"] = args.kind_hint
        return 0

    monkeypatch.setattr("capxure.cli.note.capture.command", fake_command)
    code = main(["add", "--kind", "quote", "stoic wisdom"])
    assert code == 0
    assert captured["content"] == "stoic wisdom"
    assert captured["kind_hint"] == "quote"
