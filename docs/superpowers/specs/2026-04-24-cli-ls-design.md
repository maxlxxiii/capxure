# cap ls — CLI listing command

## Goal

Add a `ls` subcommand to the `cap` CLI that lists captured repos (and captured
topics) from the local SQLite database. Primary use case is **auditing** your
own captures — "what do I have, when did I last sync it, and what did I skip?"

Two output modes:
- **Pretty** (human-readable table) when stdout is a TTY.
- **Plain** (tab-separated, script-friendly) when stdout is piped or redirected.

## Non-goals

- No `cap show <repo>` (reserved for a future spec).
- No filtering by language, fork, or archive state (defer until pain emerges).
- No JSON output (defer; plain TSV covers current scripting needs).
- No README content in output — `ls` is metadata-only.
- No changes to `readme_content` loading behavior in `list_repos()` (a known
  scaling concern on large databases for library consumers, out of scope here).

## Command shape

```
cap ls [SUBJECT]
       [-s {synced,captured,stars} | --sort {synced,captured,stars}]
       [-r | --reverse]
       [-t TOPIC | --topic TOPIC]  (repeatable)
       [-l N | --limit N]
       [--format {pretty,plain}]
```

- `SUBJECT` is an optional positional: `repos` (default) or `topics`.
  `cap ls` is equivalent to `cap ls repos`.
- `--sort` and `--topic` are **only valid when `SUBJECT=repos`**. Using either
  with `topics` is a usage error (exit 2).
- `--format` defaults to auto-detect: `pretty` when stdout is a TTY, `plain`
  otherwise. Explicit `--format` overrides the detection.

### Defaults

| Flag         | Default (repos)             | Default (topics)                 |
|--------------|-----------------------------|----------------------------------|
| `--sort`     | `synced` (`last_synced_at`) | n/a (always by `count`)          |
| `--reverse`  | off → `DESC`                | off → `DESC`                     |
| `--limit`    | `10`                        | `10`                             |
| `--topic`    | none                        | n/a                              |
| `--format`   | auto (TTY→pretty)           | auto (TTY→pretty)                |

`--limit` defaults are **CLI-layer only**. The library method
`Storage.list_repos()` continues to accept `limit=None` and, when unset, returns
every row.

### Flag semantics

- `-s` / `--sort` takes one value from `{synced, captured, stars}`. Unknown value
  is rejected by argparse (exit 2).
- `-r` / `--reverse` is boolean; flips the default `DESC` sort to `ASC`.
- `-t` / `--topic` is repeatable. Multiple values are combined with **OR**
  semantics: `cap ls -t ml -t nlp` returns repos tagged with either `ml` OR
  `nlp`. Matching is case-insensitive, exact (no substring).
- `-l` / `--limit` requires `N >= 1`. `N <= 0` is a usage error (exit 2).

### Short-flag combinations

- `cap ls -r -s stars` ✓
- `cap ls -rs stars` ✓ (argparse consumes `-r` boolean, then `-s stars`)
- `cap ls -sr stars` ✗ argparse interprets `r` as the value of `-s`.
  This is standard Unix `getopt` behavior; live with it. Not worth a
  non-argparse parser.

## Output — repos / pretty

Columns, in order:

1. `last_synced_at` — rendered as `YYYY-MM-DD` (date only; time dropped, audit
   is day-granular).
2. `owner` — **right-aligned** within its column.
3. `name` — **left-aligned** within its column.
4. `stars` — right-aligned numeric, no thousands separator.
5. `description` — wraps to the column's available width, clipped to **2 visual
   lines max**. When clipped, the final character of line 2 is replaced with `…`.
   `NULL` description → empty cell.

Layout:

- Columns 1–4 are fixed-width, sized to the widest value in the result set.
  `owner` and `name` are capped at 20 and 30 characters respectively; values
  longer than the cap are right-truncated with a trailing `…`.
- `description` absorbs the remainder of the terminal width.
- Terminal width is read via `shutil.get_terminal_size()`. When stdout isn't a
  TTY but the user forces `--format pretty`, fall back to width `100`.
- Headers appear above the rows in a single header row. Each header follows
  its column's alignment (so `owner` is right-aligned, `name` left-aligned).
- No box-drawing characters; a single blank line separator between header and
  rows, produced by a dashed rule matching each column's width.
- Rendered by hand using Python's standard library (`textwrap`, string format).
  No `rich` / `tabulate` dependency.

**Empty result:** print `No repos captured.` (or `No repos match the filters.`
when `--topic` / `--limit` produced the empty set) to **stderr**; exit 0.
stdout stays empty to preserve clean output for redirected pipes that a user
forced into pretty mode.

## Output — repos / plain

Tab-separated, one row per repo. No header line. Newline-terminated rows.

Fields, in order:

1. `id` (internal SQLite rowid; intended for scripting updates against the db)
2. `github_id`
3. `full_name` (`owner/name`)
4. `description` (whitespace-scrubbed — see below)
5. `language`
6. `stars`
7. `pushed_at` (raw ISO string from the database)
8. `captured_at`
9. `last_synced_at`

**Whitespace scrubbing:** applied **only** to `description`. Replace `\t`,
`\n`, `\r` with a single space, then collapse runs of spaces to one. Lossy but
keeps downstream `awk`/`cut` correctness. All other fields are structurally
safe (IDs, timestamps, single-word language, numeric stars).

**NULL rendering:** empty string for the field (adjacent tabs). Applies to
nullable columns: `description`, `language`, `pushed_at`.

**Empty result:** nothing on stdout. Exit 0.

## Output — topics / pretty

Columns, in order: `count` (right-aligned), `topic` (left-aligned).

Sorted by count descending (then topic ascending for tie-break). No
description wrapping; topics are short identifiers.

**Empty result:** `No topics captured.` on stderr; exit 0.

## Output — topics / plain

Tab-separated: `topic<TAB>count\n`. No header. Sorted identically to pretty.

**Empty result:** nothing on stdout. Exit 0.

## Exit codes

- `0` success (including empty result sets).
- `2` usage error: unknown subject, unknown sort value, `--topic` or `--sort`
  with `SUBJECT=topics`, non-positive `--limit`.
- `130` Ctrl-C during execution (unlikely given the read-only path, but
  handled for parity with `cap capture`).

No `1` / `3` — there is no network, no token, no target-parsing to fail on.

## Library changes

### `Storage.list_repos` — extended signature

```python
def list_repos(
    self,
    *,
    sort: Literal["synced", "captured", "stars"] = "synced",
    reverse: bool = False,
    topics: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[Repo]: ...
```

- `sort="synced"` → `ORDER BY last_synced_at`.
- `sort="captured"` → `ORDER BY captured_at`.
- `sort="stars"` → `ORDER BY stars`.
- Direction defaults to `DESC`; `reverse=True` flips to `ASC`.
- `topics` (non-empty): inner-joins against `repo_topics`, filters with
  `WHERE LOWER(topic) IN (?, …)`, deduplicates repos via `DISTINCT`.
- `limit=None` returns all rows; `limit=N` applies `LIMIT N` after sort.

**Breaking change to the library contract:** the existing zero-arg call
`list_repos()` previously returned rows ordered by `github_id ASC`. The new
default ordering is `last_synced_at DESC`. One test assertion and one line of
rationale in `docs/superpowers/specs/2026-04-22-sqlite-storage-migration-design.md`
need updating (see Testing).

### `Storage.list_topic_counts` — new method

```python
def list_topic_counts(
    self,
    *,
    reverse: bool = False,
    limit: int | None = None,
) -> list[tuple[str, int]]: ...
```

- SQL: `SELECT topic, COUNT(*) AS c FROM repo_topics GROUP BY topic
  ORDER BY c DESC, topic ASC [LIMIT ?]`.
- `reverse=True` flips to `c ASC, topic ASC`.

## CLI structure

- New module: `src/capxure/cli/list_.py` (underscore suffix avoids shadowing
  the `list` builtin inside the module; the subparser name is still `ls`).
- Exports `register(subparsers)` matching the `capture.py` pattern.
- `src/capxure/cli/__init__.py`: import and register alongside `capture`.
- The top-level `/`-in-argv dispatch in `main()` is untouched. `cap ls` routes
  to the ls subparser normally; `cap owner/repo` still routes to `capture`.

## Testing strategy

Three tiers, mirroring the capture command.

### Unit — `tests/test_cli_list.py`

- Argument parsing: each flag parses; unknown `--sort` value exits 2; unknown
  `SUBJECT` exits 2; `--topic` with `topics` exits 2; `--sort` with `topics`
  exits 2; `--limit 0` exits 2.
- Format resolution: TTY → pretty; pipe → plain; explicit `--format` wins.
- Pretty rendering helpers:
  - Description wraps and clips to 2 lines with `…`.
  - Owner right-aligned, name left-aligned, correct column widths.
  - Empty result message appears on stderr.
  - Header row present and aligned.
- Plain rendering helpers:
  - Tab-separated layout for repos mode (9 fields) and topics mode (2 fields).
  - Description whitespace scrubbing (tabs/newlines → single space, runs
    collapsed).
  - NULL fields render as empty string.

### Storage — extends `tests/test_storage.py`

- `list_repos(sort="synced")` returns rows descending by `last_synced_at`.
- `list_repos(sort="stars", reverse=True)` returns ascending by stars.
- `list_repos(sort="captured")` returns descending by `captured_at`.
- `list_repos(topics=["ml"])` filters case-insensitively (exact match).
- `list_repos(topics=["ml", "nlp"])` OR-semantics, deduplicated.
- `list_repos(limit=5)` caps the result.
- `list_topic_counts()` sorts by count desc then topic asc; `limit` and
  `reverse` honored.
- **Update existing test** at `tests/test_storage.py:338`: assertion
  `ids == sorted(ids)` (github_id ascending) changes to
  `last_synced_at` descending. Keeps the bare `list_repos()` call — the point
  is to assert the new default contract.

### Integration / smoke — `tests/test_cli_list_smoke.py`

- End-to-end: populate a tmp db with 3 repos via real `Storage.upsert`, run
  `capxure.cli.main(["ls"])`, capture stdout/stderr, assert pretty output
  contains the repos in `synced DESC` order.
- `cap ls --help` exits 0 and mentions the documented flags.

## Documentation touch-ups

- `README.md` `## CLI` section: add `cap ls` usage alongside `cap capture`.
- `README.md` library-usage paragraph around line 96: note the new default
  ordering of `list_repos()` to avoid surprising library consumers.
- `docs/superpowers/specs/2026-04-22-sqlite-storage-migration-design.md`: the
  single sentence that declares `github_id ASC` as the `list_repos` order gets
  a follow-up line noting the 2026-04-24 change.
