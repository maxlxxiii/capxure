"""Stars subcommand. Bulk-captures a user's starred GitHub repos."""
from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "stars",
        help="Bulk-capture a user's starred repos.",
        description="Capture every starred repo for a GitHub user (auth'd self by default).",
    )
    p.add_argument(
        "user",
        nargs="?",
        default=None,
        help="GitHub username; omit for the authenticated user.",
    )
    p.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="Cap the number of starred entries walked (most-recent-first).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-repo log lines; the summary still prints.",
    )
    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (required when stdin is not a TTY).",
    )
    p.add_argument(
        "--data-dir",
        default=None,
        help="Directory to store capxure.db (defaults to platformdirs location).",
    )
    p.set_defaults(handler=command)


def command(args: argparse.Namespace) -> int:
    """Stub — full implementation lands in Task 5."""
    return 0


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer")
    if n <= 0:
        raise argparse.ArgumentTypeError(f"{value!r} must be a positive integer")
    return n
