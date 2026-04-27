# cap mcp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a stdio MCP server (`cap mcp`) that exposes capxure's captured repos and notes to local AI clients via six read-only tools backed by SQLite FTS5.

**Architecture:** Schema v3 adds two FTS5 virtual tables (`repos_fts`, `notes_fts`) kept in sync by triggers. New methods on `RepoStore` and `NoteStore` (`search`, `list_source_counts`, extended `list_topic_counts`) expose query semantics. A new `capxure.mcp` subpackage wraps these methods in MCP tool handlers using the FastMCP API. The CLI gains a `mcp` sibling under `cap` that boots the server.

**Tech Stack:** Python 3.11+, SQLite (FTS5), `mcp` Python SDK (FastMCP), pytest.

**Spec:** `docs/superpowers/specs/2026-04-27-mcp-server-design.md`

---

## File Structure

**New files:**
- `src/capxure/mcp/__init__.py` — exports `build_server`
- `src/capxure/mcp/server.py` — FastMCP instance + tool registration
- `src/capxure/mcp/tools.py` — pure handler functions (testable in isolation)
- `src/capxure/cli/mcp.py` — `cap mcp` subcommand entry
- `tests/mcp/__init__.py`
- `tests/mcp/test_repo_search.py` — `RepoStore.search` tests
- `tests/mcp/test_note_search.py` — `NoteStore.search` tests
- `tests/mcp/test_topic_counts.py` — extended `list_topic_counts` tests
- `tests/mcp/test_source_counts.py` — `NoteStore.list_source_counts` tests
- `tests/mcp/test_migration_v3.py` — schema v2→v3 migration tests
- `tests/mcp/test_tools.py` — pure handler tests
- `tests/mcp/test_server_smoke.py` — end-to-end stdio smoke test

**Modified files:**
- `src/capxure/db.py` — bump `_SCHEMA_VERSION` to 3, add v3 migration body, append v3 contents to `_SCHEMA_SQL`
- `src/capxure/git/store.py` — add `search()`, extend `list_topic_counts()`
- `src/capxure/note/__init__.py` — add `search()`, `list_source_counts()`
- `src/capxure/cli/__init__.py` — add `mcp` route
- `src/capxure/cli/git/ls.py` — update `list_topic_counts` call to use new `order` param
- `pyproject.toml` — add `mcp>=1.0` dep

---

## Task 1: Schema v3 — FTS5 virtual tables + triggers + migration

**Files:**
- Modify: `src/capxure/db.py`
- Test: `tests/mcp/test_migration_v3.py`
- Create: `tests/mcp/__init__.py` (empty file)

- [ ] **Step 1.1: Create empty test package marker**

```bash
mkdir -p tests/mcp && touch tests/mcp/__init__.py
```

- [ ] **Step 1.2: Write the failing fresh-install test**

Create `tests/mcp/test_migration_v3.py`:

```python
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

    # Open via Database — triggers v2→v3 migration.
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
        repo_row = db.connection.execute(
            "SELECT description, readme_content FROM repos_fts"
        ).fetchone()
        note_row = db.connection.execute(
            "SELECT annotation, source FROM notes_fts"
        ).fetchone()

    assert repo_row == ("", "")
    assert note_row == ("", "")
```

- [ ] **Step 1.3: Run the tests to verify they fail**

Run: `pytest tests/mcp/test_migration_v3.py -v`
Expected: All tests FAIL — schema is still v2; no FTS tables; assertion `version == 3` fails first.

- [ ] **Step 1.4: Update `db.py` — bump version, append v3 to schema, add migration body**

Modify `src/capxure/db.py`:

Change `_SCHEMA_VERSION`:
```python
_SCHEMA_VERSION = 3
```

Append the v3 contents to `_SCHEMA_SQL` (after the `notes` table block, before `PRAGMA user_version = 2;`). The `PRAGMA user_version` line at the bottom of `_SCHEMA_SQL` also bumps to 3:

```python
_SCHEMA_SQL = """
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

CREATE VIRTUAL TABLE repos_fts USING fts5(
    full_name, description, readme_content,
    content='repos', content_rowid='id',
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE notes_fts USING fts5(
    content, annotation, source,
    content='notes', content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER repos_ai AFTER INSERT ON repos BEGIN
    INSERT INTO repos_fts(rowid, full_name, description, readme_content)
    VALUES (new.id, new.full_name,
            COALESCE(new.description, ''),
            COALESCE(new.readme_content, ''));
END;

CREATE TRIGGER repos_ad AFTER DELETE ON repos BEGIN
    INSERT INTO repos_fts(repos_fts, rowid, full_name, description, readme_content)
    VALUES ('delete', old.id, old.full_name,
            COALESCE(old.description, ''),
            COALESCE(old.readme_content, ''));
END;

CREATE TRIGGER repos_au AFTER UPDATE ON repos BEGIN
    INSERT INTO repos_fts(repos_fts, rowid, full_name, description, readme_content)
    VALUES ('delete', old.id, old.full_name,
            COALESCE(old.description, ''),
            COALESCE(old.readme_content, ''));
    INSERT INTO repos_fts(rowid, full_name, description, readme_content)
    VALUES (new.id, new.full_name,
            COALESCE(new.description, ''),
            COALESCE(new.readme_content, ''));
END;

CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, content, annotation, source)
    VALUES (new.id, new.content,
            COALESCE(new.annotation, ''),
            COALESCE(new.source, ''));
END;

CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content, annotation, source)
    VALUES ('delete', old.id, old.content,
            COALESCE(old.annotation, ''),
            COALESCE(old.source, ''));
END;

CREATE TRIGGER notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content, annotation, source)
    VALUES ('delete', old.id, old.content,
            COALESCE(old.annotation, ''),
            COALESCE(old.source, ''));
    INSERT INTO notes_fts(rowid, content, annotation, source)
    VALUES (new.id, new.content,
            COALESCE(new.annotation, ''),
            COALESCE(new.source, ''));
END;

PRAGMA user_version = 3;
"""
```

Add `_MIGRATIONS[3]` (after the existing `_MIGRATIONS[2]` entry):

```python
_MIGRATIONS = {
    2: """
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
    """,
    3: """
        CREATE VIRTUAL TABLE repos_fts USING fts5(
            full_name, description, readme_content,
            content='repos', content_rowid='id',
            tokenize='porter unicode61'
        );

        CREATE VIRTUAL TABLE notes_fts USING fts5(
            content, annotation, source,
            content='notes', content_rowid='id',
            tokenize='porter unicode61'
        );

        INSERT INTO repos_fts(rowid, full_name, description, readme_content)
        SELECT id, full_name,
               COALESCE(description, ''),
               COALESCE(readme_content, '')
        FROM repos;

        INSERT INTO notes_fts(rowid, content, annotation, source)
        SELECT id, content,
               COALESCE(annotation, ''),
               COALESCE(source, '')
        FROM notes;

        CREATE TRIGGER repos_ai AFTER INSERT ON repos BEGIN
            INSERT INTO repos_fts(rowid, full_name, description, readme_content)
            VALUES (new.id, new.full_name,
                    COALESCE(new.description, ''),
                    COALESCE(new.readme_content, ''));
        END;

        CREATE TRIGGER repos_ad AFTER DELETE ON repos BEGIN
            INSERT INTO repos_fts(repos_fts, rowid, full_name, description, readme_content)
            VALUES ('delete', old.id, old.full_name,
                    COALESCE(old.description, ''),
                    COALESCE(old.readme_content, ''));
        END;

        CREATE TRIGGER repos_au AFTER UPDATE ON repos BEGIN
            INSERT INTO repos_fts(repos_fts, rowid, full_name, description, readme_content)
            VALUES ('delete', old.id, old.full_name,
                    COALESCE(old.description, ''),
                    COALESCE(old.readme_content, ''));
            INSERT INTO repos_fts(rowid, full_name, description, readme_content)
            VALUES (new.id, new.full_name,
                    COALESCE(new.description, ''),
                    COALESCE(new.readme_content, ''));
        END;

        CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
            INSERT INTO notes_fts(rowid, content, annotation, source)
            VALUES (new.id, new.content,
                    COALESCE(new.annotation, ''),
                    COALESCE(new.source, ''));
        END;

        CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
            INSERT INTO notes_fts(notes_fts, rowid, content, annotation, source)
            VALUES ('delete', old.id, old.content,
                    COALESCE(old.annotation, ''),
                    COALESCE(old.source, ''));
        END;

        CREATE TRIGGER notes_au AFTER UPDATE ON notes BEGIN
            INSERT INTO notes_fts(notes_fts, rowid, content, annotation, source)
            VALUES ('delete', old.id, old.content,
                    COALESCE(old.annotation, ''),
                    COALESCE(old.source, ''));
            INSERT INTO notes_fts(rowid, content, annotation, source)
            VALUES (new.id, new.content,
                    COALESCE(new.annotation, ''),
                    COALESCE(new.source, ''));
        END;

        PRAGMA user_version = 3;
    """,
}
```

- [ ] **Step 1.5: Update existing v2-expecting tests for the new version**

Modify `tests/test_database.py`: every assertion of `version == 2` becomes `version == 3`. Search and replace `version == 2` → `version == 3` in that file.

The existing `test_v1_db_auto_upgrades_to_v2` test should be renamed and adapted: change its name to `test_v1_db_auto_upgrades_to_v3` and update the final assertion to `version == 3` (the migration runner will run v1→v2→v3 automatically).

- [ ] **Step 1.6: Run all tests to verify v3 migration passes and no v2 tests break**

Run: `pytest tests/mcp/test_migration_v3.py tests/test_database.py -v`
Expected: All tests PASS. The fresh-install path creates v3 tables + triggers; the v2→v3 migration path backfills correctly.

- [ ] **Step 1.7: Run the full test suite to confirm no regressions**

Run: `pytest -v`
Expected: All previous tests still PASS. (Existing repo/note insert tests now also exercise the FTS triggers but should be transparent.)

- [ ] **Step 1.8: Commit**

```bash
git add src/capxure/db.py tests/mcp/__init__.py tests/mcp/test_migration_v3.py tests/test_database.py
git commit -m "$(cat <<'EOF'
Schema v3: FTS5 virtual tables for repos and notes

Adds repos_fts and notes_fts external-content tables with insert/update/delete
triggers to keep them in sync. Backfills from existing rows on v2->v3 upgrade.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Extend `RepoStore.list_topic_counts` with new filter/order params

**Files:**
- Modify: `src/capxure/git/store.py:349` (the `list_topic_counts` method)
- Modify: `src/capxure/cli/git/ls.py:83` (the call site)
- Test: `tests/mcp/test_topic_counts.py`

- [ ] **Step 2.1: Write failing tests for the extended signature**

Create `tests/mcp/test_topic_counts.py`:

```python
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
```

- [ ] **Step 2.2: Run tests to confirm they fail**

Run: `pytest tests/mcp/test_topic_counts.py -v`
Expected: FAIL — `list_topic_counts` doesn't accept the new params yet.

- [ ] **Step 2.3: Replace `list_topic_counts` in `src/capxure/git/store.py`**

Find the existing method (around line 349) and replace its body:

```python
_VALID_TOPIC_ORDERS = {
    "count_desc": "c DESC, topic ASC",
    "count_asc":  "c ASC, topic ASC",
    "topic_asc": "topic ASC",
}

def list_topic_counts(
    self,
    *,
    prefix: str | None = None,
    min_count: int | None = None,
    max_count: int | None = None,
    order: str = "count_desc",
    limit: int | None = None,
) -> list[tuple[str, int]]:
    """Return (topic, count) pairs across all captured repos.

    Filters compose: WHERE prefix, HAVING min/max_count, ORDER BY order.
    """
    if order not in self._VALID_TOPIC_ORDERS:
        raise ValueError(
            f"order must be one of {sorted(self._VALID_TOPIC_ORDERS)}, got {order!r}"
        )
    order_clause = self._VALID_TOPIC_ORDERS[order]

    where_parts: list[str] = []
    params: list[Any] = []
    if prefix is not None:
        where_parts.append("LOWER(topic) LIKE ?")
        params.append(prefix.lower() + "%")

    having_parts: list[str] = []
    if min_count is not None:
        having_parts.append("c >= ?")
        params.append(min_count)
    if max_count is not None:
        having_parts.append("c <= ?")
        params.append(max_count)

    sql = "SELECT topic, COUNT(*) AS c FROM repo_topics"
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    sql += " GROUP BY topic"
    if having_parts:
        sql += " HAVING " + " AND ".join(having_parts)
    sql += f" ORDER BY {order_clause}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    return [(row["topic"], row["c"])
            for row in self.connection.execute(sql, params).fetchall()]
```

The existing `reverse: bool` parameter is removed. The class-level dict `_VALID_TOPIC_ORDERS` lives at the top of the `RepoStore` class (alongside `_SORT_COLUMNS`).

- [ ] **Step 2.4: Update the CLI `ls topics` caller**

Modify `src/capxure/cli/git/ls.py:83` (the existing `list_topic_counts(reverse=args.reverse, limit=limit)` call). Replace with:

```python
topic_rows = db.repos.list_topic_counts(
    order="count_asc" if args.reverse else "count_desc",
    limit=limit,
)
```

- [ ] **Step 2.5: Run new and CLI tests**

Run: `pytest tests/mcp/test_topic_counts.py tests/cli -v`
Expected: All PASS. CLI behavior unchanged from a user perspective.

- [ ] **Step 2.6: Commit**

```bash
git add src/capxure/git/store.py src/capxure/cli/git/ls.py tests/mcp/test_topic_counts.py
git commit -m "$(cat <<'EOF'
RepoStore.list_topic_counts: add prefix, min/max_count, order

Replaces reverse=bool with a more expressive order enum and adds prefix
+ count-bound filters that compose in SQL. CLI caller migrated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `NoteStore.list_source_counts`

**Files:**
- Modify: `src/capxure/note/__init__.py`
- Test: `tests/mcp/test_source_counts.py`

- [ ] **Step 3.1: Write failing tests**

Create `tests/mcp/test_source_counts.py`:

```python
"""Tests for NoteStore.list_source_counts — discovery tool over notes.source."""

import pytest

from capxure.db import Database


def _seed_notes(db, sources: dict[str | None, int]) -> None:
    """Insert N notes for each given source. Source can be None to test exclusion."""
    for source, count in sources.items():
        for i in range(count):
            db.notes.add(f"note {source} {i}", source=source)


def test_default_count_desc(db_path):
    """Default order is count desc, topic asc tiebreak."""
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 3, "hn": 1, "lex": 2})
        rows = db.notes.list_source_counts()
    assert rows == [("karpathy", 3), ("lex", 2), ("hn", 1)]


def test_null_sources_excluded(db_path):
    """Notes with source=NULL don't appear in results."""
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 2, None: 5})
        rows = db.notes.list_source_counts()
    assert rows == [("karpathy", 2)]


def test_prefix_filter(db_path):
    """prefix matches case-insensitively."""
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 1, "kaczynski": 1, "lex": 2})
        rows = db.notes.list_source_counts(prefix="ka")
    assert {r[0] for r in rows} == {"karpathy", "kaczynski"}


def test_min_max_count(db_path):
    with Database(db_path) as db:
        _seed_notes(db, {"a": 5, "b": 3, "c": 1})
        rows = db.notes.list_source_counts(min_count=2, max_count=4)
    assert rows == [("b", 3)]


def test_order_source_asc(db_path):
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 3, "altman": 1})
        rows = db.notes.list_source_counts(order="source_asc")
    assert rows == [("altman", 1), ("karpathy", 3)]


def test_invalid_order_raises(db_path):
    with Database(db_path) as db:
        _seed_notes(db, {"karpathy": 1})
        with pytest.raises(ValueError):
            db.notes.list_source_counts(order="banana")


def test_limit(db_path):
    with Database(db_path) as db:
        _seed_notes(db, {"a": 1, "b": 2, "c": 3, "d": 4})
        rows = db.notes.list_source_counts(limit=2)
    assert len(rows) == 2
```

- [ ] **Step 3.2: Run tests to confirm failure**

Run: `pytest tests/mcp/test_source_counts.py -v`
Expected: FAIL — `list_source_counts` doesn't exist.

- [ ] **Step 3.3: Implement `list_source_counts` on `NoteStore`**

Add to `src/capxure/note/__init__.py` (before the existing `_fetch_one` method):

Add the import at the top:
```python
from typing import Any
```

Then in the class:

```python
_VALID_SOURCE_ORDERS = {
    "count_desc": "c DESC, source ASC",
    "count_asc":  "c ASC, source ASC",
    "source_asc": "source ASC",
}

def list_source_counts(
    self,
    *,
    prefix: str | None = None,
    min_count: int | None = None,
    max_count: int | None = None,
    order: str = "count_desc",
    limit: int | None = None,
) -> list[tuple[str, int]]:
    """Return (source, count) over notes with non-NULL source.

    Mirrors RepoStore.list_topic_counts: prefix + min/max_count + order all
    compose in SQL. NULL sources are always excluded — they're not a "source."
    """
    if order not in self._VALID_SOURCE_ORDERS:
        raise ValueError(
            f"order must be one of {sorted(self._VALID_SOURCE_ORDERS)}, got {order!r}"
        )
    order_clause = self._VALID_SOURCE_ORDERS[order]

    where_parts: list[str] = ["source IS NOT NULL"]
    params: list[Any] = []
    if prefix is not None:
        where_parts.append("LOWER(source) LIKE ?")
        params.append(prefix.lower() + "%")

    having_parts: list[str] = []
    if min_count is not None:
        having_parts.append("c >= ?")
        params.append(min_count)
    if max_count is not None:
        having_parts.append("c <= ?")
        params.append(max_count)

    sql = "SELECT source, COUNT(*) AS c FROM notes"
    sql += " WHERE " + " AND ".join(where_parts)
    sql += " GROUP BY source"
    if having_parts:
        sql += " HAVING " + " AND ".join(having_parts)
    sql += f" ORDER BY {order_clause}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    return [(row["source"], row["c"])
            for row in self._connection.execute(sql, params).fetchall()]
```

- [ ] **Step 3.4: Run tests**

Run: `pytest tests/mcp/test_source_counts.py -v`
Expected: All PASS.

- [ ] **Step 3.5: Commit**

```bash
git add src/capxure/note/__init__.py tests/mcp/test_source_counts.py
git commit -m "$(cat <<'EOF'
NoteStore: add list_source_counts (discovery over notes.source)

Mirrors list_topic_counts shape: prefix, min/max_count, order, limit.
NULL sources are always excluded.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `RepoStore.search` (FTS5)

**Files:**
- Modify: `src/capxure/git/store.py` (add `RepoHit` dataclass + `search()` method)
- Test: `tests/mcp/test_repo_search.py`

- [ ] **Step 4.1: Write failing tests**

Create `tests/mcp/test_repo_search.py`:

```python
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
    """A match in full_name ranks above a match buried in a long README."""
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="facebook", name="react",
                     readme="lorem ipsum dolor sit amet" * 100)
        _insert_repo(db, github_id=2, owner="o", name="other",
                     readme=("react " * 200))
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
```

- [ ] **Step 4.2: Run tests to confirm failure**

Run: `pytest tests/mcp/test_repo_search.py -v`
Expected: FAIL — `RepoHit` and `RepoStore.search` don't exist.

- [ ] **Step 4.3: Add `RepoHit` dataclass + `search` method to `RepoStore`**

Modify `src/capxure/git/store.py`:

Update `__all__` to include `RepoHit`:
```python
__all__ = [
    "DuplicateRepoNameError",
    "Repo",
    "RepoHit",
    "RepoStore",
    "UpsertOutcome",
]
```

Add the `RepoHit` dataclass after the `Repo` dataclass:

```python
@dataclass(frozen=True)
class RepoHit:
    """A single search hit. Lean shape — fetch full README via get_readme."""
    owner: str
    name: str
    full_name: str
    url: str
    language: str | None
    stars: int
    description: str | None
    snippet: str
    score: float
```

Add the `search` method to `RepoStore` (anywhere in the read-path section, conventionally after `existing_urls`):

```python
def search(
    self,
    query: str,
    *,
    topics: Sequence[str] | None = None,
    language: str | None = None,
    k: int = 20,
) -> list[RepoHit]:
    """FTS5-backed search across full_name + description + readme_content.

    BM25 weights: full_name 10x, description 5x, readme 1x.
    Snippets are taken from readme_content (col 2) only — when the match
    is on a shorter column, snippet is empty.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    k = max(1, min(k, 100))

    sql_parts = [
        "SELECT repos.owner, repos.name, repos.full_name, repos.url,",
        "       repos.language, repos.stars, repos.description,",
        "       COALESCE(snippet(repos_fts, 2, '<<', '>>', '...', 32), '') AS snippet,",
        "       bm25(repos_fts, 10.0, 5.0, 1.0) AS score",
        "FROM repos_fts",
        "JOIN repos ON repos.id = repos_fts.rowid",
        "WHERE repos_fts MATCH ?",
    ]
    params: list[Any] = [query]

    if topics:
        placeholders = ",".join("?" for _ in topics)
        sql_parts.append(
            f"AND repos.id IN ("
            f"  SELECT repo_id FROM repo_topics "
            f"  WHERE LOWER(topic) IN ({placeholders})"
            f")"
        )
        params.extend(t.lower() for t in topics)

    if language is not None:
        sql_parts.append("AND repos.language = ?")
        params.append(language)

    sql_parts.append("ORDER BY score ASC LIMIT ?")
    params.append(k)

    rows = self.connection.execute(" ".join(sql_parts), params).fetchall()
    return [
        RepoHit(
            owner=row["owner"],
            name=row["name"],
            full_name=row["full_name"],
            url=row["url"],
            language=row["language"],
            stars=row["stars"],
            description=row["description"],
            snippet=row["snippet"],
            score=row["score"],
        )
        for row in rows
    ]
```

Also update the top-level `src/capxure/__init__.py` to re-export `RepoHit`:

```python
from capxure.git.store import (
    DuplicateRepoNameError,
    Repo,
    RepoHit,
    RepoStore,
    UpsertOutcome,
)
```

And add `"RepoHit"` to its `__all__`.

- [ ] **Step 4.4: Run tests**

Run: `pytest tests/mcp/test_repo_search.py -v`
Expected: All PASS.

- [ ] **Step 4.5: Commit**

```bash
git add src/capxure/git/store.py src/capxure/__init__.py tests/mcp/test_repo_search.py
git commit -m "$(cat <<'EOF'
RepoStore.search: FTS5-backed lexical search with BM25 weighting

Returns RepoHit objects with snippet + score. full_name 10x, description 5x,
readme 1x — keeps direct name matches above incidental README mentions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add `NoteStore.search` (FTS5)

**Files:**
- Modify: `src/capxure/note/__init__.py` (add `NoteHit` dataclass + `search()` method)
- Modify: `src/capxure/__init__.py` (re-export `NoteHit`)
- Test: `tests/mcp/test_note_search.py`

- [ ] **Step 5.1: Write failing tests**

Create `tests/mcp/test_note_search.py`:

```python
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
    """A note whose source matches the query ranks above a note whose body mentions it."""
    with Database(db_path) as db:
        db.notes.add("an essay about ML training " * 20, source="other")
        db.notes.add("brief note", source="karpathy")
        hits = db.notes.search("karpathy")
    assert hits[0].source == "karpathy"


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
```

- [ ] **Step 5.2: Run to confirm failure**

Run: `pytest tests/mcp/test_note_search.py -v`
Expected: FAIL — `NoteHit` and `NoteStore.search` don't exist.

- [ ] **Step 5.3: Add `NoteHit` + `search` to `NoteStore`**

Modify `src/capxure/note/__init__.py`:

Update `__all__`:
```python
__all__ = ["Note", "NoteHit", "NoteStore"]
```

Add `NoteHit` after `Note`:

```python
@dataclass(frozen=True)
class NoteHit:
    id: int
    snippet: str
    annotation: str | None
    source: str | None
    source_locator: str | None
    captured_at: str
    score: float
```

Add `search` to `NoteStore`:

```python
def search(
    self,
    query: str,
    *,
    sources: list[str] | None = None,
    k: int = 20,
) -> list[NoteHit]:
    """FTS5-backed search across content + annotation + source.

    BM25 weights: content 1x, annotation 3x, source 8x — notes attributed
    to a source rank above notes that just mention it in passing.
    Snippets are from notes.content (col 0).
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    k = max(1, min(k, 100))

    sql_parts = [
        "SELECT notes.id, notes.annotation, notes.source,",
        "       notes.source_locator, notes.captured_at,",
        "       COALESCE(snippet(notes_fts, 0, '<<', '>>', '...', 32), '') AS snippet,",
        "       bm25(notes_fts, 1.0, 3.0, 8.0) AS score",
        "FROM notes_fts",
        "JOIN notes ON notes.id = notes_fts.rowid",
        "WHERE notes_fts MATCH ?",
    ]
    params: list[Any] = [query]

    if sources:
        placeholders = ",".join("?" for _ in sources)
        sql_parts.append(f"AND LOWER(notes.source) IN ({placeholders})")
        params.extend(s.lower() for s in sources)

    sql_parts.append("ORDER BY score ASC LIMIT ?")
    params.append(k)

    rows = self._connection.execute(" ".join(sql_parts), params).fetchall()
    return [
        NoteHit(
            id=row["id"],
            snippet=row["snippet"],
            annotation=row["annotation"],
            source=row["source"],
            source_locator=row["source_locator"],
            captured_at=row["captured_at"],
            score=row["score"],
        )
        for row in rows
    ]
```

Update `src/capxure/__init__.py` to re-export `NoteHit`:

```python
from capxure.note import Note, NoteHit, NoteStore
```

And add `"NoteHit"` to its `__all__`.

- [ ] **Step 5.4: Run tests**

Run: `pytest tests/mcp/test_note_search.py -v`
Expected: All PASS.

- [ ] **Step 5.5: Commit**

```bash
git add src/capxure/note/__init__.py src/capxure/__init__.py tests/mcp/test_note_search.py
git commit -m "$(cat <<'EOF'
NoteStore.search: FTS5-backed search with source-weighted BM25

Returns NoteHit objects with snippet + score. content 1x, annotation 3x,
source 8x — notes attributed to a source rank above incidental mentions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add `mcp` dependency, `cap mcp` CLI subcommand, server scaffolding

**Files:**
- Modify: `pyproject.toml` (add `mcp` dep)
- Create: `src/capxure/mcp/__init__.py`
- Create: `src/capxure/mcp/server.py`
- Create: `src/capxure/mcp/tools.py`
- Create: `src/capxure/cli/mcp.py`
- Modify: `src/capxure/cli/__init__.py` (add `mcp` route)

This task wires the server with no tools registered. Tool handlers ship in subsequent tasks.

- [ ] **Step 6.1: Add mcp dep**

Modify `pyproject.toml`:

```toml
dependencies = [
    "httpx>=0.27.0",
    "mcp>=1.0",
    "platformdirs>=4",
]
```

Run `pip install -e ".[dev]"` (or whatever the user's standard install command is) to get the `mcp` package.

- [ ] **Step 6.2: Create the empty tools module**

Create `src/capxure/mcp/tools.py`:

```python
"""Pure tool handler functions. Each takes a Database and returns a JSON-serializable dict/list.

Handlers are deliberately thin wrappers over RepoStore / NoteStore so they can
be tested in isolation without spinning up the MCP runtime.
"""
from __future__ import annotations

# Tool handlers will be added in subsequent tasks.
```

- [ ] **Step 6.3: Create the server module**

Create `src/capxure/mcp/server.py`:

```python
"""FastMCP server factory. Registers tool handlers and binds them to a Database."""
from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from capxure.db import Database


def build_server(db_path: Path | None = None) -> tuple[FastMCP, Database]:
    """Build a FastMCP server bound to a Database.

    Returns (server, database). The caller is responsible for closing the
    database when the server shuts down.
    """
    db = Database(db_path=db_path) if db_path is not None else Database()
    app = FastMCP("capxure")

    # Tool registrations land here (filled in by later tasks).

    return app, db
```

- [ ] **Step 6.4: Create the package entry**

Create `src/capxure/mcp/__init__.py`:

```python
"""capxure MCP server — read-only tools over the captured repo / note corpus."""
from __future__ import annotations

from capxure.mcp.server import build_server

__all__ = ["build_server"]
```

- [ ] **Step 6.5: Create the CLI subcommand**

Create `src/capxure/cli/mcp.py`:

```python
"""`cap mcp` subcommand: spawn a stdio MCP server."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cap mcp",
        description="Run capxure as a stdio MCP server.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directory containing capxure.db (defaults to platformdirs location).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `cap mcp`. Argv is the args after `mcp`."""
    args_list = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(args_list)

    db_path = (
        (Path(args.data_dir).expanduser().resolve() / "capxure.db")
        if args.data_dir is not None else None
    )

    # Import lazily so `cap --help` doesn't pay the mcp-import cost.
    from capxure.mcp import build_server

    app, db = build_server(db_path)
    try:
        app.run("stdio")
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    finally:
        db.close()
    return 0
```

- [ ] **Step 6.6: Wire the route in `cli/__init__.py`**

Modify `src/capxure/cli/__init__.py`:

Update the imports:
```python
from capxure.cli import git, mcp, note
```

Update `build_parser()` — add an `mcp` subparser:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cap",
        description="capxure — capture and organize.",
    )
    subparsers = parser.add_subparsers(dest="domain", metavar="{git,mcp,note}")

    git_parser = subparsers.add_parser(
        "git",
        help="GitHub repo capture commands (capture, ls, stars).",
        add_help=False,
    )
    git_parser.set_defaults(_domain="git")

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run capxure as a stdio MCP server.",
        add_help=False,
    )
    mcp_parser.set_defaults(_domain="mcp")

    note_parser = subparsers.add_parser(
        "note",
        help="Quick-capture notes (add, ls).",
        add_help=False,
    )
    note_parser.set_defaults(_domain="note")

    return parser
```

Update `main()` — add the mcp dispatch:

```python
def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if not args_list:
        build_parser().print_usage(sys.stderr)
        return 2

    if args_list[0] == "git":
        return git.main(args_list[1:])
    if args_list[0] == "mcp":
        return mcp.main(args_list[1:])
    if args_list[0] == "note":
        return note.main(args_list[1:])

    build_parser().parse_args(args_list)
    return 2
```

- [ ] **Step 6.7: Smoke check the import**

Run: `python -c "from capxure.mcp import build_server; print(build_server.__name__)"`
Expected: prints `build_server`. No import errors.

Run: `cap mcp --help`
Expected: argparse help text shown. (Don't actually run the server yet — it would block on stdio.)

- [ ] **Step 6.8: Commit**

```bash
git add pyproject.toml src/capxure/mcp/ src/capxure/cli/mcp.py src/capxure/cli/__init__.py
git commit -m "$(cat <<'EOF'
Add cap mcp scaffolding: stdio server + CLI route (no tools yet)

Sets up the capxure.mcp subpackage with FastMCP, the cap mcp CLI subcommand,
and the top-level CLI router. Tool handlers land in subsequent commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Tools — `get_repo` and `get_readme`

**Files:**
- Modify: `src/capxure/mcp/tools.py`
- Modify: `src/capxure/mcp/server.py`
- Modify: `src/capxure/git/store.py` (small read-by-key helper if needed)
- Test: `tests/mcp/test_tools.py`

These two tools are the simplest: direct lookups by `(owner, name)`.

- [ ] **Step 7.1: Write failing tool handler tests**

Create `tests/mcp/test_tools.py`:

```python
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
```

- [ ] **Step 7.2: Run to confirm failure**

Run: `pytest tests/mcp/test_tools.py -v`
Expected: FAIL — `tools.get_repo` and `tools.get_readme` don't exist.

- [ ] **Step 7.3: Implement the handlers**

Modify `src/capxure/mcp/tools.py` — replace its contents:

```python
"""Pure tool handler functions. Each takes a Database and returns a JSON-serializable dict/list.

Handlers are deliberately thin wrappers over RepoStore / NoteStore so they can
be tested in isolation without spinning up the MCP runtime.
"""
from __future__ import annotations

from typing import Any

from capxure.db import Database


def get_repo(db: Database, *, owner: str, name: str) -> dict[str, Any] | None:
    """Return structured metadata for a captured repo, or None if missing.

    Excludes readme_content; use get_readme for that.
    """
    repo = db.repos.get_repo(owner, name)
    if repo is None:
        return None
    return {
        "owner": repo.owner,
        "name": repo.name,
        "full_name": repo.full_name,
        "url": repo.url,
        "default_branch": repo.default_branch,
        "description": repo.description,
        "language": repo.language,
        "stars": repo.stars,
        "forks": repo.forks,
        "pushed_at": repo.pushed_at,
        "is_fork": repo.is_fork,
        "is_archived": repo.is_archived,
        "topics": list(repo.topics),
        "captured_at": repo.captured_at,
        "last_synced_at": repo.last_synced_at,
    }


def get_readme(
    db: Database, *, owner: str, name: str
) -> dict[str, Any] | None:
    """Return the full README for a repo. None if the repo isn't captured.

    `readme_content` may be None for repos that genuinely have no README.
    """
    repo = db.repos.get_repo(owner, name)
    if repo is None:
        return None
    return {
        "owner": repo.owner,
        "name": repo.name,
        "readme_content": repo.readme_content,
    }
```

- [ ] **Step 7.4: Register the tools in the server**

Modify `src/capxure/mcp/server.py`:

```python
"""FastMCP server factory. Registers tool handlers and binds them to a Database."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from capxure.db import Database
from capxure.mcp import tools


def build_server(db_path: Path | None = None) -> tuple[FastMCP, Database]:
    """Build a FastMCP server bound to a Database.

    Returns (server, database). The caller is responsible for closing the
    database when the server shuts down.
    """
    db = Database(db_path=db_path) if db_path is not None else Database()
    app = FastMCP("capxure")

    @app.tool()
    def get_repo(owner: str, name: str) -> dict[str, Any] | None:
        """Return structured metadata for a captured GitHub repo (no README body).

        Returns null if the repo isn't in the library.
        """
        return tools.get_repo(db, owner=owner, name=name)

    @app.tool()
    def get_readme(owner: str, name: str) -> dict[str, Any] | None:
        """Return the full README of a captured GitHub repo.

        Returns null if the repo isn't captured. `readme_content` may itself
        be null for repos that genuinely have no README.
        """
        return tools.get_readme(db, owner=owner, name=name)

    return app, db
```

- [ ] **Step 7.5: Run tool tests**

Run: `pytest tests/mcp/test_tools.py -v`
Expected: All PASS.

- [ ] **Step 7.6: Commit**

```bash
git add src/capxure/mcp/tools.py src/capxure/mcp/server.py tests/mcp/test_tools.py
git commit -m "$(cat <<'EOF'
mcp: add get_repo and get_readme tools

Pure handlers over RepoStore.get_repo. Exclude README body from get_repo
(use get_readme instead). Both return null for missing repos.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Tools — `list_topics` and `list_sources`

**Files:**
- Modify: `src/capxure/mcp/tools.py`
- Modify: `src/capxure/mcp/server.py`
- Test: `tests/mcp/test_tools.py` (add cases)

- [ ] **Step 8.1: Add failing tests**

Append to `tests/mcp/test_tools.py`:

```python
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
        for i in range(5):
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
        result = tools.list_sources(db, sources_prefix="kar")
    assert result == [{"source": "karpathy", "count": 1}]
```

Note: the tool name for the source-prefix arg is `prefix` in the handler signature; the test uses `sources_prefix=` only as a placeholder — adjust to `prefix=` after Step 8.2 implements the handler. (See implementation below — the actual call is `tools.list_sources(db, prefix="kar")`. Update the test accordingly before running it.)

Replace the last test with the corrected version:

```python
def test_list_sources_passes_filters(db_path):
    with Database(db_path) as db:
        db.notes.add("a", source="karpathy")
        db.notes.add("b", source="lex")
        result = tools.list_sources(db, prefix="kar")
    assert result == [{"source": "karpathy", "count": 1}]
```

- [ ] **Step 8.2: Implement the handlers**

Append to `src/capxure/mcp/tools.py`:

```python
def list_topics(
    db: Database,
    *,
    prefix: str | None = None,
    min_count: int | None = None,
    max_count: int | None = None,
    order: str = "count_desc",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return [{topic, count}, ...] across captured repos."""
    limit = max(1, min(limit, 500))
    rows = db.repos.list_topic_counts(
        prefix=prefix,
        min_count=min_count,
        max_count=max_count,
        order=order,
        limit=limit,
    )
    return [{"topic": t, "count": c} for t, c in rows]


def list_sources(
    db: Database,
    *,
    prefix: str | None = None,
    min_count: int | None = None,
    max_count: int | None = None,
    order: str = "count_desc",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return [{source, count}, ...] across notes (NULL sources excluded)."""
    limit = max(1, min(limit, 500))
    rows = db.notes.list_source_counts(
        prefix=prefix,
        min_count=min_count,
        max_count=max_count,
        order=order,
        limit=limit,
    )
    return [{"source": s, "count": c} for s, c in rows]
```

- [ ] **Step 8.3: Register the tools**

In `src/capxure/mcp/server.py`, inside `build_server` after the existing `@app.tool()` blocks, add:

```python
@app.tool()
def list_topics(
    prefix: str | None = None,
    min_count: int | None = None,
    max_count: int | None = None,
    order: str = "count_desc",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List topic names with their repo counts.

    `order` is one of "count_desc" (default), "count_asc", "topic_asc".
    `prefix` filters to topics starting with the given string (case-insensitive).
    `min_count` / `max_count` bound the result by topic frequency.
    Useful for discovering what topics exist before filtering search_repos.
    """
    return tools.list_topics(
        db, prefix=prefix, min_count=min_count, max_count=max_count,
        order=order, limit=limit,
    )

@app.tool()
def list_sources(
    prefix: str | None = None,
    min_count: int | None = None,
    max_count: int | None = None,
    order: str = "count_desc",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List note sources with their note counts.

    Same shape as list_topics but over notes.source. NULL sources are excluded.
    Useful for discovering what authors/projects you've taken notes from.
    """
    return tools.list_sources(
        db, prefix=prefix, min_count=min_count, max_count=max_count,
        order=order, limit=limit,
    )
```

- [ ] **Step 8.4: Run tests**

Run: `pytest tests/mcp/test_tools.py -v`
Expected: All PASS.

- [ ] **Step 8.5: Commit**

```bash
git add src/capxure/mcp/tools.py src/capxure/mcp/server.py tests/mcp/test_tools.py
git commit -m "$(cat <<'EOF'
mcp: add list_topics and list_sources discovery tools

Mirror shapes (prefix, min_count, max_count, order, limit). Server-side
clamps limit to <=500. Lets Claude see what topics/sources exist before
filtering search calls.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Tool — `search_repos`

**Files:**
- Modify: `src/capxure/mcp/tools.py`
- Modify: `src/capxure/mcp/server.py`
- Test: `tests/mcp/test_tools.py` (add cases)

- [ ] **Step 9.1: Add failing tests**

Append to `tests/mcp/test_tools.py`:

```python
# --- search_repos ---

def test_search_repos_returns_hit_dicts(db_path):
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="r",
                     language="Python", description="cool tool",
                     stars=10, readme="auth library for python")
        result = tools.search_repos(db, query="auth")
    assert len(result) == 1
    hit = result[0]
    assert set(hit.keys()) == {
        "owner", "name", "full_name", "url",
        "language", "stars", "description",
        "snippet", "score",
    }
    assert hit["owner"] == "o"
    assert hit["language"] == "Python"
    assert hit["stars"] == 10


def test_search_repos_empty_query_raises(db_path):
    with Database(db_path) as db:
        with pytest.raises(ValueError):
            tools.search_repos(db, query="")


def test_search_repos_clamps_k(db_path):
    """k > 100 is clamped server-side."""
    with Database(db_path) as db:
        for i in range(5):
            _insert_repo(db, github_id=i, owner="o", name=f"r{i}",
                         readme="match")
        result = tools.search_repos(db, query="match", k=999)
    assert len(result) == 5  # only 5 hits exist


def test_search_repos_passes_topic_filter(db_path):
    with Database(db_path) as db:
        _insert_repo(db, github_id=1, owner="o", name="a", readme="lib")
        _insert_repo(db, github_id=2, owner="o", name="b", readme="lib")
        db.connection.execute(
            "INSERT INTO repo_topics (repo_id, topic) "
            "SELECT id, 'rust' FROM repos WHERE name='a'"
        )
        result = tools.search_repos(db, query="lib", topics=["rust"])
    assert len(result) == 1
    assert result[0]["name"] == "a"
```

- [ ] **Step 9.2: Implement the handler**

Append to `src/capxure/mcp/tools.py`:

```python
def search_repos(
    db: Database,
    *,
    query: str,
    topics: list[str] | None = None,
    language: str | None = None,
    k: int = 20,
) -> list[dict[str, Any]]:
    """FTS5 search over repos. Raises ValueError on empty query."""
    hits = db.repos.search(query, topics=topics, language=language, k=k)
    return [
        {
            "owner": h.owner,
            "name": h.name,
            "full_name": h.full_name,
            "url": h.url,
            "language": h.language,
            "stars": h.stars,
            "description": h.description,
            "snippet": h.snippet,
            "score": h.score,
        }
        for h in hits
    ]
```

- [ ] **Step 9.3: Register the tool**

In `src/capxure/mcp/server.py`, inside `build_server`, add:

```python
@app.tool()
def search_repos(
    query: str,
    topics: list[str] | None = None,
    language: str | None = None,
    k: int = 20,
) -> list[dict[str, Any]]:
    """Search captured GitHub repos by README + name + description (FTS5).

    Plain words work fine; FTS5 operators (AND, OR, NEAR, "phrase") are also
    accepted. Hits are ranked by BM25 (lower score = more relevant).

    `topics` (list of strings, OR'd, case-insensitive exact match) and
    `language` (exact match) compose with the text query. `k` defaults to 20,
    capped at 100.

    Each hit contains a `snippet` from the README (`<<...>>` markers around
    matches) when the match is in the README; empty otherwise. Use get_readme
    to fetch the full README for a promising hit.
    """
    return tools.search_repos(
        db, query=query, topics=topics, language=language, k=k,
    )
```

- [ ] **Step 9.4: Run tests**

Run: `pytest tests/mcp/test_tools.py -v -k search_repos`
Expected: PASS.

- [ ] **Step 9.5: Commit**

```bash
git add src/capxure/mcp/tools.py src/capxure/mcp/server.py tests/mcp/test_tools.py
git commit -m "$(cat <<'EOF'
mcp: add search_repos tool

FTS5-backed search over name + description + README, with optional topic
and language filters. Returns lean hits with snippets; full README via
get_readme.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Tool — `search_notes`

**Files:**
- Modify: `src/capxure/mcp/tools.py`
- Modify: `src/capxure/mcp/server.py`
- Test: `tests/mcp/test_tools.py` (add cases)

- [ ] **Step 10.1: Add failing tests**

Append to `tests/mcp/test_tools.py`:

```python
# --- search_notes ---

def test_search_notes_returns_hit_dicts(db_path):
    with Database(db_path) as db:
        db.notes.add("matching content here",
                     annotation="annot",
                     source="karpathy",
                     source_locator="loc/x")
        result = tools.search_notes(db, query="matching")
    assert len(result) == 1
    hit = result[0]
    assert set(hit.keys()) == {
        "id", "snippet", "annotation", "source",
        "source_locator", "captured_at", "score",
    }
    assert hit["source"] == "karpathy"
    assert hit["annotation"] == "annot"


def test_search_notes_empty_query_raises(db_path):
    with Database(db_path) as db:
        with pytest.raises(ValueError):
            tools.search_notes(db, query="   ")


def test_search_notes_clamps_k(db_path):
    with Database(db_path) as db:
        for i in range(5):
            db.notes.add(f"common term n{i}", source="src")
        result = tools.search_notes(db, query="common", k=999)
    assert len(result) == 5


def test_search_notes_passes_sources_filter(db_path):
    with Database(db_path) as db:
        db.notes.add("matching", source="karpathy")
        db.notes.add("matching", source="lex")
        result = tools.search_notes(db, query="matching", sources=["lex"])
    assert len(result) == 1
    assert result[0]["source"] == "lex"
```

- [ ] **Step 10.2: Implement the handler**

Append to `src/capxure/mcp/tools.py`:

```python
def search_notes(
    db: Database,
    *,
    query: str,
    sources: list[str] | None = None,
    k: int = 20,
) -> list[dict[str, Any]]:
    """FTS5 search over notes. Raises ValueError on empty query."""
    hits = db.notes.search(query, sources=sources, k=k)
    return [
        {
            "id": h.id,
            "snippet": h.snippet,
            "annotation": h.annotation,
            "source": h.source,
            "source_locator": h.source_locator,
            "captured_at": h.captured_at,
            "score": h.score,
        }
        for h in hits
    ]
```

- [ ] **Step 10.3: Register the tool**

In `src/capxure/mcp/server.py`, inside `build_server`, add:

```python
@app.tool()
def search_notes(
    query: str,
    sources: list[str] | None = None,
    k: int = 20,
) -> list[dict[str, Any]]:
    """Search captured notes by content + annotation + source (FTS5).

    Plain words work; FTS5 operators are accepted. BM25 weights source 8x,
    annotation 3x, content 1x — notes attributed to a source rank above
    notes that just mention it.

    `sources` (list, OR'd, case-insensitive exact match against notes.source)
    composes with the text query. `k` defaults to 20, capped at 100.
    """
    return tools.search_notes(db, query=query, sources=sources, k=k)
```

- [ ] **Step 10.4: Run tests**

Run: `pytest tests/mcp/test_tools.py -v`
Expected: All PASS (entire test_tools.py file).

- [ ] **Step 10.5: Commit**

```bash
git add src/capxure/mcp/tools.py src/capxure/mcp/server.py tests/mcp/test_tools.py
git commit -m "$(cat <<'EOF'
mcp: add search_notes tool

FTS5-backed search over notes content/annotation/source with sources filter.
Source-weighted ranking surfaces attributed notes above incidental mentions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: End-to-end stdio smoke test

**Files:**
- Test: `tests/mcp/test_server_smoke.py`

This task verifies the wiring end-to-end by spawning `cap mcp` as a subprocess, performing the standard MCP handshake, and invoking one tool.

- [ ] **Step 11.1: Write the smoke test**

Create `tests/mcp/test_server_smoke.py`:

```python
"""End-to-end smoke test: spawn `cap mcp` as a subprocess and round-trip a tool call."""

import json
import os
import subprocess
import sys
import sqlite3

import pytest


def _send(proc: subprocess.Popen, payload: dict) -> None:
    line = json.dumps(payload) + "\n"
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()


def _recv(proc: subprocess.Popen) -> dict:
    line = proc.stdout.readline()
    assert line, "MCP server returned no response"
    return json.loads(line.decode("utf-8"))


def _seed(db_path):
    """Create a minimal v3 db with one repo we can search for."""
    # Importing here keeps the file independent of when DB schema changes.
    from capxure.db import Database
    with Database(db_path) as db:
        db.connection.execute(
            "INSERT INTO repos "
            "(github_id, owner, name, full_name, url, "
            " language, description, readme_content, stars, metadata) "
            "VALUES (1, 'octocat', 'hello-auth', 'octocat/hello-auth',"
            " 'https://github.com/octocat/hello-auth', 'Python',"
            " 'auth lib', 'authentication library for python', 100, '{}')"
        )


def test_cap_mcp_initializes_and_calls_tool(tmp_path):
    """Round-trip initialize -> tools/list -> tools/call(search_repos) over stdio."""
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    _seed(db_dir / "capxure.db")

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        ["cap", "mcp", "--data-dir", str(db_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        # 1. initialize
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "0.0"},
            },
        })
        init_resp = _recv(proc)
        assert init_resp.get("id") == 1
        assert "result" in init_resp

        # 2. initialized notification (no response expected)
        _send(proc, {
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })

        # 3. tools/list — confirm all six are present
        _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        })
        list_resp = _recv(proc)
        tool_names = {t["name"] for t in list_resp["result"]["tools"]}
        assert tool_names == {
            "search_repos", "get_repo", "get_readme",
            "list_topics", "search_notes", "list_sources",
        }

        # 4. tools/call search_repos
        _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "search_repos",
                "arguments": {"query": "authentication"},
            },
        })
        call_resp = _recv(proc)
        assert call_resp.get("id") == 3
        # The result is wrapped in MCP content blocks; extract the JSON payload.
        content = call_resp["result"]["content"]
        assert len(content) >= 1
        # FastMCP serializes structured returns as JSON inside a text content block.
        payload = json.loads(content[0]["text"])
        assert isinstance(payload, list)
        assert len(payload) >= 1
        assert payload[0]["name"] == "hello-auth"
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
```

- [ ] **Step 11.2: Run the smoke test**

Run: `pytest tests/mcp/test_server_smoke.py -v`
Expected: PASS. The subprocess starts, accepts JSON-RPC frames over stdio, lists six tools, and returns a search result containing `hello-auth`.

If FastMCP's content serialization differs (e.g., it returns the structured object directly rather than as a JSON string), the test's `payload = json.loads(content[0]["text"])` line may need adjustment. Check the actual response shape with a print on first failure.

- [ ] **Step 11.3: Run the full test suite**

Run: `pytest -v`
Expected: All PASS — no regressions in any prior tests.

- [ ] **Step 11.4: Commit**

```bash
git add tests/mcp/test_server_smoke.py
git commit -m "$(cat <<'EOF'
mcp: end-to-end smoke test for cap mcp stdio server

Spawns cap mcp as a subprocess, performs the MCP handshake, lists tools,
and round-trips a search_repos call.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

After Task 11:

- [ ] Run `pytest -v` — all tests pass.
- [ ] Run `cap mcp --help` — prints argparse help.
- [ ] Run `cap --help` — shows `mcp` in the subcommand list.
- [ ] Manually register `cap mcp` in Claude Code's MCP settings, restart Claude Code, and confirm the six tools appear under the `capxure` server.

This last manual step isn't a unit test — it's the only way to confirm the wiring is right from the actual consumer's perspective.

---

## Self-review notes

- Spec coverage: every section of `2026-04-27-mcp-server-design.md` maps to a task. Schema (§Schema Changes) → Task 1. Tools (§Tool Surface 1–6) → Tasks 4, 7, 8, 9, 10. Store extensions (implicit in §Tool Surface 4 + 6) → Tasks 2, 3. Server lifecycle (§Architecture, §Error Handling) → Task 6 + smoke test in Task 11. Testing (§Testing) → covered across tasks plus the e2e smoke.
- Type/name consistency: `RepoHit`, `NoteHit`, `Database`, `RepoStore`, `NoteStore`, `build_server`, `tools.get_repo` etc. used consistently across tasks.
- Placeholder scan: no TBDs/TODOs; one note in Task 11 about adapting the FastMCP content shape if it differs from the assumption — that's a documented contingency, not a placeholder.
