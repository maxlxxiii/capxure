# `cap` CLI — capture command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `cap <target>` CLI that captures a GitHub repo's metadata + README via the existing `capxure` library, as specified in `docs/superpowers/specs/2026-04-23-cli-capture-design.md`.

**Architecture:** New package `src/capxure/cli/` with `__init__.py` (parser + dispatch), `__main__.py` (enables `python -m`), and `capture.py` (capture subcommand). Declared as a console script via `[project.scripts] cap = "capxure.cli:main"` in `pyproject.toml`. CLI is a thin consumer of the library's public API.

**Tech Stack:** Python 3.11+, `argparse` (stdlib only — no new runtime deps), `pytest` with `capsys` + `monkeypatch`, `asyncio`.

---

## Reference material

Before starting, skim:

- `docs/superpowers/specs/2026-04-23-cli-capture-design.md` — the spec this plan implements.
- `src/capxure/__init__.py` — the public API surface (lists every symbol the CLI is allowed to touch).
- `src/capxure/processor.py:40-62` — `process_repo` signature and the fact that it **never raises** — it always returns a `ProcessResult`.
- `src/capxure/github.py:76-100` — `GitHubClient.__init__(token: str)` + its `__aenter__` / `__aexit__` (must be used as an async context manager before calling `process_repo`).
- `src/capxure/storage.py:111-160` — `Storage(db_path: Path | None)` constructor. `db_path` is a **file path**, not a directory. Default resolves to `{CAPXURE_DATA_DIR or user_data_dir('capxure')}/capxure.db`.
- `tests/conftest.py` — existing pytest fixture conventions.
- `tests/test_imports.py` — style for smoke tests.

**Key API facts the plan depends on:**

- `process_repo(url, *, github, storage, on_status) -> ProcessResult` — all kwargs keyword-only. Never raises. On failure: `ProcessResult.outcome is None` and `.error` populated.
- `StatusCallback.__call__(self, message: str, severity: Severity) -> None` — message first, severity second.
- `Severity` is a `StrEnum` with values including `INFO`, `SUCCESS`, `ERROR` (lowercase strings).
- `UpsertOutcome` is a `StrEnum` with `NEW`, `UPDATED`, `RENAMED`, `UNCHANGED`, `LOCAL_IS_NEWER`.
- `parse_github_url(url)` raises `ValueError` on malformed input.
- `GitHubClient` must be used via `async with GitHubClient(token) as github:` or `.client` property raises `RuntimeError`.

---

## File structure

Files this plan creates or modifies:

| Path | Kind | Responsibility |
|------|------|----------------|
| `src/capxure/cli/__init__.py` | Create | Defines `main()`, builds the top-level argparse parser, dispatches to subcommands. |
| `src/capxure/cli/__main__.py` | Create | Three-line shim enabling `python -m capxure.cli`. |
| `src/capxure/cli/capture.py` | Create | `register(subparsers)`, `command(args)`, `_print_status`, `_resolve_token`, `_resolve_db_path`, `_exit_code_for`. |
| `tests/test_cli.py` | Create | All CLI tests (parser, handler with stubbed `process_repo`, smoke test). |
| `pyproject.toml` | Modify | Add `[project.scripts] cap = "capxure.cli:main"`. |

Nothing in `src/capxure/__init__.py` changes — the CLI is not part of the library's public import surface.

---

## Task 1: Scaffold CLI package and entry point

**Files:**
- Create: `src/capxure/cli/__init__.py`
- Create: `src/capxure/cli/__main__.py`
- Modify: `pyproject.toml` (add `[project.scripts]` table, after `[project.optional-dependencies]`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py` with:

```python
"""CLI tests. Kept layered: parser-level, handler-level, and an end-to-end smoke test."""
from __future__ import annotations

import subprocess
import sys


def test_cli_runs_as_module_with_no_args_exits_2():
    """`python -m capxure.cli` with no args → argparse complains about missing target."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_cli_runs_as_module_with_no_args_exits_2 -v`
Expected: FAIL. Either `ModuleNotFoundError: No module named 'capxure.cli'` in stderr, or the subprocess returns a non-2 code because nothing exists yet.

- [ ] **Step 3: Create the package with a stub `main`**

Create `src/capxure/cli/__init__.py`:

```python
"""Command-line interface for capxure. Thin consumer of the public library API."""
from __future__ import annotations


def main() -> int:
    """Entry point for the `cap` console script.

    Temporary stub — real parser arrives in Task 2. Returning 2 matches argparse's
    behavior for missing-required-arg, which is the right terminal state for "no args".
    """
    return 2
```

Create `src/capxure/cli/__main__.py`:

```python
"""Enables `python -m capxure.cli`."""
import sys

from capxure.cli import main

sys.exit(main())
```

- [ ] **Step 4: Add the console-script entry to `pyproject.toml`**

Open `pyproject.toml`. Find the `[project.optional-dependencies]` table (currently at lines 14-17). Immediately after its block (before the `[tool.hatch.build.targets.wheel]` table), insert:

```toml
[project.scripts]
cap = "capxure.cli:main"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_cli.py::test_cli_runs_as_module_with_no_args_exits_2 -v`
Expected: PASS.

- [ ] **Step 6: Verify the `cap` entrypoint installs**

Run: `pip install -e . --quiet && cap` (this invokes the entry point directly; should exit 2 silently).
Expected: exit code 2, no traceback. (`echo $?` after to confirm if desired.)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/capxure/cli/__init__.py src/capxure/cli/__main__.py tests/test_cli.py
git commit -m "Scaffold cap CLI package skeleton and console-script entry"
```

---

## Task 2: Top-level argparse parser and dispatch rule

**Files:**
- Modify: `src/capxure/cli/__init__.py`
- Test: `tests/test_cli.py`

Dispatch rule (from spec §2): if the first positional contains `/`, treat it as a capture target; otherwise let argparse's subparser machinery handle it. For this task, no subcommands are registered yet — the dispatcher just needs the skeleton that Task 3 plugs into.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
import argparse

import pytest

from capxure.cli import build_parser, main


def test_parser_accepts_capture_subcommand_with_target():
    """`cap capture owner/repo` parses cleanly via the capture subparser."""
    parser = build_parser()
    args = parser.parse_args(["capture", "owner/repo"])
    assert args.subcommand == "capture"
    assert args.target == "owner/repo"
    assert args.data_dir is None


def test_parser_accepts_capture_with_data_dir_flag(tmp_path):
    parser = build_parser()
    args = parser.parse_args(["capture", f"--data-dir={tmp_path}", "owner/repo"])
    assert args.data_dir == str(tmp_path)
    assert args.target == "owner/repo"


def test_main_with_no_args_returns_2(capsys):
    """`cap` (no args) prints help to stderr and returns 2."""
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_main_help_flag_exits_zero(capsys):
    """`cap --help` goes through argparse which raises SystemExit(0)."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    # argparse prints usage + description to stdout on --help
    assert "cap" in out.lower()


# Note: `cap owner/repo` dispatch (without the literal "capture" word) is tested
# end-to-end in Task 4, where library calls can be safely mocked.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: all new tests FAIL with `ImportError: cannot import name 'build_parser'` or similar.

- [ ] **Step 3: Replace `src/capxure/cli/__init__.py` with the real parser**

```python
"""Command-line interface for capxure. Thin consumer of the public library API."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level `cap` parser.

    Dispatch rule: the first positional is a capture target (handled by the capture
    subcommand). Future subcommands (list, show) will be registered on the subparsers
    group, and argparse routes to them when the first positional matches a registered
    name. Capture targets always contain `/`; subcommand names never will — so the two
    never collide.
    """
    parser = argparse.ArgumentParser(
        prog="cap",
        description="Capture GitHub repos locally.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # Capture is the default action: `cap owner/repo` invokes it without a keyword.
    # We register it here so --help lists it, and we also wire it as the fallback
    # below when the user types `cap owner/repo` directly.
    from capxure.cli import capture
    capture.register(subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `cap` console script.

    Returns a process exit code. Accepts an `argv` list for testability.
    """
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        parser.print_help(sys.stderr)
        return 2

    # Dispatch rule: if the first positional contains `/`, it's a capture target.
    # Rewrite the argv to prepend the "capture" subcommand name so argparse can
    # route it through the registered handler.
    if "/" in argv[0]:
        argv = ["capture", *argv]

    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)
```

Note: this file now imports `capxure.cli.capture`, which doesn't exist yet. Task 3 creates it. To make this task's tests pass in isolation, create a minimal stub `src/capxure/cli/capture.py` **as part of this task**:

```python
"""Capture subcommand — stub replaced in Task 3."""
from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("capture", help="Capture a GitHub repo.")
    p.add_argument("target", help="GitHub URL or owner/repo.")
    p.add_argument("--data-dir", default=None, help="Directory to store capxure.db.")
    p.set_defaults(handler=lambda args: 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/capxure/cli/__init__.py src/capxure/cli/capture.py tests/test_cli.py
git commit -m "Wire cap top-level parser and subcommand dispatch"
```

---

## Task 3: Capture handler — token, db-path, status, exit-code helpers

This task adds the four pure helpers that the capture orchestration needs. Each has a focused test. The handler still returns 0 without running anything — Task 4 wires it all together.

**Files:**
- Modify: `src/capxure/cli/capture.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
from pathlib import Path

from capxure import ProcessResult, Severity, UpsertOutcome
from capxure.cli.capture import (
    _exit_code_for,
    _print_status,
    _resolve_db_path,
    _resolve_token,
)


class TestResolveToken:
    def test_prefers_github_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "primary")
        monkeypatch.setenv("GH_TOKEN", "secondary")
        assert _resolve_token() == "primary"

    def test_falls_back_to_gh_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GH_TOKEN", "secondary")
        assert _resolve_token() == "secondary"

    def test_returns_none_if_neither_set(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert _resolve_token() is None

    def test_treats_empty_string_as_unset(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "")
        monkeypatch.setenv("GH_TOKEN", "")
        assert _resolve_token() is None


class TestResolveDbPath:
    def test_returns_none_when_no_flag(self):
        assert _resolve_db_path(None) is None

    def test_composes_capxure_db_filename(self, tmp_path):
        # The function runs .resolve() on the directory before appending — match that order.
        expected = tmp_path.resolve() / "capxure.db"
        assert _resolve_db_path(str(tmp_path)) == expected

    def test_expands_user_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = _resolve_db_path("~/capxure-data")
        expected = (tmp_path / "capxure-data").resolve() / "capxure.db"
        assert result == expected


class TestPrintStatus:
    def test_writes_severity_colon_message_to_stderr(self, capsys):
        _print_status("hello world", Severity.INFO)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "info: hello world\n"

    def test_each_severity_lowercased(self, capsys):
        _print_status("done", Severity.SUCCESS)
        _print_status("bad", Severity.ERROR)
        captured = capsys.readouterr()
        assert "success: done" in captured.err
        assert "error: bad" in captured.err


class TestExitCodeFor:
    def test_zero_when_outcome_populated(self):
        result = ProcessResult(owner="a", repo="b", outcome=UpsertOutcome.NEW)
        assert _exit_code_for(result) == 0

    def test_zero_when_dedup_skip(self):
        result = ProcessResult(owner="a", repo="b", outcome=UpsertOutcome.UNCHANGED)
        assert _exit_code_for(result) == 0

    def test_one_when_outcome_none(self):
        result = ProcessResult(owner="a", repo="b", outcome=None, error="network dead")
        assert _exit_code_for(result) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: new tests FAIL with `ImportError: cannot import name '_exit_code_for'` etc.

- [ ] **Step 3: Replace `src/capxure/cli/capture.py` with the helpers**

```python
"""Capture subcommand. Wraps capxure.process_repo with stderr progress reporting."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from capxure import ProcessResult, Severity


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("capture", help="Capture a GitHub repo.")
    p.add_argument("target", help="GitHub URL or owner/repo.")
    p.add_argument(
        "--data-dir",
        default=None,
        help="Directory to store capxure.db (defaults to platformdirs location).",
    )
    p.set_defaults(handler=command)


def command(args: argparse.Namespace) -> int:
    # Wired in Task 4. Helpers are exercised by Task 3's tests.
    return 0


# --- helpers ---

def _resolve_token() -> str | None:
    """Return the resolved GitHub token, or None if neither env var is set.

    Empty-string env values are treated as unset so an empty `GITHUB_TOKEN=` in a
    user's shell profile doesn't silently produce a bogus Authorization header.
    """
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _resolve_db_path(data_dir_flag: str | None) -> Path | None:
    """Map the --data-dir flag to a Storage db_path, or None to use the library default."""
    if data_dir_flag is None:
        return None
    return (Path(data_dir_flag).expanduser().resolve()) / "capxure.db"


def _print_status(message: str, severity: Severity) -> None:
    """StatusCallback implementation. One line per event, stderr, flushed."""
    print(f"{severity.value}: {message}", file=sys.stderr, flush=True)


def _exit_code_for(result: ProcessResult) -> int:
    """Map a ProcessResult to a process exit code.

    Any non-None outcome (including dedup-skips) is success. outcome=None means the
    library caught an internal failure — exit 1. Finer distinctions aren't available
    at this boundary (see spec §5).
    """
    return 0 if result.outcome is not None else 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/capxure/cli/capture.py tests/test_cli.py
git commit -m "Add capture subcommand helpers: token, db-path, status, exit code"
```

---

## Task 4: Wire the capture handler to the library

This task replaces the `return 0` stub in `command()` with the real orchestration: missing-token guard, `parse_github_url` pre-check, `async with GitHubClient(...)`, `process_repo`, `KeyboardInterrupt` handling.

**Files:**
- Modify: `src/capxure/cli/capture.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

from capxure.cli.capture import command


@pytest.fixture
def args_ok(tmp_path):
    """A plausible argparse namespace: valid target + data-dir under tmp."""
    ns = argparse.Namespace()
    ns.target = "owner/repo"
    ns.data_dir = str(tmp_path)
    ns.handler = command
    ns.subcommand = "capture"
    return ns


def _patch_client_and_storage(monkeypatch):
    """Replace GitHubClient (async CM) and Storage with test doubles. Returns (client, storage, process_repo_mock)."""
    client_instance = MagicMock(name="GitHubClient.instance")
    client_cls = MagicMock(name="GitHubClient", return_value=_AsyncCM(client_instance))
    storage_instance = MagicMock(name="Storage.instance")
    storage_cls = MagicMock(name="Storage", return_value=storage_instance)
    process_repo_mock = AsyncMock(name="process_repo")

    monkeypatch.setattr("capxure.cli.capture.GitHubClient", client_cls)
    monkeypatch.setattr("capxure.cli.capture.Storage", storage_cls)
    monkeypatch.setattr("capxure.cli.capture.process_repo", process_repo_mock)
    return client_cls, storage_cls, process_repo_mock


class _AsyncCM:
    """Minimal async context manager for mocking GitHubClient."""
    def __init__(self, value):
        self._value = value
    async def __aenter__(self):
        return self._value
    async def __aexit__(self, *exc):
        return False


class TestCommandHappyPath:
    def test_returns_zero_on_successful_capture(self, monkeypatch, args_ok):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, _, process_repo_mock = _patch_client_and_storage(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="owner", repo="repo", outcome=UpsertOutcome.NEW
        )

        assert command(args_ok) == 0

    def test_passes_token_to_github_client(self, monkeypatch, args_ok):
        monkeypatch.setenv("GITHUB_TOKEN", "secret123")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        client_cls, _, process_repo_mock = _patch_client_and_storage(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=UpsertOutcome.NEW
        )

        command(args_ok)
        client_cls.assert_called_once_with(token="secret123")

    def test_passes_composed_db_path_to_storage(self, monkeypatch, args_ok, tmp_path):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, storage_cls, process_repo_mock = _patch_client_and_storage(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=UpsertOutcome.NEW
        )

        command(args_ok)
        # _resolve_db_path calls .expanduser().resolve() on the parent dir, so
        # compare against the same canonicalization to survive macOS's /private symlink.
        expected = tmp_path.expanduser().resolve() / "capxure.db"
        storage_cls.assert_called_once_with(db_path=expected)

    def test_omits_db_path_when_no_data_dir_flag(self, monkeypatch, args_ok):
        args_ok.data_dir = None
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, storage_cls, process_repo_mock = _patch_client_and_storage(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=UpsertOutcome.NEW
        )

        command(args_ok)
        storage_cls.assert_called_once_with()

    def test_passes_keyword_args_to_process_repo(self, monkeypatch, args_ok):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, _, process_repo_mock = _patch_client_and_storage(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=UpsertOutcome.NEW
        )

        command(args_ok)

        process_repo_mock.assert_called_once()
        call = process_repo_mock.call_args
        assert call.args == ("owner/repo",)
        assert "github" in call.kwargs
        assert "storage" in call.kwargs
        assert "on_status" in call.kwargs


class TestCommandErrorPaths:
    def test_returns_one_when_process_repo_reports_failure(
        self, monkeypatch, args_ok, capsys
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, _, process_repo_mock = _patch_client_and_storage(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="o", repo="r", outcome=None, error="rate limited"
        )

        assert command(args_ok) == 1

    def test_returns_one_and_stderr_message_when_token_missing(
        self, monkeypatch, args_ok, capsys
    ):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        # GitHubClient/Storage should NOT be constructed.
        _, _, process_repo_mock = _patch_client_and_storage(monkeypatch)

        assert command(args_ok) == 1
        err = capsys.readouterr().err
        assert "GITHUB_TOKEN" in err and "GH_TOKEN" in err
        process_repo_mock.assert_not_called()

    def test_returns_three_on_parse_error(self, monkeypatch, args_ok, capsys):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        args_ok.target = "not-a-valid-url-at-all-no-slashes-here"
        _, _, process_repo_mock = _patch_client_and_storage(monkeypatch)

        assert command(args_ok) == 3
        process_repo_mock.assert_not_called()
        assert "error:" in capsys.readouterr().err.lower()

    def test_returns_130_on_keyboard_interrupt(self, monkeypatch, args_ok, capsys):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _patch_client_and_storage(monkeypatch)

        def _boom(_coro):
            raise KeyboardInterrupt
        monkeypatch.setattr("capxure.cli.capture.asyncio.run", _boom)

        assert command(args_ok) == 130
        assert "interrupted" in capsys.readouterr().err


class TestMainDispatch:
    """`main(['owner/repo'])` must route through the capture handler via argv rewrite."""

    def test_slash_target_routes_to_capture(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, _, process_repo_mock = _patch_client_and_storage(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="owner", repo="repo", outcome=UpsertOutcome.NEW
        )

        assert main(["owner/repo"]) == 0
        process_repo_mock.assert_called_once()
        assert process_repo_mock.call_args.args == ("owner/repo",)

    def test_url_target_routes_to_capture(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "t0k3n")
        _, _, process_repo_mock = _patch_client_and_storage(monkeypatch)
        process_repo_mock.return_value = ProcessResult(
            owner="owner", repo="repo", outcome=UpsertOutcome.NEW
        )

        url = "https://github.com/owner/repo"
        assert main([url]) == 0
        assert process_repo_mock.call_args.args == (url,)

    def test_unknown_subcommand_exits_2(self, capsys):
        """A bare word without `/` isn't a capture target; argparse rejects it as an unknown subcommand and exits 2."""
        with pytest.raises(SystemExit) as exc_info:
            main(["gibberish"])
        assert exc_info.value.code == 2
        # argparse writes its "invalid choice" message to stderr
        assert "gibberish" in capsys.readouterr().err
```

Note: the parse-error test uses a value that `parse_github_url` will reject. If the library's URL parser is unexpectedly lenient and accepts this string, pick another obviously-malformed value by inspecting `src/capxure/github.py`'s `parse_github_url`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: the `TestCommandHappyPath` and `TestCommandErrorPaths` tests FAIL (handler still returns 0).

- [ ] **Step 3: Replace `command()` with the real orchestration**

Replace the `command` function (and add needed imports) in `src/capxure/cli/capture.py`:

```python
import asyncio

from capxure import GitHubClient, Storage, parse_github_url, process_repo


def command(args: argparse.Namespace) -> int:
    # Preflight: token required. Keep the check above any network/IO.
    token = _resolve_token()
    if token is None:
        print("error: GITHUB_TOKEN or GH_TOKEN must be set", file=sys.stderr)
        return 1

    # Preflight: parse target so malformed input gets a distinct exit code.
    try:
        parse_github_url(args.target)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    db_path = _resolve_db_path(args.data_dir)
    storage = Storage(db_path=db_path) if db_path is not None else Storage()

    async def _run() -> ProcessResult:
        async with GitHubClient(token=token) as github:
            return await process_repo(
                args.target,
                github=github,
                storage=storage,
                on_status=_print_status,
            )

    try:
        result = asyncio.run(_run())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    return _exit_code_for(result)
```

Full file now (for clarity) — imports at the top:

```python
"""Capture subcommand. Wraps capxure.process_repo with stderr progress reporting."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from capxure import (
    GitHubClient,
    ProcessResult,
    Severity,
    Storage,
    parse_github_url,
    process_repo,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/capxure/cli/capture.py tests/test_cli.py
git commit -m "Wire cap capture handler to process_repo with token, parse, and interrupt guards"
```

---

## Task 5: End-to-end smoke test + `--help` sanity

**Files:**
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_cli_help_exits_zero_and_mentions_cap():
    """`python -m capxure.cli --help` → exit 0, usage on stdout."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "cap" in result.stdout.lower()
    assert "capture" in result.stdout.lower()
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_cli.py::test_cli_help_exits_zero_and_mentions_cap -v`
Expected: PASS — the parser built in Task 2 already handles `--help`.

(If this test somehow fails, the fix lives in Task 2's parser, not here.)

- [ ] **Step 3: Manually verify the installed command works end-to-end**

Run: `cap --help`
Expected: usage text mentioning `capture`, exit 0.

Run: `cap` (no args)
Expected: exit code 2, help text on stderr.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "Add end-to-end smoke test for cap --help"
```

---

## Self-review checklist

Before considering the plan complete, re-read the spec and confirm:

- [ ] **Spec §1 (Architecture):** Tasks 1, 2, 3 create the `cli/` package, `__main__.py`, and entry point.
- [ ] **Spec §2 (Dispatch):** Task 2 implements the `/`-based dispatch rule and no-arg help. Future subcommands slot in via `register()`.
- [ ] **Spec §3 (Capture behavior):** Task 4 wires token resolution, data-dir → db_path composition, `parse_github_url` pre-check, async context manager, and `process_repo` invocation.
- [ ] **Spec §4 (Progress output):** Task 3's `_print_status` writes `{severity}: {message}` to stderr, flushed. stdout stays clean throughout.
- [ ] **Spec §5 (Exit codes):** Task 4 covers 0, 1 (missing token + library failure), 3 (parse error), 130 (Ctrl-C). Argparse provides 2 natively.
- [ ] **Spec §6 (Testing):** Parser tests in Task 2, handler tests in Tasks 3+4, smoke test in Task 5.
- [ ] **No placeholders:** every step shows exact code or exact commands.
- [ ] **Type consistency:** all references to library API use `github=`, `storage=`, `on_status=` (keyword-only), `Storage(db_path=...)`, `GitHubClient(token=...)` used as async CM, `_print_status(message, severity)` matches library's `StatusCallback` signature.

---

## Out of scope (future specs)

- `cap list` — enumerate captured repos.
- `cap show <target>` — print stored metadata + README path.
- `--debug` / verbose tracebacks.
- Finer exit-code taxonomy for network-error subtypes.
- Unauthenticated operation (requires a library change to `GitHubClient`).
