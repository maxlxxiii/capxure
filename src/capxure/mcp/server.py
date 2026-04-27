"""FastMCP server factory. Registers tool handlers and binds them to a Database."""
from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from capxure.db import Database


def build_server(db_path: Path | None = None) -> tuple[FastMCP, Database]:
    """Build a FastMCP server bound to a Database.

    Returns (server, database). The caller is responsible for closing the
    database when the server shuts down.
    """
    db = Database(db_path=db_path) if db_path is not None else Database()
    app = FastMCP("capxure")

    # Tool registrations land here (filled in by later tasks).

    return app, db
