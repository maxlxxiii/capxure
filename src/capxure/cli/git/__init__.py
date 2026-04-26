"""`cap git` subcommand group: routes to capture, ls, stars."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from capxure.cli.git import capture, ls, stars


def build_parser() -> argparse.ArgumentParser:
    """Build the `cap git` parser with capture, ls, stars subparsers."""
    parser = argparse.ArgumentParser(
        prog="cap git",
        description="GitHub repo capture commands.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="{capture,ls,stars}")
    capture.register(subparsers)
    ls.register(subparsers)
    stars.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `cap git`. Argv is the args after `git`."""
    args_list = list(argv) if argv is not None else sys.argv[1:]

    # Smart dispatch: `cap git owner/repo` -> `cap git capture owner/repo`.
    if args_list and "/" in args_list[0] and not args_list[0].startswith("-"):
        args_list = ["capture", *args_list]

    parser = build_parser()
    if not args_list:
        parser.print_usage(sys.stderr)
        return 2

    args = parser.parse_args(args_list)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_usage(sys.stderr)
        return 2
    return handler(args)
