# capxure

Python library for capturing GitHub repository metadata and README files to a local SQLite database.

## Install

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development with the test suite:

```
pip install -e '.[dev]'
pytest
```

## CLI

Installing capxure adds a `cap` console script. GitHub commands live under `cap git`:

```
export GITHUB_TOKEN=ghp_...                 # or GH_TOKEN
cap git owner/repo                          # capture by shorthand
cap git https://github.com/owner/repo       # or by full URL
cap git capture owner/repo                  # explicit form (alias of the above)
cap git owner/repo --data-dir ~/caps        # override storage location
cap git stars                               # bulk-capture your starred repos
```

Progress events print to stderr (`info: Fetching metadata…`, `success: owner/repo: captured successfully`). stdout is reserved for read commands (`ls`) that produce structured output.

Exit codes: `0` success (including dedup-skip), `1` library-reported failure or missing token, `2` usage error, `3` malformed target, `130` Ctrl-C.

GitHub commands live under `cap git`; quick-capture notes live under `cap note`:

```
cap note "fleeting thought"                          # quickest path; smart-dispatch to add
cap note "great quote" -s "book:Atomic Habits" -L "p.42" -k quote
echo "from a pipe" | cap note add -a "via stdin"
cap note ls                                          # cards on a TTY, TSV when piped
cap note ls --format plain                           # force TSV (7 fields per note)
```

### Listing captured repos

```
cap git ls                         # pretty table on a TTY, TSV when piped
cap git ls -s stars -r             # least-starred first
cap git ls -t ml -t nlp -l 25      # repos tagged ml OR nlp, top 25
cap git ls topics                  # topic counts, descending
cap git ls --format plain          # force TSV (9 fields per repo)
```

Plain output is tab-separated and intended for scripts; the description field has tabs/newlines collapsed to spaces so downstream `awk` / `cut` / `fzf` parse cleanly. Use `CAPXURE_DATA_DIR` to target a non-default db.

Exit codes for `ls`: `0` success (including empty results), `2` usage error, `130` Ctrl-C.

## Library usage

For programmatic use, import directly. Your consumer code is responsible for obtaining a GitHub personal-access token (e.g., via `python-dotenv`, your shell environment, or a secrets manager) and passing it to `GitHubClient`.

```python
import asyncio
import os

from capxure import Database, GitHubClient, process_repo, Severity


async def main() -> None:
    def log(message: str, severity: Severity) -> None:
        print(f"[{severity}] {message}")

    with Database() as db:
        async with GitHubClient(os.environ["GITHUB_TOKEN"]) as gh:
            await process_repo(
                "https://github.com/owner/repo",
                github=gh,
                repos=db.repos,
                on_status=log,
            )


asyncio.run(main())
```

The `notes` domain works the same way:

```python
from capxure import Database

with Database() as db:
    note = db.notes.add("fleeting thought", source="twitter", kind_hint="quote")
    for n in db.notes.list_notes():
        print(n.id, n.content)
```

## Database

Capxure persists captured repos to a single SQLite database. The default location resolves in this order:

1. `$CAPXURE_DATA_DIR` environment variable (if set and non-empty)
2. `platformdirs.user_data_dir("capxure")` — e.g. `~/.local/share/capxure/` on Linux, `~/Library/Application Support/capxure/` on macOS

The database file inside that directory is `capxure.db`. Override the full path with `Database(db_path=Path(...))`.

WAL mode is enabled, so while a connection is open you'll see `capxure.db-wal` and `capxure.db-shm` sidecar files next to the database. These are cleaned up on a normal close and do not need to be backed up separately.

### Schema (public contract)

The schema is a documented public contract — you may run arbitrary SQL against it via the `db.connection` escape hatch.

- Table `repos` — one row per captured GitHub repo. Includes denormalized columns for common query hotspots (`language`, `stars`, `forks`, `pushed_at`, `is_fork`, `is_archived`), an inline `readme_content` column (nullable; `NULL` means "no README"), and the full GitHub API response preserved as JSON in `metadata`.
- Table `repo_topics` — junction table for many-to-many topics. Composite primary key `(repo_id, topic)` provides insert-dedup; a secondary index on `topic` supports `WHERE topic = ?` filtering.
- Table `notes` — append-only quick-capture inbox. `content` is the only required column; `annotation`, `source`, `source_locator`, and `kind_hint` are optional free-form strings; `captured_at` is set by SQLite default. No indexes — minimal `cap note ls` doesn't need them yet.

The full DDL lives in `src/capxure/db.py` under `_SCHEMA_SQL`.

Existing capxure databases at schema version 1 auto-upgrade to version 2 on next open (the upgrade adds the `notes` table; no data is touched).

### Python API

`Database` owns the connection and schema; `RepoStore` (accessed via `db.repos`) owns repo queries. This split keeps each unit small and lets future capture domains (e.g. notes) drop in alongside without touching repo code.

```python
from capxure import Database, UpsertOutcome

with Database() as db:
    outcome = db.repos.upsert(github_metadata_dict, readme_content)
    # outcome is one of: NEW, UPDATED, RENAMED, UNCHANGED, LOCAL_IS_NEWER

    repo = db.repos.get_repo("sindresorhus", "awesome-nodejs")
    if repo is not None:
        print(repo.stars, repo.topics)

    # Default order is last_synced_at DESC; pass sort=/reverse=/topics=/limit=
    # to customize. Zero-arg call still returns every row.
    all_repos = db.repos.list_repos()
```

`Database.notes` returns a `NoteStore` for the catch-all notes inbox; `Database.repos` returns a `RepoStore` for git captures. Both share `db.connection` so a single transaction can span both domains via the SQL escape hatch.

The `db.connection` property exposes the underlying `sqlite3.Connection` as an escape hatch for ad-hoc SQL:

```python
with Database() as db:
    for row in db.connection.execute(
        "SELECT full_name, stars FROM repos WHERE language = ? ORDER BY stars DESC",
        ("Python",),
    ):
        print(row["full_name"], row["stars"])
```

## Changelog

### 0.5.0

- New domain: `capxure.note` for low-friction quick-capture inbox. Add via `cap note "<text>"` (smart-dispatches to `cap note add`) with optional `-a/--annotation`, `-s/--source`, `-L/--loc`, `-k/--kind` flags. Stdin pipe supported when no positional given. List with `cap note ls` (pretty cards on TTY, TSV when piped).
- Python: `Database.notes` returns a `NoteStore` with `add(...)`, `list_notes()`, `count_notes()`. New `Note` dataclass.
- Schema bumped to v2 (added `notes` table, no indexes). Existing v1 databases auto-upgrade on next open. Forward-incompatible dbs still raise `UnsupportedSchemaError`.
- Fix: `Database.__exit__` now commits pending writes (and rolls back on exception) before closing the connection — required for cross-connection visibility within the same process.

### 0.4.0

**Breaking:** CLI and Python API both reorganized around per-domain subpackages to make room for additional capture surfaces.

- CLI: GitHub commands move under `cap git`. `cap owner/repo`, `cap ls`, `cap stars`, and `cap capture` no longer work — use `cap git owner/repo`, `cap git ls`, `cap git stars`, `cap git capture` instead. `cap` with no arguments prints usage and exits 2.
- Python API: `Storage` is removed. Use `Database` (connection + schema lifecycle) plus `db.repos` (a `RepoStore` instance for repo queries). `process_repo` now takes `repos: RepoStore` instead of `storage: Storage`.
- Module reorganization: `capxure.github` → `capxure.git.client`; `capxure.processor` → `capxure.git.processor`. The top-level `capxure` package re-exports everything previously available there (minus `Storage`), so `from capxure import GitHubClient` etc. keeps working.
- Schema is unchanged. Existing databases on disk continue to work without migration.

### 0.3.0

- New `cap` console script (installed via `[project.scripts]`). First subcommand: `cap <target>` captures a repo. Targets accept full URLs or bare `owner/repo` shorthand.
- `capxure.github.parse_github_url` regex broadened so bare `owner/repo` also parses (the `github.com/` prefix is now optional). No behavior change for inputs the old regex accepted.
- New public module `capxure.cli` exposing `main()` and `build_parser()`. Not re-exported from the top-level package — users of the library directly should keep importing from `capxure` as before.

### 0.2.0

**Breaking:** Storage layer rewritten from JSON-files-on-disk to SQLite.

- `DeduplicationResult` enum removed. Replaced by `UpsertOutcome` with the values `NEW`, `UPDATED`, `RENAMED`, `UNCHANGED`, `LOCAL_IS_NEWER`.
- `Storage` constructor parameter renamed from `data_dir` to `db_path`. The new default resolves to `{CAPXURE_DATA_DIR or platformdirs.user_data_dir("capxure")}/capxure.db`.
- Old `Storage` methods removed: `load_metadata`, `save_metadata`, `check_dedup`, `save_readme`, `upsert_entry`, `find_key_by_id`, `ensure_directories`, `make_key`.
- New `Storage` methods: `upsert`, `diff`, `get_repo`, `get_repo_by_github_id`, `list_repos`, `count_repos`, `get_metadata_json`, and the `connection` property (raw SQL escape hatch).
- New types exported: `Repo` (frozen dataclass), `UpsertOutcome`, `DuplicateRepoNameError`, `UnsupportedSchemaError`.
- The `processor.process_repo` signature is unchanged, but `ProcessResult.outcome` is now `UpsertOutcome | None` instead of `DeduplicationResult | None`.
- Data previously captured at `data/metadata.json` + `data/readmes/*.md` is **not auto-migrated**. Re-capture via `process_repo` or hand-load via `storage.upsert`.
