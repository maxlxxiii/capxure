# cap stars — Bulk Capture from Starred Repos

Date: 2026-04-26
Status: Approved

## Goal

Add a `cap stars` subcommand that bulk-captures the starred repositories of a GitHub user (the authenticated user by default, or any user by username) by feeding their stars into the existing single-repo capture pipeline. The command is on-demand and idempotent: re-running it picks up new stars and skips repos already captured.

## Non-Goals

- **Continuous sync.** No tracking of unstars, no "last run" state, no scheduled execution. If the user wants periodic runs they can wrap the command in cron or a systemd timer.
- **Refreshing already-captured repos.** Dupes are skipped, never re-fetched. A separate command can address re-sync if it's needed later.
- **`starred_at` metadata.** GitHub can return when each repo was starred (via `Accept: application/vnd.github.star+json`); we deliberately skip it. The endpoint already returns reverse-chronological order, so `--limit N` gives effectively the same "recent stars" answer without a schema change.
- **Trending / discovery feeds.** GitHub's REST API has no trending endpoint, and the Search API approximation is its own feature. Out of scope.
- **Concurrency.** First version captures sequentially (one repo at a time), matching the existing `cap <url>` flow.
- **Webhooks / push-driven capture.** Out of scope.

## Command

```
cap stars [user] [--limit N] [--quiet] [--yes/-y]
```

| Form | Endpoint | Notes |
|---|---|---|
| `cap stars` | `GET /user/starred` | Authenticated user's stars |
| `cap stars <user>` | `GET /users/{user}/starred` | Public stars of `<user>` |

| Flag | Behavior |
|---|---|
| `--limit N` | Walk only the top N starred entries (most-recent-first, GitHub's default order). Diff and capture are computed against this truncated list. `N` must be a positive integer; argparse rejects `0` or negative values. |
| `--quiet` | Suppress per-repo log lines and the `--yes` acknowledgement. The final summary still prints. |
| `--yes`, `-y` | Skip the confirmation prompt. Required when stdin is not a TTY (e.g., cron). |

Auth is mandatory — the same `GITHUB_TOKEN` / `GH_TOKEN` resolution as `cap <url>`. Both forms use the token; auth lifts the rate limit to 5,000/hr and lets the auth'd-self form see private starred repos.

## Flow

1. **Parse args** → `(user_or_none, limit, quiet, yes)`.
2. **Resolve token** via the existing `_resolve_token`. Abort with the same error message as `cap <url>` if missing.
3. **Open `GitHubClient`** (existing async context manager).
4. **List starred** via new `client.list_starred(user, limit)`:
   - Walk pages of 100 via the `Link: rel="next"` header.
   - Yield `(owner, repo, html_url)` tuples. Stop at `limit`.
5. **Diff** via new `Storage.existing_urls(urls)` — single SQL `SELECT url FROM repos WHERE url IN (...)`. Returns the subset already captured.
6. **Confirm**:
   - If stdin is a TTY and `-y` was not passed: print the breakdown and read one line. Accept `y` / `yes` (case-insensitive). Anything else aborts with no work done.
   - If `-y`: skip the prompt. Unless `--quiet`, print a one-line acknowledgement (e.g., `running with --yes (166 to capture)`).
   - If stdin is not a TTY and `-y` was not passed: abort with `error: refusing to run interactively without --yes (no TTY)`. Non-TTY + `-y` is fine (cron path).
7. **Capture loop** over the new URLs (sequential):
   - Call existing `process_repo(url, ...)` per repo.
   - Unless `--quiet`: print `✓ owner/repo` on success, `✗ owner/repo (<tag>)` on per-repo failure. `<tag>` is a short error label — HTTP status for HTTP errors (`404`, `500`), the exception class name otherwise (`timeout`, `network`).
   - Increment `captured` / `failed` counters.
8. **Summary** (always printed, even with `--quiet`):
   ```
   captured: 164, already had: 47, failed: 2
   ```
9. **Exit code**: 0 if `failed == 0`, else 1.

### Confirmation prompt format

```
Found 213 starred repos for max-freeman.
  · 47 already captured
  · 166 new
Capture 166 repos? [y/N]
```

With `--limit 20`:

```
Found 213 starred repos (limited to 20).
  · 5 already captured
  · 15 new
Capture 15 repos? [y/N]
```

## Architecture

### New code

- **`src/capxure/cli/stars.py`** — subcommand handler. Mirrors `cli/capture.py`: arg parsing, token preflight, async runner.
- **`GitHubClient.list_starred(user: str | None, limit: int | None)`** on `src/capxure/github.py` — async paginator. Handles `Link` header, stops at `limit` if provided.
- **`Storage.existing_urls(urls: Iterable[str]) -> set[str]`** on `src/capxure/storage.py` — single-query existence check used for the diff step.

### Reused as-is

- `process_repo` for each individual capture. `cap stars` does not duplicate metadata/README fetching — it is purely an orchestrator that feeds URLs into the existing pipeline.
- `GitHubClient`, `Storage`, the auth/token resolver, the existing exception hierarchy.

### Wiring

The CLI subparser registry (alongside `capture` and `ls`) gains a `stars` entry pointing at the new handler.

The key design choice: **`cap stars` is a thin orchestrator over `process_repo`.** No new capture path, no new schema, no parallel DB writer. If `cap <url>` works correctly, `cap stars` works correctly — it is just a confirmed loop.

The only new I/O surface is `list_starred` (one new endpoint family) and `existing_urls` (one new query). Everything else is composition.

## Error Handling

| Where | Error | Behavior |
|---|---|---|
| Preflight | Missing token | Abort with the same message as `cap <url>` |
| List phase | 401 (invalid token) | Abort, surface `AuthenticationError` |
| List phase | 404 (user does not exist) | Abort with `error: user '<x>' not found` |
| List phase | 403 / 429 (rate limit) | Abort with rate-limit message + reset time |
| Capture loop | Per-repo 404 / transient 5xx | Log failure, continue, increment `failed` |
| Capture loop | 401 / 403 / 429 | Abort, print partial summary so far |
| Anywhere | `KeyboardInterrupt` | Clean exit, print partial summary, exit 130 |

### Idempotency guarantee

A re-run with the same args picks up exactly where the previous run left off. The diff step (`existing_urls`) is recomputed from the live DB on every invocation, so any failures from a prior run reappear as "new" and get retried; any captures that succeeded mid-batch are reported as "already had" and skipped.

## Testing

- **Unit — `list_starred`**: pagination via mocked `Link` header chaining; `--limit` truncation across page boundaries; empty starred list.
- **Unit — `existing_urls`**: empty input, partial overlap, full overlap.
- **Unit — confirmation prompt**: TTY + accept (`y`, `Y`, `yes`), TTY + reject (anything else aborts), non-TTY without `-y` (aborts with the right message), `-y` skip path. Patch `sys.stdin.isatty` and `input`.
- **Unit — list-phase error mapping**: 401, 404, 403/429 each abort with the expected user-facing message.
- **Integration — happy path**: full flow with a mocked `GitHubClient` returning a fake starred list of ~5 repos and the real `Storage`. Verify the diff computation, `-y` skip, the summary tally, and the resulting DB rows.
- **Integration — mid-batch failure**: one fake repo raises a non-fatal error during `process_repo`; assert the loop continues and `failed` counts it.
- **Smoke**: `cap stars --help` exits 0 and mentions `[user]`, `--limit`, `--quiet`, `-y`.

## Open Questions

None. All decisions resolved during brainstorming.
