# capxure Library-Polish Pass — Design

Date: 2026-04-21
Status: Approved
Builds on: `2026-04-21-strip-to-core-library-design.md`

## Goal

Resolve the issues surfaced by the final code review of the strip-to-core-library work. Fix the one important latent footgun (package-relative `DEFAULT_DATA_DIR`), clean up the minor residue (stale TUI docstrings, redundant exception clause, `.env`-prescriptive error message, hardcoded User-Agent), and add a `py.typed` marker so downstream consumers' type-checkers honor capxure's annotations. Record a durable principle in this document so the future CLI build-out and any downstream integrations (Cortex) inherit the right conventions for path resolution.

## Non-Goals

- Building the CLI. Still deferred.
- SQL migration of existing `data/metadata.json`. Still deferred.
- Adding a test suite. Still out of scope, matching the preceding strip pass.
- Refactoring internal module boundaries. The three core modules (`github`, `storage`, `processor`) keep their current shape.
- Changing the public API surface exposed by `__init__.py`. No re-exports added or removed.

## Principles

The following principle applies to all capxure code — this library today, the CLI tomorrow, and any integration that ships as part of the capxure project.

> **Path resolution for library vs. consumer code**
>
> The core library never resolves paths relative to `__file__` for user data or
> config. Any path that a user might reasonably want to override must resolve via,
> in order:
>
> 1. Explicit argument passed by the consumer (e.g., `Storage(data_dir=...)`)
> 2. Environment variable (`$CAPXURE_DATA_DIR`, etc.)
> 3. `platformdirs.user_data_dir("capxure")` for data, `user_config_dir` for config
>
> The `__file__`-relative pattern is a footgun: it works for editable installs and
> breaks silently on wheel installs. It must not appear in the core library or in
> any future CLI/TUI consumer that ships alongside it.
>
> For consumers loading `.env` files (future CLI, future TUI, Cortex integration):
> `.env` resolution is the consumer's responsibility, not the library's. Consumers
> should resolve `.env` via python-dotenv's default search (CWD walk) or an explicit
> path — never via `__file__`-relative paths anchored inside the installed package.

The changes in this spec are the first code-level application of this principle. Future specs should cite it rather than re-deriving it.

## Changes

### 1. `DEFAULT_DATA_DIR` redesign — `src/capxure/storage.py`

Remove the current module-level constant:

```python
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
```

Replace it with a private helper function that resolves the default lazily per the principle above:

```python
def _resolve_default_data_dir() -> Path:
    """Resolve the default data directory per the project principle.

    Order:
      1. $CAPXURE_DATA_DIR environment variable (if set and non-empty)
      2. platformdirs.user_data_dir("capxure")
      3. RuntimeError if neither yields a usable path
    """
    env = os.environ.get("CAPXURE_DATA_DIR", "").strip()
    if env:
        return Path(env)
    from platformdirs import user_data_dir
    resolved = user_data_dir("capxure")
    if resolved:
        return Path(resolved)
    raise RuntimeError(
        "Cannot resolve default data dir: set $CAPXURE_DATA_DIR "
        "or ensure platformdirs is working"
    )
```

Update `Storage.__init__` signature to accept `Path | None` and resolve the default at call time:

```python
class Storage:
    """Manages metadata.json and readme files on disk."""

    def __init__(self, data_dir: Path | None = None) -> None:
        """Initialize storage at a data directory.

        Args:
            data_dir: Where to store metadata.json and readmes/.
                Library consumers (e.g., future CLI, Cortex integration,
                custom scripts) should pass an explicit Path — this is the
                clean contract for embedding capxure in other tools.

                The no-argument default is intended for CLI/TUI end-users
                and resolves via, in order:
                  1. $CAPXURE_DATA_DIR environment variable
                  2. platformdirs.user_data_dir("capxure")
                  3. RuntimeError if neither is usable
        """
        if data_dir is None:
            data_dir = _resolve_default_data_dir()
        self._data_dir = data_dir
        self._metadata_path = data_dir / "metadata.json"
        self._readmes_dir = data_dir / "readmes"
```

Import additions at top of `storage.py`:

```python
import os
```

(`pathlib.Path` and `json` are already imported. `platformdirs` is imported lazily inside `_resolve_default_data_dir` to keep cold import time for `from capxure import Storage` minimal when consumers pass an explicit path.)

Add `platformdirs>=4` to `pyproject.toml` dependencies, so `dependencies` becomes:

```toml
dependencies = [
    "httpx>=0.27.0",
    "platformdirs>=4",
]
```

Runtime footprint is now `httpx` + `platformdirs`.

### 2. User-Agent via `importlib.metadata` — `src/capxure/github.py`

Replace the hardcoded User-Agent with a version read from installed package metadata at module load:

At the top of `github.py`, add:

```python
import importlib.metadata

try:
    _VERSION = importlib.metadata.version("capxure")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "unknown"

_USER_AGENT = f"capxure/{_VERSION}"
```

In `GitHubClient.__aenter__`, change the headers dict to use `_USER_AGENT`:

```python
self._client = httpx.AsyncClient(
    headers={
        "Authorization": f"token {self._token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": _USER_AGENT,
    },
    timeout=30.0,
)
```

The `importlib.metadata` call executes once at module import time. Any consumer-level `pip install capxure` (editable or wheel) registers the package metadata, so the `PackageNotFoundError` branch is reached only in exotic vendored/bare-sys.path setups.

Rationale for not introducing a circular import with `from capxure import __version__`: `__init__.py` imports `GitHubClient` from `github.py`, so `github.py` must not import from the `capxure` namespace. `importlib.metadata` reads from the installed distribution's metadata (derived from `pyproject.toml`), sidestepping the cycle entirely.

### 3. Stale TUI docstring cleanup

Three module docstrings still reference the TUI.

**`src/capxure/github.py:1`** — change:

```python
"""GitHub API client. No TUI dependencies."""
```

to:

```python
"""GitHub API client."""
```

**`src/capxure/storage.py:1`** — change:

```python
"""Local storage for metadata and READMEs. No TUI dependencies."""
```

to:

```python
"""Local filesystem persistence for repo metadata and READMEs."""
```

**`src/capxure/processor.py:1-6`** — the current six-line docstring explicitly mentions the TUI. Replace with:

```python
"""Core orchestrator.

Coordinates GitHub API calls and local storage operations.
Accepts a StatusCallback so consumers can surface progress.
"""
```

### 4. Redundant exception clause — `src/capxure/processor.py:89`

Change:

```python
except (GitHubError, Exception) as exc:
```

to:

```python
except Exception as exc:
```

`GitHubError` subclasses `Exception`, so including both in the tuple is redundant. The two specific `GitHubError` subclasses (`NotFoundError`, `RateLimitExceededError`, `AuthenticationError`) are already caught by earlier `except` clauses in the same `try`, so the broad `except Exception` catches only the "unexpected" cases it's meant to.

### 5. `.env`-prescriptive error message — `src/capxure/processor.py:84`

Change:

```python
msg = "Authentication failed — check GITHUB_TOKEN in .env"
```

to:

```python
msg = "Authentication failed — invalid or missing GITHUB_TOKEN"
```

The library does not know or care whether the consumer obtains the token from `.env`, the shell environment, a secrets manager, or a literal string. The error message should state the fact without prescribing a mechanism.

### 6. `py.typed` marker

Create an empty file at `src/capxure/py.typed`. No content. Hatchling automatically includes files inside the declared package (`src/capxure`) in the built wheel, so no change to `pyproject.toml` is required for packaging. The presence of this marker tells downstream type-checkers (mypy, pyright, pylance) that capxure ships with inline type annotations they should honor per PEP 561.

### 7. `README.md` follow-up paragraph

The current README ends with:

> Captured metadata goes to `data/metadata.json`; README files go to `data/readmes/{owner}--{repo}.md`. Pass a custom `Path` to `Storage(data_dir=...)` to change the location.

Replace with:

> Library consumers should pass an explicit `data_dir` to `Storage(data_dir=Path(...))` — that's the clean contract for embedding capxure in other tools. When `Storage()` is called with no argument, the default resolves via, in order: `$CAPXURE_DATA_DIR`, then `platformdirs.user_data_dir("capxure")` (e.g. `~/.local/share/capxure` on Linux, `~/Library/Application Support/capxure` on macOS). Inside that directory, metadata goes to `metadata.json` and READMEs to `readmes/{owner}--{repo}.md`.

No other part of the README changes.

## Final Shape (post-polish)

```
src/capxure/
├── __init__.py     # (unchanged from strip)
├── github.py       # + importlib.metadata-driven User-Agent
├── processor.py    # + cleaned docstring, exception tuple, error message
├── storage.py      # + _resolve_default_data_dir, Path|None signature
└── py.typed        # (new, empty)
```

`pyproject.toml` dependencies: `httpx>=0.27.0`, `platformdirs>=4`.

## Verification

After implementation, these must all hold:

1. `python -c "from capxure import Storage; import os; os.environ['CAPXURE_DATA_DIR'] = '/tmp/capxure-test'; s = Storage(); assert str(s._data_dir) == '/tmp/capxure-test'; print('env override ok')"` prints `env override ok`.

2. Without `$CAPXURE_DATA_DIR` set: `python -c "import os; os.environ.pop('CAPXURE_DATA_DIR', None); from capxure import Storage; from platformdirs import user_data_dir; s = Storage(); assert str(s._data_dir) == user_data_dir('capxure'); print('platformdirs default ok')"` prints `platformdirs default ok`.

3. Explicit path still wins: `python -c "from pathlib import Path; from capxure import Storage; s = Storage(data_dir=Path('/tmp/explicit')); assert str(s._data_dir) == '/tmp/explicit'; print('explicit override ok')"` prints `explicit override ok`.

4. User-Agent picks up the installed version: `python -c "from capxure.github import _USER_AGENT; assert _USER_AGENT == 'capxure/0.1.0', _USER_AGENT; print('ua ok')"` prints `ua ok`.

5. Grep: `grep -rn "No TUI dependencies\|parent\.parent\.parent" src/` returns no matches.

6. Grep: `grep -n "check GITHUB_TOKEN in .env\|except (GitHubError, Exception)" src/capxure/processor.py` returns no matches.

7. Public API smoke test still passes: `python -c "from capxure import GitHubClient, Storage, process_repo, DeduplicationResult, Severity, ProcessResult, parse_github_url, GitHubError, AuthenticationError, NotFoundError, RateLimitExceededError, RateLimitInfo, StatusCallback, __version__; print(__version__)"` prints `0.1.0`.

8. `pip show capxure | grep -E '^(Name|Version|Requires):'` shows `Requires: httpx, platformdirs`.

9. `src/capxure/py.typed` exists and is empty (`test ! -s src/capxure/py.typed && echo "ok"` prints `ok`).

10. `data/`, `.env`, `.env.example`, `.gitignore` on-disk contents unchanged by this pass.

11. README paragraph at the end matches the new text from §7.

## Out of Scope / Future Work

- CLI on top of the library.
- SQL migration of existing `data/metadata.json`.
- Test suite.
- `__version__` single-source-of-truth refactor (e.g., removing the duplicated `0.1.0` in `pyproject.toml` and `__init__.py` by switching `pyproject.toml` to a dynamic version). This polish pass's `importlib.metadata` approach in `github.py` already treats `pyproject.toml` as the source for the User-Agent; unifying `__init__.py`'s `__version__` is a separate cleanup, worth doing before the first release but not now.
- Re-export cleanup (e.g., whether `StatusCallback` should remain in `__all__`). No change here.
