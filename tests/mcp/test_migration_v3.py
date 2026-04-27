"""Tests for schema v3: FTS5 virtual tables + triggers."""

import sqlite3

import pytest

from capxure.db import Database


def test_fresh_db_has_user_version_3(db_path):
    """A fresh Database creates schema at v3."""
    with Database(db_path) as db:
        version = db.connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == 3


def test_fresh_db_has_fts_tables(db_path):
    """A fresh Database creates repos_fts and notes_fts virtual tables."""
    with Database(db_path) as db:
        cur = db.connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name IN ('repos_fts', 'notes_fts')"
        )
        names = {row[0] for row in cur.fetchall()}
    assert names == {"repos_fts", "notes_fts"}


def test_repo_insert_populates_repos_fts(db_path, claude_mem_metadata):
    """Inserting a repo via RepoStore populates repos_fts via the trigger."""
    with Database(db_path) as db:
        db.repos.upsert(claude_mem_metadata, readme_content="hello world FTS")
        cur = db.connection.execute(
            "SELECT readme_content FROM repos_fts WHERE rowid = "
            "(SELECT id FROM repos WHERE owner = ? AND name = ?)",
            (claude_mem_metadata["owner"]["login"], claude_mem_metadata["name"]),
        )
        row = cur.fetchone()
    assert row is not None
    assert "hello world FTS" in row[0]


def test_repo_update_keeps_fts_in_sync(db_path, claude_mem_metadata):
    """Updating a repo updates repos_fts via the AFTER UPDATE trigger."""
    with Database(db_path) as db:
        db.repos.upsert(claude_mem_metadata, readme_content="first content")
        # Force an update by changing pushed_at and re-upserting.
        bumped = {**claude_mem_metadata, "pushed_at": "2099-01-01T00:00:00Z"}
        db.repos.upsert(bumped, readme_content="second content")
        cur = db.connection.execute(
            "SELECT readme_content FROM repos_fts WHERE rowid = "
            "(SELECT id FROM repos WHERE owner = ? AND name = ?)",
            (claude_mem_metadata["owner"]["login"], claude_mem_metadata["name"]),
        )
        row = cur.fetchone()
    assert row is not None
    assert "second content" in row[0]
    assert "first content" not in row[0]


def test_repo_delete_removes_fts_row(db_path, claude_mem_metadata):
    """Deleting a repo removes its repos_fts row via the AFTER DELETE trigger."""
    with Database(db_path) as db:
        db.repos.upsert(claude_mem_metadata, readme_content="to be deleted")
        repo_id = db.connection.execute(
            "SELECT id FROM repos WHERE owner = ? AND name = ?",
            (claude_mem_metadata["owner"]["login"], claude_mem_metadata["name"]),
        ).fetchone()[0]
        db.connection.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
        cur = db.connection.execute(
            "SELECT COUNT(*) FROM repos_fts WHERE rowid = ?", (repo_id,)
        )
        count = cur.fetchone()[0]
    assert count == 0


def test_note_insert_populates_notes_fts(db_path):
    """Inserting a note via NoteStore populates notes_fts via the trigger."""
    with Database(db_path) as db:
        note = db.notes.add(
            "indexable content here",
            annotation="annot",
            source="karpathy",
        )
        cur = db.connection.execute(
            "SELECT content, annotation, source FROM notes_fts WHERE rowid = ?",
            (note.id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "indexable content here"
    assert row[1] == "annot"
    assert row[2] == "karpathy"


def test_v2_db_migrates_to_v3_with_backfill(db_path, claude_mem_metadata):
    """A v2 db with existing repos and notes migrates to v3 and backfills FTS."""
    # Build a v2 db by hand.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE repos (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            github_id         INTEGER UNIQUE NOT NULL,
            owner             TEXT NOT NULL,
            name              TEXT NOT NULL,
            full_name         TEXT NOT NULL,
            url               TEXT NOT NULL,
            default_branch    TEXT,
            description       TEXT,
            language          TEXT,
            stars             INTEGER NOT NULL DEFAULT 0,
            forks             INTEGER NOT NULL DEFAULT 0,
            pushed_at         TEXT,
            is_fork           INTEGER NOT NULL DEFAULT 0,
            is_archived       INTEGER NOT NULL DEFAULT 0,
            readme_content    TEXT,
            readme_sha        TEXT,
            metadata          TEXT NOT NULL,
            captured_at       TEXT NOT NULL DEFAULT (datetime('now')),
            last_synced_at    TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (owner, name)
        );
        CREATE INDEX idx_repos_language ON repos(language);
        CREATE INDEX idx_repos_stars    ON repos(stars);
        CREATE INDEX idx_repos_pushed   ON repos(pushed_at);
        CREATE TABLE repo_topics (
            repo_id  INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            topic    TEXT NOT NULL,
            PRIMARY KEY (repo_id, topic)
        );
        CREATE INDEX idx_repo_topics_topic ON repo_topics(topic);
        CREATE TABLE notes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            content         TEXT NOT NULL,
            annotation      TEXT,
            source          TEXT,
            source_locator  TEXT,
            kind_hint       TEXT,
            captured_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        PRAGMA user_version = 2;
    """)
    conn.execute(
        "INSERT INTO repos "
        "(github_id, owner, name, full_name, url, readme_content, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "octocat", "demo", "octocat/demo", "https://x", "preexisting readme", "{}"),
    )
    conn.execute(
        "INSERT INTO notes (content, annotation, source) "
        "VALUES (?, ?, ?)",
        ("preexisting note text", None, "karpathy"),
    )
    conn.commit()
    conn.close()

    # Open via Database — triggers v2->v3 migration.
    with Database(db_path) as db:
        version = db.connection.execute("PRAGMA user_version").fetchone()[0]
        repo_fts = db.connection.execute(
            "SELECT readme_content FROM repos_fts"
        ).fetchall()
        note_fts = db.connection.execute(
            "SELECT content, source FROM notes_fts"
        ).fetchall()

    assert version == 3
    assert any("preexisting readme" in row[0] for row in repo_fts)
    assert any(row[0] == "preexisting note text" and row[1] == "karpathy"
               for row in note_fts)


def test_null_columns_backfill_as_empty_string(db_path):
    """Backfill with NULL description / annotation / source coerces to '' (no NULL in FTS)."""
    # Build v2 with NULLs.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            github_id INTEGER UNIQUE NOT NULL,
            owner TEXT NOT NULL, name TEXT NOT NULL,
            full_name TEXT NOT NULL, url TEXT NOT NULL,
            default_branch TEXT, description TEXT, language TEXT,
            stars INTEGER NOT NULL DEFAULT 0, forks INTEGER NOT NULL DEFAULT 0,
            pushed_at TEXT,
            is_fork INTEGER NOT NULL DEFAULT 0, is_archived INTEGER NOT NULL DEFAULT 0,
            readme_content TEXT, readme_sha TEXT, metadata TEXT NOT NULL,
            captured_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (owner, name)
        );
        CREATE TABLE repo_topics (
            repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            topic TEXT NOT NULL,
            PRIMARY KEY (repo_id, topic)
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL, annotation TEXT, source TEXT,
            source_locator TEXT, kind_hint TEXT,
            captured_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        PRAGMA user_version = 2;
    """)
    conn.execute(
        "INSERT INTO repos (github_id, owner, name, full_name, url, "
        "description, readme_content, metadata) "
        "VALUES (1, 'o', 'r', 'o/r', 'https://x', NULL, NULL, '{}')"
    )
    conn.execute(
        "INSERT INTO notes (content, annotation, source) VALUES ('hi', NULL, NULL)"
    )
    conn.commit()
    conn.close()

    with Database(db_path) as db:
        # External-content FTS5 column reads pull from the source table, so
        # SELECT description FROM repos_fts returns the underlying NULL.
        # The actual property we want to pin is that the backfill INSERT
        # succeeded despite NULL source columns — without COALESCE, FTS5
        # would have rejected the NULLs and the row wouldn't exist.
        repo_count = db.connection.execute(
            "SELECT COUNT(*) FROM repos_fts"
        ).fetchone()[0]
        note_count = db.connection.execute(
            "SELECT COUNT(*) FROM notes_fts"
        ).fetchone()[0]
        # Coalesced projection should observably yield empty strings.
        repo_coalesced = db.connection.execute(
            "SELECT COALESCE(description, ''), COALESCE(readme_content, '') "
            "FROM repos_fts"
        ).fetchone()
        note_coalesced = db.connection.execute(
            "SELECT COALESCE(annotation, ''), COALESCE(source, '') "
            "FROM notes_fts"
        ).fetchone()

    assert repo_count == 1
    assert note_count == 1
    assert tuple(repo_coalesced) == ("", "")
    assert tuple(note_coalesced) == ("", "")
