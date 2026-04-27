"""`cap note` subcommand group: routes to add and ls with smart-dispatch."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from capxure.cli.note import capture, ls


_KNOWN_VERBS = ("add", "ls")


def build_parser() -> argparse.ArgumentParser:
    """Build the `cap note` parser with add and ls subparsers."""
    parser = argparse.ArgumentParser(
        prog="cap note",
        description="Quick-capture notes (add, ls).",
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="{add,ls}")
    capture.register(subparsers)
    ls.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `cap note`. Argv is the args after `note`."""
    args_list = list(argv) if argv is not None else sys.argv[1:]

    # Smart dispatch: `cap note "thought"` -> `cap note add "thought"`.
    # Trigger: first arg exists, isn't a flag, isn't a known verb.
    if (
        args_list
        and not args_list[0].startswith("-")
        and args_list[0] not in _KNOWN_VERBS
    ):
        args_list = ["add", *args_list]

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
