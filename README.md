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

## Usage

`capxure` is a pure library — there is no CLI or console script. Your consumer code is responsible for obtaining a GitHub personal-access token (e.g., via `python-dotenv`, your shell environment, or a secrets manager) and passing it to `GitHubClient`.

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

The `data/metadata.json` and `data/readmes/` directories at the repo root are historical fixtures from the pre-SQLite era. The library no longer reads them.
