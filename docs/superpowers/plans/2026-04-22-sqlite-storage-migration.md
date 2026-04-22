# SQLite Storage Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Capxure's JSON-file-and-markdown-file storage layer with a single SQLite database. The `Storage` class becomes a typed facade over a SQL schema (public contract), with an escape-hatch property exposing the live `sqlite3.Connection`.

**Architecture:**
- Two tables: `repos` (main entity, with denormalized query-hotspot columns and an inline `readme_content` column) and `repo_topics` (junction table for many-to-many topics).
- `Storage` class wraps a single long-lived `sqlite3.Connection` opened in `__init__`. Consumers can use it as a context manager. An `UpsertOutcome` enum drives the capture-flow decisions (NEW / UPDATED / RENAMED / UNCHANGED / LOCAL_IS_NEWER).
- `Storage.upsert()` is single-phase and atomic; `Storage.diff()` is the read-only sibling the processor uses to skip README fetches for already-current repos.
- No migration of existing `data/metadata.json`; the old files stay on disk untouched for manual CLI testing later.

**Tech Stack:** Python ≥3.11, stdlib `sqlite3`, `hashlib.sha256`, `dataclasses`, `enum.StrEnum`, `platformdirs` (already a dep). Test harness: `pytest` + `pytest-asyncio` (new dev deps).

**Background spec:** `docs/superpowers/specs/2026-04-22-sqlite-storage-migration-design.md` (commit `f2824b0`). Read it if any decision below seems unmotivated — every choice is grounded there.

---

## File Structure

**Created:**
- `tests/conftest.py` — shared pytest fixtures (tmp db path helper, fixture loaders).
- `tests/fixtures/sindresorhus-awesome-nodejs.json` — real GitHub metadata snapshot.
- `tests/fixtures/thedotmack-claude-mem.json` — real GitHub metadata snapshot (lots of topics).
- `tests/fixtures/GiovanniPasq-chunky.json` — real GitHub metadata snapshot (minimal topics).
- `tests/test_storage.py` — 14 contract tests for the new Storage class.
- `tests/test_processor.py` — 1 integration smoke test.
- `tests/test_imports.py` — public API shape smoke test.

**Modified:**
- `pyproject.toml` — add `[project.optional-dependencies].dev` with `pytest` + `pytest-asyncio`; add `[tool.pytest.ini_options]` section; bump version in Task 11.
- `src/capxure/storage.py` — **full rewrite** (old filesystem code gone).
- `src/capxure/__init__.py` — remove `DeduplicationResult` re-export, add `UpsertOutcome`, `Repo`, `DuplicateRepoNameError`, `UnsupportedSchemaError`.
- `src/capxure/processor.py` — temporarily stubbed in Task 2; fully rewired in Task 8.
- `README.md` — rewrite storage section and document the schema as a public contract.

**Untouched:** `src/capxure/github.py`, `src/capxure/py.typed`, `data/metadata.json`, `data/readmes/`.

---

## Task 1: Test harness + fixtures

**Files:**
- Modify: `pyproject.toml` (add `[project.optional-dependencies].dev` and `[tool.pytest.ini_options]`)
- Create: `tests/conftest.py`
- Create: `tests/fixtures/sindresorhus-awesome-nodejs.json`
- Create: `tests/fixtures/thedotmack-claude-mem.json`
- Create: `tests/fixtures/GiovanniPasq-chunky.json`

- [ ] **Step 1: Add dev dependencies and pytest config to `pyproject.toml`**

Replace the entire `pyproject.toml` with:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "capxure"
version = "0.1.0"
description = "Library for capturing GitHub repo metadata and READMEs locally"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27.0",
    "platformdirs>=4",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
]

[tool.hatch.build.targets.wheel]
packages = ["src/capxure"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
asyncio_mode = "auto"
```

`pythonpath = ["src"]` lets pytest import `capxure` without an editable install. `asyncio_mode = "auto"` means pytest-asyncio auto-wraps every `async def test_*` — no per-test decorator needed.

- [ ] **Step 2: Install the dev dependencies**

Run:
```bash
pip install -e '.[dev]'
```

Expected: installs pytest, pytest-asyncio, and re-installs capxure in editable mode. No errors.

- [ ] **Step 3: Create `tests/conftest.py`**

```python
"""Shared pytest fixtures for capxure tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a GitHub metadata fixture by filename (without .json extension)."""
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def awesome_nodejs_metadata() -> dict:
    return load_fixture("sindresorhus-awesome-nodejs")


@pytest.fixture
def claude_mem_metadata() -> dict:
    return load_fixture("thedotmack-claude-mem")


@pytest.fixture
def chunky_metadata() -> dict:
    return load_fixture("GiovanniPasq-chunky")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Ephemeral SQLite db path; tmp_path is auto-cleaned after the test."""
    return tmp_path / "test.db"
```

- [ ] **Step 4: Extract three real fixtures from `data/metadata.json`**

```bash
mkdir -p tests/fixtures
python3 <<'EOF'
import json
from pathlib import Path

src = json.load(open("data/metadata.json"))
out_dir = Path("tests/fixtures")

picks = [
    ("sindresorhus--awesome-nodejs", "sindresorhus-awesome-nodejs"),
    ("thedotmack--claude-mem",       "thedotmack-claude-mem"),
    ("GiovanniPasq--chunky",         "GiovanniPasq-chunky"),
]
for src_key, filename in picks:
    if src_key not in src:
        raise SystemExit(f"missing expected fixture repo in metadata.json: {src_key}")
    (out_dir / f"{filename}.json").write_text(
        json.dumps(src[src_key], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {filename}.json")
EOF
```

Expected output:
```
wrote sindresorhus-awesome-nodejs.json
wrote thedotmack-claude-mem.json
wrote GiovanniPasq-chunky.json
```

If any repo is missing, swap in any other `owner--repo` key from `data/metadata.json` (the current set has 16 repos; any three will do, but ideally pick one with many topics and one with few).

- [ ] **Step 5: Verify pytest runs (zero tests collected is fine)**

Run:
```bash
pytest -v
```

Expected output (exact numbers may vary):
```
============================= test session starts ==============================
...
collected 0 items

============================ no tests ran in ... ===============================
```

Zero collected is the correct state. If you see an error about missing pytest-asyncio or failed imports, revisit Step 2.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/
git commit -m "Add pytest harness and real-data fixtures

Introduces pytest + pytest-asyncio under [project.optional-dependencies].dev,
configures pytest to find tests/ and resolve src/ on the path, and extracts
three real GitHub metadata snapshots into tests/fixtures/ for use in the
SQLite storage migration tests."
```

---

## Task 2: Storage skeleton + atomic switchover

This task is deliberately chunky because the three files (`storage.py`, `__init__.py`, `processor.py`) are tightly coupled at the import level — you cannot rewrite storage.py in isolation without breaking `import capxure`. They all move together, once, in this commit.

**Files:**
- Modify: `src/capxure/storage.py` (full rewrite)
- Modify: `src/capxure/__init__.py` (update re-exports)
- Modify: `src/capxure/processor.py` (temporarily stubbed — full rewrite in Task 8)
- Create: `tests/test_storage.py` (tests 1-2 only)

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_storage.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
pytest tests/test_storage.py -v
```

Expected: tests fail because the current `storage.py` has a filesystem-based API and no `Storage.connection` property. You will see either `AttributeError: 'Storage' object has no attribute 'connection'` or similar.

- [ ] **Step 3: Replace `src/capxure/storage.py` entirely**

The old filesystem implementation gets deleted. Write the new module:

```python
"""SQLite-backed persistence for captured GitHub repo data."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class UpsertOutcome(StrEnum):
    NEW = "new"                          # not present before
    UPDATED = "updated"                  # existed, content or metadata changed
    RENAMED = "renamed"                  # same github_id, owner/name changed
    UNCHANGED = "unchanged"              # existed, nothing to persist
    LOCAL_IS_NEWER = "local_is_newer"    # local pushed_at > remote pushed_at


class DuplicateRepoNameError(Exception):
    """Another GitHub repo already occupies this (owner, name)."""


class UnsupportedSchemaError(Exception):
    """DB on disk uses a schema version this library doesn't know."""


@dataclass(frozen=True)
class Repo:
    id: int
    github_id: int
    owner: str
    name: str
    full_name: str
    url: str
    default_branch: str | None
    description: str | None
    language: str | None
    stars: int
    forks: int
    pushed_at: str | None
    is_fork: bool
    is_archived: bool
    topics: tuple[str, ...]
    readme_content: str | None
    readme_sha: str | None
    captured_at: str
    last_synced_at: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_SCHEMA_VERSION = 1

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
"""


def _resolve_default_db_path() -> Path:
    """Resolve the default SQLite db location.

    Uses platformdirs.user_data_dir when the library is embedded; falls back to
    package-relative `data/` when running in-repo (useful for development).
    """
    try:
        package_data = Path(__file__).resolve().parent.parent.parent / "data"
        if package_data.is_dir():
            return package_data / "capxure.db"
    except Exception:
        pass
    return Path(user_data_dir("capxure")) / "capxure.db"


# ---------------------------------------------------------------------------
# Storage facade
# ---------------------------------------------------------------------------


class Storage:
    """SQLite-backed persistence for captured GitHub repos.

    The class owns a single long-lived sqlite3.Connection for its lifetime.
    Use as a context manager, or call close() explicitly.

    The .connection property is the documented escape hatch — consumers may
    run arbitrary SQL against the schema. The schema itself is a public
    contract; see docs/superpowers/specs/2026-04-22-sqlite-storage-migration-design.md.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else _resolve_default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self._db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level="",
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema()

    # --- lifecycle ---

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    # --- schema management ---

    def _ensure_schema(self) -> None:
        cur = self._conn.execute("PRAGMA user_version")
        current = cur.fetchone()[0]
        if current == 0:
            with self._conn:
                self._conn.executescript(_SCHEMA_SQL)
                self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        elif current != _SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"DB at {self._db_path} uses schema version {current}, "
                f"but this library knows version {_SCHEMA_VERSION}."
            )

    # --- write path (filled in Tasks 3-6) ---

    def upsert(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
    ) -> UpsertOutcome:
        raise NotImplementedError("upsert() is implemented in Task 3+")

    def diff(self, metadata: dict[str, Any]) -> UpsertOutcome:
        raise NotImplementedError("diff() is implemented in Task 5")

    # --- read path (filled in Task 7) ---

    def get_repo(self, owner: str, name: str) -> Repo | None:
        raise NotImplementedError("get_repo() is implemented in Task 7")

    def get_repo_by_github_id(self, github_id: int) -> Repo | None:
        raise NotImplementedError("get_repo_by_github_id() is implemented in Task 7")

    def list_repos(self) -> list[Repo]:
        raise NotImplementedError("list_repos() is implemented in Task 7")

    def count_repos(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM repos")
        return cur.fetchone()[0]

    def get_metadata_json(self, owner: str, name: str) -> dict[str, Any] | None:
        raise NotImplementedError("get_metadata_json() is implemented in Task 7")
```

- [ ] **Step 4: Update `src/capxure/__init__.py`**

Replace the file with:

```python
"""capxure - Capture GitHub repos locally."""

from capxure.github import (
    AuthenticationError,
    GitHubClient,
    GitHubError,
    NotFoundError,
    RateLimitExceededError,
    RateLimitInfo,
    parse_github_url,
)
from capxure.processor import (
    ProcessResult,
    Severity,
    StatusCallback,
    process_repo,
)
from capxure.storage import (
    DuplicateRepoNameError,
    Repo,
    Storage,
    UnsupportedSchemaError,
    UpsertOutcome,
)

__version__ = "0.1.0"

__all__ = [
    "AuthenticationError",
    "DuplicateRepoNameError",
    "GitHubClient",
    "GitHubError",
    "NotFoundError",
    "ProcessResult",
    "RateLimitExceededError",
    "RateLimitInfo",
    "Repo",
    "Severity",
    "StatusCallback",
    "Storage",
    "UnsupportedSchemaError",
    "UpsertOutcome",
    "__version__",
    "parse_github_url",
    "process_repo",
]
```

- [ ] **Step 5: Temporarily stub `src/capxure/processor.py`**

The old processor calls `storage.load_metadata()`, `storage.check_dedup()`, and `storage.upsert_entry()` — none of which exist on the new Storage. Replace `processor.py` with a stub that preserves the public types (`Severity`, `StatusCallback`, `ProcessResult`, `process_repo`) so `import capxure` works, but where `process_repo` raises clearly until Task 8 restores it:

```python
"""Core orchestrator.

Coordinates GitHub API calls and local storage operations.
Accepts a StatusCallback so consumers can surface progress.

NOTE: this module is temporarily stubbed during the SQLite storage migration.
The full implementation is restored in Task 8 of
docs/superpowers/plans/2026-04-22-sqlite-storage-migration.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from capxure.github import GitHubClient
from capxure.storage import Storage, UpsertOutcome


class Severity(StrEnum):
    SUCCESS = "success"
    INFO = "info"
    ERROR = "error"


class StatusCallback(Protocol):
    def __call__(self, message: str, severity: Severity) -> None: ...


@dataclass(frozen=True)
class ProcessResult:
    owner: str
    repo: str
    outcome: UpsertOutcome | None   # None if error
    error: str | None = None


async def process_repo(
    url: str,
    *,
    github: GitHubClient,
    storage: Storage,
    on_status: StatusCallback,
) -> ProcessResult:
    raise NotImplementedError(
        "process_repo is temporarily stubbed during the SQLite storage "
        "migration; see docs/superpowers/plans/2026-04-22-sqlite-storage-migration.md "
        "Task 8."
    )
```

- [ ] **Step 6: Run the tests to verify the two pass and nothing else broke**

Run:
```bash
pytest tests/test_storage.py -v
```

Expected: 2 passed.

Also verify the package still imports cleanly:
```bash
python3 -c "import capxure; print(sorted(capxure.__all__))"
```

Expected: a sorted list containing `Storage`, `UpsertOutcome`, `Repo`, `DuplicateRepoNameError`, `UnsupportedSchemaError`, and the other retained exports. No `DeduplicationResult`.

- [ ] **Step 7: Commit**

```bash
git add src/capxure/storage.py src/capxure/__init__.py src/capxure/processor.py tests/test_storage.py
git commit -m "Replace JSON storage with SQLite schema and lifecycle

Full rewrite of src/capxure/storage.py introducing a SQLite-backed Storage
class. The schema (repos + repo_topics) is set up idempotently via
PRAGMA user_version and is documented as a public contract in the linked
spec. The __init__.py re-exports match the new public surface; processor.py
is temporarily stubbed with NotImplementedError and will be rewired in a
later commit. Includes the first two storage contract tests (fresh DB
creation, reopen)."
```

---

## Task 3: `upsert()` — NEW path

**Files:**
- Modify: `src/capxure/storage.py` (implement NEW branch of upsert + helpers)
- Modify: `tests/test_storage.py` (add test 3)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_storage.py`:

```python
import hashlib

from capxure.storage import Storage, UpsertOutcome


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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_storage.py::test_upsert_new -v
```

Expected: `NotImplementedError: upsert() is implemented in Task 3+`.

- [ ] **Step 3: Implement upsert() NEW path + helpers in `src/capxure/storage.py`**

Add this import at the top of `storage.py` if it's not already there:

```python
import json
```

Replace the `upsert` method body (and add the helpers below it, above `diff()`):

```python
    def upsert(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
    ) -> UpsertOutcome:
        github_id = metadata["id"]
        readme_sha = _sha256_hex(readme_content) if readme_content is not None else None

        with self._conn:
            existing = self._fetch_internal_by_github_id(github_id)
            if existing is None:
                repo_id = self._insert_repo(metadata, readme_content, readme_sha)
                self._replace_topics(repo_id, metadata.get("topics", []))
                return UpsertOutcome.NEW

            # Remaining branches (UPDATED / UNCHANGED / RENAMED / LOCAL_IS_NEWER)
            # are added in Task 4.
            raise NotImplementedError("update branches land in Task 4")

    # --- internal helpers ---

    def _fetch_internal_by_github_id(self, github_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT id, owner, name, pushed_at, readme_sha "
            "FROM repos WHERE github_id = ?",
            (github_id,),
        ).fetchone()

    def _insert_repo(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
        readme_sha: str | None,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO repos (
                github_id, owner, name, full_name, url, default_branch,
                description, language, stars, forks, pushed_at,
                is_fork, is_archived, readme_content, readme_sha, metadata
            ) VALUES (
                :github_id, :owner, :name, :full_name, :url, :default_branch,
                :description, :language, :stars, :forks, :pushed_at,
                :is_fork, :is_archived, :readme_content, :readme_sha, :metadata
            )
            """,
            {
                "github_id":      metadata["id"],
                "owner":          metadata["owner"]["login"],
                "name":           metadata["name"],
                "full_name":      metadata["full_name"],
                "url":            metadata["html_url"],
                "default_branch": metadata.get("default_branch"),
                "description":    metadata.get("description"),
                "language":       metadata.get("language"),
                "stars":          metadata.get("stargazers_count") or 0,
                "forks":          metadata.get("forks_count") or 0,
                "pushed_at":      metadata.get("pushed_at"),
                "is_fork":        1 if metadata.get("fork") else 0,
                "is_archived":    1 if metadata.get("archived") else 0,
                "readme_content": readme_content,
                "readme_sha":     readme_sha,
                "metadata":       json.dumps(metadata),
            },
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def _replace_topics(self, repo_id: int, topics: list[str]) -> None:
        self._conn.execute("DELETE FROM repo_topics WHERE repo_id = ?", (repo_id,))
        if topics:
            self._conn.executemany(
                "INSERT OR IGNORE INTO repo_topics (repo_id, topic) VALUES (?, ?)",
                [(repo_id, t) for t in topics],
            )
```

Also add the module-level helper near the other internal helpers at the top (just below `_SCHEMA_SQL`):

```python
def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_storage.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/capxure/storage.py tests/test_storage.py
git commit -m "Implement Storage.upsert() NEW path with topics

Adds the NEW branch of upsert() plus its internal helpers
(_fetch_internal_by_github_id, _insert_repo, _replace_topics) and the
module-level _sha256_hex helper. Topics are written to the junction
table in the same transaction as the parent repo row. Covered by
tests/test_storage.py::test_upsert_new."
```

---

## Task 4: `upsert()` — UPDATED, UNCHANGED, RENAMED, LOCAL_IS_NEWER branches

**Files:**
- Modify: `src/capxure/storage.py` (flesh out the "existing repo" branch in upsert)
- Modify: `tests/test_storage.py` (add four new tests)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_storage.py`:

```python
import copy


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
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
pytest tests/test_storage.py -v
```

Expected: `test_upsert_new` still passes; the four new tests fail with `NotImplementedError("update branches land in Task 4")`.

- [ ] **Step 3: Extend `upsert()` with the existing-row branches**

Replace the `upsert` method body in `src/capxure/storage.py` with the full version:

```python
    def upsert(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
    ) -> UpsertOutcome:
        github_id = metadata["id"]
        readme_sha = _sha256_hex(readme_content) if readme_content is not None else None

        with self._conn:
            existing = self._fetch_internal_by_github_id(github_id)
            outcome = self._classify(existing, metadata, readme_sha)

            if outcome == UpsertOutcome.NEW:
                repo_id = self._insert_repo(metadata, readme_content, readme_sha)
                self._replace_topics(repo_id, metadata.get("topics", []))
            elif outcome in (UpsertOutcome.UPDATED, UpsertOutcome.RENAMED):
                assert existing is not None  # type-narrow: UPDATED/RENAMED imply an existing row
                self._update_repo(existing["id"], metadata, readme_content, readme_sha)
                self._replace_topics(existing["id"], metadata.get("topics", []))
            # UNCHANGED and LOCAL_IS_NEWER: no-op writes.

        return outcome
```

Add the `_classify` and `_update_repo` helpers (place `_classify` near the top of the internal-helpers section, `_update_repo` below `_insert_repo`):

```python
    def _classify(
        self,
        existing: sqlite3.Row | None,
        metadata: dict[str, Any],
        readme_sha: str | None,
    ) -> UpsertOutcome:
        if existing is None:
            return UpsertOutcome.NEW

        remote_push = metadata.get("pushed_at")
        local_push = existing["pushed_at"]

        if local_push and remote_push and local_push > remote_push:
            return UpsertOutcome.LOCAL_IS_NEWER

        renamed = (
            existing["owner"] != metadata["owner"]["login"]
            or existing["name"] != metadata["name"]
        )

        if (
            existing["readme_sha"] == readme_sha
            and local_push == remote_push
            and not renamed
        ):
            return UpsertOutcome.UNCHANGED

        return UpsertOutcome.RENAMED if renamed else UpsertOutcome.UPDATED

    def _update_repo(
        self,
        repo_id: int,
        metadata: dict[str, Any],
        readme_content: str | None,
        readme_sha: str | None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE repos SET
                owner          = :owner,
                name           = :name,
                full_name      = :full_name,
                url            = :url,
                default_branch = :default_branch,
                description    = :description,
                language       = :language,
                stars          = :stars,
                forks          = :forks,
                pushed_at      = :pushed_at,
                is_fork        = :is_fork,
                is_archived    = :is_archived,
                readme_content = :readme_content,
                readme_sha     = :readme_sha,
                metadata       = :metadata,
                last_synced_at = datetime('now')
            WHERE id = :id
            """,
            {
                "id":             repo_id,
                "owner":          metadata["owner"]["login"],
                "name":           metadata["name"],
                "full_name":      metadata["full_name"],
                "url":            metadata["html_url"],
                "default_branch": metadata.get("default_branch"),
                "description":    metadata.get("description"),
                "language":       metadata.get("language"),
                "stars":          metadata.get("stargazers_count") or 0,
                "forks":          metadata.get("forks_count") or 0,
                "pushed_at":      metadata.get("pushed_at"),
                "is_fork":        1 if metadata.get("fork") else 0,
                "is_archived":    1 if metadata.get("archived") else 0,
                "readme_content": readme_content,
                "readme_sha":     readme_sha,
                "metadata":       json.dumps(metadata),
            },
        )
```

- [ ] **Step 4: Run the tests to verify all pass**

```bash
pytest tests/test_storage.py -v
```

Expected: 6 passed (tests 1-3 previously + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/capxure/storage.py tests/test_storage.py
git commit -m "Flesh out Storage.upsert() with UPDATED/UNCHANGED/RENAMED/LOCAL_IS_NEWER

Adds the _classify helper (used by upsert and later by diff) and the
_update_repo helper. Classification logic: same github_id with newer local
pushed_at yields LOCAL_IS_NEWER; identical content (readme_sha + pushed_at
+ owner/name) yields UNCHANGED; owner/name drift yields RENAMED; otherwise
UPDATED. UNCHANGED and LOCAL_IS_NEWER are no-op writes."
```

---

## Task 5: `diff()` — read-only classification

**Files:**
- Modify: `src/capxure/storage.py` (implement diff using _classify)
- Modify: `tests/test_storage.py` (add parametrized test)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_storage.py`:

```python
import pytest


@pytest.mark.parametrize(
    "prepare,query_mutation,expected",
    [
        # NEW: nothing in db yet.
        (
            lambda storage, md: None,
            lambda md: md,
            UpsertOutcome.NEW,
        ),
        # UNCHANGED: insert first, then diff identical.
        (
            lambda storage, md: storage.upsert(md, "readme-x"),
            lambda md: md,
            UpsertOutcome.UNCHANGED,
        ),
        # UPDATED: insert, then diff with advanced pushed_at.
        (
            lambda storage, md: storage.upsert(md, "readme-x"),
            lambda md: {**md, "pushed_at": "2099-01-01T00:00:00Z"},
            UpsertOutcome.UPDATED,
        ),
        # LOCAL_IS_NEWER: insert with future pushed_at, diff with older.
        (
            lambda storage, md: storage.upsert(
                {**md, "pushed_at": "2099-01-01T00:00:00Z"},
                "readme-x",
            ),
            lambda md: {**md, "pushed_at": "2000-01-01T00:00:00Z"},
            UpsertOutcome.LOCAL_IS_NEWER,
        ),
    ],
    ids=["new", "unchanged", "updated", "local_is_newer"],
)
def test_diff_matches_upsert_classification(
    db_path, claude_mem_metadata, prepare, query_mutation, expected
):
    """diff() returns the same outcome upsert() would, without writing."""
    storage = Storage(db_path)
    try:
        prepare(storage, claude_mem_metadata)

        # For UNCHANGED, the prepared upsert uses readme "readme-x". We need the
        # diff to supply the SAME readme content to compute the same sha.
        # (Metadata-only diff is not the public contract; it includes the
        # intended readme_content so the outcome matches what upsert would do.)
        query_meta = query_mutation(claude_mem_metadata)

        # diff() signature is diff(metadata) — it does not take the readme
        # because the caller hasn't fetched it yet. So diff can't detect a
        # "README-only change"; UNCHANGED/UPDATED here are driven by pushed_at.
        outcome = storage.diff(query_meta)
        assert outcome == expected

        # Proof of read-only: count_repos reflects only what prepare() did.
        expected_row_count = 0 if expected == UpsertOutcome.NEW else 1
        assert storage.count_repos() == expected_row_count
    finally:
        storage.close()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_storage.py::test_diff_matches_upsert_classification -v
```

Expected: four parametrized cases fail with `NotImplementedError: diff() is implemented in Task 5`.

- [ ] **Step 3: Implement `diff()`**

Replace the `diff` method body in `src/capxure/storage.py` with:

```python
    def diff(self, metadata: dict[str, Any]) -> UpsertOutcome:
        """Classify what upsert() *would* do, without writing.

        Note: diff() cannot know about README-only changes because the caller
        has not fetched the README yet — that's the whole point of this method
        (skip the fetch when pushed_at + owner/name + existence say nothing
        has changed). When diff returns UNCHANGED, upsert() called afterward
        with the freshly-fetched README may itself decide UPDATED if the
        README content changed despite a static pushed_at; that's correct.
        """
        existing = self._fetch_internal_by_github_id(metadata["id"])
        # Pass existing readme_sha so that an UNCHANGED classification is
        # conservative: if a README-only change happened AND pushed_at didn't
        # move, diff() returns UNCHANGED (caller skips fetch), and that's the
        # acceptable outcome — we trust pushed_at.
        readme_sha = existing["readme_sha"] if existing is not None else None
        return self._classify(existing, metadata, readme_sha)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_storage.py -v
```

Expected: 6 prior passes + 4 parametrized `test_diff_matches_upsert_classification` cases = 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/capxure/storage.py tests/test_storage.py
git commit -m "Add Storage.diff() read-only classification

diff() reuses _classify() to return the outcome upsert() would produce,
without writing. Preserves the pre-fetch API-call optimization from the
old check_dedup() flow: callers can skip a README download when diff
returns UNCHANGED or LOCAL_IS_NEWER. Uses the existing row's readme_sha
so UNCHANGED is pushed_at-driven, matching the spec."
```

---

## Task 6: Collision, topics-replacement, nullable README

**Files:**
- Modify: `src/capxure/storage.py` (catch IntegrityError from INSERT into repos; rely on existing topics replace)
- Modify: `tests/test_storage.py` (add tests 9, 10, 11)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_storage.py`:

```python
from capxure.storage import DuplicateRepoNameError


def test_duplicate_repo_name_raises(db_path, claude_mem_metadata):
    """Inserting a new repo whose (owner, name) matches an existing repo
    with a DIFFERENT github_id raises DuplicateRepoNameError."""
    storage = Storage(db_path)
    try:
        storage.upsert(claude_mem_metadata, "readme one")

        collider = copy.deepcopy(claude_mem_metadata)
        collider["id"] = claude_mem_metadata["id"] + 9999
        collider["node_id"] = "different-node-id"
        # Same owner + name as the original — only the github_id changed.

        with pytest.raises(DuplicateRepoNameError):
            storage.upsert(collider, "readme two")
    finally:
        storage.close()


def test_topics_add_and_remove(db_path, claude_mem_metadata):
    """Re-upsert with a modified topics list replaces the stored set."""
    storage = Storage(db_path)
    try:
        first = copy.deepcopy(claude_mem_metadata)
        first["topics"] = ["a", "b", "c"]
        first["pushed_at"] = "2030-01-01T00:00:00Z"
        storage.upsert(first, "readme-v1")

        repo_id_before = storage.connection.execute(
            "SELECT id FROM repos"
        ).fetchone()[0]

        second = copy.deepcopy(claude_mem_metadata)
        second["topics"] = ["a", "b", "d"]
        second["pushed_at"] = "2030-06-01T00:00:00Z"  # advance to force UPDATED
        storage.upsert(second, "readme-v2")

        topics = sorted(
            r[0] for r in storage.connection.execute(
                "SELECT topic FROM repo_topics"
            ).fetchall()
        )
        assert topics == ["a", "b", "d"]

        repo_id_after = storage.connection.execute(
            "SELECT id FROM repos"
        ).fetchone()[0]
        assert repo_id_after == repo_id_before, "repos.id should be stable across upserts"
    finally:
        storage.close()


def test_upsert_nullable_readme(db_path, claude_mem_metadata):
    """readme_content=None round-trips as NULL in the DB."""
    storage = Storage(db_path)
    try:
        outcome = storage.upsert(claude_mem_metadata, None)
        assert outcome == UpsertOutcome.NEW

        row = storage.connection.execute(
            "SELECT readme_content, readme_sha FROM repos"
        ).fetchone()
        assert row["readme_content"] is None
        assert row["readme_sha"] is None
    finally:
        storage.close()
```

- [ ] **Step 2: Run tests to confirm the failure modes**

```bash
pytest tests/test_storage.py -v
```

Expected:
- `test_duplicate_repo_name_raises` fails with `sqlite3.IntegrityError` (not `DuplicateRepoNameError`) — we need to wrap it.
- `test_topics_add_and_remove` may already pass (the delete-then-insert logic is in place), but verify.
- `test_upsert_nullable_readme` should already pass (the NEW path writes `readme_content=None` → NULL).

- [ ] **Step 3: Wrap IntegrityError into DuplicateRepoNameError**

Update the `upsert()` method in `src/capxure/storage.py` to catch and re-raise the specific collision:

```python
    def upsert(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
    ) -> UpsertOutcome:
        github_id = metadata["id"]
        readme_sha = _sha256_hex(readme_content) if readme_content is not None else None

        try:
            with self._conn:
                existing = self._fetch_internal_by_github_id(github_id)
                outcome = self._classify(existing, metadata, readme_sha)

                if outcome == UpsertOutcome.NEW:
                    repo_id = self._insert_repo(metadata, readme_content, readme_sha)
                    self._replace_topics(repo_id, metadata.get("topics", []))
                elif outcome in (UpsertOutcome.UPDATED, UpsertOutcome.RENAMED):
                    assert existing is not None
                    self._update_repo(existing["id"], metadata, readme_content, readme_sha)
                    self._replace_topics(existing["id"], metadata.get("topics", []))
        except sqlite3.IntegrityError as exc:
            if "repos.owner" in str(exc) or "UNIQUE constraint failed: repos.owner, repos.name" in str(exc):
                raise DuplicateRepoNameError(
                    f"(owner={metadata['owner']['login']!r}, name={metadata['name']!r}) "
                    f"already occupied by a different github_id"
                ) from exc
            raise

        return outcome
```

- [ ] **Step 4: Run the tests**

```bash
pytest tests/test_storage.py -v
```

Expected: 13 passed (10 prior + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/capxure/storage.py tests/test_storage.py
git commit -m "Translate UNIQUE(owner,name) collisions into DuplicateRepoNameError

When inserting a new repo whose (owner, name) matches an existing repo
with a different github_id, the sqlite3.IntegrityError is caught and
re-raised as the typed DuplicateRepoNameError. Also covers the topic
add/remove and nullable-README paths with explicit tests; these already
worked correctly but are now documented by tests."
```

---

## Task 7: Read-path methods (`get_repo`, `list_repos`, `get_metadata_json`, escape hatch)

**Files:**
- Modify: `src/capxure/storage.py` (implement read-path methods and the Row → Repo mapper)
- Modify: `tests/test_storage.py` (add tests 12, 13, 14)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_storage.py`:

```python
from capxure.storage import Repo


def test_list_and_count_repos(db_path, claude_mem_metadata, awesome_nodejs_metadata, chunky_metadata):
    storage = Storage(db_path)
    try:
        storage.upsert(claude_mem_metadata,    "readme-1")
        storage.upsert(awesome_nodejs_metadata, "readme-2")
        storage.upsert(chunky_metadata,         "readme-3")

        assert storage.count_repos() == 3

        repos = storage.list_repos()
        assert len(repos) == 3
        assert all(isinstance(r, Repo) for r in repos)

        # Deterministic order: by github_id ascending.
        ids = [r.github_id for r in repos]
        assert ids == sorted(ids)

        # Each Repo carries its topics populated from the junction table.
        claude_repo = next(r for r in repos if r.github_id == claude_mem_metadata["id"])
        assert tuple(sorted(claude_repo.topics)) == tuple(sorted(claude_mem_metadata.get("topics", [])))
    finally:
        storage.close()


def test_get_metadata_json_roundtrip(db_path, claude_mem_metadata):
    storage = Storage(db_path)
    try:
        storage.upsert(claude_mem_metadata, "readme")
        got = storage.get_metadata_json(
            claude_mem_metadata["owner"]["login"],
            claude_mem_metadata["name"],
        )
        assert got == claude_mem_metadata

        missing = storage.get_metadata_json("nobody", "nope")
        assert missing is None
    finally:
        storage.close()


def test_escape_hatch_connection(db_path, claude_mem_metadata):
    storage = Storage(db_path)
    try:
        storage.upsert(claude_mem_metadata, "readme")

        # Consumer drops to raw SQL via the documented escape hatch.
        cur = storage.connection.execute(
            "SELECT COUNT(*) FROM repos WHERE language IS NOT NULL OR language IS NULL"
        )
        assert cur.fetchone()[0] == 1
    finally:
        storage.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_storage.py -v
```

Expected: the three new tests fail with `NotImplementedError` on `list_repos`, `get_repo`/`get_metadata_json`, etc.

- [ ] **Step 3: Implement the read-path methods**

In `src/capxure/storage.py`, replace the stubbed `get_repo`, `get_repo_by_github_id`, `list_repos`, and `get_metadata_json`; `count_repos` is already correct from Task 2. Add a `_row_to_repo` helper:

```python
    # --- read path ---

    def get_repo(self, owner: str, name: str) -> Repo | None:
        row = self._conn.execute(
            "SELECT * FROM repos WHERE owner = ? AND name = ?",
            (owner, name),
        ).fetchone()
        return self._row_to_repo(row) if row is not None else None

    def get_repo_by_github_id(self, github_id: int) -> Repo | None:
        row = self._conn.execute(
            "SELECT * FROM repos WHERE github_id = ?",
            (github_id,),
        ).fetchone()
        return self._row_to_repo(row) if row is not None else None

    def list_repos(self) -> list[Repo]:
        rows = self._conn.execute(
            "SELECT * FROM repos ORDER BY github_id ASC"
        ).fetchall()
        return [self._row_to_repo(row) for row in rows]

    def count_repos(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM repos")
        return cur.fetchone()[0]

    def get_metadata_json(self, owner: str, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT metadata FROM repos WHERE owner = ? AND name = ?",
            (owner, name),
        ).fetchone()
        return json.loads(row["metadata"]) if row is not None else None

    def _row_to_repo(self, row: sqlite3.Row) -> Repo:
        topics = tuple(
            r[0] for r in self._conn.execute(
                "SELECT topic FROM repo_topics WHERE repo_id = ? ORDER BY topic",
                (row["id"],),
            ).fetchall()
        )
        return Repo(
            id=row["id"],
            github_id=row["github_id"],
            owner=row["owner"],
            name=row["name"],
            full_name=row["full_name"],
            url=row["url"],
            default_branch=row["default_branch"],
            description=row["description"],
            language=row["language"],
            stars=row["stars"],
            forks=row["forks"],
            pushed_at=row["pushed_at"],
            is_fork=bool(row["is_fork"]),
            is_archived=bool(row["is_archived"]),
            topics=topics,
            readme_content=row["readme_content"],
            readme_sha=row["readme_sha"],
            captured_at=row["captured_at"],
            last_synced_at=row["last_synced_at"],
        )
```

- [ ] **Step 4: Run the full storage test suite**

```bash
pytest tests/test_storage.py -v
```

Expected: 16 passed (13 prior + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/capxure/storage.py tests/test_storage.py
git commit -m "Implement Storage read-path methods and Repo materialization

Adds get_repo, get_repo_by_github_id, list_repos, get_metadata_json, and
the _row_to_repo helper that materializes a Repo dataclass including its
topics from the junction table. list_repos orders by github_id for
deterministic output."
```

---

## Task 8: Rewire `processor.py` to the new Storage API

**Files:**
- Modify: `src/capxure/processor.py` (restore full implementation, wired to `diff()` + `upsert()`)
- Create: `tests/test_processor.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_processor.py`:

```python
"""Integration smoke test for processor.process_repo() over the new storage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from capxure.processor import ProcessResult, Severity, process_repo
from capxure.storage import Storage, UpsertOutcome


@pytest.mark.asyncio
async def test_process_repo_new_then_unchanged(db_path, claude_mem_metadata):
    """First run captures; second run skips the README fetch via diff()."""
    storage = Storage(db_path)
    try:
        github = MagicMock()
        github.fetch_metadata = AsyncMock(return_value=claude_mem_metadata)
        github.fetch_readme = AsyncMock(return_value="# claude-mem\nreadme body\n")

        statuses: list[tuple[str, Severity]] = []

        def on_status(message: str, severity: Severity) -> None:
            statuses.append((message, severity))

        full_name = claude_mem_metadata["full_name"]
        url = f"https://github.com/{full_name}"

        # First call: NEW.
        first = await process_repo(url, github=github, storage=storage, on_status=on_status)
        assert isinstance(first, ProcessResult)
        assert first.outcome == UpsertOutcome.NEW
        assert storage.count_repos() == 1
        assert github.fetch_metadata.await_count == 1
        assert github.fetch_readme.await_count == 1

        # Second call: UNCHANGED — diff() short-circuits, no fetch_readme call.
        second = await process_repo(url, github=github, storage=storage, on_status=on_status)
        assert second.outcome == UpsertOutcome.UNCHANGED
        assert github.fetch_metadata.await_count == 2
        assert github.fetch_readme.await_count == 1, (
            "fetch_readme must NOT be called when diff() returns UNCHANGED"
        )
    finally:
        storage.close()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_processor.py -v
```

Expected: `NotImplementedError: process_repo is temporarily stubbed...`

- [ ] **Step 3: Restore `src/capxure/processor.py` with the new API wiring**

Replace the entire file:

```python
"""Core orchestrator.

Coordinates GitHub API calls and local storage operations.
Accepts a StatusCallback so consumers can surface progress.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from capxure.github import (
    AuthenticationError,
    GitHubClient,
    NotFoundError,
    RateLimitExceededError,
    parse_github_url,
)
from capxure.storage import Storage, UpsertOutcome


class Severity(StrEnum):
    SUCCESS = "success"
    INFO = "info"
    ERROR = "error"


class StatusCallback(Protocol):
    def __call__(self, message: str, severity: Severity) -> None: ...


@dataclass(frozen=True)
class ProcessResult:
    owner: str
    repo: str
    outcome: UpsertOutcome | None  # None if error
    error: str | None = None


async def process_repo(
    url: str,
    *,
    github: GitHubClient,
    storage: Storage,
    on_status: StatusCallback,
) -> ProcessResult:
    """Process a single GitHub repo URL end-to-end.

    1. Parse URL
    2. Fetch metadata
    3. diff() against local — skip README fetch if UNCHANGED/LOCAL_IS_NEWER
    4. Fetch README (only for NEW/UPDATED/RENAMED outcomes)
    5. upsert() atomically
    """
    try:
        owner, repo = parse_github_url(url)
    except ValueError as exc:
        on_status(str(exc), Severity.ERROR)
        return ProcessResult(owner="", repo="", outcome=None, error=str(exc))

    on_status(f"Fetching metadata for {owner}/{repo}...", Severity.INFO)

    try:
        metadata_entry = await github.fetch_metadata(owner, repo)
    except NotFoundError:
        msg = f"Repository {owner}/{repo} not found on GitHub"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)
    except RateLimitExceededError:
        msg = "GitHub API rate limit exceeded — wait and retry"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)
    except AuthenticationError:
        msg = "Authentication failed — invalid or missing GITHUB_TOKEN"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)
    except Exception as exc:
        msg = f"Error fetching {owner}/{repo}: {exc}"
        on_status(msg, Severity.ERROR)
        return ProcessResult(owner=owner, repo=repo, outcome=None, error=msg)

    # Pre-fetch dedup check: skip README download when nothing has changed.
    outcome = storage.diff(metadata_entry)

    if outcome == UpsertOutcome.UNCHANGED:
        on_status(f"{owner}/{repo}: already up to date", Severity.INFO)
        return ProcessResult(owner=owner, repo=repo, outcome=outcome)

    if outcome == UpsertOutcome.LOCAL_IS_NEWER:
        on_status(f"{owner}/{repo}: local copy is newer, skipping", Severity.INFO)
        return ProcessResult(owner=owner, repo=repo, outcome=outcome)

    # Fetch README.
    default_branch = metadata_entry.get("default_branch", "main")
    on_status(f"Downloading README for {owner}/{repo}...", Severity.INFO)

    try:
        readme_content = await github.fetch_readme(owner, repo, default_branch)
    except NotFoundError:
        readme_content = None
        on_status(
            f"{owner}/{repo}: no README.md found, storing NULL readme",
            Severity.INFO,
        )

    # Atomic upsert. SQLite's WAL + single-writer replaces the old asyncio lock.
    outcome = storage.upsert(metadata_entry, readme_content)

    if outcome == UpsertOutcome.NEW:
        on_status(f"{owner}/{repo}: captured successfully", Severity.SUCCESS)
    elif outcome in (UpsertOutcome.UPDATED, UpsertOutcome.RENAMED):
        on_status(f"{owner}/{repo}: updated to latest version", Severity.SUCCESS)

    return ProcessResult(owner=owner, repo=repo, outcome=outcome)
```

Notable deltas from the pre-migration processor:
- `_storage_lock` is **deleted** — SQLite's WAL + single-writer handles what the asyncio lock was protecting.
- `load_metadata()` / `check_dedup()` replaced with a single `storage.diff(metadata_entry)` call.
- `upsert_entry(owner, repo, metadata, readme)` → `storage.upsert(metadata, readme_content)` (the new signature derives owner/name from the metadata itself).
- No-README case stores `None` instead of a placeholder string (the schema allows NULL; readers distinguish absence from emptiness).
- Added `RENAMED` outcome to the "updated to latest" status branch.

- [ ] **Step 4: Run both test modules**

```bash
pytest tests/ -v
```

Expected: all tests pass (16 storage + 1 processor).

- [ ] **Step 5: Commit**

```bash
git add src/capxure/processor.py tests/test_processor.py
git commit -m "Rewire processor.process_repo() to Storage.diff() + Storage.upsert()

Restores the full processor implementation on top of the new SQLite-backed
Storage. The pre-fetch dedup optimization is preserved via diff(): when it
returns UNCHANGED or LOCAL_IS_NEWER, fetch_readme is skipped. The asyncio
_storage_lock is removed — SQLite's WAL + single-writer handles write
serialization. When GitHub has no README, we now store NULL instead of a
placeholder string. Covered by tests/test_processor.py."
```

---

## Task 9: Public API import smoke test

**Files:**
- Create: `tests/test_imports.py`

- [ ] **Step 1: Write the test**

Create `tests/test_imports.py`:

```python
"""Smoke test: every symbol in capxure.__all__ is importable from the package root."""
from __future__ import annotations

import capxure


EXPECTED_EXPORTS = {
    "AuthenticationError",
    "DuplicateRepoNameError",
    "GitHubClient",
    "GitHubError",
    "NotFoundError",
    "ProcessResult",
    "RateLimitExceededError",
    "RateLimitInfo",
    "Repo",
    "Severity",
    "StatusCallback",
    "Storage",
    "UnsupportedSchemaError",
    "UpsertOutcome",
    "__version__",
    "parse_github_url",
    "process_repo",
}


def test_all_matches_expected():
    assert set(capxure.__all__) == EXPECTED_EXPORTS


def test_every_name_in_all_is_resolvable():
    for name in capxure.__all__:
        assert hasattr(capxure, name), f"capxure.__all__ lists {name!r} but attribute is missing"


def test_removed_symbols_are_gone():
    assert not hasattr(capxure, "DeduplicationResult"), (
        "DeduplicationResult was removed in the SQLite migration; do not re-add"
    )
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/test_imports.py -v
```

Expected: 3 passed.

- [ ] **Step 3: Run the full suite one more time for the checkpoint**

```bash
pytest -v
```

Expected: all tests pass across `test_storage.py`, `test_processor.py`, `test_imports.py`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_imports.py
git commit -m "Add public API import smoke test

Locks down the capxure.__all__ surface so that accidentally dropping or
renaming a re-exported symbol surfaces as a test failure rather than as
a silent downstream break. Explicitly asserts the old DeduplicationResult
symbol is gone."
```

---

## Task 10: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Inspect the current README**

Read the existing file to see what needs editing:

```bash
cat README.md
```

Target sections to rewrite:
- Any description of the storage layer (currently describes filesystem JSON).
- The "data_dir" configuration paragraph (recently updated per memory — still filesystem-shaped).
- Any mention of `metadata.json` or `readmes/` as the authoritative store.

- [ ] **Step 2: Rewrite the storage section of README.md**

Replace the storage-layer description with a section matching this shape (adapt tone to match the surrounding README):

```markdown
## Storage

Capxure persists captured repos to a single SQLite database. The default
location is `{user_data_dir}/capxure.db`, where `user_data_dir` follows the
`platformdirs` convention for your OS (e.g. `~/.local/share/capxure/` on
Linux). Override with `Storage(db_path=Path(...))`.

WAL mode is enabled, so you'll see `capxure.db-wal` and `capxure.db-shm`
sidecar files next to the database while a connection is open. These are
cleaned up on a normal close and do not need to be backed up separately.

### Schema (public contract)

The schema is a documented public contract — you may run arbitrary SQL
against it via `storage.connection`.

- Table `repos` — one row per captured GitHub repo. Includes denormalized
  columns for common query hotspots (`language`, `stars`, `forks`,
  `pushed_at`, `is_fork`, `is_archived`), an inline `readme_content`
  column (nullable; NULL means "no README"), and the full GitHub API
  response preserved as JSON in `metadata`.
- Table `repo_topics` — junction table for many-to-many topics. Composite
  PK `(repo_id, topic)` provides insert-dedup; a secondary index on
  `topic` supports `WHERE topic = ?` filtering.

### Python API

```python
from capxure import Storage, UpsertOutcome

with Storage() as storage:
    outcome = storage.upsert(github_metadata_dict, readme_content)
    # outcome is one of: NEW, UPDATED, RENAMED, UNCHANGED, LOCAL_IS_NEWER

    repo = storage.get_repo("sindresorhus", "awesome-nodejs")
    if repo is not None:
        print(repo.stars, repo.topics)

    all_repos = storage.list_repos()
```

The `storage.connection` property exposes the underlying
`sqlite3.Connection` as an escape hatch for ad-hoc SQL:

```python
with Storage() as storage:
    for row in storage.connection.execute(
        "SELECT full_name, stars FROM repos WHERE language = ? ORDER BY stars DESC",
        ("Python",),
    ):
        print(row["full_name"], row["stars"])
```
```

Delete any paragraphs in the README that still describe `data/metadata.json`
or `data/readmes/` as the storage location. (Note for readers: those
directories on disk are not read by the library — they're preserved as
fixture data from the pre-migration era.)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Rewrite README storage section for SQLite layer

Replaces the filesystem storage description with the new SQLite-backed
model. Documents the schema as a public contract, shows usage examples for
both the facade and the escape-hatch connection, and notes the WAL sidecar
files so consumers don't panic when they see them."
```

---

## Task 11: Version bump

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/capxure/__init__.py`

- [ ] **Step 1: Bump version**

Edit `pyproject.toml` line 7 (`version = "0.1.0"`) → `version = "0.2.0"`.
Edit `src/capxure/__init__.py` line with `__version__ = "0.1.0"` → `__version__ = "0.2.0"`.

Minor bump: this is pre-1.0, so semver is loose. The storage layer rewrite is a breaking public-API change, but bumping to 1.0 would imply stability commitments we don't want to make yet.

- [ ] **Step 2: Confirm the bump shows up**

```bash
pip install -e . --quiet && python3 -c "import capxure; print(capxure.__version__)"
```

Expected: `0.2.0`

- [ ] **Step 3: Run the full test suite one last time**

```bash
pytest -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/capxure/__init__.py
git commit -m "Bump version to 0.2.0

Breaking change: storage layer rewritten from JSON-files-on-disk to
SQLite. DeduplicationResult is removed; Storage's API is entirely new.
See docs/superpowers/specs/2026-04-22-sqlite-storage-migration-design.md
for the full contract and docs/superpowers/plans/2026-04-22-sqlite-storage-migration.md
for the migration path."
```

---

## Summary of final state

After Task 11, the repo should contain:

- `src/capxure/storage.py` — ~380–430 LOC; schema, Storage class, upsert/diff, read methods, error types, Repo dataclass.
- `src/capxure/processor.py` — ~110 LOC; rewired to diff() + upsert(), no asyncio lock.
- `src/capxure/__init__.py` — 17 exports (was 14; -1 `DeduplicationResult`, +4 new: `Repo`, `UpsertOutcome`, `DuplicateRepoNameError`, `UnsupportedSchemaError`).
- `tests/test_storage.py` — 16 tests (some parametrized).
- `tests/test_processor.py` — 1 integration test.
- `tests/test_imports.py` — 3 smoke tests.
- `tests/fixtures/` — 3 real GitHub metadata snapshots.
- `tests/conftest.py` — shared fixtures.
- `pyproject.toml` — `0.2.0`, pytest/pytest-asyncio dev deps, pytest ini config.
- `README.md` — updated storage section.
- `data/metadata.json`, `data/readmes/` — **unchanged**, preserved as manual CLI test fixtures for the future.

Eleven commits, each self-contained. Library is fully green at the end of every task except Task 2 (where `process_repo` is intentionally stubbed with `NotImplementedError`; import and storage tests pass, but calling the processor would crash — Task 8 restores it).
