# cap note — Quick-Capture Notes Inbox

Date: 2026-04-26
Status: Approved

## Goal

Add a low-friction quick-capture surface so any thought, quote, link, or half-formed idea can be dumped into the local capxure database faster than the urge to capture decays. Add via `cap note "<text>"`; read back via `cap note ls`. The capture path is one shell command with no required metadata; optional flags exist for the moments when context is at hand.

This is the second capture domain in capxure (alongside `git`), and the first concrete validation that the per-domain refactor (`Database` + per-domain stores) actually scales to a second domain.

## Non-Goals

- **Promotion / processing pipeline.** No `promoted_to` polymorphic pointer, no `processed_at` timestamp. Designed and discussed earlier but explicitly trimmed: dead columns until a "promoter" exists. Easy to add via a future schema bump.
- **Filtering / search on `ls`.** No `--source`, `--kind`, `--since`, `--grep` flags in v1. No FTS5. Add when the inbox actually feels noisy, not speculatively. No indexes on `notes` columns until a query pattern is real.
- **`cap note show <id>` / `cap note rm <id>`.** Out of scope. Read individual rows or delete via the `db.connection` SQL escape hatch if needed today.
- **Editor fallback.** `cap note` with no positional and a TTY stdin exits 2 with usage. No `$EDITOR` integration in v1.
- **Multi-add / batch import.** Capture is one note per invocation. (Stdin pipes are still supported — they pipe one blob of content.)
- **Updating existing notes.** Notes are append-only in v1. Edit via SQL escape hatch.

## Schema

New table added to `_SCHEMA_SQL` in `src/capxure/db.py`:

```sql
CREATE TABLE notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    annotation      TEXT,
    source          TEXT,
    source_locator  TEXT,
    kind_hint       TEXT,
    captured_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
```

No indexes. No `CHECK` constraint on `content` — emptiness is enforced at the application boundary (`NoteStore.add` strips and validates), which keeps the error message human and consistent across CLI and library callers.

### Field semantics

| Field | Required | Meaning |
|---|---|---|
| `content` | yes | The captured text. Whitespace-stripped at the application boundary; empty (after strip) is rejected. |
| `annotation` | no | The user's own commentary on the captured thing — "why this matters", "relates to X". |
| `source` | no | Where it came from. Free-form: `"twitter"`, `"book:Atomic Habits"`, a URL, anything. |
| `source_locator` | no | Position within the source — URL, page number, timestamp. Free-form. |
| `kind_hint` | no | What kind of thing it is — `"quote"`, `"link"`, `"thought"`. Free-form (the `_hint` suffix signals "non-authoritative"). |
| `captured_at` | auto | SQLite `DEFAULT (datetime('now'))`. Not user-settable in v1. |

### Migration

Bump `_SCHEMA_VERSION` from `1` to `2`. Rework `_ensure_schema` to support forward migrations:

```python
_SCHEMA_VERSION = 2

_MIGRATIONS = {
    2: """
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            annotation TEXT,
            source TEXT,
            source_locator TEXT,
            kind_hint TEXT,
            captured_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        PRAGMA user_version = 2;
    """,
}

def _ensure_schema(self):
    current = self._conn.execute("PRAGMA user_version").fetchone()[0]
    if current == 0:
        self._conn.executescript(_SCHEMA_SQL)   # fresh: full v2 DDL in one shot
        return
    if current > _SCHEMA_VERSION:
        raise UnsupportedSchemaError(...)
    for v in range(current + 1, _SCHEMA_VERSION + 1):
        self._conn.executescript(_MIGRATIONS[v])
```

Fresh installs run the full `_SCHEMA_SQL` (which now includes `notes`). v1 dbs auto-upgrade on next open by applying `_MIGRATIONS[2]`. Forward-incompatible dbs (version > `_SCHEMA_VERSION`) still raise `UnsupportedSchemaError`. The migration registry is the extension point for future schema bumps.

## Library API

### Module: `src/capxure/note/__init__.py`

Single-file package — no `client.py` / `processor.py` because the note domain has nothing to fetch and nothing to orchestrate. If note ever grows (importers, parsers), `__init__.py` → `store.py` is a one-commit move with no public API change.

```python
__all__ = ["Note", "NoteStore"]

@dataclass(frozen=True)
class Note:
    id: int
    content: str
    annotation: str | None
    source: str | None
    source_locator: str | None
    kind_hint: str | None
    captured_at: str

class NoteStore:
    """Note-domain queries. Construct over a connection from `Database`."""

    def __init__(self, connection: sqlite3.Connection) -> None: ...

    @property
    def connection(self) -> sqlite3.Connection: ...

    def add(
        self,
        content: str,
        *,
        annotation: str | None = None,
        source: str | None = None,
        source_locator: str | None = None,
        kind_hint: str | None = None,
    ) -> Note:
        """Insert a note. Strips content; raises ValueError if empty after strip.
        Returns the inserted Note (with assigned id and DB-generated captured_at)."""

    def list_notes(self, *, limit: int | None = None) -> list[Note]:
        """All notes, sorted (captured_at DESC, id DESC). Limit is library-side
        only — no CLI flag in v1."""

    def count_notes(self) -> int: ...
```

Design calls:
- **`add` strips and raises `ValueError` on empty.** Same enforcement as the CLI — library callers can't bypass the one schema invariant. Plain `ValueError` (not a custom exception) because empty content is a programming error with no recovery story.
- **`add` returns `Note`** — caller gets the assigned `id` and DB-generated `captured_at` without a second query.
- **Sort key `(captured_at DESC, id DESC)`.** `id` breaks ties when two adds land in the same `datetime('now')` second (likely on bulk pipes); without it, sqlite ordering for ties is undefined.

### `Database.notes` lazy accessor

Mirror of `db.repos`. In `db.py`:

```python
# top of file
if TYPE_CHECKING:
    from capxure.git.store import RepoStore
    from capxure.note import NoteStore

# Database.__init__ gains
self._notes: "NoteStore | None" = None

# Database gains
@property
def notes(self) -> "NoteStore":
    if self._notes is None:
        from capxure.note import NoteStore
        self._notes = NoteStore(self._conn)
    return self._notes
```

### Top-level package exports

`src/capxure/__init__.py` adds:

```python
from capxure.note import Note, NoteStore
```

`Note` and `NoteStore` slot into `__all__` alphabetically. No new exception types exported (`ValueError` is a builtin).

## CLI

### Top-level routing

`src/capxure/cli/__init__.py`:

```python
from capxure.cli import git, note

def build_parser():
    ...
    subparsers = parser.add_subparsers(dest="domain", metavar="{git,note}")
    git_parser = subparsers.add_parser("git", help="...", add_help=False)
    note_parser = subparsers.add_parser("note", help="Quick-capture notes (add, ls).", add_help=False)
    ...

def main(argv):
    ...
    if args_list[0] == "git":
        return git.main(args_list[1:])
    if args_list[0] == "note":
        return note.main(args_list[1:])
    build_parser().parse_args(args_list)
    return 2
```

### `cli/note/__init__.py` — domain router with smart-dispatch

```python
def build_parser():
    parser = argparse.ArgumentParser(prog="cap note", description="Quick-capture notes.")
    subparsers = parser.add_subparsers(dest="subcommand", metavar="{add,ls}")
    capture.register(subparsers)
    ls.register(subparsers)
    return parser

def main(argv=None):
    args_list = list(argv) if argv is not None else sys.argv[1:]

    # Smart dispatch: `cap note "thought"` -> `cap note add "thought"`.
    # Trigger: first arg exists, isn't a flag, isn't a known verb.
    if args_list and not args_list[0].startswith("-") and args_list[0] not in ("add", "ls"):
        args_list = ["add", *args_list]

    parser = build_parser()
    if not args_list:
        parser.print_usage(sys.stderr)
        return 2
    args = parser.parse_args(args_list)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_usage(sys.stderr)
        return 2
    return handler(args)
```

The dispatch heuristic differs from `cap git` (which keys on `/` in the first arg) because note content has no required shape. **Documented quirk:** `cap note "ls"` (literal string `"ls"`) routes to the `ls` verb, not capture. Escape via `cap note add ls`. This is the trade for keeping `cap note ls` short.

### `cli/note/capture.py` — the add path

```
cap note "<content>" [-a TEXT] [-s TEXT] [-L TEXT] [-k TEXT]
cap note add "<content>" [...same flags...]
```

| Flag | Long form | Maps to |
|---|---|---|
| `-a` | `--annotation` | `NoteStore.add(annotation=...)` |
| `-s` | `--source` | `NoteStore.add(source=...)` |
| `-L` | `--loc` | `NoteStore.add(source_locator=...)` |
| `-k` | `--kind` | `NoteStore.add(kind_hint=...)` |

The CLI flag names (`--loc`, `--kind`) are deliberately shorter than the column names (`source_locator`, `kind_hint`) for terseness in the capture-speed-critical path. `-L` (uppercase) avoids visual collision with `cap git ls -l/--limit`.

Content sourcing precedence:
1. **Positional given** → use it; ignore stdin (unix convention; `grep` and friends behave this way).
2. **No positional, stdin not a TTY** → read full stdin as content.
3. **No positional, stdin is a TTY** → exit 2 with usage error.

After sourcing, content is `.strip()`-ed; empty result → exit 2 `error: content cannot be empty`.

On success, `NoteStore.add` is called and a single line prints to stderr:

```
success: note 42 captured
```

stdout is silent (write commands keep stdout empty per the README convention).

### `cli/note/ls.py` — the read path

```
cap note ls [--format {pretty,plain}]
```

Defaults: `pretty` when `sys.stdout.isatty()`, `plain` (TSV) when piped. `--format` overrides both.

**Plain format** — TSV, 7 fields per note, no header row:

```
id <TAB> captured_at <TAB> kind_hint <TAB> source <TAB> source_locator <TAB> annotation <TAB> content
```

Tabs/newlines in `content` and `annotation` are collapsed to spaces (same convention as `cap git ls --format plain`). Empty optionals → empty string.

**Pretty format** — card-style, not table. Note content is variable-length (one-liner to paragraph); a table either truncates (lossy) or wraps (ugly). Cards keep multi-line content readable:

```
[1] 2026-04-26 20:15  kind=quote  source=twitter
loc: https://twitter.com/example/status/123
note: relevant to compound-interest thinking
> The best time to plant a tree was 20 years ago. The second best time is now.

[2] 2026-04-26 19:42
> just-content example, no metadata
```

Rules:
- Skip blank metadata lines (no `kind=`/`source=`/`loc:`/`note:` line if the field is null).
- Blank line between cards.
- Content lines prefixed with `> `; multi-line content stays multi-line.
- Empty result → exit 0 with no output (scripts detect via `wc -l` returning 0).

### Exit codes

Same shape as `cap git`:

- `0` — success (including empty `ls`).
- `1` — library error (wrapped exception from `NoteStore`/`Database`). Message goes to stderr as `error: <reason>`.
- `2` — usage error (no content, empty content, bad flags, unknown verb).
- `130` — Ctrl-C.

## Architecture

### New code

- **`src/capxure/note/__init__.py`** — `Note` dataclass + `NoteStore` class (~120 lines).
- **`src/capxure/cli/note/__init__.py`** — domain router with smart-dispatch (~30 lines).
- **`src/capxure/cli/note/capture.py`** — add handler with stdin handling (~50 lines).
- **`src/capxure/cli/note/ls.py`** — list handler with pretty/plain formatting (~80 lines).

### Modified code

- **`src/capxure/db.py`** — `_SCHEMA_SQL` gains `notes` table; `_SCHEMA_VERSION` 1→2; `_MIGRATIONS` registry added; `_ensure_schema` learns to apply forward migrations; `Database._notes` lazy accessor added.
- **`src/capxure/__init__.py`** — `Note`, `NoteStore` added to imports and `__all__`.
- **`src/capxure/cli/__init__.py`** — top-level parser gains `note` subparser; `main` dispatches `note` to `cli.note.main`.
- **`pyproject.toml`** — version `0.4.0` → `0.5.0`.
- **`README.md`** — `cap note` examples in CLI section; library example for `db.notes`; schema docs gain `notes` bullet; changelog entry for 0.5.0.

### Reused as-is

- `Database` connection lifecycle, WAL mode, context manager protocol.
- `_resolve_default_db_path`, `CAPXURE_DATA_DIR` env var, `platformdirs` resolution.
- The lazy-accessor pattern from `db.repos`.
- The CLI smart-dispatch pattern from `cap git`.
- The `pretty/plain` format split from `cap git ls`.
- Exit code conventions from `cap git`.

The key design choice: **the note domain piggybacks entirely on infrastructure built during the per-domain refactor.** No new connection lifecycle, no new auth, no new path resolution, no new entry-point machinery. The whole note feature is a table + a Store + two CLI files.

## Error Handling

| Where | Error | Behavior |
|---|---|---|
| Schema upgrade | v1 db, migration succeeds | Silent — `notes` table appears. |
| Schema upgrade | v1 db, migration fails | Exception bubbles up; `Database.__init__` does not swallow. User sees stack trace. |
| Schema check | v3+ db | Raises `UnsupportedSchemaError` (existing behavior, preserved). |
| `NoteStore.add` | Empty/whitespace content | Raises `ValueError("content cannot be empty")`. |
| `NoteStore.add` | DB write failure | sqlite3 exception bubbles up. CLI catches and surfaces as `error: <reason>` exit 1. |
| CLI add | No positional, TTY stdin | Exit 2, `error: no content provided (positional or via stdin)`. |
| CLI add | Empty content (after strip) | Exit 2, `error: content cannot be empty`. |
| CLI add | Other exception from store | Exit 1, `error: <stringified exception>`. |
| CLI ls | DB read failure | Exit 1, `error: <reason>`. |
| Anywhere | `KeyboardInterrupt` | Exit 130 (argparse / Python default behavior). |

## Testing

Mirror the existing `tests/` layout exactly.

```
tests/
├── note/
│   ├── __init__.py
│   └── test_store.py
├── cli/
│   └── note/
│       ├── __init__.py
│       ├── test_dispatch.py
│       ├── test_capture.py
│       ├── test_capture_smoke.py
│       ├── test_ls.py
│       └── test_ls_smoke.py
├── test_database.py            # extended
└── cli/test_dispatcher.py      # extended
```

### `tests/test_database.py` extensions

- Fresh path → `PRAGMA user_version` returns 2; `notes` table exists and is queryable.
- Synthesize a v1 db (write the v1 schema by hand + `PRAGMA user_version = 1`); open with `Database`; verify `PRAGMA user_version` is now 2 and `notes` is queryable; verify pre-existing `repos` rows still readable.
- Synthesize a v3 db; opening raises `UnsupportedSchemaError`.
- `db.notes` returns the same `NoteStore` instance across calls.
- `db.notes` and `db.repos` share `db.connection`.

### `tests/note/test_store.py`

- `add(content)` happy path; returned `Note` matches inserted row.
- `add` strips leading/trailing whitespace from content.
- `add(content="   ")` raises `ValueError`.
- `add(content="")` raises `ValueError`.
- All optional fields nullable; round-trip preserves `None`.
- All optional fields stored verbatim (no transformation on `annotation`/`source`/`source_locator`/`kind_hint`).
- `list_notes()` returns rows newest-first by `captured_at`, then id desc as tiebreak.
- `list_notes(limit=N)` caps result.
- `list_notes()` on empty table returns `[]`.
- `count_notes()` returns row count.

### `tests/cli/note/test_dispatch.py`

- `cap note "thought"` → routes to `add` handler.
- `cap note add "thought"` → routes to `add` handler.
- `cap note ls` → routes to `ls` handler.
- `cap note "ls"` → routes to `ls` handler (documented quirk; pinned by test).
- `cap note add ls` → captures literal string `"ls"`.
- `cap note` (empty argv) → exit 2; usage on stderr.
- `cap note --help` → exit 0.
- `cap note nonexistent_verb` → smart-dispatch treats as content; routes to `add`.

### `tests/cli/note/test_capture.py`

- Positional content → captured to `NoteStore.add`.
- No positional, mocked piped stdin → captured.
- Positional present, mocked piped stdin → positional wins, stdin discarded.
- No positional, mocked TTY stdin → exit 2.
- Empty content (positional `""`) → exit 2.
- Whitespace-only content → exit 2.
- Each flag (`-a`/`-s`/`-L`/`-k`) propagates to the matching `NoteStore.add` kwarg.
- Long forms (`--annotation`/`--source`/`--loc`/`--kind`) work.
- Success → `success: note <id> captured` on stderr; stdout silent; exit 0.
- `NoteStore.add` raises (mocked) → exit 1; `error: <msg>` on stderr.

### `tests/cli/note/test_capture_smoke.py`

End-to-end with real `Database` against `tmp_path`:
- `cap note "first"` → row exists; `captured_at` populated.
- `cap note "second" -s twitter -L "https://x/123" -k quote` → row has all fields populated.
- Two rapid invocations → both rows exist; ids monotonic.

### `tests/cli/note/test_ls.py`

- Pretty format on mocked `isatty() == True`.
- Plain (TSV) format on mocked `isatty() == False`.
- `--format pretty` forces pretty even when piped; `--format plain` forces plain even on TTY.
- Empty result → exit 0; no output on stdout or stderr.
- Plain output: exactly 7 tab-separated fields per line.
- Plain output: tabs/newlines in content collapsed to spaces.
- Plain output: null optional fields → empty string.
- Pretty output: blank metadata lines skipped.
- Pretty output: multi-line content preserved across `> ` lines.
- Pretty output: blank line between cards.

### `tests/cli/note/test_ls_smoke.py`

End-to-end with real `Database` against `tmp_path`:
- Add 3 notes, then `cap note ls` returns them newest-first.
- `cap note ls | wc -l` works (confirms TSV mode auto-engaged when piped).

### `tests/cli/test_dispatcher.py` extension

- `cap note ...` routes to `note.main` (not `git.main`); `cap git ...` still routes correctly.

## Verification gates ("done" definition)

1. `pytest` — full suite passes (existing 203 + ~40 new ≈ 243 tests).
2. `pytest tests/note tests/cli/note tests/test_database.py` — focused subset green.
3. **Fresh-db smoke** (uses a throwaway `CAPXURE_DATA_DIR`):
   ```
   CAPXURE_DATA_DIR=/tmp/captest cap note "first thought"
   CAPXURE_DATA_DIR=/tmp/captest cap note "with metadata" -s twitter -L "https://x/123" -k quote
   echo "from a pipe" | CAPXURE_DATA_DIR=/tmp/captest cap note -a "via stdin"
   CAPXURE_DATA_DIR=/tmp/captest cap note ls
   CAPXURE_DATA_DIR=/tmp/captest cap note ls | wc -l   # confirms TSV mode when piped
   ```
4. **Real-db migration smoke** — your existing v1 db at the default path auto-upgrades silently on the first `cap note` invocation; `cap git ls | head -3` still returns the existing repo rows.
5. `python -c "from capxure import Note, NoteStore, Database; ..."` — public exports importable.
6. README examples copy-pasted into a Python REPL run cleanly.

## Open questions

None. All ambiguity surfaced during brainstorming was resolved before this spec was written.
