"""Contract tests for capxure.storage.Storage."""
from __future__ import annotations

import hashlib
import sqlite3

from capxure.storage import Storage, UpsertOutcome


def test_fresh_db_creation(db_path):
    """A new Storage() creates the db file, schema, and sets user_version=1."""
    storage = Storage(db_path)
    try:
        assert db_path.exists()
        cur = storage.connection.execute("PRAGMA user_version")
        assert cur.fetchone()[0] == 1

        cur = storage.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cur.fetchall()]
        assert "repos" in tables
        assert "repo_topics" in tables
    finally:
        storage.close()


def test_reopen_existing_db(db_path):
    """Re-opening an existing db doesn't re-run schema creation."""
    s1 = Storage(db_path)
    s1.close()

    s2 = Storage(db_path)
    try:
        cur = s2.connection.execute("PRAGMA user_version")
        assert cur.fetchone()[0] == 1
    finally:
        s2.close()


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_upsert_new(db_path, claude_mem_metadata):
    storage = Storage(db_path)
    try:
        readme = "# claude-mem\n\nA sample README.\n"
        outcome = storage.upsert(claude_mem_metadata, readme)
        assert outcome == UpsertOutcome.NEW

        assert storage.count_repos() == 1

        # Sanity-check row shape via escape hatch (full read API comes in Task 7).
        row = storage.connection.execute(
            "SELECT github_id, owner, name, readme_sha, readme_content FROM repos"
        ).fetchone()
        assert row["github_id"] == claude_mem_metadata["id"]
        assert row["owner"] == claude_mem_metadata["owner"]["login"]
        assert row["name"] == claude_mem_metadata["name"]
        assert row["readme_content"] == readme
        assert row["readme_sha"] == _sha256_hex(readme)

        # Topics populated via junction table.
        topics_in_db = sorted(
            r[0] for r in storage.connection.execute(
                "SELECT topic FROM repo_topics"
            ).fetchall()
        )
        expected_topics = sorted(claude_mem_metadata.get("topics", []))
        assert topics_in_db == expected_topics
    finally:
        storage.close()
