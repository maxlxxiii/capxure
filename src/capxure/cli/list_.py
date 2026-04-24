"""ls subcommand. Lists captured repos (or topic counts) from the local db."""
from __future__ import annotations

import argparse
import sys
from typing import Literal


Format = Literal["pretty", "plain"]


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("ls", help="List captured repos or topics.")
    p.add_argument(
        "subject",
        nargs="?",
        default="repos",
        choices=["repos", "topics"],
        help="What to list (default: repos).",
    )
    p.add_argument(
        "-s",
        "--sort",
        choices=["synced", "captured", "stars"],
        default=None,
        help="Sort key for repos (default: synced).",
    )
    p.add_argument(
        "-r",
        "--reverse",
        action="store_true",
        help="Reverse sort direction (default DESC becomes ASC).",
    )
    p.add_argument(
        "-t",
        "--topic",
        dest="topics",
        action="append",
        default=[],
        help="Filter repos by topic (repeatable, OR semantics, case-insensitive).",
    )
    p.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help="Maximum number of rows (default: 10 in the CLI).",
    )
    p.add_argument(
        "--format",
        choices=["pretty", "plain"],
        default=None,
        dest="format",
        help="Override format auto-detection (TTY -> pretty, pipe -> plain).",
    )
    p.set_defaults(handler=command)


def command(args: argparse.Namespace) -> int:
    preflight = _preflight(args)
    if preflight != 0:
        return preflight

    # Wiring to Storage and renderers is added in Task 5. For now, return 0.
    return 0


# --- helpers ---


def _preflight(args: argparse.Namespace) -> int:
    """Validate flag combinations that argparse can't express. Returns an exit code."""
    if args.subject == "topics":
        if args.topics:
            print(
                "error: --topic is only valid with 'cap ls repos'",
                file=sys.stderr,
            )
            return 2
        if args.sort is not None:
            print(
                "error: --sort is only valid with 'cap ls repos'",
                file=sys.stderr,
            )
            return 2
    if args.limit is not None and args.limit <= 0:
        print("error: --limit must be a positive integer", file=sys.stderr)
        return 2
    return 0
