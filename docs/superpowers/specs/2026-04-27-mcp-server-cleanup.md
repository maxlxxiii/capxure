# cap mcp — Post-Merge Cleanup

Date: 2026-04-27
Status: Pending

Follow-up items deferred from the cap mcp implementation (spec `2026-04-27-mcp-server-design.md`, plan `2026-04-27-mcp-server-plan.md`). All non-blocking; the feature works at HEAD `feb640f`.

## Items

### 1. Rename or split `tests/mcp/`

`tests/mcp/` was created in Task 1 for the schema v3 migration tests, then accumulated everything related to the cap mcp work. Current contents:

- `test_migration_v3.py` — schema, not MCP
- `test_topic_counts.py` — `RepoStore.list_topic_counts` extension, not MCP
- `test_source_counts.py` — `NoteStore.list_source_counts`, not MCP
- `test_repo_search.py` — `RepoStore.search`, not MCP (tested via direct method calls)
- `test_note_search.py` — `NoteStore.search`, not MCP
- `test_tools.py` — actually MCP tool handlers
- `test_server_smoke.py` — actually MCP end-to-end

Two reasonable options:

- **(a)** Rename the directory to `tests/search/` (the unifying theme is FTS5 + discovery). Keep the MCP-specific files there too.
- **(b)** Distribute: move migration test to `tests/db/test_migration_v3.py`, move `RepoStore.*` tests to `tests/git/`, move `NoteStore.*` tests to `tests/note/`, leave `test_tools.py` + `test_server_smoke.py` in `tests/mcp/`.

(b) is purer (each test sits next to the module it tests) but is a larger churn. Pick whichever looks better after a quick survey of the existing `tests/{cli,git,note}/` shapes.

### 2. Reconsider `tests/__init__.py`

Added in Task 7 to namespace `tests.mcp` so it stops shadowing the `mcp` SDK package. If item 1 above renames `tests/mcp/`, the shadowing risk goes away too. At that point, delete `tests/__init__.py` unless there's another reason to keep it (pytest doesn't require it).

### 3. Tighten BM25 ranking tests

Two existing tests pin a name-based assertion but not the ranking score itself, which means a future refactor could silently degrade ranking without test failures.

**`tests/mcp/test_repo_search.py::test_full_name_outranks_readme`** — currently asserts `hits[0].name == "react"`. Add `assert hits[0].score < hits[1].score` so a future change to the BM25 weights or the column order is caught directly.

**`tests/mcp/test_note_search.py::test_source_outranks_content_match`** — same treatment. Currently a 2-doc corpus with one source-match and one content-match. Stronger version: add a third "noise" doc that strongly matches `"karpathy"` in content (e.g., `"karpathy " * 50` with a different source) and assert the source-tagged note still wins. Pins the 8x source weight rather than just the IDF effect.

### 4. Drain subprocess stderr in `test_server_smoke.py`

Currently the smoke test sets `stderr=subprocess.PIPE` but never reads it. Two failure modes:

- If the server writes a startup error to stderr (schema problem, missing dep), the test hangs waiting for a stdout response that never comes, then times out silently after 5s. The error never surfaces.
- If the server's stderr buffer fills (~64KB on Linux), it deadlocks on its next write. Low risk for a short test, but a foot-gun for any expansion of the test.

Fix: drain stderr in the `finally` block and surface it on assertion failure. Either capture it on a thread or switch to `stderr=subprocess.DEVNULL` if you don't care.

### 5. Add `CHANGELOG.md`

The repo bumps version in commits (0.5.0 referenced) but has no `CHANGELOG.md` at the root. The `cap mcp` feature is a meaningful surface change that warrants a 0.6.0 (probably) entry. Either:

- Create `CHANGELOG.md` with retroactive entries for 0.5.0 plus the new `cap mcp` entry, or
- Just create it forward and let history live in git.

Bump `__version__` and `pyproject.toml` to whatever's appropriate (suggest 0.6.0 since this adds a new CLI surface and a new tool API, even though it's strictly additive).

### 6. Shared seeding helper for tests

`_insert_repo(db, **kw)` exists in both `tests/mcp/test_repo_search.py` and `tests/mcp/test_tools.py` with subtly different signatures. Extract a single helper into a shared module (`tests/mcp/_seed.py` or wherever item 1 lands the tests) so future test files don't drift further.

### 7. Decide whether `get_repo` should include `github_id`

`tools.get_repo` currently projects 15 of the 17 `Repo` fields, dropping `id` (correct — internal SQLite PK) and `readme_*` (correct — use `get_readme`). It also drops `github_id`, which is the stable external identity from GitHub.

Argument for including: lets a downstream consumer cross-reference with GitHub's API or dedupe against an external corpus.
Argument against: an LLM consumer reasons in `(owner, name)` terms; `github_id` is noise.

This is a judgment call about what the downstream RAG/AI consumer needs. Lean toward **include** unless there's a concrete reason not to — it's cheap and removes a future "why isn't this here?" question.

## Suggested execution order

Do them roughly bottom-up by blast radius:

1. Item 7 (one-line addition to a tool handler).
2. Item 5 (CHANGELOG creation; pure docs).
3. Item 3 (test tightening; isolated to two tests).
4. Item 4 (smoke test stderr drain; isolated to one file).
5. Item 6 (extract shared helper; mechanical refactor).
6. Item 1 + 2 together (test reorg; the biggest churn).

None of these are deeply coupled; you could do them all in a single session or break them into separate PRs.
