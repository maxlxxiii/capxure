# cap mcp — Read-Only MCP Server for capxure

Date: 2026-04-27
Status: Approved

## Goal

Add an MCP server that exposes capxure's captured repos and notes to a local AI client (Claude Code as the first consumer), so the assistant can search and retrieve from the library while coding. The server is a thin read-only query surface over the existing `Database` / `RepoStore` / `NoteStore`.

## Non-Goals

- **Write tools.** No `capture_repo` over MCP. Ingest stays in the CLI.
- **Network transport.** Stdio only. No HTTP / SSE.
- **Multi-user / auth.** Single-user, local process.
- **Embeddings / semantic search.** SQLite FTS5 (BM25-ranked lexical search) is the v1 substrate — fast, deterministic, zero-dependency. Embeddings are a possible later evolution if FTS5 proves insufficient.
- **Notes ↔ repo linking.** Notes are searched but not joined to repos. Out of scope.
- **Re-ranking / hybrid search.** Pure BM25 with column weighting.

## Architecture

### Module layout

```
src/capxure/mcp/
    __init__.py      # exports server factory
    server.py        # MCP server + tool registration
    tools.py         # tool handlers (thin — delegate to RepoStore/NoteStore)
src/capxure/cli/mcp.py    # `cap mcp` subcommand: spawns stdio server
```

### Top-level CLI

`cap mcp` is a third sibling under the top-level parser, alongside `git` and `note`. It accepts `--data-dir` mirroring `cap git capture` / `cap git ls`. The router in `src/capxure/cli/__init__.py` adds an `mcp` branch.

### Invocation

Claude Code registers `cap mcp` like any other stdio MCP server in its settings:

```json
{ "command": "cap", "args": ["mcp"] }
```

### Process lifecycle

- `cap mcp` opens a single `Database` at startup and reuses it for the process lifetime. SQLite WAL handles read concurrency cleanly; tools never write.
- The server runs the MCP stdio event loop until EOF on stdin (the standard MCP shutdown signal) or a signal.
- On shutdown (EOF, SIGTERM, KeyboardInterrupt), `Database.__exit__` runs and closes the connection.
- Server logs go to stderr (stdio is reserved for the MCP protocol). One line per tool call: tool name, duration, result count or error class.

### Dependencies

Add the official `mcp` Python SDK to `pyproject.toml`. No other new deps.

## Tool Surface

Six tools, all read-only. Inputs and outputs are JSON. Validation happens at the tool boundary via the SDK's typed-signature → JSON Schema mapping. Errors raise; the MCP runtime maps them to error responses.

### 1. `search_repos(query, topics?, language?, k?) → list[repo_hit]`

| Field | Type | Notes |
|---|---|---|
| `query` | string, required | FTS5 query. Empty → error. Pass-through to FTS5; tool description documents that plain words work fine and FTS5 operators are accepted. |
| `topics` | list[string], optional | OR'd, case-insensitive exact match. Matches existing `RepoStore.list_repos` semantics. |
| `language` | string, optional | Exact match on `repos.language`. |
| `k` | int, optional, default 20, max 100 | Result cap. Server-side clamp. |

Hit shape:
```
{
  "owner": str,
  "name": str,
  "full_name": str,
  "url": str,
  "language": str | null,
  "stars": int,
  "description": str | null,
  "snippet": str,    // FTS5 highlight on readme_content; "" if match was on name/description only
  "score": float     // BM25; lower = more relevant
}
```

Results sorted by BM25 ascending. Claude treats list order as authoritative.

Implementation: `RepoStore.search(query, topics, language, k) → list[Hit]`.

### 2. `get_readme(owner, name) → {owner, name, readme_content} | null`

Returns the full README. `readme_content` may be `null` (some captured repos genuinely have no README). Whole-object `null` only if the repo isn't in the library.

### 3. `get_repo(owner, name) → repo_object | null`

Full structured metadata, no README body. Includes `topics` as a list. Same shape as the `Repo` dataclass minus `readme_content` and `readme_sha`. Cheap call when Claude already has the owner/name and just wants metadata.

### 4. `list_topics(prefix?, min_count?, max_count?, order?, limit?) → list[{topic, count}]`

| Field | Type | Notes |
|---|---|---|
| `prefix` | string, optional | Case-insensitive prefix match on topic. |
| `min_count` | int, optional | `HAVING count >= min_count`. |
| `max_count` | int, optional | `HAVING count <= max_count`. |
| `order` | enum, default `"count_desc"` | One of `"count_desc"`, `"count_asc"`, `"topic_asc"`. |
| `limit` | int, optional, default 50, max 500 | Result cap. |

Filters compose in SQL: `WHERE` for prefix, `HAVING` for the count bounds, `ORDER BY` for the order arg.

Implementation: extend `RepoStore.list_topic_counts` with `prefix`, `min_count`, `max_count`, `order`, `limit`. Default behavior (no new args) is unchanged — existing callers see exactly today's results.

### 5. `search_notes(query, sources?, k?) → list[note_hit]`

| Field | Type | Notes |
|---|---|---|
| `query` | string, required | FTS5 query. |
| `sources` | list[string], optional | OR'd, case-insensitive exact match against `notes.source`. |
| `k` | int, optional, default 20, max 100 | Result cap. |

Hit shape:
```
{
  "id": int,
  "snippet": str,            // FTS5 highlight on notes.content
  "annotation": str | null,  // returned in full (short by design)
  "source": str | null,
  "source_locator": str | null,
  "captured_at": str
}
```

Implementation: `NoteStore.search(query, sources, k) → list[NoteHit]`.

### 6. `list_sources(prefix?, min_count?, max_count?, order?, limit?) → list[{source, count}]`

Exact mirror of `list_topics` shape, running over `notes.source` (excluding NULL). Lets Claude do "what sources do I have notes from?" before searching, so it picks deliberate filter values instead of guessing names. Same parameter signature so the mental model transfers.

Implementation: `NoteStore.list_source_counts(prefix, min_count, max_count, order, limit)`.

### Cross-cutting tool behavior

- All numeric inputs (`k`, `limit`, `min_count`, `max_count`) are clamped server-side. Client-supplied bounds are not trusted.
- `query` strings pass through to FTS5 verbatim. The tool description tells Claude that plain words work and FTS5 operators are accepted.
- Snippet markers are `<<` / `>>` with `...` ellipsis and a ~32-token window: `snippet(<table>, <col_idx>, '<<', '>>', '...', 32)`.
- Snippets always come from the long-form column (`readme_content` for repos, `content` for notes). When the match is only on a short column (full_name, source), the snippet is empty — Claude can see the match from the other returned fields.

## Schema Changes (v3 migration)

Schema version increments to 3. `db.py` already documents the migration pattern (the `_MIGRATIONS[v]` body is duplicated into `_SCHEMA_SQL` so fresh installs run the schema once and existing dbs run migrations only). v3 follows that pattern exactly.

### New objects

```sql
-- Repos FTS: full_name + description + readme_content
CREATE VIRTUAL TABLE repos_fts USING fts5(
    full_name, description, readme_content,
    content='repos', content_rowid='id',
    tokenize='porter unicode61'
);

-- Notes FTS: content + annotation + source
CREATE VIRTUAL TABLE notes_fts USING fts5(
    content, annotation, source,
    content='notes', content_rowid='id',
    tokenize='porter unicode61'
);
```

External-content tables: no data duplication, just an index over the existing rows.

### Triggers

Six triggers — three per table — keep the FTS index in sync automatically. The `RepoStore.upsert` and `NoteStore` write paths don't change.

```sql
-- Repos triggers
CREATE TRIGGER repos_ai AFTER INSERT ON repos BEGIN
    INSERT INTO repos_fts(rowid, full_name, description, readme_content)
    VALUES (new.id, new.full_name,
            COALESCE(new.description, ''),
            COALESCE(new.readme_content, ''));
END;

CREATE TRIGGER repos_ad AFTER DELETE ON repos BEGIN
    INSERT INTO repos_fts(repos_fts, rowid, full_name, description, readme_content)
    VALUES ('delete', old.id, old.full_name,
            COALESCE(old.description, ''),
            COALESCE(old.readme_content, ''));
END;

CREATE TRIGGER repos_au AFTER UPDATE ON repos BEGIN
    INSERT INTO repos_fts(repos_fts, rowid, full_name, description, readme_content)
    VALUES ('delete', old.id, old.full_name,
            COALESCE(old.description, ''),
            COALESCE(old.readme_content, ''));
    INSERT INTO repos_fts(rowid, full_name, description, readme_content)
    VALUES (new.id, new.full_name,
            COALESCE(new.description, ''),
            COALESCE(new.readme_content, ''));
END;

-- Notes triggers (analogous, with content + annotation + source)
CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, content, annotation, source)
    VALUES (new.id, new.content,
            COALESCE(new.annotation, ''),
            COALESCE(new.source, ''));
END;

CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content, annotation, source)
    VALUES ('delete', old.id, old.content,
            COALESCE(old.annotation, ''),
            COALESCE(old.source, ''));
END;

CREATE TRIGGER notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content, annotation, source)
    VALUES ('delete', old.id, old.content,
            COALESCE(old.annotation, ''),
            COALESCE(old.source, ''));
    INSERT INTO notes_fts(rowid, content, annotation, source)
    VALUES (new.id, new.content,
            COALESCE(new.annotation, ''),
            COALESCE(new.source, ''));
END;
```

### Backfill (migration only — fresh installs skip)

```sql
INSERT INTO repos_fts(rowid, full_name, description, readme_content)
SELECT id, full_name,
       COALESCE(description, ''),
       COALESCE(readme_content, '')
FROM repos;

INSERT INTO notes_fts(rowid, content, annotation, source)
SELECT id, content,
       COALESCE(annotation, ''),
       COALESCE(source, '')
FROM notes;
```

### Migration runner

`_MIGRATIONS[3]` body executes:
1. `CREATE VIRTUAL TABLE` for both FTS tables.
2. Backfill `INSERT … SELECT` for both.
3. Six `CREATE TRIGGER`.
4. `PRAGMA user_version = 3`.

`_SCHEMA_SQL` for fresh installs gets the same FTS tables + triggers (but not the backfill — there's nothing to backfill on a fresh db) appended after the existing v2 contents.

### Ranking

- Repos: `bm25(repos_fts, 10.0, 5.0, 1.0)` — full_name 10×, description 5×, README 1×. Prevents long READMEs from drowning out direct name matches.
- Notes: `bm25(notes_fts, 1.0, 3.0, 8.0)` — content 1×, annotation 3×, source 8×. Notes attributed to "karpathy" rank above notes that mention him in passing.

### No backwards-compat concern

Existing dbs migrate cleanly. The feature is read-only and the underlying `repos` / `notes` tables are untouched.

## Error Handling

| Condition | Response |
|---|---|
| Repo not found in `get_readme` / `get_repo` | Return `null`. Not an error — Claude learns the repo isn't captured. |
| Invalid input (missing required, wrong type, `k`/`limit` out of bounds, unknown `order` value) | MCP error response with a clear message. Validation at the tool boundary via Pydantic / typed signatures from the SDK. |
| Malformed FTS5 query (e.g., unbalanced quote) | Catch the SQLite `OperationalError`, return an MCP error with a hint that the query is invalid FTS5 syntax. |
| DB-level errors (connection lost, disk full) | Propagate as MCP error; the server stays up to handle the next call. |
| `UnsupportedSchemaError` at startup (db is from a newer capxure) | Log and exit 1 before serving anything. Don't half-run. |

## Testing

| Layer | Tests |
|---|---|
| Store-layer | `RepoStore.search` (FTS5 hits, topic/language filters, `k` clamp, BM25 ordering, snippet shape, empty query → error). `NoteStore.search` (analogous, with sources filter). Extended `RepoStore.list_topic_counts` and new `NoteStore.list_source_counts` (prefix, min_count, max_count, order, limit, defaults unchanged). |
| Migration | Start with a v2 db containing several repos + notes (with NULL `description`, NULL `annotation`, NULL `source` mixed in). Run the migration. Assert FTS5 tables exist with the right rowids. Assert post-migration insert / update / delete on the underlying tables keeps FTS in sync (drives all three trigger paths per table). |
| Tool handler | Thin: call the handler with a `Database`-backed temp db, assert returned shape and field types. No round-trip through the MCP protocol. |
| End-to-end smoke | Spawn `cap mcp` as a subprocess, send the standard MCP `initialize` → `tools/list` → `tools/call` (`search_repos`) sequence, assert the response shape. One test, just to prove the wiring. Mirrors the `cap note` smoke tests. |
| Schema regression | Extend the existing v1→v2 migration tests with a v2→v3 case using the same harness. |

## Out of Scope (Future)

- Embeddings / semantic search over README content. Add a vector column or sidecar table only if BM25 proves insufficient against real Claude Code traffic.
- Notes ↔ repo linking (FK from `notes` to `repos`).
- Write tools (capture via MCP).
- HTTP/SSE transport, multi-user, auth.
- Aggregations beyond `list_topics` / `list_sources` (e.g., `count_repos(filters)`, `repo_activity(window)`).
- A `list_languages` discovery tool. Topics are more discriminating; can add later if needed.
