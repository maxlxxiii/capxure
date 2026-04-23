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

Installing capxure adds a `cap` console script for ad-hoc captures:

```
export GITHUB_TOKEN=ghp_...        # or GH_TOKEN
cap owner/repo                     # capture by shorthand
cap https://github.com/owner/repo  # or by full URL
cap owner/repo --data-dir ~/caps   # override storage location
```

Progress events print to stderr (`info: Fetching metadata…`, `success: owner/repo: captured successfully`). stdout is reserved for future subcommands (`list`, `show`) that will produce structured output.

Exit codes: `0` success (including dedup-skip), `1` library-reported failure or missing token, `2` usage error, `3` malformed target, `130` Ctrl-C.

## Library usage

For programmatic use, import directly. Your consumer code is responsible for obtaining a GitHub personal-access token (e.g., via `python-dotenv`, your shell environment, or a secrets manager) and passing it to `GitHubClient`.

```python
import asyncio
import os

from capxure import GitHubClient, Storage, process_repo, Severity


async def main() -> None:
    def log(message: str, severity: Severity) -> None:
        print(f"[{severity}] {message}")

    with Storage() as storage:
        async with GitHubClient(os.environ["GITHUB_TOKEN"]) as gh:
            await process_repo(
                "https://github.com/owner/repo",
                github=gh,
                storage=storage,
                on_status=log,
            )


asyncio.run(main())
```

## Storage

Capxure persists captured repos to a single SQLite database. The default location resolves in this order:

1. `$CAPXURE_DATA_DIR` environment variable (if set and non-empty)
2. `platformdirs.user_data_dir("capxure")` — e.g. `~/.local/share/capxure/` on Linux, `~/Library/Application Support/capxure/` on macOS

The database file inside that directory is `capxure.db`. Override the full path with `Storage(db_path=Path(...))`.

WAL mode is enabled, so while a connection is open you'll see `capxure.db-wal` and `capxure.db-shm` sidecar files next to the database. These are cleaned up on a normal close and do not need to be backed up separately.

### Schema (public contract)

The schema is a documented public contract — you may run arbitrary SQL against it via the `storage.connection` escape hatch.

- Table `repos` — one row per captured GitHub repo. Includes denormalized columns for common query hotspots (`language`, `stars`, `forks`, `pushed_at`, `is_fork`, `is_archived`), an inline `readme_content` column (nullable; `NULL` means "no README"), and the full GitHub API response preserved as JSON in `metadata`.
- Table `repo_topics` — junction table for many-to-many topics. Composite primary key `(repo_id, topic)` provides insert-dedup; a secondary index on `topic` supports `WHERE topic = ?` filtering.

The full DDL lives in `src/capxure/storage.py` under `_SCHEMA_SQL`.

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

The `storage.connection` property exposes the underlying `sqlite3.Connection` as an escape hatch for ad-hoc SQL:

```python
with Storage() as storage:
    for row in storage.connection.execute(
        "SELECT full_name, stars FROM repos WHERE language = ? ORDER BY stars DESC",
        ("Python",),
    ):
        print(row["full_name"], row["stars"])
```

## Changelog

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
