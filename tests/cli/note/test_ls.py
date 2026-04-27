"""Tests for cap note ls — format detection, override, empty, multi-line."""

from unittest.mock import MagicMock

import pytest

from capxure.cli.note import main
from capxure.note import Note


def _fake_notes(*notes):
    """Helper: build a fake Database whose notes.list_notes returns the given notes."""
    db = MagicMock()
    db.__enter__.return_value = db
    db.__exit__.return_value = False
    db.notes.list_notes.return_value = list(notes)
    return db


def _patch_db(monkeypatch, notes_list):
    """Patch Database() to return a context manager yielding fake notes."""
    fake = _fake_notes(*notes_list)
    monkeypatch.setattr(
        "capxure.cli.note.ls.Database", lambda *a, **k: fake
    )


def _make_note(**overrides) -> Note:
    defaults = dict(
        id=1,
        content="hello",
        annotation=None,
        source=None,
        source_locator=None,
        kind_hint=None,
        captured_at="2026-04-26 20:15:00",
    )
    defaults.update(overrides)
    return Note(**defaults)


# ---------- format detection ----------

def test_pretty_format_when_stdout_is_tty(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    _patch_db(monkeypatch, [_make_note(content="hi")])
    code = main(["ls"])
    assert code == 0
    out = capsys.readouterr().out
    assert "[1]" in out
    assert "> hi" in out


def test_plain_format_when_stdout_is_piped(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    _patch_db(monkeypatch, [_make_note(content="hi")])
    code = main(["ls"])
    assert code == 0
    out = capsys.readouterr().out
    line = out.rstrip("\n")
    assert "\t" in line
    assert len(line.split("\t")) == 7


def test_format_pretty_overrides_piped(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    _patch_db(monkeypatch, [_make_note(content="hi")])
    code = main(["ls", "--format", "pretty"])
    assert code == 0
    out = capsys.readouterr().out
    assert "[1]" in out


def test_format_plain_overrides_tty(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    _patch_db(monkeypatch, [_make_note(content="hi")])
    code = main(["ls", "--format", "plain"])
    assert code == 0
    out = capsys.readouterr().out
    line = out.rstrip("\n")
    assert len(line.split("\t")) == 7


# ---------- empty result ----------

def test_empty_result_returns_0_and_no_output(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    _patch_db(monkeypatch, [])
    code = main(["ls"])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ---------- plain format details ----------

def test_plain_field_order_and_count(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    note = _make_note(
        id=42,
        captured_at="2026-04-26 20:15:00",
        kind_hint="quote",
        source="twitter",
        source_locator="https://x/123",
        annotation="why this matters",
        content="the text",
    )
    _patch_db(monkeypatch, [note])
    code = main(["ls"])
    assert code == 0
    fields = capsys.readouterr().out.rstrip("\n").split("\t")
    assert fields == [
        "42",
        "2026-04-26 20:15:00",
        "quote",
        "twitter",
        "https://x/123",
        "why this matters",
        "the text",
    ]


def test_plain_collapses_tabs_and_newlines(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    note = _make_note(content="line1\nline2\twith tab")
    _patch_db(monkeypatch, [note])
    code = main(["ls"])
    assert code == 0
    fields = capsys.readouterr().out.rstrip("\n").split("\t")
    assert "\n" not in fields[6]
    assert "line1 line2 with tab" == fields[6]


def test_plain_null_optionals_become_empty_string(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    note = _make_note(content="x", annotation=None, source=None,
                      source_locator=None, kind_hint=None)
    _patch_db(monkeypatch, [note])
    code = main(["ls"])
    assert code == 0
    fields = capsys.readouterr().out.rstrip("\n").split("\t")
    assert fields[2] == ""
    assert fields[3] == ""
    assert fields[4] == ""
    assert fields[5] == ""


# ---------- pretty format details ----------

def test_pretty_skips_blank_metadata_lines(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    note = _make_note(content="just content")
    _patch_db(monkeypatch, [note])
    code = main(["ls"])
    assert code == 0
    out = capsys.readouterr().out
    assert "kind=" not in out
    assert "source=" not in out
    assert "loc:" not in out
    assert "note:" not in out


def test_pretty_renders_all_metadata_when_present(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    note = _make_note(
        kind_hint="quote",
        source="twitter",
        source_locator="https://x/123",
        annotation="why this matters",
        content="text",
    )
    _patch_db(monkeypatch, [note])
    code = main(["ls"])
    assert code == 0
    out = capsys.readouterr().out
    assert "kind=quote" in out
    assert "source=twitter" in out
    assert "loc: https://x/123" in out
    assert "note: why this matters" in out


def test_pretty_preserves_multiline_content(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    note = _make_note(content="line one\nline two\nline three")
    _patch_db(monkeypatch, [note])
    code = main(["ls"])
    assert code == 0
    out = capsys.readouterr().out
    assert "> line one" in out
    assert "> line two" in out
    assert "> line three" in out


def test_pretty_blank_line_between_cards(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    n1 = _make_note(id=1, content="first")
    n2 = _make_note(id=2, content="second")
    _patch_db(monkeypatch, [n2, n1])
    code = main(["ls"])
    assert code == 0
    out = capsys.readouterr().out
    assert "> second\n\n[1]" in out


# ---------- error path ----------

def test_db_error_returns_1(monkeypatch, capsys):
    fake = MagicMock()
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = False
    fake.notes.list_notes.side_effect = RuntimeError("kaboom")
    monkeypatch.setattr("capxure.cli.note.ls.Database", lambda *a, **k: fake)
    code = main(["ls"])
    assert code == 1
    err = capsys.readouterr().err
    assert "error" in err.lower()
    assert "kaboom" in err
