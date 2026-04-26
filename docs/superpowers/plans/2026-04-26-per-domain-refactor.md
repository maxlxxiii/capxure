# Per-Domain Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the existing `Storage` class into `Database` (lifecycle) + `RepoStore` (queries) and reorganize the CLI under `cap git`, removing top-level smart-dispatch — without changing any observable behavior or on-disk schema.

**Architecture:** Two-phase refactor. Phase A introduces the new shape (`Database`, `RepoStore`, `cli/git/*`) alongside the old. Phase B switches consumers and deletes the old shape. Each task is committed independently and leaves the codebase in a working, fully-tested state.

**Tech Stack:** Python 3.12+, sqlite3 (stdlib), httpx (async), argparse, pytest. CLI tool, single SQLite DB, no external library users.

**Spec:** `docs/superpowers/specs/2026-04-26-per-domain-refactor-design.md`

---

## Reference: Current Source Structure

| File | Lines | Role |
|---|---|---|
| `src/capxure/storage.py` | 515 | `Storage` (lifecycle + queries), `Repo`, `UpsertOutcome`, `DuplicateRepoNameError`, `UnsupportedSchemaError`, `_resolve_default_db_path`, `_sha256_hex` |
| `src/capxure/github.py` | 206 | `GitHubClient`, `GitHubError`, `AuthenticationError`, `NotFoundError`, `RateLimitExceededError`, `RateLimitInfo`, `parse_github_url`, `_next_link` |
| `src/capxure/processor.py` | 114 | `process_repo`, `ProcessResult`, `Severity`, `StatusCallback` |
| `src/capxure/cli/__init__.py` | 60 | top-level argparse + smart-dispatch on `/` |
| `src/capxure/cli/capture.py` | 101 | `cap capture` handler |
| `src/capxure/cli/list_.py` | 286 | `cap ls` handler |
| `src/capxure/cli/stars.py` | 258 | `cap stars` handler |

| Test file | Lines | Role |
|---|---|---|
| `tests/conftest.py` | 36 | shared fixtures (`db_path`, metadata loaders) |
| `tests/test_imports.py` | 41 | public API surface |
| `tests/test_storage.py` | 609 | `Storage` lifecycle + queries |
| `tests/test_github.py` | 250 | `GitHubClient` |
| `tests/test_processor.py` | 45 | `process_repo` |
| `tests/test_cli.py` | 353 | top-level dispatch + capture handler |
| `tests/test_cli_list.py` | 401 | `cap ls` handler |
| `tests/test_cli_list_smoke.py` | 114 | `cap ls` end-to-end |
| `tests/test_cli_stars.py` | 590 | `cap stars` handler |
| `tests/test_cli_stars_smoke.py` | 34 | `cap stars` end-to-end |

---

## Task 1: Introduce `Database` class in `db.py`

Extract lifecycle (connection, schema, context manager) into a new `Database` class. `Storage` is refactored to hold a `Database` internally and delegate lifecycle to it. All query methods stay on `Storage`. No public API change.

**Files:**
- Create: `src/capxure/db.py`
- Modify: `src/capxure/storage.py` (delegate lifecycle to inner `Database`)
- Create: `tests/test_database.py`

**Methods that move from `Storage` to `Database`:**
- `__init__(db_path)` — full body
- `_ensure_schema()` — full body
- `close()`, `__enter__()`, `__exit__()`, `connection` property — full bodies
- Module-level `_resolve_default_db_path()` — moves from `storage.py` to `db.py`
- `UnsupportedSchemaError` — moves to `db.py`

`Storage` is left with: query methods, `_classify`, `_fetch_internal_by_github_id`, `_insert_repo`, `_update_repo`, `_replace_topics`, `diff`, `_row_to_repo`, plus `Repo`, `UpsertOutcome`, `DuplicateRepoNameError`, `_sha256_hex`.

- [ ] **Step 1: Write failing tests for `Database`**

Create `tests/test_database.py`:

```python
"""Tests for the Database class — lifecycle, schema, context manager."""

import sqlite3

import pytest

from capxure.db import Database, UnsupportedSchemaError


def test_fresh_db_creation(db_path):
    """A new Database() creates the db file, schema, and sets user_version=1."""
    assert not db_path.exists()
    with Database(db_path) as db:
        assert db_path.exists()
        # Tables expected by current schema:
        cursor = db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "repos" in tables
        assert "topics" in tables
        version = db.connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == 1


def test_reopen_existing_db(db_path):
    """Re-opening an existing db doesn't re-run schema creation."""
    with Database(db_path):
        pass
    # Second open succeeds and finds the same schema_version.
    with Database(db_path) as db:
        version = db.connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == 1


def test_unsupported_schema_raises(db_path):
    """Opening a db with a future schema_version raises UnsupportedSchemaError."""
    # Manually create a db with an unknown user_version.
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database.py -v`
Expected: ImportError or "module 'capxure.db' has no attribute 'Database'".

- [ ] **Step 3: Create `src/capxure/db.py`**

```python
"""Connection lifecycle, schema management, and migrations for capxure's SQLite store."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from platformdirs import user_data_dir

SCHEMA_VERSION = 1


class UnsupportedSchemaError(Exception):
    """DB on disk uses a schema version this library doesn't know."""


def _resolve_default_db_path() -> Path:
    """Resolve the default SQLite db location.

    Order: $CAPXURE_DATA_DIR > platformdirs user data dir.
    """
    env_dir = os.environ.get("CAPXURE_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser() / "capxure.db"
    return Path(user_data_dir("capxure", "capxure")) / "capxure.db"


class Database:
    """Owns the SQLite connection, schema lifecycle, and context-manager protocol.

    Query operations live on RepoStore (and future per-domain stores). Acquire
    one via the `repos` property (added in a later task) or by constructing
    `RepoStore(db.connection)` directly.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _resolve_default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._db_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except sqlite3.ProgrammingError:
                pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        """Initialize schema on a fresh DB; reject unknown versions on existing ones."""
        # Copy the body of Storage._ensure_schema from src/capxure/storage.py
        # lines 181-190 verbatim. Replace any `self.connection` reference with
        # `self._connection` (the @property keeps `self.connection` working
        # too, so leaving as-is is fine). No SQL or logic changes.
        ...
```

**Verbatim port from `src/capxure/storage.py:181-190`.** The CREATE TABLE statements for `repos` and `topics`, the `PRAGMA user_version` write on fresh DBs, and the version-mismatch check (raise `UnsupportedSchemaError` for any version other than `SCHEMA_VERSION`) are identical to today's behavior. No schema changes — that's the load-bearing invariant of this whole refactor.

- [ ] **Step 4: Refactor `Storage` to delegate lifecycle to inner `Database`**

In `src/capxure/storage.py`:

1. Remove the `UnsupportedSchemaError` class definition (now in `db.py`).
2. Remove the module-level `_resolve_default_db_path` function (now in `db.py`).
3. Add `from capxure.db import Database, UnsupportedSchemaError` at the top.
4. Re-export `UnsupportedSchemaError` (existing imports from `capxure.storage` continue to work).
5. Replace `Storage.__init__`, `_ensure_schema`, `close`, `__enter__`, `__exit__`, `connection` with delegates:

```python
class Storage:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db = Database(db_path)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._db.connection

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self._db.close()

    # _ensure_schema is gone — handled inside Database.

    # All query methods (upsert, diff, list_repos, etc.) stay as-is.
    # They reference self.connection, which now resolves through the property.
```

- [ ] **Step 5: Run all tests**

Run: `pytest -v`
Expected: all existing tests pass; new `tests/test_database.py` passes.

- [ ] **Step 6: Commit**

```bash
git add src/capxure/db.py src/capxure/storage.py tests/test_database.py
git commit -m "$(cat <<'EOF'
Extract Database class for connection lifecycle

Moves connection/schema/context-manager into capxure.db. Storage now
delegates lifecycle to an inner Database; query methods stay put.
UnsupportedSchemaError and _resolve_default_db_path move to db.py and
are re-exported via storage for backward compat during transition.

No public API change. No schema change.
EOF
)"
```

---

## Task 2: Introduce `RepoStore` class in `git/store.py`

Move query methods, dataclasses, and helpers from `Storage` into a new `RepoStore` class that takes a connection in its constructor. `Storage` constructs a `RepoStore` internally and delegates query methods to it. No public API change.

**Files:**
- Create: `src/capxure/git/__init__.py` (empty marker)
- Create: `src/capxure/git/store.py`
- Modify: `src/capxure/storage.py` (delegate query methods to inner `RepoStore`; re-export moved symbols)
- Create: `tests/git/__init__.py` (empty marker)
- Create: `tests/git/test_store.py`

**Things that move from `storage.py` to `git/store.py`:**
- `Repo` dataclass
- `UpsertOutcome` enum
- `DuplicateRepoNameError`
- `_sha256_hex` helper
- All query/internal methods on `Storage`: `upsert`, `diff`, `_classify`, `_fetch_internal_by_github_id`, `_insert_repo`, `_update_repo`, `_replace_topics`, `get_repo`, `get_repo_by_github_id`, `list_repos`, `count_repos`, `existing_urls`, `list_topic_counts`, `get_metadata_json`, `_row_to_repo`

These methods come over to `RepoStore` with one mechanical change: every reference to `self.connection` in the method body stays the same (RepoStore exposes `self.connection` as the connection it was given).

- [ ] **Step 1: Write failing tests for `RepoStore`**

Create `tests/git/__init__.py`:

```python
```

Create `tests/git/test_store.py`:

```python
"""Tests for RepoStore — repo queries against an injected connection."""

from capxure.db import Database
from capxure.git.store import (
    DuplicateRepoNameError,
    Repo,
    RepoStore,
    UpsertOutcome,
)
from capxure.git.store import _sha256_hex  # noqa: F401  (re-export check)


def _store(db_path):
    db = Database(db_path)
    return RepoStore(db.connection), db


def test_upsert_new_returns_new_outcome(db_path, claude_mem_metadata):
    store, _ = _store(db_path)
    repo, outcome = store.upsert(claude_mem_metadata, readme_content="# Hello")
    assert outcome is UpsertOutcome.NEW
    assert isinstance(repo, Repo)
    assert repo.owner == claude_mem_metadata["owner"]["login"]


def test_upsert_unchanged_on_repeat(db_path, claude_mem_metadata):
    store, _ = _store(db_path)
    store.upsert(claude_mem_metadata, readme_content="# Hello")
    _, outcome = store.upsert(claude_mem_metadata, readme_content="# Hello")
    assert outcome is UpsertOutcome.UNCHANGED


def test_list_repos_returns_inserted(db_path, claude_mem_metadata):
    store, _ = _store(db_path)
    store.upsert(claude_mem_metadata, readme_content="# Hello")
    repos = store.list_repos()
    assert len(repos) == 1
    assert repos[0].owner == claude_mem_metadata["owner"]["login"]


def test_count_repos_starts_at_zero(db_path):
    store, _ = _store(db_path)
    assert store.count_repos() == 0


def test_duplicate_repo_name_raises(db_path, claude_mem_metadata):
    store, _ = _store(db_path)
    store.upsert(claude_mem_metadata, readme_content="# Hello")
    twin = dict(claude_mem_metadata)
    twin["id"] = claude_mem_metadata["id"] + 1  # Different github_id, same owner/name.
    import pytest
    with pytest.raises(DuplicateRepoNameError):
        store.upsert(twin, readme_content="# Hello")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/git/test_store.py -v`
Expected: ImportError on `capxure.git.store`.

- [ ] **Step 3: Create `src/capxure/git/__init__.py`**

```python
```

(Empty file — package marker.)

- [ ] **Step 4: Create `src/capxure/git/store.py`**

The body is a verbatim port of the query/dataclass code currently in `storage.py`:

```python
"""Repo-domain queries against a SQLite connection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class UpsertOutcome(StrEnum):
    """Classification of what an upsert call did to the store."""
    NEW = "new"
    UPDATED = "updated"
    RENAMED = "renamed"
    UNCHANGED = "unchanged"
    LOCAL_IS_NEWER = "local_is_newer"


class DuplicateRepoNameError(Exception):
    """Another GitHub repo already occupies this (owner, name)."""


@dataclass(frozen=True)
class Repo:
    # Verbatim copy of the Repo dataclass body at src/capxure/storage.py:39-58.
    # Field order and types are unchanged.
    ...


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class RepoStore:
    """Repo-domain queries. Construct over a connection from `Database`."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    # Verbatim port of the following methods from the current Storage class
    # in src/capxure/storage.py. The @property above keeps `self.connection`
    # working unchanged in copied bodies — no body edits required.
    #
    #   upsert                          → storage.py:194-223
    #   _classify                       → storage.py:227-257
    #   _fetch_internal_by_github_id    → storage.py:259-264
    #   _insert_repo                    → storage.py:266-304
    #   _update_repo                    → storage.py:306-352
    #   _replace_topics                 → storage.py:354-360
    #   diff                            → storage.py:362-378
    #   get_repo                        → storage.py:382-387
    #   get_repo_by_github_id           → storage.py:389-394
    #   list_repos                      → storage.py:402-435
    #   count_repos                     → storage.py:437-439
    #   existing_urls                   → storage.py:441-455
    #   list_topic_counts               → storage.py:457-478
    #   get_metadata_json               → storage.py:480-485
    #   _row_to_repo                    → storage.py:487-514
    ...
```

- [ ] **Step 5: Refactor `Storage` to delegate query methods to inner `RepoStore`**

In `src/capxure/storage.py`:

1. Delete `UpsertOutcome`, `DuplicateRepoNameError`, `Repo`, `_sha256_hex` definitions.
2. Add `from capxure.git.store import DuplicateRepoNameError, Repo, RepoStore, UpsertOutcome` at the top.
3. Re-export them at the module level so existing `from capxure.storage import Repo` keeps working.
4. In `Storage.__init__`, after constructing the `Database`, build a `RepoStore`:

```python
class Storage:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db = Database(db_path)
        self._repos = RepoStore(self._db.connection)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._db.connection

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self._db.close()

    # Delegate every query method to the inner RepoStore:
    def upsert(self, *args, **kwargs):
        return self._repos.upsert(*args, **kwargs)

    def diff(self, *args, **kwargs):
        return self._repos.diff(*args, **kwargs)

    def get_repo(self, *args, **kwargs):
        return self._repos.get_repo(*args, **kwargs)

    def get_repo_by_github_id(self, *args, **kwargs):
        return self._repos.get_repo_by_github_id(*args, **kwargs)

    def list_repos(self, *args, **kwargs):
        return self._repos.list_repos(*args, **kwargs)

    def count_repos(self, *args, **kwargs):
        return self._repos.count_repos(*args, **kwargs)

    def existing_urls(self, *args, **kwargs):
        return self._repos.existing_urls(*args, **kwargs)

    def list_topic_counts(self, *args, **kwargs):
        return self._repos.list_topic_counts(*args, **kwargs)

    def get_metadata_json(self, *args, **kwargs):
        return self._repos.get_metadata_json(*args, **kwargs)
```

The internal helpers (`_classify`, `_fetch_internal_by_github_id`, `_insert_repo`, `_update_repo`, `_replace_topics`, `_row_to_repo`) move with the public methods to `RepoStore`. They're internal — no `Storage` callers reach them.

5. Update the module-level `__all__` (if present) to keep `Repo`, `UpsertOutcome`, `DuplicateRepoNameError` listed (they're now re-exports).

- [ ] **Step 6: Run all tests**

Run: `pytest -v`
Expected: every existing test passes (Storage's public API behavior is preserved); new `tests/git/test_store.py` passes.

- [ ] **Step 7: Commit**

```bash
git add src/capxure/git/__init__.py src/capxure/git/store.py src/capxure/storage.py tests/git/__init__.py tests/git/test_store.py
git commit -m "$(cat <<'EOF'
Extract RepoStore class for repo-domain queries

Moves query methods, Repo dataclass, UpsertOutcome, DuplicateRepoNameError,
and _sha256_hex into capxure.git.store. Storage now constructs a RepoStore
internally and delegates each public query method through it. The moved
symbols are re-exported from capxure.storage so consumers keep working
during the transition.
EOF
)"
```

---

## Task 3: Add `db.repos` accessor

A property on `Database` that lazily constructs a `RepoStore` over `db.connection`. Consumers get to write `db.repos.upsert(...)` instead of `RepoStore(db.connection).upsert(...)`.

**Files:**
- Modify: `src/capxure/db.py`
- Modify: `tests/test_database.py` (add tests)

- [ ] **Step 1: Write failing test**

Add to `tests/test_database.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database.py::test_repos_accessor_returns_repostore -v`
Expected: AttributeError on `Database.repos`.

- [ ] **Step 3: Add the accessor**

In `src/capxure/db.py`, add to the `Database` class:

```python
class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        # ... existing body ...
        self._repos: "RepoStore | None" = None

    @property
    def repos(self) -> "RepoStore":
        """Lazy accessor — constructs a RepoStore over self.connection on first use."""
        if self._repos is None:
            from capxure.git.store import RepoStore
            self._repos = RepoStore(self._connection)
        return self._repos
```

The `RepoStore` import is local to avoid an import cycle (`db.py` is otherwise a leaf module).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/capxure/db.py tests/test_database.py
git commit -m "$(cat <<'EOF'
Add db.repos lazy accessor for RepoStore

Constructs a single RepoStore instance per Database, cached on first access.
Lets callers write db.repos.upsert(...) instead of constructing the store
explicitly. Local import inside the property avoids an import cycle between
db.py and git.store.
EOF
)"
```

---

## Task 4: Migrate `process_repo` to take `RepoStore`

Change the `process_repo` signature from `storage: Storage` to `repos: RepoStore`. Update the only consumers — `cli/capture.py` and `cli/stars.py` — to pass `db.repos` instead of `storage`. Keep CLI handlers using `Storage` for the moment (Task 5 finishes their migration).

**Files:**
- Modify: `src/capxure/processor.py`
- Modify: `src/capxure/cli/capture.py`
- Modify: `src/capxure/cli/stars.py`
- Modify: `tests/test_processor.py`
- Modify: `tests/test_cli.py` (update `_patch_client_and_storage` and any direct `process_repo` calls)
- Modify: `tests/test_cli_stars.py` (similar)

- [ ] **Step 1: Update `tests/test_processor.py` to construct a `RepoStore`**

The current test imports `Storage`. Change to:

```python
from capxure.db import Database
from capxure.git.store import RepoStore, UpsertOutcome
```

Replace any `Storage(db_path)` construction with:

```python
db = Database(db_path)
repos = db.repos  # or RepoStore(db.connection)
```

Replace `process_repo(client=..., storage=storage, ...)` with `process_repo(client=..., repos=repos, ...)`.

- [ ] **Step 2: Run the updated test to verify it fails**

Run: `pytest tests/test_processor.py -v`
Expected: TypeError or "unexpected keyword argument 'repos'".

- [ ] **Step 3: Change the `process_repo` signature**

In `src/capxure/processor.py`:

1. Replace the import `from capxure.storage import Storage, UpsertOutcome` with `from capxure.git.store import RepoStore, UpsertOutcome`.
2. Change the parameter name `storage: Storage` to `repos: RepoStore` throughout the function signature and body.
3. Inside the body, every `storage.upsert(...)`, `storage.diff(...)`, etc. becomes `repos.upsert(...)`, `repos.diff(...)`. Mechanical rename — the methods and signatures are identical because `RepoStore` has the same query API.

- [ ] **Step 4: Update CLI handlers that call `process_repo`**

In `src/capxure/cli/capture.py`, the `command()` function currently constructs a `Storage` and passes it to `process_repo`. Change to pass `storage._repos` (still a `Storage`, but reach into its inner `RepoStore`) — **wrong: do not poke privates**. Instead, change the handler to construct a `Database` directly:

Replace:
```python
with Storage(db_path) as storage:
    result = await process_repo(client, storage=storage, ...)
```
With:
```python
with Database(db_path) as db:
    result = await process_repo(client, repos=db.repos, ...)
```

Add `from capxure.db import Database` at the top.

In `src/capxure/cli/stars.py`, do the same for any `process_repo` call site.

- [ ] **Step 5: Update tests that mock `process_repo` or `Storage`**

In `tests/test_cli.py`, the helper `_patch_client_and_storage` patches `Storage`. Update to patch `Database` and `RepoStore` (or just `Database`, since `db.repos` returns a `RepoStore` from the patched `Database`).

Look at every test that calls `process_repo` directly or asserts on its kwargs:
- `test_passes_keyword_args_to_process_repo` — change the expected kwarg from `storage` to `repos`.
- `test_passes_composed_db_path_to_storage` — rename to `..._to_database`, assert against `Database` constructor.
- `test_omits_db_path_when_no_data_dir_flag` — same rename pattern.
- `test_integration_real_asyncio_and_storage` — switch the real construction to `Database`.

In `tests/test_cli_stars.py`, do the same survey: anywhere `process_repo` is invoked or asserted against, substitute `repos` for `storage`.

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/capxure/processor.py src/capxure/cli/capture.py src/capxure/cli/stars.py tests/test_processor.py tests/test_cli.py tests/test_cli_stars.py
git commit -m "$(cat <<'EOF'
Switch process_repo to take RepoStore directly

process_repo no longer needs Storage's full surface — it only ever called
upsert/diff. Take a RepoStore explicitly. CLI handlers now construct a
Database and pass db.repos. Tests updated for the new kwarg name.
EOF
)"
```

---

## Task 5: Migrate `cap ls` handler off `Storage`

`cli/list_.py` is the last consumer using `Storage` directly (it calls `list_repos`, `list_topic_counts`, `count_repos` via `Storage`). Switch it to `Database` + `db.repos`.

**Files:**
- Modify: `src/capxure/cli/list_.py`
- Modify: `tests/test_cli_list.py`

- [ ] **Step 1: Update `cli/list_.py` imports and construction**

Replace:
```python
from capxure.storage import Repo, Storage
```
With:
```python
from capxure.db import Database
from capxure.git.store import Repo
```

Inside `command()`:

Replace:
```python
with Storage(db_path) as storage:
    repos = storage.list_repos(...)
```
With:
```python
with Database(db_path) as db:
    repos = db.repos.list_repos(...)
```

Apply the same pattern for `list_topic_counts` and `count_repos`.

- [ ] **Step 2: Update `tests/test_cli_list.py` imports**

Change `from capxure.storage import ...` and any direct `Storage(...)` construction to use `Database` + `db.repos`. If any test patches `Storage` to inject a mock, switch the patch target to `Database` (the test asserts on the path, not the class name).

- [ ] **Step 3: Run all tests**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/capxure/cli/list_.py tests/test_cli_list.py
git commit -m "$(cat <<'EOF'
Migrate cap ls handler to Database + RepoStore

Last direct Storage consumer; the class can now be deleted in a later task.
EOF
)"
```

---

## Task 6: Move `github.py` → `git/client.py`

Pure rename plus import-path updates. No code body changes.

**Files:**
- Move: `src/capxure/github.py` → `src/capxure/git/client.py`
- Move: `tests/test_github.py` → `tests/git/test_client.py`
- Modify: every consumer's import

**Consumers that import from `capxure.github`:**
- `src/capxure/__init__.py`
- `src/capxure/processor.py`
- `src/capxure/cli/capture.py`
- `src/capxure/cli/stars.py`
- `tests/test_cli_stars.py` (for `RateLimitExceededError` etc.)

- [ ] **Step 1: Move the source file**

```bash
git mv src/capxure/github.py src/capxure/git/client.py
```

- [ ] **Step 2: Move the test file**

```bash
git mv tests/test_github.py tests/git/test_client.py
```

- [ ] **Step 3: Update every consumer's import**

Find every occurrence of `from capxure.github import` or `import capxure.github` and replace `capxure.github` with `capxure.git.client`:

```bash
grep -rln "capxure\.github" src/ tests/ | while read f; do
  sed -i 's|capxure\.github|capxure.git.client|g' "$f"
done
```

(After running, manually verify with `grep -rn "capxure.github" src/ tests/` — must be empty.)

- [ ] **Step 4: Run all tests**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
Move github.py to git/client.py

Rename + import path update only. No behavior change.
EOF
)"
```

---

## Task 7: Move `processor.py` → `git/processor.py`

Same shape as Task 6.

**Files:**
- Move: `src/capxure/processor.py` → `src/capxure/git/processor.py`
- Move: `tests/test_processor.py` → `tests/git/test_processor.py`

**Consumers that import from `capxure.processor`:**
- `src/capxure/__init__.py`
- `src/capxure/cli/capture.py`
- `src/capxure/cli/stars.py`

- [ ] **Step 1: Move the source file**

```bash
git mv src/capxure/processor.py src/capxure/git/processor.py
```

- [ ] **Step 2: Move the test file**

```bash
git mv tests/test_processor.py tests/git/test_processor.py
```

- [ ] **Step 3: Update every consumer's import**

```bash
grep -rln "capxure\.processor" src/ tests/ | while read f; do
  sed -i 's|capxure\.processor|capxure.git.processor|g' "$f"
done
```

Verify with `grep -rn "capxure.processor" src/ tests/` — must be empty.

- [ ] **Step 4: Run all tests**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
Move processor.py to git/processor.py

Rename + import path update only.
EOF
)"
```

---

## Task 8: Move CLI handler files into `cli/git/`

Three handlers move; `list_.py` is renamed to `ls.py` along the way to match the spec naming.

**Files:**
- Create: `src/capxure/cli/git/__init__.py` (empty marker; full dispatcher comes in Task 9)
- Move: `src/capxure/cli/capture.py` → `src/capxure/cli/git/capture.py`
- Move: `src/capxure/cli/list_.py` → `src/capxure/cli/git/ls.py`
- Move: `src/capxure/cli/stars.py` → `src/capxure/cli/git/stars.py`
- Modify: `src/capxure/cli/__init__.py` (update imports to new paths; dispatcher rewrite is Task 10)

- [ ] **Step 1: Create the package marker**

Create `src/capxure/cli/git/__init__.py`:

```python
```

- [ ] **Step 2: Move the three CLI handler files**

```bash
git mv src/capxure/cli/capture.py src/capxure/cli/git/capture.py
git mv src/capxure/cli/list_.py   src/capxure/cli/git/ls.py
git mv src/capxure/cli/stars.py   src/capxure/cli/git/stars.py
```

- [ ] **Step 3: Update `cli/__init__.py` imports**

In `src/capxure/cli/__init__.py`, change:

```python
from capxure.cli import capture, list_, stars
```

to:

```python
from capxure.cli.git import capture, ls, stars
```

Anywhere in the file that references `list_`, rename to `ls`. The dispatcher logic and `register()` calls stay where they are for now — the structural rewrite is Tasks 9 and 10.

- [ ] **Step 4: Update test imports**

In `tests/test_cli.py`, `tests/test_cli_list.py`, `tests/test_cli_list_smoke.py`, `tests/test_cli_stars.py`, `tests/test_cli_stars_smoke.py`:

```bash
grep -rln "capxure\.cli\.capture\|capxure\.cli\.list_\|capxure\.cli\.stars" tests/ | while read f; do
  sed -i \
    -e 's|capxure\.cli\.capture|capxure.cli.git.capture|g' \
    -e 's|capxure\.cli\.list_|capxure.cli.git.ls|g' \
    -e 's|capxure\.cli\.stars|capxure.cli.git.stars|g' \
    "$f"
done
```

Verify: `grep -rn "capxure\.cli\.\(capture\|list_\|stars\)" src/ tests/` is empty.

- [ ] **Step 5: Run all tests**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
Move CLI handler files into cli/git/

capture.py, list_.py (renamed ls.py), stars.py move into the new git
namespace. cli/__init__.py temporarily imports from the new paths but
keeps its existing flat dispatcher; the cap-git smart-dispatch and
top-level rewrite land in subsequent tasks.
EOF
)"
```

---

## Task 9: Add `cli/git/__init__.py` dispatcher with smart-dispatch

The git-level dispatcher: argparse with `capture`, `ls`, `stars` subparsers, plus the smart-dispatch trick that turns `cap git owner/repo` into `cap git capture owner/repo` before argparse runs.

**Files:**
- Create: `tests/cli/__init__.py` (empty marker)
- Create: `tests/cli/git/__init__.py` (empty marker)
- Create: `tests/cli/git/test_dispatch.py`
- Modify: `src/capxure/cli/git/__init__.py` (replace empty marker with dispatcher)

- [ ] **Step 1: Write failing tests**

Create `tests/cli/__init__.py`:

```python
```

Create `tests/cli/git/__init__.py`:

```python
```

Create `tests/cli/git/test_dispatch.py`:

```python
"""Tests for the cap git dispatcher: smart-dispatch on '/', subcommand routing."""

from capxure.cli.git import build_parser, main


def test_help_lists_capture_ls_stars(capsys):
    """`cap git --help` mentions all three subcommands."""
    import pytest
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "capture" in out
    assert "ls" in out
    assert "stars" in out


def test_smart_dispatch_owner_slash_repo_routes_to_capture(monkeypatch):
    """`cap git owner/repo` triggers the capture handler with the target arg."""
    captured = {}

    def fake_capture(args):
        captured["target"] = args.target
        return 0

    # Patch the capture handler. Implementation detail: dispatcher rewrites
    # argv to ["capture", "owner/repo"] before argparse, which calls the
    # capture subcommand's handler.
    monkeypatch.setattr("capxure.cli.git.capture.command", fake_capture)
    code = main(["owner/repo"])
    assert code == 0
    assert captured["target"] == "owner/repo"


def test_explicit_capture_subcommand_works(monkeypatch):
    """`cap git capture owner/repo` (without smart-dispatch) routes the same."""
    captured = {}

    def fake_capture(args):
        captured["target"] = args.target
        return 0

    monkeypatch.setattr("capxure.cli.git.capture.command", fake_capture)
    code = main(["capture", "owner/repo"])
    assert code == 0
    assert captured["target"] == "owner/repo"


def test_ls_subcommand_routes_to_ls_handler(monkeypatch):
    called = {"ls": False}

    def fake_ls(args):
        called["ls"] = True
        return 0

    monkeypatch.setattr("capxure.cli.git.ls.command", fake_ls)
    code = main(["ls"])
    assert code == 0
    assert called["ls"]


def test_stars_subcommand_routes_to_stars_handler(monkeypatch):
    called = {"stars": False}

    def fake_stars(args):
        called["stars"] = True
        return 0

    monkeypatch.setattr("capxure.cli.git.stars.command", fake_stars)
    code = main(["stars"])
    assert code == 0
    assert called["stars"]


def test_no_args_returns_2_and_prints_usage(capsys):
    """`cap git` with no subcommand prints usage to stderr and returns 2."""
    code = main([])
    assert code == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_unknown_subcommand_returns_2(capsys):
    """`cap git wat` is rejected by argparse with exit 2."""
    import pytest
    with pytest.raises(SystemExit) as exc:
        main(["wat"])
    assert exc.value.code == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/git/test_dispatch.py -v`
Expected: AttributeError on `capxure.cli.git.build_parser` (the package marker has no such symbol).

- [ ] **Step 3: Implement the git-level dispatcher**

Replace `src/capxure/cli/git/__init__.py` with:

```python
"""`cap git` subcommand group: routes to capture, ls, stars."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from capxure.cli.git import capture, ls, stars


def build_parser() -> argparse.ArgumentParser:
    """Build the `cap git` parser with capture, ls, stars subparsers."""
    parser = argparse.ArgumentParser(
        prog="cap git",
        description="GitHub repo capture commands.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="{capture,ls,stars}")
    capture.register(subparsers)
    ls.register(subparsers)
    stars.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `cap git`. Argv is the args after `git`."""
    args_list = list(argv) if argv is not None else sys.argv[1:]

    # Smart dispatch: `cap git owner/repo` → `cap git capture owner/repo`.
    if args_list and "/" in args_list[0] and not args_list[0].startswith("-"):
        args_list = ["capture", *args_list]

    parser = build_parser()
    if not args_list:
        parser.print_usage(sys.stderr)
        return 2

    args = parser.parse_args(args_list)
    if not hasattr(args, "func"):
        parser.print_usage(sys.stderr)
        return 2
    return args.func(args)
```

**Note for the implementor:** the existing `register()` functions in `capture.py`, `ls.py`, `stars.py` already set `parser.set_defaults(func=command)` (or equivalent — verify against the actual file). If a given module sets the dispatch via `args.command` instead of `args.func`, adapt the `args.func(args)` line to match. The pattern is: each subparser stamps a callable on the namespace; the dispatcher invokes it.

- [ ] **Step 4: Run tests**

Run: `pytest tests/cli/git/test_dispatch.py -v`
Expected: all pass.

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: all pass. Existing top-level dispatcher tests still pass because `cli/__init__.py` is unchanged in this task.

- [ ] **Step 6: Commit**

```bash
git add src/capxure/cli/git/__init__.py tests/cli/__init__.py tests/cli/git/__init__.py tests/cli/git/test_dispatch.py
git commit -m "$(cat <<'EOF'
Add cap-git dispatcher with smart-dispatch on '/'

cli/git/__init__.py now hosts the per-domain argparse with capture/ls/stars
subparsers. Smart-dispatch on a slash-containing first arg matches today's
top-level behavior, just scoped under git. Top-level cli/__init__.py still
runs the old flat dispatcher; that gets rewritten next.
EOF
)"
```

---

## Task 10: Rewrite top-level `cli/__init__.py` to route only to `git`

The breaking change task: top-level smart-dispatch on `/` is removed; `cap` becomes a router that knows only about subcommand groups.

**Files:**
- Create: `tests/cli/test_dispatcher.py`
- Modify: `src/capxure/cli/__init__.py` (full rewrite)
- Modify: `tests/test_cli.py` (delete tests that pinned the old top-level smart-dispatch behavior)

- [ ] **Step 1: Write failing tests for the new top-level dispatcher**

Create `tests/cli/test_dispatcher.py`:

```python
"""Tests for top-level cap dispatcher: routes to git, no smart-dispatch."""

import pytest

from capxure.cli import build_parser, main


def test_no_args_returns_2(capsys):
    """`cap` with no subcommand prints usage and returns 2."""
    code = main([])
    assert code == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_unknown_subcommand_returns_2():
    """`cap wat` → argparse 'invalid choice', exit 2."""
    with pytest.raises(SystemExit) as exc:
        main(["wat"])
    assert exc.value.code == 2


def test_owner_slash_repo_at_top_level_returns_2():
    """`cap owner/repo` no longer works — pinned regression test for the
    breaking change."""
    with pytest.raises(SystemExit) as exc:
        main(["owner/repo"])
    assert exc.value.code == 2


def test_help_mentions_git():
    """`cap --help` lists `git` as an available subcommand."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--help"])
    assert exc.value.code == 0


def test_git_subcommand_dispatches(monkeypatch):
    """`cap git ls` reaches the git-level dispatcher."""
    called = {"ls": False}

    def fake_ls(args):
        called["ls"] = True
        return 0

    monkeypatch.setattr("capxure.cli.git.ls.command", fake_ls)
    code = main(["git", "ls"])
    assert code == 0
    assert called["ls"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_dispatcher.py -v`
Expected: tests fail because top-level still does smart-dispatch.

- [ ] **Step 3: Replace `src/capxure/cli/__init__.py`**

```python
"""Command-line interface for capxure. Top-level router only."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from capxure.cli import git


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level `cap` parser. Routes to subcommand groups only."""
    parser = argparse.ArgumentParser(
        prog="cap",
        description="capxure — capture and organize.",
    )
    subparsers = parser.add_subparsers(dest="domain", metavar="{git}")

    git_parser = subparsers.add_parser(
        "git",
        help="GitHub repo capture commands (capture, ls, stars).",
        add_help=False,  # Defer --help to the git-level parser.
    )
    git_parser.set_defaults(_domain="git")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `cap` console script."""
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if not args_list:
        build_parser().print_usage(sys.stderr)
        return 2

    if args_list[0] == "git":
        return git.main(args_list[1:])

    # Anything else → argparse rejects.
    build_parser().parse_args(args_list)
    return 2  # Unreachable: parse_args raises SystemExit on invalid input.
```

**Note:** `add_help=False` on the `git` subparser is intentional — once the user types `cap git ...`, control passes to `cli.git.main()`, which has its own argparse with `--help`. The top-level parser still gets a `--help` from argparse's default.

- [ ] **Step 4: Update `tests/test_cli.py`**

The existing file has:
- Top-level dispatch tests that pinned old behavior (`test_main_with_no_args_returns_2`, `test_main_help_flag_exits_zero`, `test_cli_runs_as_module_with_no_args_exits_2`, `test_parser_accepts_capture_subcommand_with_target`, `test_parser_accepts_capture_with_data_dir_flag`, `TestMainDispatch::test_slash_target_routes_to_capture`, `test_url_target_routes_to_capture`, `test_unknown_subcommand_exits_2`, `test_cli_help_exits_zero_and_mentions_cap`).
- Capture handler tests (`TestResolveToken`, `TestResolveDbPath`, `TestPrintStatus`, `TestExitCodeFor`, `TestCommandHappyPath`, `TestCommandErrorPaths`).

For this task, **only delete or update top-level dispatch tests that pin the old smart-dispatch behavior**:

- Delete `TestMainDispatch::test_slash_target_routes_to_capture` (pinned old behavior; replaced by `test_owner_slash_repo_at_top_level_returns_2` in the new dispatcher tests).
- Delete `TestMainDispatch::test_url_target_routes_to_capture` (same).
- Update `TestMainDispatch::test_unknown_subcommand_exits_2` if its assertions still match the new behavior (a bare word without `/` is still rejected with exit 2 — keep it).
- Keep the rest. Capture handler tests will be relocated in Task 11.

- [ ] **Step 5: Run all tests**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/capxure/cli/__init__.py tests/cli/test_dispatcher.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
Rewrite top-level cap dispatcher; remove smart-dispatch

Top-level cap now routes only to subcommand groups (just `git` for now).
The smart-dispatch on '/' is gone — `cap owner/repo` exits 2 with argparse's
default 'invalid choice' message. Pinned regression test ensures this stays
broken if anyone reintroduces top-level smart-dispatch.

Breaking change for end users: `cap owner/repo`, `cap ls`, `cap stars` all
require the `git` prefix now.
EOF
)"
```

---

## Task 11: Reorganize remaining tests to mirror source layout

Final test reorg: capture handler tests, ls tests, stars tests move under `tests/cli/git/`.

**Files:**
- Move: `tests/test_cli.py` → split into `tests/cli/test_dispatcher.py` (already exists from Task 10) and `tests/cli/git/test_capture.py`
- Move: `tests/test_cli_list.py` → `tests/cli/git/test_ls.py`
- Move: `tests/test_cli_list_smoke.py` → `tests/cli/git/test_ls_smoke.py`
- Move: `tests/test_cli_stars.py` → `tests/cli/git/test_stars.py`
- Move: `tests/test_cli_stars_smoke.py` → `tests/cli/git/test_stars_smoke.py`

- [ ] **Step 1: Split `tests/test_cli.py`**

The remaining tests in `tests/test_cli.py` after Task 10 are capture-handler-focused. Move them to `tests/cli/git/test_capture.py`:

```bash
git mv tests/test_cli.py tests/cli/git/test_capture.py
```

(`tests/cli/test_dispatcher.py` was created in Task 10 from scratch — no merge needed.)

If `test_capture.py` ends up with any leftover top-level dispatch tests after the move, manually relocate them into `tests/cli/test_dispatcher.py`. Sweep the file post-move for any test name containing "main", "dispatch", or "parser" that doesn't actually exercise the capture handler.

- [ ] **Step 2: Move the rest**

```bash
git mv tests/test_cli_list.py        tests/cli/git/test_ls.py
git mv tests/test_cli_list_smoke.py  tests/cli/git/test_ls_smoke.py
git mv tests/test_cli_stars.py       tests/cli/git/test_stars.py
git mv tests/test_cli_stars_smoke.py tests/cli/git/test_stars_smoke.py
```

- [ ] **Step 3: Update conftest discovery**

`tests/conftest.py` lives at the test root and is auto-discovered for nested directories — no changes needed. If any subdirectory test file uses relative imports (e.g., `from ..conftest import ...`), it'll break — search for `from .` / `from ..` in moved tests and rewrite to absolute imports.

- [ ] **Step 4: Run all tests**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
Reorganize tests under tests/cli/git/ to mirror source layout

Capture/ls/stars tests now live next to the modules they test. No test
bodies changed; only paths.
EOF
)"
```

---

## Task 12: Delete `Storage` class; update public API

The transition shim is no longer load-bearing — every consumer uses `Database` + `RepoStore`. Delete `storage.py` and its tests, and update `__init__.py`'s public API.

**Files:**
- Delete: `src/capxure/storage.py`
- Delete: `tests/test_storage.py`
- Modify: `src/capxure/__init__.py` (remove `Storage`, add `Database` and `RepoStore`)
- Modify: `tests/test_imports.py` (update expected `__all__`)

- [ ] **Step 1: Audit for remaining `Storage` consumers**

```bash
grep -rn "Storage" src/ tests/ | grep -v RepoStore
```

Expected: only references in `storage.py` itself, `__init__.py`'s re-export, `test_imports.py`'s expected list, and possibly the docstring of some test. If any test file still imports or constructs `Storage`, fix it before proceeding (replace with `Database` + `db.repos`).

- [ ] **Step 2: Update `src/capxure/__init__.py`**

Replace the imports and `__all__`:

```python
"""capxure - Capture GitHub repos locally."""

from capxure.db import Database, UnsupportedSchemaError
from capxure.git.client import (
    AuthenticationError,
    GitHubClient,
    GitHubError,
    NotFoundError,
    RateLimitExceededError,
    RateLimitInfo,
    parse_github_url,
)
from capxure.git.processor import (
    ProcessResult,
    Severity,
    StatusCallback,
    process_repo,
)
from capxure.git.store import (
    DuplicateRepoNameError,
    Repo,
    RepoStore,
    UpsertOutcome,
)

__version__ = "0.4.0"

__all__ = [
    "AuthenticationError",
    "Database",
    "DuplicateRepoNameError",
    "GitHubClient",
    "GitHubError",
    "NotFoundError",
    "ProcessResult",
    "RateLimitExceededError",
    "RateLimitInfo",
    "Repo",
    "RepoStore",
    "Severity",
    "StatusCallback",
    "UnsupportedSchemaError",
    "UpsertOutcome",
    "__version__",
    "parse_github_url",
    "process_repo",
]
```

`Storage` is removed from both the import block and `__all__`. Bumping `__version__` to `0.4.0` reflects the breaking API change.

- [ ] **Step 3: Update `tests/test_imports.py`**

Change the expected `__all__` list to match the new contents. The three existing tests:

- `test_all_matches_expected`: update its expected list to the new one.
- `test_every_name_in_all_is_resolvable`: still works structurally.
- `test_removed_symbols_are_gone`: add `"Storage"` to the list of names that must NOT be importable.

```python
def test_storage_class_removed():
    """Storage is gone — use Database + RepoStore."""
    import capxure
    assert not hasattr(capxure, "Storage")
```

- [ ] **Step 4: Delete `storage.py` and `test_storage.py`**

```bash
git rm src/capxure/storage.py tests/test_storage.py
```

- [ ] **Step 5: Run all tests**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
Remove Storage class; bump to 0.4.0

The Database + RepoStore split is now load-bearing. storage.py and its
tests are gone. Public API: Storage replaced by Database (+ db.repos for
the RepoStore accessor). UnsupportedSchemaError moves with Database.

Breaking change for any direct library users.
EOF
)"
```

---

## Task 13: Verification gates

Three concrete checks the spec calls out, run before declaring the refactor done.

**Files:**
- None modified (verification only). Output captured for the PR description.

- [ ] **Step 1: Schema parity check**

Compare the schema produced by a fresh DB on `main` versus on this branch. Use a temporary git worktree to avoid disturbing the working tree:

```bash
# Capture current (post-refactor) schema from this branch:
python -c "from capxure.db import Database; Database('/tmp/capxure-after.db')"
sqlite3 /tmp/capxure-after.db .schema > /tmp/schema-after.sql

# Capture pre-refactor schema from main:
git worktree add /tmp/capxure-main main
(cd /tmp/capxure-main && pip install -e . > /dev/null && \
  python -c "from capxure.storage import Storage; Storage('/tmp/capxure-before.db')")
sqlite3 /tmp/capxure-before.db .schema > /tmp/schema-before.sql
git worktree remove /tmp/capxure-main

diff /tmp/schema-before.sql /tmp/schema-after.sql
```

Expected: empty diff. If non-empty, the refactor leaked a schema change — investigate and fix before merging.

- [ ] **Step 2: Live-DB compatibility check**

If you have a real capxure DB on disk from before the refactor, open it with the new code:

```bash
CAPXURE_DATA_DIR=$HOME/.local/share/capxure cap git ls --limit 5
```

Expected: same five rows with same fields as `cap ls --limit 5` produced before the refactor. If that DB doesn't exist locally, build a fixture: capture a few repos with the new code, then re-run `cap git ls`.

- [ ] **Step 3: Full test suite, clean**

```bash
pytest -v
```

Expected: all green, no skips that weren't skipped before, no warnings introduced by this branch.

- [ ] **Step 4: Document results in PR description**

Paste in the PR:
- Schema diff (empty).
- Live-DB output (a few rows from `cap git ls`).
- `pytest` final line.

No commit for this task — just verification before opening the PR.
