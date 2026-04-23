# Design: `cap` CLI — capture command

**Date:** 2026-04-23
**Status:** Approved (pending implementation plan)
**Scope:** Ship the first subcommand of a new CLI (`cap`) that wraps the existing `capxure` library. This spec covers **capture only**. Future subcommands (`list`, `show`) are anticipated in the architecture but are explicitly out of scope here.

## Goals

- Give users a single shell command — `cap <target>` — that captures a GitHub repo's metadata and README to local storage.
- Keep the CLI a thin consumer of the existing public library API. No reaching into internals.
- Lay out the package structure so `list` and `show` can be added later without restructuring.
- Zero new runtime dependencies. Use `argparse` from the stdlib.

## Non-goals

- `list` / `show` / any other subcommand — separate future specs.
- Rich output (colors, spinners, progress bars).
- A `--debug` / verbose-traceback flag.
- A config file.
- `GITHUB_TOKEN` sources beyond environment variables (no `gh auth token` fallback, no `--token` flag).
- Publishing to PyPI (packaging already works; publishing is a separate operational step).

## Architecture

### Package layout

New package at `src/capxure/cli/`:

```
src/capxure/cli/
├── __init__.py   # defines main(), builds the argparse parser, dispatches
├── __main__.py   # enables `python -m capxure.cli`
└── capture.py    # capture_command(args) -> int
```

Future siblings: `list.py`, `show.py`.

### Entry point

`pyproject.toml` gains:

```toml
[project.scripts]
cap = "capxure.cli:main"
```

After install (pipx / uv tool / pip), a `cap` launcher is placed on `$PATH` and runs from any directory, like any other shell command.

### Subcommand contract

Each subcommand module exposes two things:

- `register(subparsers: argparse._SubParsersAction) -> None` — adds its parser to the top-level subparser group.
- `command(args: argparse.Namespace) -> int` — runs the subcommand and returns a process exit code.

This keeps `__init__.py` agnostic of individual subcommands and makes adding `list` / `show` a matter of creating a new file and adding one import.

## Command dispatch

`cap` inspects the first positional arg:

- If it looks like a capture target (a `/` present, or a GitHub URL), dispatch to `capture.command(...)`.
- Otherwise, treat it as a subcommand name and dispatch via argparse's subparser mechanism.
- No args → print help and exit 2 (argparse's default for missing required arg).
- `cap --help` / `cap -h` → print top-level usage and exit 0.

Since capture targets always contain a `/` and subcommand names (`list`, `show`) never will, there is no collision. This rule is stable as future subcommands land.

## Capture behavior

Invocation: `cap <target> [--data-dir PATH]`

### Target parsing

- Delegated entirely to `capxure.github.parse_github_url`. The CLI adds no parsing of its own.
- If `parse_github_url` raises, catch at the handler boundary, print `error: <message>` to stderr, exit 3.
- **Library change during implementation:** `parse_github_url`'s regex was broadened so bare `owner/repo` shorthand parses (the `github.com/` prefix became optional). The dispatch rule in §2 relies on `/` as the capture-target marker, so the library must accept that form. See `tests/test_github.py` for the accept/reject matrix.

### Token resolution

- Read `GITHUB_TOKEN`; if unset, fall back to `GH_TOKEN`.
- **If neither is set, exit 1 with `error: GITHUB_TOKEN or GH_TOKEN must be set` on stderr.** A token is required — the current `GitHubClient` unconditionally sends `Authorization: token <token>`, so unauthenticated mode is not available without a library change. Deferred to a future spec if a concrete need arises.
- No `--token` flag. No `gh auth token` fallback.
- Resolution happens once at the top of `capture_command`, before any async work begins.

### Data directory resolution

- The `Storage` constructor takes a `db_path` (a full path to the SQLite file), not a directory. The CLI presents a directory-level flag and composes the file path:
  - If `--data-dir PATH` is provided: expand `~`, resolve to absolute, then pass `Storage(db_path=Path(PATH) / "capxure.db")`. `capxure.db` is the library's documented default filename.
  - Otherwise: pass no args — `Storage()` — and let the library's own default (respecting `CAPXURE_DATA_DIR`, then `platformdirs.user_data_dir("capxure")`) resolve the path.
- Parent directory creation is already handled by the `Storage` constructor.
- Note: the library already honors a `CAPXURE_DATA_DIR` env var in its default-path resolution. The CLI does not expose that env var itself, but users who set it will see the override applied transparently (when no `--data-dir` flag is passed).

### Orchestration

```python
# Parse first so malformed input can be reported with a distinct exit code.
# process_repo also catches ValueError internally, but the CLI needs to
# distinguish "the user typed nonsense" from "GitHub said no".
try:
    parse_github_url(target)
except ValueError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 3

storage = Storage(db_path=resolved_db_path)  # or Storage() if no --data-dir

async def _run() -> ProcessResult:
    async with GitHubClient(token=resolved_token) as github:
        return await process_repo(
            target,
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

## Progress output

- A single callback `_print_status(message: str, severity: Severity)` defined in `cli/capture.py`, matching the library's `StatusCallback` protocol.
- One line to **stderr** per event: `{severity}: {message}` (e.g., `info: Fetching metadata for owner/repo...`). Severity values are those from the library's `Severity` enum (`INFO`, `SUCCESS`, `ERROR`, etc.), lowercased for display.
- No color, no spinners, no timestamps. stdout writes flushed after each line.
- **stdout stays empty during capture.** Reserved for structured output from future subcommands (`list`, `show`), so `cap <repo> && cap show <repo>` composes cleanly.
- No explicit "captured owner/repo" summary line from the CLI on success — the library's own status callback already emits a terminal `success: owner/repo: captured successfully` (or the equivalent for update/dedup outcomes), and piling a CLI-level summary on top would be redundant.

## Error handling & exit codes

| Exit code | Condition |
|-----------|-----------|
| `0`       | Capture succeeded (any non-`None` `ProcessResult.outcome`, including `UNCHANGED` / `LOCAL_IS_NEWER` dedup-skip outcomes) |
| `1`       | Capture failed. Covers: missing token (neither `GITHUB_TOKEN` nor `GH_TOKEN` set), OR `ProcessResult.outcome is None` with `.error` populated (network failure, GitHub rejection, auth failure, rate limit, unexpected library error) |
| `2`       | Usage error (argparse — missing args, unknown flag) |
| `3`       | Target parse error (`parse_github_url` raised `ValueError` at the CLI boundary before `process_repo` was invoked) |
| `130`     | `KeyboardInterrupt` (Ctrl-C), per shell convention |

Important constraint: `process_repo` already catches `ValueError`, `NotFoundError`, `RateLimitExceededError`, `AuthenticationError`, and bare `Exception` internally, always returning a `ProcessResult`. The CLI therefore cannot distinguish between those failure modes at this boundary without parsing error strings (brittle — rejected) or changing the library (out of scope). All library-reported failures collapse into exit `1`.

Finer-grained exit codes can be added later if scripting needs grow, either by widening `ProcessResult` with a structured error kind or by splitting catch-blocks out of `process_repo`. Any such change would be additive: `== 0` / `!= 0` scripts remain correct.

Handler behavior:

- Exit `3` path: the CLI calls `parse_github_url` up-front (see Section 3 orchestration). On `ValueError`, print `error: <message>` to stderr, return 3, never construct `GitHubClient` or invoke `process_repo`.
- Exit `1` (missing token) path: before constructing `GitHubClient`, check that `GITHUB_TOKEN` or `GH_TOKEN` is set. If neither, print `error: GITHUB_TOKEN or GH_TOKEN must be set` to stderr, return 1.
- Exit `1` (library failure) path: after `process_repo` returns, if `result.outcome is None`, return 1. The `error` message has already been surfaced via the status callback (processor calls `on_status(..., Severity.ERROR)` for every failure before returning), so the CLI does not re-print it.
- Exit `0` path: `result.outcome is not None`. Status callback has already printed the success/dedup line.
- Exit `130` path: wrap `asyncio.run(...)` in `try/except KeyboardInterrupt`, print `interrupted` to stderr, return 130.
- No tracebacks shown to the user by default. A `--debug` flag is out of scope.

## Testing

Tests live in `tests/test_cli.py`. Three layers:

### 1. Parser / dispatch tests

Pure, no I/O. Exercise the argparse builder directly. Cover:

- `cap owner/repo` → capture path selected, target populated.
- `cap` (no args) → exit 2.
- `cap --help` → exit 0, usage on stdout.
- `--data-dir PATH` parsed and resolved correctly.
- Bad flags rejected with exit 2.

### 2. Capture-handler tests (stubbed `process_repo`)

Monkeypatch `capxure.cli.capture.process_repo` with an async stub that returns canned `ProcessResult`s. Verify:

- Token resolution: `GITHUB_TOKEN` used; falls back to `GH_TOKEN`; `None` if neither.
- Data dir plumbing: `--data-dir` flows into `Storage`; absent flag leaves library default.
- `process_repo` is called with keyword args `github=`, `storage=`, `on_status=` (keyword-only in the library's signature).
- Status callback routes to stderr with `{severity}: {message}` format.
- Exit code mapping:
  - `ProcessResult(outcome=UpsertOutcome.NEW, ...)` → exit 0
  - `ProcessResult(outcome=UpsertOutcome.UNCHANGED, ...)` → exit 0
  - `ProcessResult(outcome=None, error="...")` → exit 1 (error already surfaced via callback, not reprinted)
- Parse-error path: passing a malformed target (e.g., `"not-a-repo"`) exits 3 *without* invoking the monkeypatched `process_repo` (verify the stub was not called).
- `KeyboardInterrupt` path: let the real `asyncio.run` drive an awaited coroutine whose inner `process_repo` raises `KeyboardInterrupt`; assert the CLI's `try/except KeyboardInterrupt` maps it to exit 130 with `interrupted` on stderr. (Monkeypatching `asyncio.run` directly works too but doesn't exercise the real propagation path and leaks a never-awaited coroutine warning.)

Uses pytest `capsys` for stdout/stderr assertions and `monkeypatch` for env vars. No network, no real disk beyond `tmp_path` for `Storage`.

### 3. End-to-end smoke test

`subprocess.run([sys.executable, "-m", "capxure.cli", "--help"])` → exit 0, usage on stdout. Confirms the entry point is actually invokable. No network.

### Explicitly not tested

- Real GitHub network calls — covered by the library's existing tests.
- The `pipx install … && cap` install path — that's packaging, verified by correctly declaring the entry point.

## Future work (out of scope for this spec)

- `cap list` — enumerate captured repos from Storage.
- `cap show <target>` — print stored metadata + README path.
- `--debug` for verbose tracebacks.
- Richer exit code taxonomy if scripting needs grow.
- Publication to PyPI.
