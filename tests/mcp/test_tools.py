"""Tests for the pure tool handler functions in capxure.mcp.tools."""

import pytest

from capxure.db import Database
from capxure.mcp import tools


def _insert_repo(db, **kw) -> None:
    db.connection.execute(
        "INSERT INTO repos "
        "(github_id, owner, name, full_name, url, language, description, "
        " stars, forks, pushed_at, is_fork, is_archived, "
        " readme_content, metadata) "
        "VALUES (:github_id, :owner, :name, :full_name, :url, :language, "
        " :description, :stars, :forks, :pushed_at, :is_fork, :is_archived, "
        " :readme_content, :metadata)",
        {
            "github_id": kw["github_id"],
            "owner": kw["owner"],
            "name": kw["name"],
            "full_name": f"{kw['owner']}/{kw['name']}",
            "url": f"https://github.com/{kw['owner']}/{kw['name']}",
            "language": kw.get("language"),
            "description": kw.get("description"),
            "stars": kw.get("stars", 0),
            "forks": kw.get("forks", 0),
            "pushed_at": kw.get("pushed_at"),
            "is_fork": 1 if kw.get("is_fork") else 0,
            "is_archived": 1 if kw.get("is_archived") else 0,
            "readme_content": kw.get("readme"),
            "metadata": "{}",
        },
    )


# --- get_repo ---

def test_get_repo_returns_metadata(db_path):
    with Database(db_path) as db:
        _insert_repo(
            db, github_id=1, owner="o", name="r",
            language="Python", description="cool", stars=42,
        )
        result = tools.get_repo(db, owner="o", name="r")
    assert result is not None
    assert result["owner"] == "o"
    assert result["name"] == "r"
    assert result["full_name"] == "o/r"
    assert result["language"] == "Python"
    assert result["description"] == "cool"
    assert result["stars"] == 42
    assert "readme_content" not in result  # explicitly excluded
    assert "topics" in result
    assert isinstance(result["topics"], list)


def test_get_repo_missing_returns_none(db_path):
    with Database(db_path) as db:
        result = tools.get_repo(db, owner="absent", name="absent")
    assert result is None


def test_get_repo_includes_topics(db_path):
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="r")
        repo_id = db.connection.execute(
            "SELECT id FROM repos WHERE owner='o' AND name='r'"
        ).fetchone()[0]
        db.connection.executemany(
            "INSERT INTO repo_topics (repo_id, topic) VALUES (?, ?)",
            [(repo_id, "rust"), (repo_id, "cli")],
        )
        result = tools.get_repo(db, owner="o", name="r")
    assert sorted(result["topics"]) == ["cli", "rust"]


# --- get_readme ---

def test_get_readme_returns_content(db_path):
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="r",
                     readme="# Hello\n\nbody here")
        result = tools.get_readme(db, owner="o", name="r")
    assert result == {"owner": "o", "name": "r",
                      "readme_content": "# Hello\n\nbody here"}


def test_get_readme_missing_repo_returns_none(db_path):
    with Database(db_path) as db:
        result = tools.get_readme(db, owner="absent", name="absent")
    assert result is None


def test_get_readme_repo_with_no_readme(db_path):
    """Repo exists but has no README: object returned, readme_content is None."""
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="r", readme=None)
        result = tools.get_readme(db, owner="o", name="r")
    assert result == {"owner": "o", "name": "r", "readme_content": None}


# --- list_topics ---

def test_list_topics_returns_list_of_dicts(db_path):
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="a")
        _insert_repo(db, github_id=2, owner="o", name="b")
        repo_ids = [r[0] for r in db.connection.execute("SELECT id FROM repos")]
        for rid in repo_ids:
            db.connection.execute(
                "INSERT INTO repo_topics (repo_id, topic) VALUES (?, 'rust')",
                (rid,),
            )
        result = tools.list_topics(db)
    assert result == [{"topic": "rust", "count": 2}]


def test_list_topics_passes_filters(db_path):
    """Filters defined on the tool flow through to list_topic_counts."""
    with Database(db_path) as db:
        for i, topic in enumerate(["py-a", "py-b", "rust"], start=1):
            _insert_repo(db, github_id=i, owner="o", name=f"r{i}")
            db.connection.execute(
                "INSERT INTO repo_topics (repo_id, topic) VALUES (?, ?)",
                (i, topic),
            )
        result = tools.list_topics(db, prefix="py")
    topics = {r["topic"] for r in result}
    assert topics == {"py-a", "py-b"}


def test_list_topics_clamps_limit(db_path):
    """limit > 500 is clamped server-side."""
    with Database(db_path) as db:
        for i in range(1, 6):
            _insert_repo(db, github_id=i, owner="o", name=f"r{i}")
            db.connection.execute(
                "INSERT INTO repo_topics (repo_id, topic) VALUES (?, ?)",
                (i, f"t{i}"),
            )
        # Should not raise even though we ask for 999.
        result = tools.list_topics(db, limit=999)
    assert len(result) == 5  # only 5 topics exist


# --- list_sources ---

def test_list_sources_returns_dicts(db_path):
    with Database(db_path) as db:
        db.notes.add("n1", source="karpathy")
        db.notes.add("n2", source="karpathy")
        db.notes.add("n3", source="lex")
        result = tools.list_sources(db)
    assert result == [
        {"source": "karpathy", "count": 2},
        {"source": "lex", "count": 1},
    ]


def test_list_sources_passes_filters(db_path):
    with Database(db_path) as db:
        db.notes.add("a", source="karpathy")
        db.notes.add("b", source="lex")
        result = tools.list_sources(db, prefix="kar")
    assert result == [{"source": "karpathy", "count": 1}]
