"""Command-line interface for capxure. Top-level router only."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from capxure.cli import git, mcp, note


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level `cap` parser. Routes to subcommand groups only."""
    parser = argparse.ArgumentParser(
        prog="cap",
        description="capxure — capture and organize.",
    )
    subparsers = parser.add_subparsers(dest="domain", metavar="{git,mcp,note}")

    git_parser = subparsers.add_parser(
        "git",
        help="GitHub repo capture commands (capture, ls, stars).",
        add_help=False,  # Defer --help to the git-level parser.
    )
    git_parser.set_defaults(_domain="git")

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run capxure as a stdio MCP server.",
        add_help=False,  # Defer --help to the mcp-level parser.
    )
    mcp_parser.set_defaults(_domain="mcp")

    note_parser = subparsers.add_parser(
        "note",
        help="Quick-capture notes (add, ls).",
        add_help=False,  # Defer --help to the note-level parser.
    )
    note_parser.set_defaults(_domain="note")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `cap` console script."""
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if not args_list:
        build_parser().print_usage(sys.stderr)
        return 2

    if args_list[0] == "git":
        return git.main(args_list[1:])
    if args_list[0] == "mcp":
        return mcp.main(args_list[1:])
    if args_list[0] == "note":
        return note.main(args_list[1:])

    # Anything else → argparse rejects with "invalid choice".
    build_parser().parse_args(args_list)
    return 2  # Unreachable: parse_args raises SystemExit on invalid input.
