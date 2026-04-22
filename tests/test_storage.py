"""Contract tests for capxure.storage.Storage."""
from __future__ import annotations

import copy
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


def test_upsert_unchanged(db_path, claude_mem_metadata):
    """Upserting identical inputs twice returns UNCHANGED on the second call."""
    storage = Storage(db_path)
    try:
        readme = "identical readme content"
        first = storage.upsert(claude_mem_metadata, readme)
        assert first == UpsertOutcome.NEW

        synced_before = storage.connection.execute(
            "SELECT last_synced_at FROM repos"
        ).fetchone()[0]

        second = storage.upsert(claude_mem_metadata, readme)
        assert second == UpsertOutcome.UNCHANGED

        synced_after = storage.connection.execute(
            "SELECT last_synced_at FROM repos"
        ).fetchone()[0]
        assert synced_before == synced_after, "UNCHANGED must not advance last_synced_at"
    finally:
        storage.close()


def test_upsert_updated(db_path, claude_mem_metadata):
    """Changed pushed_at + changed README produce UPDATED and persist the new data."""
    storage = Storage(db_path)
    try:
        readme_v1 = "v1 readme"
        storage.upsert(claude_mem_metadata, readme_v1)

        newer = copy.deepcopy(claude_mem_metadata)
        newer["pushed_at"] = "2099-01-01T00:00:00Z"
        readme_v2 = "v2 readme — updated"
        outcome = storage.upsert(newer, readme_v2)
        assert outcome == UpsertOutcome.UPDATED

        row = storage.connection.execute(
            "SELECT pushed_at, readme_content, readme_sha FROM repos"
        ).fetchone()
        assert row["pushed_at"] == "2099-01-01T00:00:00Z"
        assert row["readme_content"] == readme_v2
        assert row["readme_sha"] == _sha256_hex(readme_v2)
    finally:
        storage.close()


def test_upsert_renamed(db_path, claude_mem_metadata):
    """Same github_id with different owner/name yields RENAMED."""
    storage = Storage(db_path)
    try:
        readme = "same readme"
        storage.upsert(claude_mem_metadata, readme)

        renamed = copy.deepcopy(claude_mem_metadata)
        renamed["owner"]["login"] = "new-owner"
        renamed["name"] = "new-name"
        renamed["full_name"] = "new-owner/new-name"
        outcome = storage.upsert(renamed, readme)
        assert outcome == UpsertOutcome.RENAMED

        row = storage.connection.execute(
            "SELECT owner, name FROM repos"
        ).fetchone()
        assert row["owner"] == "new-owner"
        assert row["name"] == "new-name"

        # Only one row — no phantom copy of the pre-rename repo.
        assert storage.count_repos() == 1
    finally:
        storage.close()


def test_upsert_local_is_newer(db_path, claude_mem_metadata):
    """Remote pushed_at older than local returns LOCAL_IS_NEWER without writing."""
    storage = Storage(db_path)
    try:
        readme_v1 = "v1 readme"
        first = copy.deepcopy(claude_mem_metadata)
        first["pushed_at"] = "2030-01-01T00:00:00Z"
        storage.upsert(first, readme_v1)

        older = copy.deepcopy(claude_mem_metadata)
        older["pushed_at"] = "2020-01-01T00:00:00Z"
        outcome = storage.upsert(older, "would-be-v2 readme")
        assert outcome == UpsertOutcome.LOCAL_IS_NEWER

        # Nothing changed.
        row = storage.connection.execute(
            "SELECT pushed_at, readme_content FROM repos"
        ).fetchone()
        assert row["pushed_at"] == "2030-01-01T00:00:00Z"
        assert row["readme_content"] == readme_v1
    finally:
        storage.close()


def test_upsert_local_has_pushed_at_remote_does_not(db_path, claude_mem_metadata):
    """Local has a real pushed_at; remote sends pushed_at=None. Must NOT overwrite."""
    storage = Storage(db_path)
    try:
        first = copy.deepcopy(claude_mem_metadata)
        first["pushed_at"] = "2030-01-01T00:00:00Z"
        storage.upsert(first, "preserved readme")

        incoming = copy.deepcopy(claude_mem_metadata)
        incoming["pushed_at"] = None
        outcome = storage.upsert(incoming, "would-be new readme")
        assert outcome == UpsertOutcome.LOCAL_IS_NEWER

        row = storage.connection.execute(
            "SELECT pushed_at, readme_content FROM repos"
        ).fetchone()
        assert row["pushed_at"] == "2030-01-01T00:00:00Z"
        assert row["readme_content"] == "preserved readme"
    finally:
        storage.close()
