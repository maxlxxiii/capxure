"""End-to-end smoke tests for cap note add: real Database, tmp_path."""

import io

import pytest

from capxure.cli.note import main
from capxure.db import Database


@pytest.fixture
def capxure_data_dir(monkeypatch, tmp_path):
    """Point CAPXURE_DATA_DIR at tmp_path so the real Database lives there."""
    monkeypatch.setenv("CAPXURE_DATA_DIR", str(tmp_path))
    return tmp_path


def test_add_writes_row_to_real_db(capxure_data_dir):
    code = main(["add", "first thought"])
    assert code == 0
    with Database() as db:
        notes = db.notes.list_notes()
    assert len(notes) == 1
    assert notes[0].content == "first thought"
    assert notes[0].captured_at  # default populated


def test_add_with_all_flags_persists_all_fields(capxure_data_dir):
    code = main([
        "add", "the text",
        "-s", "twitter",
        "-L", "https://x/123",
        "-k", "quote",
        "-a", "why it matters",
    ])
    assert code == 0
    with Database() as db:
        notes = db.notes.list_notes()
    assert len(notes) == 1
    n = notes[0]
    assert n.content == "the text"
    assert n.source == "twitter"
    assert n.source_locator == "https://x/123"
    assert n.kind_hint == "quote"
    assert n.annotation == "why it matters"


def test_add_via_stdin_persists(capxure_data_dir, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("from a pipe"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    code = main(["add"])
    assert code == 0
    with Database() as db:
        notes = db.notes.list_notes()
    assert len(notes) == 1
    assert notes[0].content == "from a pipe"


def test_two_rapid_adds_yield_monotonic_ids(capxure_data_dir):
    main(["add", "first"])
    main(["add", "second"])
    with Database() as db:
        notes = db.notes.list_notes()  # newest-first
    ids = [n.id for n in notes]
    assert ids == sorted(ids, reverse=True)
    assert len(set(ids)) == 2
