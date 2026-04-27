"""Tests for RepoStore.search — FTS5-backed lexical search."""

import pytest

from capxure.db import Database
from capxure.git.store import RepoHit


def _insert_repo(
    db,
    *,
    github_id: int,
    owner: str,
    name: str,
    language: str | None = None,
    description: str | None = None,
    readme: str | None = None,
    topics: list[str] | None = None,
    stars: int = 0,
) -> int:
    """Insert a repo directly (bypassing upsert) for deterministic test data."""
    cur = db.connection.execute(
        "INSERT INTO repos "
        "(github_id, owner, name, full_name, url, language, description, "
        " readme_content, stars, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (github_id, owner, name, f"{owner}/{name}",
         f"https://github.com/{owner}/{name}", language, description,
         readme, stars, "{}"),
    )
    repo_id = cur.lastrowid
    for topic in topics or []:
        db.connection.execute(
            "INSERT INTO repo_topics (repo_id, topic) VALUES (?, ?)",
            (repo_id, topic),
        )
    return repo_id


def test_search_returns_hits(db_path):
    """A simple FTS query returns matching repos as RepoHit objects."""
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="r1",
                     readme="this discusses authentication patterns")
        _insert_repo(db, github_id=2, owner="o", name="r2",
                     readme="completely unrelated")
        hits = db.repos.search("authentication")
    assert len(hits) == 1
    assert isinstance(hits[0], RepoHit)
    assert hits[0].name == "r1"


def test_full_name_outranks_readme(db_path):
    """A match in full_name ranks above a match buried in a long README.

    Corpus shape: 8 noise repos (term absent — keeps BM25 IDF positive),
    one repo whose full_name contains the term and one whose long README
    contains the term exactly once buried in lorem-ipsum padding.
    """
    with Database(db_path) as db:
        # 8 noise repos so the term doesn't appear in every indexed doc.
        for i in range(8):
            _insert_repo(db, github_id=100 + i, owner="noise", name=f"r{i}",
                         readme="lorem ipsum dolor sit amet " * 5)
        # full_name match.
        _insert_repo(db, github_id=1, owner="facebook", name="react",
                     readme="lorem ipsum dolor sit amet " * 100)
        # README match — term buried once in a long body.
        _insert_repo(
            db, github_id=2, owner="o", name="other",
            readme=("lorem ipsum dolor sit amet " * 100)
                   + " react "
                   + ("lorem ipsum dolor sit amet " * 100),
        )
        hits = db.repos.search("react")
    assert hits[0].name == "react"


def test_topic_filter(db_path):
    """topics param filters results to repos with matching topics."""
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="a",
                     readme="rust programming", topics=["rust"])
        _insert_repo(db, github_id=2, owner="o", name="b",
                     readme="rust programming", topics=["python"])
        hits = db.repos.search("rust", topics=["rust"])
    assert len(hits) == 1
    assert hits[0].name == "a"


def test_topic_filter_case_insensitive(db_path):
    """Topic filter matching is case-insensitive."""
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="a",
                     readme="rust", topics=["Rust"])
        hits = db.repos.search("rust", topics=["rust"])
    assert len(hits) == 1


def test_topic_filter_or_semantics(db_path):
    """Multiple topics OR'd together."""
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="a",
                     readme="content", topics=["rust"])
        _insert_repo(db, github_id=2, owner="o", name="b",
                     readme="content", topics=["python"])
        _insert_repo(db, github_id=3, owner="o", name="c",
                     readme="content", topics=["go"])
        hits = db.repos.search("content", topics=["rust", "python"])
    names = {h.name for h in hits}
    assert names == {"a", "b"}


def test_language_filter(db_path):
    """language filter is exact-match."""
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="a",
                     readme="lib", language="Rust")
        _insert_repo(db, github_id=2, owner="o", name="b",
                     readme="lib", language="Python")
        hits = db.repos.search("lib", language="Rust")
    assert len(hits) == 1
    assert hits[0].name == "a"


def test_k_caps_results(db_path):
    """k limits the number of returned hits."""
    with Database(db_path) as db:
        for i in range(10):
            _insert_repo(db, github_id=i, owner="o", name=f"r{i}",
                         readme="match match match")
        hits = db.repos.search("match", k=3)
    assert len(hits) == 3


def test_default_k_is_20(db_path):
    """Default k is 20."""
    with Database(db_path) as db:
        for i in range(30):
            _insert_repo(db, github_id=i, owner="o", name=f"r{i}",
                         readme="match")
        hits = db.repos.search("match")
    assert len(hits) == 20


def test_empty_query_raises(db_path):
    """An empty / whitespace-only query raises ValueError."""
    with Database(db_path) as db:
        with pytest.raises(ValueError):
            db.repos.search("")
        with pytest.raises(ValueError):
            db.repos.search("   ")


def test_no_hits_returns_empty(db_path):
    """A query with zero matches returns an empty list, not an error."""
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="a", readme="hello")
        hits = db.repos.search("nonexistent_term")
    assert hits == []


def test_snippet_present_for_readme_match(db_path):
    """When the match is in the README, snippet contains highlight markers."""
    with Database(db_path) as db:
        _insert_repo(
            db, github_id=1, owner="o", name="a",
            readme="some context before the keyword and some after the keyword",
        )
        hits = db.repos.search("keyword")
    assert "<<keyword>>" in hits[0].snippet


def test_hit_includes_metadata_fields(db_path):
    """RepoHit includes owner, name, full_name, url, language, stars, description."""
    with Database(db_path) as db:
        _insert_repo(
            db, github_id=1, owner="o", name="r",
            language="Python", description="cool tool",
            stars=42, readme="match",
        )
        hits = db.repos.search("match")
    h = hits[0]
    assert h.owner == "o"
    assert h.name == "r"
    assert h.full_name == "o/r"
    assert h.url == "https://github.com/o/r"
    assert h.language == "Python"
    assert h.stars == 42
    assert h.description == "cool tool"
