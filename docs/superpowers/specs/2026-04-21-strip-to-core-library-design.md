# Strip capxure to Core Library — Design

Date: 2026-04-21
Status: Approved

## Goal

Remove all non-library code and packaging from the capxure repository, leaving a pure importable Python library with no executable surface. The TUI experiment was an architectural misstep; removing it creates a clean foundation for a future CLI (out of scope here) to be built on top of the library.

## Non-Goals

- Building a CLI. A CLI will be designed and built in a separate pass once the library is clean.
- Migrating captured data to a SQL database. This is a future task; the existing `data/` directory is preserved for that migration.
- Refactoring `github.py` or `storage.py`. Both are already TUI-free and well-factored.
- Adding tests. Scope is strictly stripping and a minimal public-API polish.

## What Remains (the Core Library)

Three modules under `src/capxure/`:

- **`github.py`** — Async `GitHubClient` using `httpx`, URL parser (`parse_github_url`), `RateLimitInfo` dataclass, and the exception hierarchy (`GitHubError`, `AuthenticationError`, `NotFoundError`, `RateLimitExceededError`). Untouched by this change.
- **`storage.py`** — `Storage` class managing `metadata.json` + `readmes/` with atomic writes and deduplication; `DeduplicationResult` enum. Untouched by this change.
- **`processor.py`** — `process_repo(url, *, github, storage, on_status)` orchestrator plus `Severity` enum, `StatusCallback` protocol, and `ProcessResult` dataclass. Edited only to remove the vestigial `fetch_rate_limit` wrapper.

After this change, consumers import from the top-level `capxure` package and instantiate these components themselves.

## Changes

### Deletions

Permanently removed from the repo:

- `src/capxure/app.py` — Textual TUI application
- `src/capxure/app.tcss` — Textual CSS stylesheet
- `src/capxure/__main__.py` — TUI launcher (`python -m capxure`)
- `src/capxure/__pycache__/` — bytecode cache; regenerates on next import

No other files are deleted. `data/`, `.env`, `.env.example`, and `.gitignore` are preserved on disk.

### `pyproject.toml` rewrite

- Update `description` from `"TUI app that captures GitHub repo metadata and READMEs"` to `"Library for capturing GitHub repo metadata and READMEs locally"`.
- Remove `textual>=0.80.0` from `dependencies`.
- Remove `python-dotenv>=1.0.0` from `dependencies`.
- Keep `httpx>=0.27.0`.
- Remove the entire `[project.scripts]` section (deletes the `capxure` console-script entry point).
- `requires-python`, `[build-system]`, and `[tool.hatch.build.targets.wheel]` are unchanged.

Resulting runtime-dependency footprint: `httpx` only.

### `README.md` rewrite

Short, library-usage-focused. Covers: install via `pip install -e .`, note that consumers provide their own `GITHUB_TOKEN` (e.g., via their own `python-dotenv` or shell env), and a minimal import + call example. No TUI references.

### `src/capxure/__init__.py` — public API surface

Replace the current three-line file (which only defines `__version__`) with explicit re-exports so the entire public surface is reachable as `from capxure import X`:

- From `github`: `GitHubClient`, `RateLimitInfo`, `parse_github_url`, `GitHubError`, `AuthenticationError`, `NotFoundError`, `RateLimitExceededError`
- From `storage`: `Storage`, `DeduplicationResult`
- From `processor`: `process_repo`, `Severity`, `StatusCallback`, `ProcessResult`

Keep `__version__ = "0.1.0"`. Define `__all__` listing every re-exported name plus `__version__`.

### `src/capxure/processor.py` edit

Remove the `fetch_rate_limit(github)` module-level function at the bottom of the file. It is a one-line passthrough to `github.fetch_rate_limit()` that existed only for symmetry with how the TUI invoked it via a worker; consumers can call the method on `GitHubClient` directly. No other change to `processor.py`.

## Library Shape (post-strip)

```
src/capxure/
├── __init__.py    # public API re-exports + __version__
├── github.py      # GitHubClient + URL parsing + exceptions
├── storage.py     # Storage + DeduplicationResult
└── processor.py   # process_repo orchestrator + Severity/StatusCallback/ProcessResult
```

Typical consumer usage:

```python
import os
from dotenv import load_dotenv  # consumer's own dep, not capxure's
from capxure import GitHubClient, Storage, process_repo, Severity

load_dotenv()

async def main():
    storage = Storage()
    storage.ensure_directories()

    async with GitHubClient(os.environ["GITHUB_TOKEN"]) as gh:
        def log(msg: str, sev: Severity) -> None:
            print(f"[{sev}] {msg}")

        await process_repo(
            "https://github.com/owner/repo",
            github=gh,
            storage=storage,
            on_status=log,
        )
```

## Verification

After implementation, these must all hold:

1. `python -c "from capxure import GitHubClient, Storage, process_repo, DeduplicationResult, Severity, ProcessResult, parse_github_url, GitHubError, AuthenticationError, NotFoundError, RateLimitExceededError, RateLimitInfo, StatusCallback"` succeeds with no error.
2. `pip install -e .` in a fresh venv installs only `httpx` (and its transitive deps) as runtime dependencies — no `textual`, no `python-dotenv`.
3. `grep -r textual src/ pyproject.toml` returns no matches.
4. `grep -r dotenv src/ pyproject.toml` returns no matches.
5. `grep -r "app\.py\|app\.tcss\|__main__" src/` returns no matches.
6. Running `capxure` as a shell command fails (the script no longer exists).
7. `data/metadata.json` and `data/readmes/` are unchanged byte-for-byte compared to before the strip.

## Out of Scope / Future Work

- CLI built on top of this library (next project).
- SQL migration of `data/metadata.json` contents.
- Test suite.
- Rename or version bump of the package.
