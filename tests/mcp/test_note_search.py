"""Tests for NoteStore.search — FTS5-backed lexical search over notes."""

import pytest

from capxure.db import Database
from capxure.note import NoteHit


def test_search_returns_hits(db_path):
    with Database(db_path) as db:
        db.notes.add("alpha bravo charlie", source="karpathy")
        db.notes.add("delta echo foxtrot", source="lex")
        hits = db.notes.search("bravo")
    assert len(hits) == 1
    assert isinstance(hits[0], NoteHit)
    assert hits[0].source == "karpathy"


def test_source_outranks_content_match(db_path):
    """A note whose source matches the query ranks above one whose body mentions it.

    Three-doc corpus exercises the 8x source-column weight directly:
    a noise doc with the term buried in a long content body, a brief
    note tagged with the term as source, and a third doc that does
    not match (kept for IDF realism).  BM25 length-normalises the
    content match down so the 8x source-column weight wins cleanly.
    """
    with Database(db_path) as db:
        # Content match — term buried in a long body; BM25 length-normalises it down.
        db.notes.add("karpathy " + "lorem ipsum dolor sit amet " * 100, source="other")
        # Source match — should win because of the 8x column weight.
        db.notes.add("brief note", source="karpathy")
        # Non-matching noise doc to keep IDF positive.
        db.notes.add("an essay about ML training " * 5, source="lex")
        hits = db.notes.search("karpathy")
    # Source-tagged note ranks first.
    assert hits[0].source == "karpathy"
    # Pin the 8x source weight: source match scores strictly better
    # than the content match (lower BM25 = better rank).
    assert len(hits) >= 2
    assert hits[0].score < hits[1].score


def test_sources_filter(db_path):
    """sources filter restricts to notes with matching source (case-insensitive exact)."""
    with Database(db_path) as db:
        db.notes.add("matching content here", source="karpathy")
        db.notes.add("matching content here", source="lex")
        hits = db.notes.search("matching", sources=["karpathy"])
    assert len(hits) == 1
    assert hits[0].source == "karpathy"


def test_sources_filter_case_insensitive(db_path):
    with Database(db_path) as db:
        db.notes.add("xyz", source="Karpathy")
        hits = db.notes.search("xyz", sources=["karpathy"])
    assert len(hits) == 1


def test_sources_or_semantics(db_path):
    with Database(db_path) as db:
        db.notes.add("zzz", source="a")
        db.notes.add("zzz", source="b")
        db.notes.add("zzz", source="c")
        hits = db.notes.search("zzz", sources=["a", "b"])
    assert {h.source for h in hits} == {"a", "b"}


def test_k_caps_results(db_path):
    with Database(db_path) as db:
        for i in range(10):
            db.notes.add(f"common term n{i}", source="src")
        hits = db.notes.search("common", k=3)
    assert len(hits) == 3


def test_default_k_is_20(db_path):
    with Database(db_path) as db:
        for i in range(30):
            db.notes.add(f"common n{i}", source="src")
        hits = db.notes.search("common")
    assert len(hits) == 20


def test_empty_query_raises(db_path):
    with Database(db_path) as db:
        with pytest.raises(ValueError):
            db.notes.search("")


def test_snippet_includes_markers(db_path):
    with Database(db_path) as db:
        db.notes.add("text before keyword and after", source="src")
        hits = db.notes.search("keyword")
    assert "<<keyword>>" in hits[0].snippet


def test_hit_shape(db_path):
    with Database(db_path) as db:
        note = db.notes.add(
            "matching content",
            annotation="anot",
            source="karpathy",
            source_locator="some/loc",
        )
        hits = db.notes.search("matching")
    h = hits[0]
    assert h.id == note.id
    assert h.annotation == "anot"
    assert h.source == "karpathy"
    assert h.source_locator == "some/loc"
    assert h.captured_at == note.captured_at


def test_malformed_fts5_query_raises_value_error(db_path):
    """Malformed FTS5 syntax surfaces as ValueError, not opaque OperationalError."""
    with Database(db_path) as db:
        db.notes.add("hello", source="src")
        with pytest.raises(ValueError, match="invalid FTS5 query"):
            db.notes.search('"unclosed')
