"""`cap mcp` subcommand: spawn a stdio MCP server."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cap mcp",
        description="Run capxure as a stdio MCP server.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directory containing capxure.db (defaults to platformdirs location).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `cap mcp`. Argv is the args after `mcp`."""
    args_list = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(args_list)

    db_path = (
        (Path(args.data_dir).expanduser().resolve() / "capxure.db")
        if args.data_dir is not None else None
    )

    # Import lazily so `cap --help` doesn't pay the mcp-import cost.
    from capxure.mcp import build_server

    app, db = build_server(db_path)
    try:
        app.run("stdio")
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    finally:
        db.close()
    return 0
