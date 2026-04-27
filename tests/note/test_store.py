"""Tests for NoteStore — add, list, count, and validation."""

import sqlite3

import pytest

from capxure.db import Database
from capxure.note import Note, NoteStore


@pytest.fixture
def store(db_path):
    """A NoteStore over a fresh Database, yielded inside the with-block.
    Constructed directly (not via db.notes) — db.notes is added in Task 3."""
    with Database(db_path) as db:
        yield NoteStore(db.connection)


def test_add_returns_note_with_id_and_captured_at(store):
    note = store.add("first thought")
    assert isinstance(note, Note)
    assert note.id >= 1
    assert note.content == "first thought"
    assert note.captured_at  # populated by sqlite default


def test_add_strips_whitespace_from_content(store):
    note = store.add("  spaced  \n")
    assert note.content == "spaced"


def test_add_rejects_empty_content(store):
    with pytest.raises(ValueError):
        store.add("")


def test_add_rejects_whitespace_only_content(store):
    with pytest.raises(ValueError):
        store.add("   \t\n  ")


def test_add_with_all_optional_fields(store):
    note = store.add(
        "quote text",
        annotation="why this matters",
        source="book:Atomic Habits",
        source_locator="p.42",
        kind_hint="quote",
    )
    assert note.annotation == "why this matters"
    assert note.source == "book:Atomic Habits"
    assert note.source_locator == "p.42"
    assert note.kind_hint == "quote"


def test_add_optional_fields_default_to_none(store):
    note = store.add("just content")
    assert note.annotation is None
    assert note.source is None
    assert note.source_locator is None
    assert note.kind_hint is None


def test_add_does_not_transform_optional_fields(store):
    """Optionals are stored verbatim — no stripping or normalization."""
    note = store.add("c", annotation="  spaced  ", source="\tTAB\t")
    assert note.annotation == "  spaced  "
    assert note.source == "\tTAB\t"


def test_list_notes_empty_returns_empty_list(store):
    assert store.list_notes() == []


def test_list_notes_returns_newest_first(store):
    store.connection.execute(
        "INSERT INTO notes (content, captured_at) VALUES (?, ?)",
        ("oldest", "2026-01-01 00:00:00"),
    )
    store.connection.execute(
        "INSERT INTO notes (content, captured_at) VALUES (?, ?)",
        ("newest", "2026-12-31 00:00:00"),
    )
    store.connection.execute(
        "INSERT INTO notes (content, captured_at) VALUES (?, ?)",
        ("middle", "2026-06-15 00:00:00"),
    )
    contents = [n.content for n in store.list_notes()]
    assert contents == ["newest", "middle", "oldest"]


def test_list_notes_tiebreaks_by_id_desc_when_captured_at_equal(store):
    """When two rows share captured_at, the higher id (more recent insert) sorts first."""
    store.connection.execute(
        "INSERT INTO notes (content, captured_at) VALUES (?, ?)",
        ("first", "2026-06-15 12:00:00"),
    )
    store.connection.execute(
        "INSERT INTO notes (content, captured_at) VALUES (?, ?)",
        ("second", "2026-06-15 12:00:00"),
    )
    contents = [n.content for n in store.list_notes()]
    assert contents == ["second", "first"]


def test_list_notes_limit_caps_result(store):
    for i in range(5):
        store.add(f"note {i}")
    assert len(store.list_notes(limit=3)) == 3


def test_count_notes(store):
    assert store.count_notes() == 0
    store.add("a")
    store.add("b")
    assert store.count_notes() == 2


def test_notestore_can_be_constructed_directly(db_path):
    """NoteStore takes a sqlite3.Connection — Database is not required."""
    with Database(db_path) as db:
        direct = NoteStore(db.connection)
        direct.add("via direct construction")
        assert direct.count_notes() == 1


def test_top_level_imports_work():
    """`from capxure import Note, NoteStore` works."""
    from capxure import Note as N1, NoteStore as N2
    assert N1 is Note
    assert N2 is NoteStore
