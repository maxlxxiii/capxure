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

    return app, db
