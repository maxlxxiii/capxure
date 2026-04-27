"""Tests for the extended RepoStore.list_topic_counts signature."""

import pytest

from capxure.db import Database


def _seed_topics(db, topic_counts: dict[str, int]) -> None:
    """Insert N synthetic repos for each topic, attaching that topic.

    Bypasses the upsert path — directly inserts rows so we control the topic
    set deterministically.
    """
    next_id = 1
    for topic, count in topic_counts.items():
        for _ in range(count):
            db.connection.execute(
                "INSERT INTO repos "
                "(github_id, owner, name, full_name, url, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (next_id, "o", f"r{next_id}", f"o/r{next_id}", "https://x", "{}"),
            )
            db.connection.execute(
                "INSERT INTO repo_topics (repo_id, topic) VALUES (?, ?)",
                (next_id, topic),
            )
            next_id += 1


def test_default_unchanged(db_path):
    """No new args → identical to today's default behavior (count_desc, no limit)."""
    with Database(db_path) as db:
        _seed_topics(db, {"python": 3, "rust": 1, "go": 2})
        rows = db.repos.list_topic_counts()
    assert rows == [("python", 3), ("go", 2), ("rust", 1)]


def test_order_count_asc(db_path):
    """order='count_asc' returns least-popular topics first."""
    with Database(db_path) as db:
        _seed_topics(db, {"python": 3, "rust": 1, "go": 2})
        rows = db.repos.list_topic_counts(order="count_asc")
    assert rows == [("rust", 1), ("go", 2), ("python", 3)]


def test_order_topic_asc(db_path):
    """order='topic_asc' returns alphabetically sorted topics."""
    with Database(db_path) as db:
        _seed_topics(db, {"python": 3, "rust": 1, "go": 2})
        rows = db.repos.list_topic_counts(order="topic_asc")
    assert rows == [("go", 2), ("python", 3), ("rust", 1)]


def test_prefix_filter(db_path):
    """prefix matches case-insensitively on topic name."""
    with Database(db_path) as db:
        _seed_topics(db, {"python": 3, "py-tools": 1, "rust": 2})
        rows = db.repos.list_topic_counts(prefix="py")
    assert {r[0] for r in rows} == {"python", "py-tools"}


def test_prefix_filter_case_insensitive(db_path):
    """prefix matches case-insensitively."""
    with Database(db_path) as db:
        _seed_topics(db, {"Python": 1, "python": 2, "rust": 1})
        rows = db.repos.list_topic_counts(prefix="PY")
    assert {r[0] for r in rows} == {"Python", "python"}


def test_min_count_filter(db_path):
    """min_count excludes topics with fewer matches."""
    with Database(db_path) as db:
        _seed_topics(db, {"python": 3, "rust": 1, "go": 2})
        rows = db.repos.list_topic_counts(min_count=2)
    assert {r[0] for r in rows} == {"python", "go"}


def test_max_count_filter(db_path):
    """max_count excludes topics with more matches."""
    with Database(db_path) as db:
        _seed_topics(db, {"python": 3, "rust": 1, "go": 2})
        rows = db.repos.list_topic_counts(max_count=2)
    assert {r[0] for r in rows} == {"rust", "go"}


def test_filters_compose(db_path):
    """prefix + min_count + max_count + order all compose."""
    with Database(db_path) as db:
        _seed_topics(db, {
            "python": 5, "py-tools": 2, "py-utils": 1,
            "rust": 4, "go": 1,
        })
        rows = db.repos.list_topic_counts(
            prefix="py", min_count=2, max_count=4, order="topic_asc"
        )
    assert rows == [("py-tools", 2)]


def test_invalid_order_raises(db_path):
    """Unknown order value raises ValueError."""
    with Database(db_path) as db:
        _seed_topics(db, {"python": 1})
        with pytest.raises(ValueError):
            db.repos.list_topic_counts(order="banana")


def test_limit_still_works(db_path):
    """The existing limit param still works."""
    with Database(db_path) as db:
        _seed_topics(db, {"a": 1, "b": 2, "c": 3, "d": 4})
        rows = db.repos.list_topic_counts(limit=2)
    assert len(rows) == 2
    assert rows[0] == ("d", 4)
    assert rows[1] == ("c", 3)
