# Changelog

All notable changes to capxure are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] — 2026-04-27

### Added
- `cap mcp` — stdio MCP server exposing six tools (`search_repos`,
  `get_repo`, `get_readme`, `list_topics`, `search_notes`,
  `list_sources`) for AI/RAG consumers. See `README.md` for the
  Claude Code wiring snippet.
- FTS5 lexical search over repos and notes (schema v3) with BM25
  ranking. Column weights surfaced as named module constants
  (`_REPO_BM25_WEIGHTS`, `_NOTE_BM25_WEIGHTS`).
- `RepoStore.list_topic_counts` and `NoteStore.list_source_counts`
  for topic/source discovery with prefix, count-range, ordering, and
  limit filters.
- `tools.get_repo` projection now includes `github_id` so downstream
  consumers can cross-reference with the GitHub API.

### Changed
- Database context manager now rolls back on exception instead of
  committing partial state.
