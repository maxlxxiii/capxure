"""Tests for NoteStore.list_source_counts — discovery tool over notes.source."""

import pytest

from capxure.db import Database


def _seed_notes(db, sources: dict[str | None, int]) -> None:
    """Insert N notes for each given source. Source can be None to test exclusion."""
    for source, count in sources.items():
        for i in range(count):
            db.notes.add(f"note {source} {i}", source=source)


def test_default_count_desc(db_path):
    """Default order is count desc, topic asc tiebreak."""
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 3, "hn": 1, "lex": 2})
        rows = db.notes.list_source_counts()
    assert rows == [("karpathy", 3), ("lex", 2), ("hn", 1)]


def test_null_sources_excluded(db_path):
    """Notes with source=NULL don't appear in results."""
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 2, None: 5})
        rows = db.notes.list_source_counts()
    assert rows == [("karpathy", 2)]


def test_prefix_filter(db_path):
    """prefix matches case-insensitively."""
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 1, "kaczynski": 1, "lex": 2})
        rows = db.notes.list_source_counts(prefix="ka")
    assert {r[0] for r in rows} == {"karpathy", "kaczynski"}


def test_min_max_count(db_path):
    with Database(db_path) as db:
        _seed_notes(db, {"a": 5, "b": 3, "c": 1})
        rows = db.notes.list_source_counts(min_count=2, max_count=4)
    assert rows == [("b", 3)]


def test_order_topic_asc(db_path):
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 3, "altman": 1})
        rows = db.notes.list_source_counts(order="topic_asc")
    assert rows == [("altman", 1), ("karpathy", 3)]


def test_invalid_order_raises(db_path):
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 1})
        with pytest.raises(ValueError):
            db.notes.list_source_counts(order="banana")


def test_limit(db_path):
    with Database(db_path) as db:
        _seed_notes(db, {"a": 1, "b": 2, "c": 3, "d": 4})
        rows = db.notes.list_source_counts(limit=2)
    assert len(rows) == 2
