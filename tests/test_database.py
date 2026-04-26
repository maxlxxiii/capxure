"""Tests for the Database class — lifecycle, schema, context manager."""

import sqlite3

import pytest

from capxure.db import Database, UnsupportedSchemaError


def test_fresh_db_creation(db_path):
    """A new Database() creates the db file, schema, and sets user_version=1."""
    assert not db_path.exists()
    with Database(db_path) as db:
        assert db_path.exists()
        cursor = db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "repos" in tables
        assert "repo_topics" in tables
        version = db.connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == 1


def test_reopen_existing_db(db_path):
    """Re-opening an existing db doesn't re-run schema creation."""
    with Database(db_path):
        pass
    with Database(db_path) as db:
        version = db.connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == 1


def test_unsupported_schema_raises(db_path):
    """Opening a db with a future schema_version raises UnsupportedSchemaError."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 999")
    conn.commit()
    conn.close()
    with pytest.raises(UnsupportedSchemaError):
        Database(db_path)


def test_context_manager_closes_connection(db_path):
    """Exiting the with-block closes the connection."""
    with Database(db_path) as db:
        conn = db.connection
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_close_is_idempotent(db_path):
    """Calling close() twice is safe."""
    db = Database(db_path)
    db.close()
    db.close()  # Must not raise.


def test_repos_accessor_returns_repostore(db_path):
    """db.repos returns a RepoStore over db.connection."""
    from capxure.git.store import RepoStore
    with Database(db_path) as db:
        store = db.repos
        assert isinstance(store, RepoStore)
        assert store.connection is db.connection


def test_repos_accessor_is_stable(db_path):
    """Repeated access returns the same RepoStore instance (cached)."""
    with Database(db_path) as db:
        assert db.repos is db.repos
