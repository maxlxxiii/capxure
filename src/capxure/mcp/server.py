"""FastMCP server factory. Registers tool handlers and binds them to a Database."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from capxure.db import Database
from capxure.mcp import tools


def build_server(db_path: Path | None = None) -> tuple[FastMCP, Database]:
    """Build a FastMCP server bound to a Database.

    Returns (server, database). The caller is responsible for closing the
    database when the server shuts down.
    """
    db = Database(db_path=db_path) if db_path is not None else Database()
    app = FastMCP("capxure")

    @app.tool()
    def get_repo(owner: str, name: str) -> dict[str, Any] | None:
        """Return structured metadata for a captured GitHub repo (no README body).

        Returns null if the repo isn't in the library.
        """
        return tools.get_repo(db, owner=owner, name=name)

    @app.tool()
    def get_readme(owner: str, name: str) -> dict[str, Any] | None:
        """Return the full README of a captured GitHub repo.

        Returns null if the repo isn't captured. `readme_content` may itself
        be null for repos that genuinely have no README.
        """
        return tools.get_readme(db, owner=owner, name=name)

    @app.tool()
    def list_topics(
        prefix: str | None = None,
        min_count: int | None = None,
        max_count: int | None = None,
        order: str = "count_desc",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List topic names with their repo counts.

        `order` is one of "count_desc" (default), "count_asc", "topic_asc".
        `prefix` filters to topics starting with the given string (case-insensitive).
        `min_count` / `max_count` bound the result by topic frequency.
        Useful for discovering what topics exist before filtering search_repos.
        """
        return tools.list_topics(
            db, prefix=prefix, min_count=min_count, max_count=max_count,
            order=order, limit=limit,
        )

    @app.tool()
    def list_sources(
        prefix: str | None = None,
        min_count: int | None = None,
        max_count: int | None = None,
        order: str = "count_desc",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List note sources with their note counts.

        `order` is one of "count_desc" (default), "count_asc", "source_asc".
        Same shape as list_topics but over notes.source. NULL sources are excluded.
        Useful for discovering what authors/projects you've taken notes from.
        """
        return tools.list_sources(
            db, prefix=prefix, min_count=min_count, max_count=max_count,
            order=order, limit=limit,
        )

    @app.tool()
    def search_repos(
        query: str,
        topics: list[str] | None = None,
        language: str | None = None,
        k: int = 20,
    ) -> list[dict[str, Any]]:
        """Search captured GitHub repos by README + name + description (FTS5).

        Plain words work fine; FTS5 operators (AND, OR, NEAR, "phrase") are also
        accepted. Hits are ranked by BM25 (lower score = more relevant).

        `topics` (list of strings, OR'd, case-insensitive exact match) and
        `language` (exact match) compose with the text query. `k` defaults to 20,
        capped at 100.

        Each hit contains a `snippet` from the README (`<<...>>` markers around
        matches) when the match is in the README; empty otherwise. Use get_readme
        to fetch the full README for a promising hit.
        """
        return tools.search_repos(
            db, query=query, topics=topics, language=language, k=k,
        )

    @app.tool()
    def search_notes(
        query: str,
        sources: list[str] | None = None,
        k: int = 20,
    ) -> list[dict[str, Any]]:
        """Search captured notes by content + annotation + source (FTS5).

        Plain words work; FTS5 operators are accepted. BM25 weights source 8x,
        annotation 3x, content 1x — notes attributed to a source rank above
        notes that just mention it.

        `sources` (list, OR'd, case-insensitive exact match against notes.source)
        composes with the text query. `k` defaults to 20, capped at 100.
        """
        return tools.search_notes(db, query=query, sources=sources, k=k)

    return app, db
