# Strip capxure to Core Library — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all TUI and non-library code from the capxure repo, leaving a pure importable Python library with a clean public API and no executable entry points.

**Architecture:** Delete the Textual UI surface (`app.py`, `app.tcss`, `__main__.py`). Rewrite `pyproject.toml` to drop `textual` and `python-dotenv` dependencies and remove the `capxure` console script. Replace `__init__.py` with explicit public-API re-exports. Remove one vestigial wrapper from `processor.py`. Rewrite `README.md` for library usage. Preserve `data/`, `.env`, `.env.example` on disk.

**Tech Stack:** Python 3.11+, `httpx` (only remaining runtime dep), `hatchling` (build backend). No test framework in scope — verification is import/grep/install-based.

**Spec:** `docs/superpowers/specs/2026-04-21-strip-to-core-library-design.md`

---

## Pre-flight Context

The repo's working tree already has two unrelated deletions that must be committed before the strip begins, so the strip lands on a clean baseline:

- `src/capxure/parser.py` — deleted from disk, uncommitted
- `src/capxure/queue.py` — deleted from disk, uncommitted

The working tree also shows `src/capxure/app.py` as modified. Those modifications are irrelevant because `app.py` is being deleted in Task 2.

There is no test suite. Every task below ends with explicit verification commands (grep, Python import check, or `pip install`) before the commit step.

---

### Task 1: Commit pre-existing working-tree deletions

**Files:**
- Stage deletions: `src/capxure/parser.py`, `src/capxure/queue.py`

- [ ] **Step 1: Confirm current working-tree state**

Run: `git status --short`
Expected output contains these lines (plus possibly others):
```
 M src/capxure/app.py
 D src/capxure/parser.py
 D src/capxure/queue.py
```

- [ ] **Step 2: Stage only the two deletions**

Run:
```bash
git add src/capxure/parser.py src/capxure/queue.py
```

- [ ] **Step 3: Verify staging**

Run: `git status --short`
Expected: `D  src/capxure/parser.py` and `D  src/capxure/queue.py` now appear in the staged (left) column. `app.py` remains unstaged.

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Remove unused parser and queue modules

Clears working-tree deletions from a prior experiment so the
core-library strip lands on a clean baseline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Verify commit**

Run: `git log --oneline -1`
Expected: shows the new commit at HEAD.

---

### Task 2: Delete TUI source files

**Files:**
- Delete: `src/capxure/app.py`
- Delete: `src/capxure/app.tcss`
- Delete: `src/capxure/__main__.py`
- Delete: `src/capxure/__pycache__/` (not tracked; just removes bytecode)

- [ ] **Step 1: Delete the TUI files**

Run:
```bash
git rm src/capxure/app.py src/capxure/app.tcss src/capxure/__main__.py
rm -rf src/capxure/__pycache__
```

- [ ] **Step 2: Verify src/capxure now contains only the core modules**

Run: `ls src/capxure/`
Expected output:
```
__init__.py
github.py
processor.py
storage.py
```

- [ ] **Step 3: Verify no TUI references remain in src/**

Run: `grep -r -l textual src/ || echo "no matches"`
Expected: `no matches`

Run: `grep -rn "^from textual\|^import textual\|CapxureApp\|app\.tcss" src/ || echo "no matches"`
Expected: `no matches`

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Remove TUI layer (app.py, app.tcss, __main__.py)

Strips the Textual UI entirely. Core library modules (github,
storage, processor) are preserved and remain TUI-free per their
own docstrings.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Rewrite pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Replace the entire file contents**

Overwrite `pyproject.toml` with exactly this:

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
]

[tool.hatch.build.targets.wheel]
packages = ["src/capxure"]
```

- [ ] **Step 2: Verify the dropped deps and script entry are gone**

Run: `grep -E "textual|dotenv|project.scripts" pyproject.toml || echo "clean"`
Expected: `clean`

Run: `grep -c httpx pyproject.toml`
Expected: `1`

- [ ] **Step 3: Reinstall the package so metadata refreshes in the active venv**

Run: `pip install -e .`
Expected: installs `capxure` in editable mode with only `httpx` (and its transitive deps like `anyio`, `certifi`, `h11`, `httpcore`, `idna`, `sniffio`) as runtime deps. No `textual`, no `python-dotenv` in the install log.

- [ ] **Step 4: Verify the `capxure` console script is gone**

Run: `which capxure 2>/dev/null; echo "exit=$?"`
Expected: the `which` line is empty; `exit=1` (meaning not found).

Note: if this is run in a venv where the old script was previously installed, it may still resolve until the reinstall above propagates. If `which capxure` still returns a path, run `pip install -e . --force-reinstall` and re-check.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
Drop textual and python-dotenv deps; remove capxure console script

Runtime footprint is now httpx only. The library has no
executable entry point — consumers import from the capxure
package directly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Rewrite `src/capxure/__init__.py` with public API

**Files:**
- Modify: `src/capxure/__init__.py`

- [ ] **Step 1: Replace the entire file contents**

Overwrite `src/capxure/__init__.py` with exactly this:

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
from capxure.storage import DeduplicationResult, Storage

__version__ = "0.1.0"

__all__ = [
    "AuthenticationError",
    "DeduplicationResult",
    "GitHubClient",
    "GitHubError",
    "NotFoundError",
    "ProcessResult",
    "RateLimitExceededError",
    "RateLimitInfo",
    "Severity",
    "StatusCallback",
    "Storage",
    "__version__",
    "parse_github_url",
    "process_repo",
]
```

- [ ] **Step 2: Verify every public symbol imports from the top level**

Run:
```bash
python -c "from capxure import GitHubClient, Storage, process_repo, DeduplicationResult, Severity, ProcessResult, StatusCallback, parse_github_url, GitHubError, AuthenticationError, NotFoundError, RateLimitExceededError, RateLimitInfo, __version__; print(__version__)"
```
Expected output: `0.1.0`

- [ ] **Step 3: Verify `__all__` resolution**

Run:
```bash
python -c "import capxure; names = sorted(capxure.__all__); assert names == sorted(set(names)), 'duplicates'; [getattr(capxure, n) for n in capxure.__all__]; print('ok', len(capxure.__all__))"
```
Expected output: `ok 14`

- [ ] **Step 4: Commit**

```bash
git add src/capxure/__init__.py
git commit -m "$(cat <<'EOF'
Expose public API from capxure top-level package

Re-exports GitHubClient, Storage, process_repo, and friends so
consumers can import them directly from capxure instead of
reaching into submodules. __all__ is defined explicitly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Remove `fetch_rate_limit` wrapper from processor.py

**Files:**
- Modify: `src/capxure/processor.py`

The current `processor.py` has:
1. A `RateLimitInfo` import at the top (used only by the wrapper's return type).
2. A `fetch_rate_limit` function at the bottom that's a one-line passthrough to `github.fetch_rate_limit()`.

Both must go. `RateLimitInfo` stays reachable to consumers because `__init__.py` re-exports it from `capxure.github`.

- [ ] **Step 1: Verify current state of the file**

Run: `grep -n "RateLimitInfo\|fetch_rate_limit" src/capxure/processor.py`
Expected output (line numbers may vary slightly):
```
15:    RateLimitExceededError,
16:    RateLimitInfo,
17:    parse_github_url,
...
127:async def fetch_rate_limit(github: GitHubClient) -> RateLimitInfo:
128:    """Fetch current rate limit info."""
129:    return await github.fetch_rate_limit()
```

- [ ] **Step 2: Remove `RateLimitInfo` from the import block**

In the `from capxure.github import (...)` block at the top of `processor.py`, remove the line `    RateLimitInfo,`. The import block should go from:

```python
from capxure.github import (
    AuthenticationError,
    GitHubClient,
    GitHubError,
    NotFoundError,
    RateLimitExceededError,
    RateLimitInfo,
    parse_github_url,
)
```

to:

```python
from capxure.github import (
    AuthenticationError,
    GitHubClient,
    GitHubError,
    NotFoundError,
    RateLimitExceededError,
    parse_github_url,
)
```

- [ ] **Step 3: Remove the `fetch_rate_limit` function at the bottom**

Delete these three lines (plus the blank line separating them from the previous function) at the end of `processor.py`:

```python
async def fetch_rate_limit(github: GitHubClient) -> RateLimitInfo:
    """Fetch current rate limit info."""
    return await github.fetch_rate_limit()
```

The file should now end with the final `return ProcessResult(...)` line of `process_repo` (followed by exactly one trailing newline).

- [ ] **Step 4: Verify both pieces are gone**

Run: `grep -n "RateLimitInfo\|fetch_rate_limit" src/capxure/processor.py || echo "clean"`
Expected: `clean`

- [ ] **Step 5: Verify the module still imports cleanly**

Run: `python -c "from capxure.processor import process_repo, Severity, ProcessResult, StatusCallback; print('ok')"`
Expected: `ok`

Run: `python -c "from capxure import RateLimitInfo; print(RateLimitInfo.__name__)"`
Expected: `RateLimitInfo` (still reachable via the `github` re-export in `__init__.py`).

- [ ] **Step 6: Commit**

```bash
git add src/capxure/processor.py
git commit -m "$(cat <<'EOF'
Remove vestigial fetch_rate_limit wrapper from processor

The wrapper was a one-line passthrough that only existed for
symmetry with how the TUI invoked it via a worker. Consumers
call GitHubClient.fetch_rate_limit() directly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Rewrite README.md for library usage

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the entire file contents**

Overwrite `README.md` with exactly this:

````markdown
# capxure

Python library for capturing GitHub repository metadata and README files locally.

## Install

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

`capxure` is a pure library — there is no CLI or console script. Your consumer code is responsible for obtaining a GitHub personal-access token (e.g., via `python-dotenv`, your shell environment, or a secrets manager) and passing it to `GitHubClient`.

```python
import asyncio
import os

from capxure import GitHubClient, Storage, process_repo, Severity


async def main() -> None:
    storage = Storage()
    storage.ensure_directories()

    def log(message: str, severity: Severity) -> None:
        print(f"[{severity}] {message}")

    async with GitHubClient(os.environ["GITHUB_TOKEN"]) as gh:
        await process_repo(
            "https://github.com/owner/repo",
            github=gh,
            storage=storage,
            on_status=log,
        )


asyncio.run(main())
```

Captured metadata goes to `data/metadata.json`; README files go to `data/readmes/{owner}--{repo}.md`. Pass a custom `Path` to `Storage(data_dir=...)` to change the location.
````

Note: the outer fence in this spec is ``` ```` ``` (four backticks) so the inner triple-backtick code blocks render correctly. When writing the actual file, use regular triple-backtick fences throughout — there is no outer wrapping fence in the final file.

- [ ] **Step 2: Verify no TUI language remains**

Run: `grep -iE "tui|textual|paste.*url.*press enter" README.md || echo "clean"`
Expected: `clean`

- [ ] **Step 3: Verify the usage example imports from `capxure` top-level**

Run: `grep -c "from capxure import" README.md`
Expected: `1`

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Rewrite README for library usage

Replaces TUI-oriented copy with install instructions and a
minimal async usage example that imports from the capxure
top-level package.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Final end-to-end verification

**Files:** none modified — this is the spec's verification checklist, executed against the completed strip.

- [ ] **Step 1: Public-API import smoke test**

Run:
```bash
python -c "from capxure import GitHubClient, Storage, process_repo, DeduplicationResult, Severity, ProcessResult, parse_github_url, GitHubError, AuthenticationError, NotFoundError, RateLimitExceededError, RateLimitInfo, StatusCallback; print('ok')"
```
Expected: `ok`

- [ ] **Step 2: Runtime deps check**

Run: `pip show capxure | grep -E '^(Name|Version|Requires):'`
Expected:
```
Name: capxure
Version: 0.1.0
Requires: httpx
```
(`Requires:` line shows exactly `httpx` — no `textual`, no `python-dotenv`.)

- [ ] **Step 3: Source-tree clean of textual/dotenv references**

Run: `grep -rn "textual\|dotenv" src/ pyproject.toml || echo "clean"`
Expected: `clean`

- [ ] **Step 4: No executable surface remains**

Run: `find src/capxure -type f -name "*.py" | sort`
Expected:
```
src/capxure/__init__.py
src/capxure/github.py
src/capxure/processor.py
src/capxure/storage.py
```
(A `src/capxure/__pycache__/` directory may exist — that is bytecode cache regenerated by Python when the package is imported during earlier tasks. Ignore it; `.gitignore` already excludes `__pycache__/`.)

Run: `which capxure 2>/dev/null; echo "exit=$?"`
Expected: empty first line, `exit=1`.

- [ ] **Step 5: Captured data unchanged**

`data/` is gitignored (see `.gitignore` line 7) and no task in this plan touches it, so the on-disk state should be identical to before the strip. Sanity-check that the directory and its key contents still exist:

Run: `ls data/`
Expected: lists `awesome-lists`, `metadata.json`, `readmes` (and possibly other pre-existing entries).

Run: `[ -f data/metadata.json ] && echo "metadata present"`
Expected: `metadata present`

Run: `[ -d data/readmes ] && ls data/readmes | head -1 && echo "readmes present"`
Expected: a filename line followed by `readmes present`.

- [ ] **Step 6: Final commit-log sanity check**

Run: `git log --oneline -8`
Expected: the top of the log shows the six commits created by this plan, in order (most recent first):
1. Rewrite README for library usage
2. Remove vestigial fetch_rate_limit wrapper from processor
3. Expose public API from capxure top-level package
4. Drop textual and python-dotenv deps; remove capxure console script
5. Remove TUI layer (app.py, app.tcss, __main__.py)
6. Remove unused parser and queue modules

If any step fails, fix before reporting the task complete.

---

## Out of Scope Reminders

- **Do not** write a CLI. A CLI will be built in a separate future project.
- **Do not** migrate `data/metadata.json` to SQL. Also future work.
- **Do not** add tests. Not in scope.
- **Do not** touch `github.py` or `storage.py`. They are already library-clean.
- **Do not** modify, move, or delete anything in `data/`, `.env`, or `.env.example`.
