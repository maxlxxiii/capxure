# SQLite Storage Migration — Design

**Status:** Approved 2026-04-22. Ready for implementation planning.

## Context

Capxure currently persists captured GitHub repo metadata to a single
`data/metadata.json` dict-of-dicts, with README content written as sibling
files under `data/readmes/{owner}--{repo}.md`. Every upsert rewrites the
entire metadata JSON file atomically (temp file + rename).

This design replaces that filesystem layer with a SQLite database. The move
is a foundational one rather than a response to a specific feature need —
the user confirmed motives span four concerns:

- **A.** Downstream consumers may want SQL (filter by language, join by topic).
- **B.** The atomic-rewrite-whole-file pattern doesn't scale and offers no
  multi-writer safety.
- **C.** A single `.db` artifact is more portable than a directory tree of
  JSON + markdown.
- **D.** Structured data belongs in a structured store; we want the foundation
  correct before the library grows.

Because no specific consumer dictates requirements, we optimize for a clean,
stable foundation — not for features a hypothetical consumer might need.

## Current state (pre-migration)

- `src/capxure/storage.py` (~157 LOC): `Storage` class with `load_metadata`,
  `save_metadata`, `check_dedup`, `upsert_entry`, `save_readme`,
  `find_key_by_id`, `count_repos`, `ensure_directories`, `make_key`.
  Returns a `DeduplicationResult` enum from `check_dedup`.
- `src/capxure/processor.py` (~122 LOC): `process_repo()` calls
  `load_metadata → check_dedup → (maybe) fetch_readme → upsert_entry`.
  Uses an `asyncio.Lock` (`_storage_lock`) to serialize writes.
- `src/capxure/__init__.py`: 13 public exports including `Storage`,
  `DeduplicationResult`, processor types.
- Legacy folder `data/awesome-lists/` exists but is untouched by the library
  (pre-refactor artifact). No awesome-list / regular-repo path split in code.
- No queue system. The queue was TUI-era and was removed in the
  strip-to-library refactor.
- 16 repos currently captured under `data/metadata.json` — user preserves
  these as manual test data for the future CLI; the library does not migrate
  or touch them.

## Target state

A single SQLite database file containing all captured data. README content
stored inline on the `repos` table. Topics normalized to a junction table.
The `Storage` class is a typed facade over the schema; an escape-hatch
property exposes the underlying connection for advanced consumers.

Default DB path: `{resolved_data_dir}/capxure.db`, where `resolved_data_dir`
follows the principle already established for the library — `platformdirs.user_data_dir("capxure")` when the library is embedded, package-relative `data/` for in-repo development. Overridable via `Storage(db_path=...)`.

The schema is a documented public contract. Consumers may query it directly
through the escape-hatch connection; we commit to not breaking it lightly.

## Schema

```sql
PRAGMA user_version = 1;
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE repos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id         INTEGER UNIQUE NOT NULL,
    owner             TEXT NOT NULL,               -- = metadata["owner"]["login"]
    name              TEXT NOT NULL,
    full_name         TEXT NOT NULL,
    url               TEXT NOT NULL,
    default_branch    TEXT,
    description       TEXT,
    language          TEXT,                        -- primary language only
    stars             INTEGER NOT NULL DEFAULT 0,
    forks             INTEGER NOT NULL DEFAULT 0,
    pushed_at         TEXT,                        -- ISO8601 from GitHub
    is_fork           INTEGER NOT NULL DEFAULT 0,  -- 0/1 boolean
    is_archived       INTEGER NOT NULL DEFAULT 0,  -- 0/1 boolean
    readme_content    TEXT,                        -- NULL = no README
    readme_sha        TEXT,                        -- SHA-256 of UTF-8 bytes
    metadata          TEXT NOT NULL,               -- full GitHub JSON response
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
```

Notes:
- `owner` stores `owner.login` (a string). The rest of GitHub's `owner` object
  (`id`, `avatar_url`, `type`, etc.) remains in the `metadata` JSON blob.
- `readme_content` and `readme_sha` are nullable: `NULL` honestly represents
  "this repo has no README." Empty string means "README exists and is empty."
- Topics use a junction table with composite PK `(repo_id, topic)` — insert
  dedup comes for free, and the clustered lookup covers the common
  "topics-of-repo" direction. A separate index on `topic` covers the
  "repos-with-topic" direction.
- No `repo_documents` table. If we later capture LICENSE, CONTRIBUTING, etc.,
  we add the table then as an additive migration (YAGNI).
- `stars` / `forks` default to 0 but are `NOT NULL` — GitHub always returns
  integers for these, so `NOT NULL` guards against malformed input rather than
  accommodating absence.

## Python API

New module shape for `src/capxure/storage.py`:

```python
class UpsertOutcome(StrEnum):
    NEW = "new"                       # not present before
    UPDATED = "updated"               # existed, content or metadata changed
    RENAMED = "renamed"               # same github_id, owner/name changed
    UNCHANGED = "unchanged"           # existed, nothing to persist
    LOCAL_IS_NEWER = "local_is_newer" # local pushed_at > remote

class DuplicateRepoNameError(Exception):
    """Different GitHub repo already occupies this (owner, name)."""

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
    # Full metadata blob deliberately excluded — consumers who need it call
    # Storage.get_metadata_json() separately to avoid paying JSON-deserialize
    # cost on every list_repos().

class Storage:
    def __init__(self, db_path: Path | None = None) -> None: ...
    def close(self) -> None: ...
    def __enter__(self) -> "Storage": ...
    def __exit__(self, *exc_info) -> None: ...

    @property
    def connection(self) -> sqlite3.Connection: ...   # escape hatch

    # write path
    def upsert(
        self,
        metadata: dict[str, Any],
        readme_content: str | None,
    ) -> UpsertOutcome: ...

    def diff(self, metadata: dict[str, Any]) -> UpsertOutcome: ...
    # Read-only "what would upsert do?" — lets the processor skip the README
    # fetch when the result would be UNCHANGED / LOCAL_IS_NEWER.

    # read path
    def get_repo(self, owner: str, name: str) -> Repo | None: ...
    def get_repo_by_github_id(self, github_id: int) -> Repo | None: ...
    def list_repos(self) -> list[Repo]: ...
    def count_repos(self) -> int: ...
    def get_metadata_json(self, owner: str, name: str) -> dict[str, Any] | None: ...
```

## Upsert semantics

The behavioral contract for `upsert()`:

```python
def upsert(self, metadata: dict[str, Any], readme_content: str | None) -> UpsertOutcome:
    github_id   = metadata["id"]
    owner       = metadata["owner"]["login"]
    name        = metadata["name"]
    remote_push = metadata.get("pushed_at")
    readme_sha  = sha256(readme_content.encode("utf-8")).hexdigest() if readme_content else None

    with self.connection:  # atomic: commits on success, rolls back on exception
        existing = self._fetch_by_github_id(github_id)

        if existing is None:
            self._insert_repo(metadata, readme_content, readme_sha)
            self._replace_topics(github_id, metadata.get("topics", []))
            return UpsertOutcome.NEW

        if existing.pushed_at and remote_push and existing.pushed_at > remote_push:
            return UpsertOutcome.LOCAL_IS_NEWER   # no write

        renamed = (existing.owner, existing.name) != (owner, name)

        if (existing.readme_sha == readme_sha
                and existing.pushed_at == remote_push
                and not renamed):
            return UpsertOutcome.UNCHANGED        # no write

        self._update_repo(existing.id, metadata, readme_content, readme_sha)
        self._replace_topics(existing.id, metadata.get("topics", []))
        return UpsertOutcome.RENAMED if renamed else UpsertOutcome.UPDATED
```

Rules baked into this design:

- **Identity is `github_id`, always.** `(owner, name)` is a human-readable
  secondary key. This is why rename detection works: same `github_id`,
  different owner/name → `RENAMED`.

- **`UNCHANGED` requires three equalities:** same `readme_sha`, same
  `pushed_at`, not renamed. We do NOT compare the full metadata blob because
  GitHub mutates counts (stars, forks, subscribers) constantly — comparing
  those would make `UNCHANGED` almost never fire, defeating the pre-fetch
  optimization.

- **`LOCAL_IS_NEWER` short-circuits before rename/unchanged checks.** If
  local timestamp is newer than remote (clock skew, unusual), we refuse to
  overwrite.

- **Topics: delete-then-insert in the same transaction.**
  `DELETE FROM repo_topics WHERE repo_id=?` then `INSERT OR IGNORE` the
  current list. Correctly handles both added and removed topics.

- **`diff()` shares the same classification logic** — factored into a private
  `_classify(existing, metadata, readme_sha) → UpsertOutcome` helper that
  both methods call. `diff()` runs only the classification (no writes).

- **Collision handling (D1):** a new `(owner, name)` that collides with an
  existing row having a different `github_id` raises `DuplicateRepoNameError`.
  Rare event (GitHub rename/recreate scenarios); loud error is better than
  silent data loss. The alternative of dropping `UNIQUE (owner, name)` was
  rejected because it would make `get_repo(owner, name)` ambiguous for all
  consumers, forever.

## Connection lifecycle

```python
def __init__(self, db_path: Path | None = None) -> None:
    self._db_path = db_path if db_path is not None else _resolve_default_db_path()
    self._db_path.parent.mkdir(parents=True, exist_ok=True)

    self._conn = sqlite3.connect(
        self._db_path,
        detect_types=sqlite3.PARSE_DECLTYPES,
        isolation_level="",  # default: implicit txn via `with conn:`
    )
    self._conn.row_factory = sqlite3.Row
    self._conn.execute("PRAGMA foreign_keys = ON")
    self._conn.execute("PRAGMA journal_mode = WAL")
    self._ensure_schema()


def _resolve_default_db_path() -> Path:
    # Reuses the library's existing data-dir resolver, then appends the
    # filename. Matches the path-resolution principle established for the
    # library: platformdirs.user_data_dir when embedded, package-relative
    # data/ for in-repo dev.
    return _resolve_default_data_dir() / "capxure.db"
```

Notes:

- Connection opens in `__init__`, stays open for the `Storage` lifetime.
  Closed explicitly via `close()` or context-manager exit.
- `_ensure_schema()` is idempotent: checks `PRAGMA user_version`. If 0
  (fresh) → runs `CREATE TABLE` statements, sets `user_version = 1`. If 1 →
  no-op. If higher → raises `UnsupportedSchemaError` (prevents downgrade
  footguns).
- `isolation_level=""` is sqlite3's default and the correct setting for
  `with conn:` implicit transactions.
- `row_factory = sqlite3.Row` lets internal queries use named column access.
- WAL mode produces `capxure.db-wal` and `capxure.db-shm` sidecar files.
  Harmless; cleaned up on normal close. Documented in README.
- Thread safety: `check_same_thread=True` (SQLite default). Consumers
  wanting multi-threaded access open one `Storage` per thread.
- Escape hatch: `storage.connection` returns the same connection the facade
  uses. Destructive SQL is the consumer's responsibility.

## Migration of existing library code

**`src/capxure/storage.py`** — full rewrite.

Removed: `DeduplicationResult`, `load_metadata`, `save_metadata`,
`check_dedup`, `find_key_by_id`, `save_readme`, `upsert_entry`,
`ensure_directories`, `make_key`, `_resolve_default_data_dir` (replaced by
`_resolve_default_db_path`).

Retained: module name, `Storage` class name.

Added: everything in this spec.

**`src/capxure/processor.py`** — minor surgery.

Lines ~91–114 change from:

```python
all_metadata = storage.load_metadata()
dedup_result = storage.check_dedup(all_metadata, metadata_entry)
if dedup_result == DeduplicationResult.ALREADY_UP_TO_DATE: ...
if dedup_result == DeduplicationResult.LOCAL_IS_NEWER: ...
# fetch readme
async with _storage_lock:
    result = storage.upsert_entry(owner, repo, metadata_entry, readme_content)
```

to:

```python
outcome = storage.diff(metadata_entry)
if outcome in (UpsertOutcome.UNCHANGED, UpsertOutcome.LOCAL_IS_NEWER):
    on_status(f"{owner}/{repo}: {outcome.value}", Severity.INFO)
    return ProcessResult(owner=owner, repo=repo, outcome=outcome)
# fetch readme
outcome = storage.upsert(metadata_entry, readme_content)
```

Also:
- `ProcessResult.outcome` retyped from `DeduplicationResult | None` →
  `UpsertOutcome | None`.
- `_storage_lock` deleted. SQLite's WAL + single-writer serialization
  handles what the asyncio lock was protecting.
- Status messages remapped: `NEW` → "captured successfully",
  `UPDATED` / `RENAMED` → "updated to latest version",
  `UNCHANGED` → "already up to date",
  `LOCAL_IS_NEWER` → "local copy is newer, skipping".

**`src/capxure/__init__.py`** — public exports.

- Remove: `DeduplicationResult`.
- Add: `UpsertOutcome`, `Repo`, `DuplicateRepoNameError`,
  `UnsupportedSchemaError`.
- Keep everything else.
- Updated in the same commit as the `storage.py` rewrite (Phase 2) — not
  later — because a dangling import of `DeduplicationResult` would make
  `import capxure` fail and take the whole test suite down with it.

**`pyproject.toml`** — no new runtime dependencies. `sqlite3` and
`hashlib` are stdlib. `platformdirs` is already present. Version bump
(pre-1.0, so breaking change is fine).

**What does not change:** `src/capxure/github.py` (storage-agnostic),
`data/metadata.json`, `data/readmes/` (preserved as manual test fixtures),
`py.typed` marker.

## Testing approach

A new test harness. Library has no tests today; we're introducing one as
part of this work.

Infrastructure:
- `pytest` + `pytest-asyncio` under `[project.optional-dependencies].dev`.
- `tests/` at repo root. Every test uses `tmp_path` for an ephemeral DB.
- `tests/fixtures/` holds 2–3 real metadata JSON snapshots extracted from
  `data/metadata.json` so tests exercise the real field shape.

`tests/test_storage.py` — the core contract, 14 tests:

1. Fresh DB creation: `Storage(tmp_path/"test.db")` creates file, tables,
   pragmas; `user_version == 1`.
2. Re-open existing DB: create, close, re-open; no errors, schema unchanged.
3. Upsert NEW: first insert returns `NEW`, `count_repos == 1`, `get_repo`
   returns correct `Repo`, topics populated, `readme_sha` matches SHA-256.
4. Upsert UNCHANGED: same inputs twice; second returns `UNCHANGED`,
   `last_synced_at` does not advance.
5. Upsert UPDATED: changed `pushed_at` + changed README returns `UPDATED`,
   new content reflected.
6. Upsert RENAMED: same `github_id`, different `owner`/`name` returns
   `RENAMED`, old lookup returns `None`, new lookup returns the row.
7. Upsert LOCAL_IS_NEWER: second upsert with older `pushed_at` returns
   `LOCAL_IS_NEWER`, no write.
8. `diff()` matches `upsert()` classification: parametrized across all the
   above scenarios.
9. `DuplicateRepoNameError` raised on `(owner, name)` collision with
   different `github_id`.
10. Topics add + remove: replacing `[a, b, c]` with `[a, b, d]` leaves
    exactly `{a, b, d}`.
11. Nullable README: `readme_content=None` round-trips; `readme_sha is None`.
12. `list_repos` / `count_repos` with N repos return correct counts and
    deterministic order (by `github_id`).

> **Update 2026-04-24:** default order is now `last_synced_at DESC` (with `github_id` ASC as tie-break). See `2026-04-24-cli-ls-design.md`.
13. `get_metadata_json` round-trips the full blob.
14. Escape-hatch connection: `storage.connection.execute(...)` returns
    expected rows.

`tests/test_processor.py` — one integration smoke test:

- Mock `github.fetch_repo_metadata` and `github.fetch_readme` with fixture
  data.
- Call `process_repo(url, Storage(tmp_path), on_status=...)`.
- Assert `NEW` outcome, DB has one row, status messages received.
- Second call returns `UNCHANGED` and `fetch_readme` is NOT called (proving
  the pre-fetch `diff()` optimization).

`tests/test_imports.py` — one-liner covering the public API surface:

- Imports every symbol re-exported by `capxure/__init__.py`. Catches
  regressions where a refactor drops or renames a public name without
  updating the re-export list.

Not tested:
- Performance or stress (hundreds of repos, not millions).
- Migration from old JSON layout (we are not writing one).
- Concurrent writers (single-writer is explicit design constraint).
- Escape-hatch destructive SQL (consumer responsibility).

## Implementation phases

Six phases, each a self-contained commit. Library is compilable and
internally consistent at every checkpoint.

**Phase 1 — Test harness + fixtures.**
Add `pytest` + `pytest-asyncio` to `[project.optional-dependencies].dev`.
Create `tests/conftest.py` and `tests/fixtures/` with 2–3 real metadata
JSON snapshots. Verify `pytest` runs (zero collected is fine).

**Phase 2 — New `storage.py` + updated `__init__.py` + its 14 tests.**
Full rewrite of `src/capxure/storage.py`. Update `src/capxure/__init__.py`
*in the same commit* — remove `DeduplicationResult` from re-exports, add
`UpsertOutcome`, `Repo`, `DuplicateRepoNameError`, `UnsupportedSchemaError`.
The `__init__.py` change is mandatory here: without it, the top-level
import of `DeduplicationResult` would fail and the package would be
uncollectable by pytest. Simultaneously write `tests/test_storage.py`.

At this point `processor.py` is broken at call sites (still calls
`check_dedup`, still references `DeduplicationResult`). Running the
capture function would crash, but `import capxure` works and
`test_storage.py` passes in isolation.

**Phase 3 — Rewire `processor.py`.**
Apply the `diff()` + `upsert()` migration. Remove `_storage_lock`. Retype
`ProcessResult.outcome`. Update status messages. Add
`tests/test_processor.py` with the one integration smoke test. Library
is now fully functional end-to-end.

**Phase 4 — Public API import test.**
Add `tests/test_imports.py` covering every re-exported symbol. All tests
green at end of phase.

**Phase 5 — Docs.**
Update `README.md` to describe SQLite storage, document the schema as a
public contract, document the `Storage` API and escape-hatch connection,
note the `.db-wal` / `.db-shm` sidecars, clarify that `data/metadata.json`
and `data/readmes/` are historical test fixtures not read by the library.

**Phase 6 — Version bump.**
Bump version in `pyproject.toml`. Note the breaking storage-layer change
in the commit message.

## Out of scope

Explicitly not in any phase:

- Migration of the 16 existing repos under `data/` (user keeps them as
  manual CLI test fixtures; library ignores them).
- CLI — separate project, planned but not part of this work.
- Multi-document support (`LICENSE`, `CONTRIBUTING`, etc.) — no concrete
  plans; `repo_documents` table deferred.
- `repo_languages` table / multi-language capture — current GitHub
  endpoint returns primary language only; expanding capture scope is a
  separate change.
- Full-text search on README content — not requested.
- Performance tuning (expression indexes, query planning) — add
  reactively when a real query is measured slow.

## Estimated scope

- `storage.py`: ~350–450 new lines (replacing ~157 existing).
- `processor.py`: ~40-line delta.
- `__init__.py`: ~6-line delta.
- Tests: ~400 new lines.
- README: documentation rewrite.

## Decision log

Each decision was walked through with the user during brainstorming.
Captured here so future readers can re-derive why, not just what.

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Move is foundational, not feature-driven | User motives span durability, portability, query access, and "correct foundation." Optimize for stability, not for a specific hypothetical consumer. |
| 2 | Facade with escape hatch (not facade-only, not thin wrapper) | Facade-only forces method sprawl when consumers want new queries; thin wrapper throws away non-trivial upsert/dedup logic. Middle ground keeps sharp edges inside while acknowledging SQLite is a query engine. Schema becomes documented public contract. |
| 3 | Inline `readme_content` on `repos`; no `repo_documents` table | YAGNI. User has no concrete plans to capture non-README docs. Speculative extensibility would commit a table shape to the public contract for no current value. |
| 4 | Hotspot denormalization (B) | Full blob forces `json_extract` into every downstream query and can't use regular indexes. Full normalization is overkill for a capture library. Hoisting description/language/stars/forks/pushed_at/is_fork/is_archived balances query ergonomics against schema surface. |
| 5 | Topics junction table | Classic many-to-many with filter hotspot. Composite PK gives free insert-dedup; separate `topic` index covers filter direction. |
| 6 | Owner stored as `owner.login` string; rest in `metadata` | Matches current `Storage.make_key` behavior. `owner.type` (User vs Organization) deliberately NOT denormalized — user has no query need for it, easy to add later via `json_extract` or migration. |
| 7 | Single-phase `upsert()` + read-only `diff()` split | Current two-phase `check_dedup → upsert_entry` pattern is a JSON-filesystem artifact. SQLite makes atomic upsert natural. `diff()` preserves the valuable pre-fetch API-call optimization as a pure read. |
| 8 | `DuplicateRepoNameError` on `(owner, name)` collision (D1) | Capxure captures current state, not historical revisions. `(owner, name)` must be a single answer, not a list. Loud error on collision > silent ambiguity spreading through every consumer query. |
| 9 | L1: primary language only | Matches what GitHub's `/repos` endpoint already returns. Full-languages would require a second API call per repo, doubling rate-limit burn. Additive migration if ever needed. |
| 10 | `readme_content` / `readme_sha` nullable | `NULL` honestly represents "no README." Empty string would conflate "no README" with "empty README." |
| 11 | `PRAGMA user_version = 1` on day one | Five lines of code now prevents unrecoverable schema drift later. Don't register migrations yet (no v2 to migrate to). |
| 12 | Escape hatch is `storage.connection` property returning live `sqlite3.Connection` | Shortest code, most flexible, matches stdlib idioms. Separate read-only `query()` method would be a paper-thin safety barrier. |
| 13 | Default DB path `{resolved_data_dir}/capxure.db` via `platformdirs` | Consistent with the path-resolution principle established in the previous session for library vs. consumer code. The prompt's `./capxure.db` violates it. |
| 14 | M3: discard 16 existing repos (do not migrate) | Data is not precious — test/demo snapshots, stale anyway. Migration script would be ~100 lines used once. User preserves `data/metadata.json` and `data/readmes/` on disk as manual test fixtures for the future CLI; library ignores them. |
| 15 | Remove `_storage_lock` from processor | SQLite's WAL + single-writer serialization replaces the asyncio lock that was protecting concurrent JSON rewrites. Real simplification. |
| 16 | `UNCHANGED` compares only `readme_sha`, `pushed_at`, and rename state | Full metadata comparison would almost never match (GitHub mutates counts constantly). `pushed_at` is the canonical "has code changed?" signal. |
