# Per-Domain Refactor — Make Room for Future Capture Surfaces

## Motivation

Capxure today is GitHub-only. Every module — `storage.py`, `github.py`,
`processor.py`, the CLI files — assumes the only thing being captured is a
GitHub repository. A second capture surface (`cap note`, a free-form inbox)
is coming next, but the current shape doesn't have a clean place to put it:

- `Storage` is one 514-line class doing both connection/schema lifecycle and
  repo-specific queries; adding ~150–200 lines of note queries would push it
  past 700.
- `cli/__init__.py` uses a top-level smart-dispatch hack (first positional
  containing `/` → capture). That rule only works while GitHub is the only
  domain; it breaks the moment a second domain exists.
- The flat CLI layout (`cli/capture.py`, `cli/list_.py`, `cli/stars.py`) has
  no namespace for "this command operates on repos vs notes."

This spec restructures the codebase so a second domain drops in next to
GitHub without touching GitHub code. **No new behavior, no new tables, no
schema change.** A separate spec will add `cap note` afterwards.

## Goals

- Split the storage layer along its existing seam (lifecycle vs. queries).
- Reorganize CLI under per-domain subcommand groups.
- Replace top-level smart-dispatch with explicit domain routing.
- Preserve all existing behavior, schema, exit codes, output formats, flags.
- Land in one PR; all existing tests still pass after import updates.

## Non-Goals

- The `notes` table, `NoteStore`, `cli/note/*` — separate spec.
- Promotion / `promoted_to_*` plumbing — not on the radar.
- Schema changes of any kind.
- Public-API backward compatibility shims (`Storage` is removed outright;
  capxure is CLI-only with no known external library users).
- A custom error message for the removed `cap owner/repo` shortcut. Users
  see argparse's default "invalid choice"; we can add a hint later if it
  trips someone.

## User-Visible Breaking Changes

| Before                    | After                          |
| ------------------------- | ------------------------------ |
| `cap owner/repo`          | `cap git owner/repo`           |
| `cap capture owner/repo`  | `cap git capture owner/repo`   |
| `cap ls`                  | `cap git ls`                   |
| `cap stars`               | `cap git stars`                |

Python API (CLI-only project, but flagged for completeness):

| Before                            | After                              |
| --------------------------------- | ---------------------------------- |
| `from capxure import Storage`     | `from capxure import Database`     |
| `Storage().upsert(...)`           | `Database().repos.upsert(...)`     |

## Architecture

### File layout (after)

```
src/capxure/
  __init__.py                 # re-exports Database, RepoStore, GitHubClient,
                              # process_repo, Repo, UpsertOutcome,
                              # DuplicateRepoNameError, UnsupportedSchemaError,
                              # AuthenticationError, NotFoundError,
                              # RateLimitExceededError, RateLimitInfo,
                              # ProcessResult, Severity, StatusCallback,
                              # parse_github_url, __version__
  db.py                       # NEW: Database class
  git/
    __init__.py
    client.py                 # was src/capxure/github.py
    processor.py              # was src/capxure/processor.py
    store.py                  # NEW: RepoStore + Repo + UpsertOutcome
                              #      + DuplicateRepoNameError
  cli/
    __init__.py               # top-level dispatcher (only routes to git)
    __main__.py               # unchanged entry point
    git/
      __init__.py             # `cap git` dispatcher with smart-dispatch on `/`
      capture.py              # was cli/capture.py
      ls.py                   # was cli/list_.py
      stars.py                # was cli/stars.py
```

### Files removed

- `src/capxure/storage.py`
- `src/capxure/github.py`
- `src/capxure/processor.py`
- `src/capxure/cli/capture.py`
- `src/capxure/cli/list_.py`
- `src/capxure/cli/stars.py`

### `db.py` — `Database` class

Owns connection lifecycle and schema only.

```python
class Database:
    def __init__(self, db_path: Path | None = None) -> None: ...
    def close(self) -> None: ...
    def __enter__(self) -> "Database": ...
    def __exit__(self, *exc_info) -> None: ...

    @property
    def connection(self) -> sqlite3.Connection: ...

    @property
    def repos(self) -> "RepoStore":
        """Lazy accessor — constructs a RepoStore over self.connection on first use."""
        ...

    def _ensure_schema(self) -> None: ...
```

`_resolve_default_db_path` stays as a module-level free function in `db.py`
(matching today's shape in `storage.py`). It's not on the class.

`UnsupportedSchemaError` lives in `db.py` (it's a schema-version concern,
not repo-specific).

The `db.repos` property caches a single `RepoStore` instance per `Database`
so callers see a stable reference; second access returns the same store.

### `git/store.py` — `RepoStore`

```python
class RepoStore:
    def __init__(self, connection: sqlite3.Connection) -> None: ...

    # write path
    def upsert(self, metadata, readme) -> tuple[Repo, UpsertOutcome]: ...
    def diff(self, metadata) -> UpsertOutcome: ...

    # read path
    def get_repo(self, owner: str, name: str) -> Repo | None: ...
    def get_repo_by_github_id(self, github_id: int) -> Repo | None: ...
    def list_repos(self, ...) -> list[Repo]: ...
    def count_repos(self) -> int: ...
    def existing_urls(self, urls: Iterable[str]) -> set[str]: ...
    def list_topic_counts(self, ...) -> list[tuple[str, int]]: ...
    def get_metadata_json(self, owner: str, name: str) -> dict | None: ...

    # internals
    def _classify(...) -> UpsertOutcome: ...
    def _fetch_internal_by_github_id(...) -> sqlite3.Row | None: ...
    def _insert_repo(...) -> Repo: ...
    def _update_repo(...) -> Repo: ...
    def _replace_topics(...) -> None: ...
    def _row_to_repo(row) -> Repo: ...
```

Co-located in `git/store.py`: the `Repo` dataclass, `UpsertOutcome` StrEnum,
`DuplicateRepoNameError`, and the `_sha256_hex` helper. They're all
repo-specific and have no callers outside the GitHub domain.

### Wiring example

```python
with Database() as db:
    repos = db.repos
    repos.upsert(metadata, readme)
```

Tests that want to inject a connection directly can construct a `RepoStore`
without going through `Database`:

```python
store = RepoStore(connection)
```

### Top-level CLI dispatcher (`cli/__init__.py`)

Argparse with one subparser, `git`. No smart dispatch.

```
cap                    → prints usage, exit 2
cap git ...            → routes to cli/git/__init__.py
cap unknown            → argparse "invalid choice", exit 2
cap owner/repo         → argparse "invalid choice", exit 2
```

The previous smart-dispatch logic (first positional contains `/` → capture)
is deleted entirely.

### Git-level dispatcher (`cli/git/__init__.py`)

Argparse with subparsers `capture`, `ls`, `stars`. Smart-dispatch lives here.

```python
def main(argv: list[str]) -> int:
    # smart-dispatch: if first positional under `cap git` contains `/`,
    # splice in `capture` so argparse sees an explicit subcommand.
    if argv and "/" in argv[0] and not argv[0].startswith("-"):
        argv = ["capture", *argv]
    args = parser.parse_args(argv)
    return args.handler(args)
```

Each of `capture.py`, `ls.py`, `stars.py` exposes a `register(subparsers)`
function that adds its subparser and wires its handler. The git-level
dispatcher imports each and calls `register`. This mirrors today's pattern
— only the registration site moves.

Resolved cases:

```
cap git                        → exit 2, prints `cap git` usage
cap git owner/repo             → smart-dispatch → capture handler
cap git capture owner/repo     → explicit form, same handler
cap git ls [flags]             → ls handler
cap git stars [flags]          → stars handler
cap git --help                 → lists capture, ls, stars
```

The explicit `cap git capture owner/repo` form stays as a working alias of
`cap git owner/repo`. It's free (argparse already wires the `capture`
subparser) and discoverable via `cap git --help`, whereas the smart-dispatch
form isn't.

## Data Flow

Unchanged. The flow is still:

```
CLI handler → Database (lifecycle) → RepoStore (queries) → SQLite
                  ↘ GitHubClient (async, via processor.process_repo)
```

The only structural change is that `RepoStore` is constructed from the
`Database` (or directly from a connection in tests) instead of `Storage`
being one class doing both jobs.

### `process_repo` signature change

Today: `process_repo(client: GitHubClient, storage: Storage, ...)`.
After: `process_repo(client: GitHubClient, repos: RepoStore, ...)`.

`process_repo` only ever needed the repo-query half of the old `Storage`,
so it takes a `RepoStore` directly. Callers (the CLI capture handler and
the stars handler) construct a `Database` and pass `db.repos`.

### CLI handler ownership of `Database`

CLI handlers in `cli/git/{capture,ls,stars}.py` construct their own
`Database` (via `with Database() as db: ...`) the same way today's handlers
construct their own `Storage`. No change in ownership; just the type name
and the `db.repos` indirection.

## Error Handling

All error types and exit codes preserved verbatim:

- `AuthenticationError` (401) — exit 2 from CLI capture path.
- `NotFoundError` (404) — exit 2.
- `RateLimitExceededError` (403 with rate-limit headers) — exit 2.
- `DuplicateRepoNameError` — propagates from `RepoStore.upsert`.
- `UnsupportedSchemaError` — propagates from `Database._ensure_schema`.
- argparse failures — exit 2 (argparse default).

The CLI dispatcher does no error handling beyond what argparse provides;
domain-specific handling stays in the existing handler modules.

## Testing

### Test layout (after)

```
tests/
  conftest.py                  # fixtures relocated; imports updated
  test_database.py             # NEW: lifecycle, schema, _ensure_schema,
                               # context manager — extracted from test_storage.py
  test_imports.py              # updated for new public-API names
  git/
    __init__.py
    test_client.py             # was test_github.py
    test_processor.py          # was test_processor.py
    test_store.py              # was the query half of test_storage.py
  cli/
    __init__.py
    test_dispatcher.py         # NEW: top-level routing tests
    git/
      __init__.py
      test_dispatch.py         # NEW: cap-git smart-dispatch tests
      test_capture.py          # was test_cli.py
      test_ls.py               # was test_cli_list.py
      test_ls_smoke.py         # was test_cli_list_smoke.py
      test_stars.py            # was test_cli_stars.py
      test_stars_smoke.py      # was test_cli_stars_smoke.py
```

Test bodies don't change. Imports change. The split of `test_storage.py`
follows the source split: lifecycle/schema → `test_database.py`, query
tests → `git/test_store.py`.

### New tests

`tests/cli/test_dispatcher.py`:

- `cap` (no args) → exit 2, usage on stderr.
- `cap unknown` → exit 2, argparse "invalid choice".
- `cap owner/repo` → exit 2, argparse "invalid choice". **This pins the
  breaking change** so a future regression that re-enables top-level smart
  dispatch fails loudly.
- `cap git --help` → exit 0, output mentions `capture`, `ls`, `stars`.

`tests/cli/git/test_dispatch.py`:

- `cap git owner/repo` → routes to capture handler with `owner/repo` arg.
- `cap git capture owner/repo` → routes to capture handler (explicit form).
- `cap git ls` → routes to ls handler.
- `cap git stars` → routes to stars handler.
- `cap git` (no args) → exit 2, prints `cap git` usage.

### Verification gates (pre-merge)

1. **Full test suite passes**: `pytest` clean, no new skipped tests.
2. **Schema parity**: `sqlite3 <fresh-db> .schema` byte-identical before
   vs. after the refactor. The diff is the load-bearing check that no
   on-disk shape changed by accident. Capture both outputs, paste the
   (empty) diff into the PR description. The current `schema_version`
   value stays untouched — no migration code runs against existing DBs
   because there's nothing to migrate.
3. **Live-DB compatibility**: open today's user DB with the new code and
   confirm `cap git ls` returns the same rows as today's `cap ls`. Either
   a manual check or a fixture that ships an existing-format DB.

## Public API (after)

`src/capxure/__init__.py` re-exports:

- From `db`: `Database`, `UnsupportedSchemaError`.
- From `git.store`: `RepoStore`, `Repo`, `UpsertOutcome`,
  `DuplicateRepoNameError`.
- From `git.client`: `GitHubClient`, `GitHubError`, `AuthenticationError`,
  `NotFoundError`, `RateLimitExceededError`, `RateLimitInfo`,
  `parse_github_url`.
- From `git.processor`: `process_repo`, `ProcessResult`, `Severity`,
  `StatusCallback`.
- `__version__`.

The name `Storage` is removed. No shim.

## Out of Scope (Explicit)

- `notes` table or any non-GitHub capture domain.
- `cap note` CLI surface.
- Promotion of captures into typed entities.
- Custom error message for the removed `cap owner/repo` shortcut.
- Schema migrations.
- Renaming the project or the `cap` binary.
