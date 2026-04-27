"""Tests for the cap note add handler — content sourcing, flags, errors."""

import io

import pytest

from capxure.cli.note import main


# ---------- content sourcing ----------

def test_positional_content_captured(monkeypatch):
    captured = {}

    def fake_add(content, **kwargs):
        captured["content"] = content
        captured.update(kwargs)
        # Return a stand-in object with an id attribute
        class Note:
            id = 1
        return Note()

    monkeypatch.setattr(
        "capxure.cli.note.capture._add_via_database", fake_add
    )
    code = main(["add", "from positional"])
    assert code == 0
    assert captured["content"] == "from positional"


def test_stdin_content_captured_when_no_positional(monkeypatch):
    captured = {}

    def fake_add(content, **kwargs):
        captured["content"] = content
        class Note:
            id = 7
        return Note()

    monkeypatch.setattr("capxure.cli.note.capture._add_via_database", fake_add)
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    code = main(["add"])
    assert code == 0
    assert captured["content"] == "from stdin"


def test_positional_wins_over_stdin(monkeypatch):
    """When both positional and piped stdin present, positional wins (unix convention)."""
    captured = {}

    def fake_add(content, **kwargs):
        captured["content"] = content
        class Note:
            id = 1
        return Note()

    monkeypatch.setattr("capxure.cli.note.capture._add_via_database", fake_add)
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    code = main(["add", "from positional"])
    assert code == 0
    assert captured["content"] == "from positional"


def test_no_positional_tty_stdin_returns_2(monkeypatch, capsys):
    """No positional and TTY stdin → exit 2 with usage error on stderr."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    code = main(["add"])
    assert code == 2
    err = capsys.readouterr().err
    assert "no content" in err.lower() or "content" in err.lower()


# ---------- validation ----------

def test_empty_positional_returns_2(capsys):
    code = main(["add", ""])
    assert code == 2
    err = capsys.readouterr().err
    assert "empty" in err.lower()


def test_whitespace_only_positional_returns_2(capsys):
    code = main(["add", "   \t\n"])
    assert code == 2
    err = capsys.readouterr().err
    assert "empty" in err.lower()


# ---------- flag propagation ----------

def test_all_flags_propagate(monkeypatch):
    captured = {}

    def fake_add(content, **kwargs):
        captured["content"] = content
        captured.update(kwargs)
        class Note:
            id = 99
        return Note()

    monkeypatch.setattr("capxure.cli.note.capture._add_via_database", fake_add)
    code = main([
        "add", "the text",
        "-a", "my note",
        "-s", "twitter",
        "-L", "https://x/123",
        "-k", "quote",
    ])
    assert code == 0
    assert captured == {
        "content": "the text",
        "annotation": "my note",
        "source": "twitter",
        "source_locator": "https://x/123",
        "kind_hint": "quote",
    }


def test_long_flag_forms_work(monkeypatch):
    captured = {}

    def fake_add(content, **kwargs):
        captured["content"] = content
        captured.update(kwargs)
        class Note:
            id = 1
        return Note()

    monkeypatch.setattr("capxure.cli.note.capture._add_via_database", fake_add)
    code = main([
        "add", "the text",
        "--annotation", "my note",
        "--source", "twitter",
        "--loc", "https://x/123",
        "--kind", "quote",
    ])
    assert code == 0
    assert captured["annotation"] == "my note"
    assert captured["source_locator"] == "https://x/123"
    assert captured["kind_hint"] == "quote"


# ---------- success / error messaging ----------

def test_success_prints_to_stderr(monkeypatch, capsys):
    def fake_add(content, **kwargs):
        class Note:
            id = 42
        return Note()

    monkeypatch.setattr("capxure.cli.note.capture._add_via_database", fake_add)
    code = main(["add", "x"])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout silent
    assert "success" in captured.err.lower()
    assert "42" in captured.err


def test_library_exception_returns_1(monkeypatch, capsys):
    def fake_add(content, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("capxure.cli.note.capture._add_via_database", fake_add)
    code = main(["add", "x"])
    assert code == 1
    err = capsys.readouterr().err
    assert "error" in err.lower()
    assert "boom" in err
