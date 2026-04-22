"""Contract tests for capxure.storage.Storage."""
from __future__ import annotations

import sqlite3

from capxure.storage import Storage


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
