"""Tests for the Database class — lifecycle, schema, context manager."""

import sqlite3

import pytest

from capxure.db import Database, UnsupportedSchemaError


def test_fresh_db_creation(db_path):
    """A new Database() creates the db file, schema, and sets user_version=3."""
    assert not db_path.exists()
    with Database(db_path) as db:
        assert db_path.exists()
        cursor = db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "repos" in tables
        assert "repo_topics" in tables
        assert "notes" in tables
        version = db.connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == 3


def test_reopen_existing_db(db_path):
    """Re-opening an existing db doesn't re-run schema creation."""
    with Database(db_path):
        pass
    with Database(db_path) as db:
        version = db.connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == 3


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


def test_notes_table_has_expected_columns(db_path):
    """Fresh notes table has exactly the columns from the spec."""
    with Database(db_path) as db:
        cols = {
            row[1] for row in db.connection.execute("PRAGMA table_info(notes)")
        }
    assert cols == {
        "id",
        "content",
        "annotation",
        "source",
        "source_locator",
        "kind_hint",
        "captured_at",
    }


def test_v1_db_auto_upgrades_to_v3(db_path):
    """A pre-existing v1 db (repos + repo_topics, user_version=1) auto-upgrades
    on open: user_version becomes 3 and the notes table exists. Existing repo
    rows remain readable. The migration runner walks v1->v2->v3 automatically."""
    # Hand-build a v1 schema to simulate a database from before this change.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            github_id INTEGER UNIQUE NOT NULL,
            owner TEXT NOT NULL,
            name TEXT NOT NULL,
            full_name TEXT NOT NULL,
            url TEXT NOT NULL,
            default_branch TEXT,
            description TEXT,
            language TEXT,
            stars INTEGER NOT NULL DEFAULT 0,
            forks INTEGER NOT NULL DEFAULT 0,
            pushed_at TEXT,
            is_fork INTEGER NOT NULL DEFAULT 0,
            is_archived INTEGER NOT NULL DEFAULT 0,
            readme_content TEXT,
            readme_sha TEXT,
            metadata TEXT NOT NULL,
            captured_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (owner, name)
        );
        CREATE INDEX idx_repos_language ON repos(language);
        CREATE INDEX idx_repos_stars    ON repos(stars);
        CREATE INDEX idx_repos_pushed   ON repos(pushed_at);
        CREATE TABLE repo_topics (
            repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            topic TEXT NOT NULL,
            PRIMARY KEY (repo_id, topic)
        );
        CREATE INDEX idx_repo_topics_topic ON repo_topics(topic);
        PRAGMA user_version = 1;
    """)
    # Seed a row so we can confirm it survives the migration.
    conn.execute(
        "INSERT INTO repos (github_id, owner, name, full_name, url, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (42, "octocat", "hello", "octocat/hello", "https://x", "{}"),
    )
    conn.commit()
    conn.close()

    with Database(db_path) as db:
        version = db.connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == 3
        # notes table now exists
        tables = {
            row[0] for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "notes" in tables
        # Pre-existing repo rows survived
        row = db.connection.execute(
            "SELECT full_name FROM repos WHERE github_id = 42"
        ).fetchone()
        assert row["full_name"] == "octocat/hello"


def test_v4_or_later_db_raises(db_path):
    """A db at a future schema version still raises, just as v999 always did."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()
    with pytest.raises(UnsupportedSchemaError):
        Database(db_path)


def test_notes_accessor_returns_notestore(db_path):
    """db.notes returns a NoteStore over db.connection."""
    from capxure.note import NoteStore
    with Database(db_path) as db:
        store = db.notes
        assert isinstance(store, NoteStore)
        assert store.connection is db.connection


def test_notes_accessor_is_stable(db_path):
    """Repeated access returns the same NoteStore instance (cached)."""
    with Database(db_path) as db:
        assert db.notes is db.notes


def test_notes_and_repos_share_connection(db_path):
    """db.notes and db.repos operate on the same connection."""
    with Database(db_path) as db:
        assert db.notes.connection is db.repos.connection


def test_exit_commits_pending_writes_so_a_second_connection_sees_them(db_path):
    """Database.__exit__ must commit pending writes — the connection is opened with
    isolation_level='' (implicit transactions), so without a commit on exit, writes
    in one Database instance are invisible to a subsequent Database instance.

    Pins the fix added in commit 8f48a96. Uses raw SQL so the test is independent
    of NoteStore.add's commit semantics — if NoteStore later adds its own
    `with self.connection:` block, this test still pins the Database-level contract.
    """
    with Database(db_path) as db:
        db.connection.execute(
            "INSERT INTO notes (content) VALUES (?)", ("smoke",)
        )
    with Database(db_path) as db2:
        count = db2.connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert count == 1


def test_exit_rolls_back_on_exception(db_path):
    """If the `with Database()` block raises, pending writes must NOT commit.
    Without this, partial multi-statement work would land silently on errors."""
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with Database(db_path) as db:
            db.connection.execute(
                "INSERT INTO notes (content) VALUES (?)", ("should-not-persist",)
            )
            raise Boom("simulated mid-write failure")

    with Database(db_path) as db2:
        count = db2.connection.execute(
            "SELECT COUNT(*) FROM notes WHERE content = 'should-not-persist'"
        ).fetchone()[0]
    assert count == 0
