# capxure Library-Polish Pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the six cleanups approved in the library-polish spec: redesigned data-dir resolution, dynamic User-Agent, docstring/exception/error-message tidying, `py.typed` marker, README update.

**Architecture:** Six targeted feature commits on top of the current `main` baseline (HEAD `6d137e3`). Each task is commit-scoped, each verified by grep/python-c smoke tests, no tests added. The pre-existing uncommitted brainstorm/plan artifacts (spec commit `0cc1cf1`) form the starting point.

**Tech Stack:** Python 3.11+, `httpx`, `platformdirs` (new), `hatchling` (build).

**Spec:** `docs/superpowers/specs/2026-04-21-library-polish-design.md`

---

## Pre-flight Context

Starting point: `main` at commit `0cc1cf1` ("Add design spec for library-polish pass"). Working tree is clean.

No test suite exists (matches the project's preceding strip pass). Every task verifies the change via deterministic commands (grep, `python -c`, file-existence checks) rather than a test runner.

Critical ordering: **Task 1 must run before Task 2** — the storage refactor depends on `platformdirs` being installed in the active venv, which Task 1's `pip install -e .` delivers.

---

### Task 1: Add `platformdirs` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Confirm baseline state**

Run: `git log --oneline -1`
Expected: `0cc1cf1 Add design spec for library-polish pass` (or later if commits were added after the spec).

Run: `cat pyproject.toml | head -20`
Expected: current file has `dependencies = [\n    "httpx>=0.27.0",\n]`.

- [ ] **Step 2: Edit `pyproject.toml` to add `platformdirs>=4`**

Change the `dependencies` block FROM:

```toml
dependencies = [
    "httpx>=0.27.0",
]
```

TO:

```toml
dependencies = [
    "httpx>=0.27.0",
    "platformdirs>=4",
]
```

Leave everything else in `pyproject.toml` unchanged.

- [ ] **Step 3: Verify the edit**

Run: `grep -A2 "^dependencies" pyproject.toml`
Expected output:
```
dependencies = [
    "httpx>=0.27.0",
    "platformdirs>=4",
]
```

- [ ] **Step 4: Reinstall so the active venv picks up the new dep**

Run: `pip install -e .`
Expected: pip reports installing or already-satisfied for `httpx` AND installs `platformdirs` (it may not be present yet).

- [ ] **Step 5: Verify platformdirs is importable**

Run: `python -c "import platformdirs; print(platformdirs.user_data_dir('capxure'))"`
Expected: prints a path like `/home/<user>/.local/share/capxure` on Linux, `/Users/<user>/Library/Application Support/capxure` on macOS. Any non-error output is success.

Run: `pip show capxure | grep -E '^Requires:'`
Expected: `Requires: httpx, platformdirs` (order may vary).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
Add platformdirs dependency

Required by the upcoming DEFAULT_DATA_DIR redesign in storage.py,
which resolves the default data directory via
platformdirs.user_data_dir when no explicit path and no
$CAPXURE_DATA_DIR env var are provided.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Verify commit**

Run: `git show --stat HEAD`
Expected: exactly one file changed (`pyproject.toml`), 1 insertion and 0 deletions (or 2 insertions / 1 deletion depending on how git counts trailing comma context).

Run: `git status --short`
Expected: empty (clean tree).

---

### Task 2: Redesign `DEFAULT_DATA_DIR` in `storage.py`

**Files:**
- Modify: `src/capxure/storage.py`

This task makes four surgical Edits to `storage.py`. Use the Edit tool with `old_string`/`new_string` for each. Do NOT use Write — the file has unrelated methods that must remain untouched.

- [ ] **Step 1: Confirm current file state**

Run: `grep -n "^\"\"\"\\|^import\\|DEFAULT_DATA_DIR\\|def __init__" src/capxure/storage.py`
Expected output includes (line numbers may vary slightly):
```
1:"""Local storage for metadata and READMEs. No TUI dependencies."""
5:import json
20:DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
26:    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR) -> None:
```

- [ ] **Step 2: Edit 1 — module docstring**

Use the Edit tool with:

- `old_string`: `"""Local storage for metadata and READMEs. No TUI dependencies."""`
- `new_string`: `"""Local filesystem persistence for repo metadata and READMEs."""`

- [ ] **Step 3: Edit 2 — add `os` import**

Use the Edit tool with:

- `old_string`:
  ```
  import json
  from enum import StrEnum
  ```
- `new_string`:
  ```
  import json
  import os
  from enum import StrEnum
  ```

- [ ] **Step 4: Edit 3 — replace `DEFAULT_DATA_DIR` constant with `_resolve_default_data_dir` helper**

Use the Edit tool with:

- `old_string`: `DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"`
- `new_string`:
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

- [ ] **Step 5: Edit 4 — update `Storage.__init__` signature, docstring, and body**

Use the Edit tool with:

- `old_string`:
  ```python
      def __init__(self, data_dir: Path = DEFAULT_DATA_DIR) -> None:
          self._data_dir = data_dir
          self._metadata_path = data_dir / "metadata.json"
          self._readmes_dir = data_dir / "readmes"
  ```
- `new_string`:
  ```python
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

- [ ] **Step 6: Verify the edits produced the expected state**

Run: `grep -n "DEFAULT_DATA_DIR\\|parent\\.parent\\.parent\\|No TUI dependencies" src/capxure/storage.py || echo "clean"`
Expected: `clean`

Run: `grep -n "_resolve_default_data_dir\\|^import os" src/capxure/storage.py`
Expected: shows `import os` near the top and `_resolve_default_data_dir` at both the definition and the call site inside `__init__`.

Run: `python -c "import ast; ast.parse(open('src/capxure/storage.py').read()); print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 7: Verify behavior — explicit path wins**

Run:
```bash
python -c "
from pathlib import Path
from capxure import Storage
s = Storage(data_dir=Path('/tmp/capxure-explicit'))
assert str(s._data_dir) == '/tmp/capxure-explicit', s._data_dir
print('explicit path ok')
"
```
Expected: `explicit path ok`.

- [ ] **Step 8: Verify behavior — env var override**

Run:
```bash
python -c "
import os
os.environ['CAPXURE_DATA_DIR'] = '/tmp/capxure-env'
from capxure import Storage
s = Storage()
assert str(s._data_dir) == '/tmp/capxure-env', s._data_dir
print('env override ok')
"
```
Expected: `env override ok`.

- [ ] **Step 9: Verify behavior — platformdirs fallback**

Run:
```bash
python -c "
import os
os.environ.pop('CAPXURE_DATA_DIR', None)
from capxure import Storage
from platformdirs import user_data_dir
s = Storage()
assert str(s._data_dir) == user_data_dir('capxure'), s._data_dir
print('platformdirs default ok')
"
```
Expected: `platformdirs default ok`.

- [ ] **Step 10: Verify the broader import surface still works**

Run:
```bash
python -c "from capxure import GitHubClient, Storage, process_repo, DeduplicationResult, Severity, ProcessResult, parse_github_url, GitHubError, AuthenticationError, NotFoundError, RateLimitExceededError, RateLimitInfo, StatusCallback, __version__; print(__version__)"
```
Expected: `0.1.0`.

- [ ] **Step 11: Commit**

```bash
git add src/capxure/storage.py
git commit -m "$(cat <<'EOF'
Redesign Storage default data_dir resolution

Remove the package-relative DEFAULT_DATA_DIR constant (which
broke silently on wheel installs) and introduce
_resolve_default_data_dir(): env var → platformdirs → error.
Storage.__init__ now takes Path | None; an explicit path remains
the clean contract for library consumers. The docstring
documents the library-vs-consumer distinction.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 12: Verify commit**

Run: `git show --stat HEAD`
Expected: exactly one file changed (`src/capxure/storage.py`).

Run: `git status --short`
Expected: empty.

---

### Task 3: Dynamic User-Agent + docstring fix in `github.py`

**Files:**
- Modify: `src/capxure/github.py`

- [ ] **Step 1: Confirm current state**

Run: `grep -n "^\"\"\"\\|^import\\|User-Agent\\|User_Agent\\|_USER_AGENT\\|importlib" src/capxure/github.py`
Expected output includes (line numbers may vary):
```
1:"""GitHub API client. No TUI dependencies."""
8:import httpx
82:                "User-Agent": "capxure/0.1",
```
(No `importlib` import, no `_USER_AGENT` constant yet.)

- [ ] **Step 2: Edit 1 — module docstring**

Use the Edit tool with:

- `old_string`: `"""GitHub API client. No TUI dependencies."""`
- `new_string`: `"""GitHub API client."""`

- [ ] **Step 3: Edit 2 — add `importlib.metadata` import + `_VERSION`/`_USER_AGENT` constants**

The current top of `github.py` looks like:

```python
"""GitHub API client."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx


# ── Exceptions ────────────────────────────────────────────────
```

Use the Edit tool with:

- `old_string`:
  ```
  from __future__ import annotations

  import re
  from dataclasses import dataclass

  import httpx


  # ── Exceptions ────────────────────────────────────────────────
  ```
- `new_string`:
  ```
  from __future__ import annotations

  import importlib.metadata
  import re
  from dataclasses import dataclass

  import httpx


  try:
      _VERSION = importlib.metadata.version("capxure")
  except importlib.metadata.PackageNotFoundError:
      _VERSION = "unknown"

  _USER_AGENT = f"capxure/{_VERSION}"


  # ── Exceptions ────────────────────────────────────────────────
  ```

- [ ] **Step 4: Edit 3 — use `_USER_AGENT` in `__aenter__`**

Use the Edit tool with:

- `old_string`: `"User-Agent": "capxure/0.1",`
- `new_string`: `"User-Agent": _USER_AGENT,`

- [ ] **Step 5: Verify edits**

Run: `grep -n "No TUI dependencies\\|capxure/0\\.1\"" src/capxure/github.py || echo "clean"`
Expected: `clean`.

Run: `grep -n "_USER_AGENT\\|importlib\\.metadata" src/capxure/github.py`
Expected: shows `import importlib.metadata`, the `_VERSION`/`_USER_AGENT` block, and the use in `__aenter__`.

Run: `python -c "import ast; ast.parse(open('src/capxure/github.py').read()); print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 6: Verify `_USER_AGENT` resolves to the expected value**

Run:
```bash
python -c "from capxure.github import _USER_AGENT; assert _USER_AGENT == 'capxure/0.1.0', _USER_AGENT; print('ua ok')"
```
Expected: `ua ok`.

- [ ] **Step 7: Verify the public import surface still works**

Run:
```bash
python -c "from capxure import GitHubClient, RateLimitInfo, parse_github_url; print('ok')"
```
Expected: `ok`.

- [ ] **Step 8: Commit**

```bash
git add src/capxure/github.py
git commit -m "$(cat <<'EOF'
Derive User-Agent from installed package version

Replace hardcoded 'capxure/0.1' with a module-level _USER_AGENT
read via importlib.metadata.version('capxure') at import time.
pyproject.toml becomes the single source of truth for the
version string in the User-Agent. Also drop the stale 'No TUI
dependencies' tag from the module docstring.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 9: Verify commit**

Run: `git show --stat HEAD`
Expected: exactly one file changed (`src/capxure/github.py`).

Run: `git status --short`
Expected: empty.

---

### Task 4: Clean up `processor.py` — docstring, exception clause, error message

**Files:**
- Modify: `src/capxure/processor.py`

- [ ] **Step 1: Confirm current state**

Run: `sed -n '1,10p' src/capxure/processor.py`
Expected: the current six-line module docstring that mentions "TUI".

Run: `grep -n "check GITHUB_TOKEN in .env\\|except (GitHubError, Exception)" src/capxure/processor.py`
Expected: exactly two lines match (one for each string).

- [ ] **Step 2: Edit 1 — module docstring**

The current top of `processor.py` is:

```
"""Core orchestrator. No TUI dependencies.

Coordinates GitHub API calls and local storage operations.
Accepts a callback for status reporting so the TUI (or any other
consumer) can display progress.
"""
```

Use the Edit tool with:

- `old_string`:
  ```
  """Core orchestrator. No TUI dependencies.

  Coordinates GitHub API calls and local storage operations.
  Accepts a callback for status reporting so the TUI (or any other
  consumer) can display progress.
  """
  ```
- `new_string`:
  ```
  """Core orchestrator.

  Coordinates GitHub API calls and local storage operations.
  Accepts a StatusCallback so consumers can surface progress.
  """
  ```

- [ ] **Step 3: Edit 2 — `.env`-prescriptive error message**

Use the Edit tool with:

- `old_string`: `msg = "Authentication failed — check GITHUB_TOKEN in .env"`
- `new_string`: `msg = "Authentication failed — invalid or missing GITHUB_TOKEN"`

- [ ] **Step 4: Edit 3 — redundant exception tuple**

Use the Edit tool with:

- `old_string`: `except (GitHubError, Exception) as exc:`
- `new_string`: `except Exception as exc:`

- [ ] **Step 5: Verify edits**

Run: `grep -n "No TUI\\|check GITHUB_TOKEN in \\.env\\|except (GitHubError, Exception)" src/capxure/processor.py || echo "clean"`
Expected: `clean`.

Run: `sed -n '1,5p' src/capxure/processor.py`
Expected: the new four-line module docstring starting with `"""Core orchestrator.`.

Run: `python -c "import ast; ast.parse(open('src/capxure/processor.py').read()); print('syntax ok')"`
Expected: `syntax ok`.

- [ ] **Step 6: Verify module still imports cleanly**

Run:
```bash
python -c "from capxure.processor import process_repo, Severity, ProcessResult, StatusCallback; print('ok')"
```
Expected: `ok`.

- [ ] **Step 7: Commit**

```bash
git add src/capxure/processor.py
git commit -m "$(cat <<'EOF'
Clean up processor.py — docstring, except clause, error message

Three small cleanups: (1) drop 'No TUI dependencies' from the
module docstring and remove the TUI reference in the body,
(2) collapse 'except (GitHubError, Exception)' to 'except
Exception' since GitHubError subclasses Exception, (3) change
the auth error from prescribing '.env' to stating the fact
(the library does not know how consumers source the token).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Verify commit**

Run: `git show --stat HEAD`
Expected: exactly one file changed (`src/capxure/processor.py`).

Run: `git status --short`
Expected: empty.

---

### Task 5: Add `py.typed` marker

**Files:**
- Create: `src/capxure/py.typed`

- [ ] **Step 1: Verify file does not yet exist**

Run: `[ ! -e src/capxure/py.typed ] && echo "absent"`
Expected: `absent`.

- [ ] **Step 2: Create the empty marker**

Use the Write tool (content is empty string):

- `file_path`: `/home/max/workspace/capxure/src/capxure/py.typed`
- `content`: `` (empty string, zero bytes)

- [ ] **Step 3: Verify it exists and is empty**

Run: `test -f src/capxure/py.typed && test ! -s src/capxure/py.typed && echo "ok"`
Expected: `ok`.

Run: `wc -c src/capxure/py.typed`
Expected: `0 src/capxure/py.typed`.

- [ ] **Step 4: Commit**

```bash
git add src/capxure/py.typed
git commit -m "$(cat <<'EOF'
Add py.typed marker (PEP 561)

Signals to downstream type-checkers (mypy, pyright) that the
capxure package ships inline type annotations that should be
honored rather than treated as untyped. Hatchling automatically
includes the marker in the built wheel because it sits inside
the declared package directory.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Verify commit**

Run: `git show --stat HEAD`
Expected: exactly one new file, zero insertions.

Run: `git status --short`
Expected: empty.

---

### Task 6: Update README paragraph on data_dir

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Confirm current final paragraph**

Run: `tail -3 README.md`
Expected: the final sentence is `Captured metadata goes to `data/metadata.json`; README files go to `data/readmes/{owner}--{repo}.md`. Pass a custom `Path` to `Storage(data_dir=...)` to change the location.` followed by an empty trailing line.

- [ ] **Step 2: Replace the final paragraph**

Use the Edit tool with:

- `old_string`: `Captured metadata goes to `data/metadata.json`; README files go to `data/readmes/{owner}--{repo}.md`. Pass a custom `Path` to `Storage(data_dir=...)` to change the location.`
- `new_string`: `Library consumers should pass an explicit `data_dir` to `Storage(data_dir=Path(...))` — that's the clean contract for embedding capxure in other tools. When `Storage()` is called with no argument, the default resolves via, in order: `$CAPXURE_DATA_DIR`, then `platformdirs.user_data_dir("capxure")` (e.g. `~/.local/share/capxure` on Linux, `~/Library/Application Support/capxure` on macOS). Inside that directory, metadata goes to `metadata.json` and READMEs to `readmes/{owner}--{repo}.md`.`

- [ ] **Step 3: Verify the edit**

Run: `grep -c "platformdirs.user_data_dir" README.md`
Expected: `1`.

Run: `grep -c "Pass a custom .Path. to .Storage" README.md`
Expected: `0` (the old sentence is gone).

Run: `grep -n "Library consumers should pass" README.md`
Expected: one match near the end of the file.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Update README data_dir paragraph to document new defaults

Explains the library-consumer vs. end-user distinction:
explicit data_dir is the clean contract; the no-argument
default resolves via \$CAPXURE_DATA_DIR, then
platformdirs.user_data_dir('capxure').

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Verify commit**

Run: `git show --stat HEAD`
Expected: exactly one file changed (`README.md`).

Run: `git status --short`
Expected: empty.

---

### Task 7: Final end-to-end verification

**Files:** none modified — runs the spec's 11-point verification checklist.

- [ ] **Step 1: Env override check**

Run:
```bash
python -c "
import os
os.environ['CAPXURE_DATA_DIR'] = '/tmp/capxure-test'
from capxure import Storage
s = Storage()
assert str(s._data_dir) == '/tmp/capxure-test', s._data_dir
print('env override ok')
"
```
Expected: `env override ok`.

- [ ] **Step 2: Platformdirs default check**

Run:
```bash
python -c "
import os
os.environ.pop('CAPXURE_DATA_DIR', None)
from capxure import Storage
from platformdirs import user_data_dir
s = Storage()
assert str(s._data_dir) == user_data_dir('capxure'), s._data_dir
print('platformdirs default ok')
"
```
Expected: `platformdirs default ok`.

- [ ] **Step 3: Explicit path override check**

Run:
```bash
python -c "
from pathlib import Path
from capxure import Storage
s = Storage(data_dir=Path('/tmp/explicit'))
assert str(s._data_dir) == '/tmp/explicit', s._data_dir
print('explicit override ok')
"
```
Expected: `explicit override ok`.

- [ ] **Step 4: User-Agent check**

Run:
```bash
python -c "from capxure.github import _USER_AGENT; assert _USER_AGENT == 'capxure/0.1.0', _USER_AGENT; print('ua ok')"
```
Expected: `ua ok`.

- [ ] **Step 5: Grep — no TUI residue, no package-relative paths**

Run: `grep -rn "No TUI dependencies\\|parent\\.parent\\.parent" src/ || echo "clean"`
Expected: `clean`.

- [ ] **Step 6: Grep — no prescriptive .env message, no redundant exception tuple**

Run: `grep -n "check GITHUB_TOKEN in \\.env\\|except (GitHubError, Exception)" src/capxure/processor.py || echo "clean"`
Expected: `clean`.

- [ ] **Step 7: Public-API smoke test still works**

Run:
```bash
python -c "from capxure import GitHubClient, Storage, process_repo, DeduplicationResult, Severity, ProcessResult, parse_github_url, GitHubError, AuthenticationError, NotFoundError, RateLimitExceededError, RateLimitInfo, StatusCallback, __version__; print(__version__)"
```
Expected: `0.1.0`.

- [ ] **Step 8: pip show reports both runtime deps**

Run: `pip show capxure | grep -E '^(Name|Version|Requires):'`
Expected: three lines showing `Name: capxure`, `Version: 0.1.0`, `Requires: httpx, platformdirs` (order of the two deps may vary).

- [ ] **Step 9: `py.typed` marker present and empty**

Run: `test -f src/capxure/py.typed && test ! -s src/capxure/py.typed && echo "ok"`
Expected: `ok`.

- [ ] **Step 10: Preserved-on-disk items unchanged**

Run:
```bash
ls data/ && [ -f data/metadata.json ] && [ -d data/readmes ] && [ -f .env ] && [ -f .env.example ] && [ -f .gitignore ] && echo "preserved"
```
Expected: directory listing followed by `preserved`.

- [ ] **Step 11: README paragraph updated**

Run: `grep -c "platformdirs.user_data_dir" README.md`
Expected: `1`.

Run: `grep -c "Library consumers should pass an explicit" README.md`
Expected: `1`.

- [ ] **Step 12: Final commit-log sanity**

Run: `git log --oneline -8`
Expected: HEAD shows (most recent first):
1. Update README data_dir paragraph to document new defaults
2. Add py.typed marker (PEP 561)
3. Clean up processor.py — docstring, except clause, error message
4. Derive User-Agent from installed package version
5. Redesign Storage default data_dir resolution
6. Add platformdirs dependency
7. Add design spec for library-polish pass
(plus earlier strip commits)

Run: `git status --short`
Expected: empty.

If any step fails, fix before reporting the task complete.

---

## Out of Scope Reminders

- No CLI. Still deferred to a separate project.
- No SQL migration. Still deferred.
- No test suite. Still out of scope, matching the strip pass.
- No `__version__` single-source-of-truth refactor in `pyproject.toml` / `__init__.py` beyond the User-Agent unification. Separate cleanup.
- No changes to `github.py`'s API surface, `processor.py`'s `process_repo` signature, `storage.py`'s method set, or `__init__.py`'s `__all__` list.
- No changes to `data/`, `.env`, `.env.example`, `.gitignore`.
