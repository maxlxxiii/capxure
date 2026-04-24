"""Command-line interface for capxure. Thin consumer of the public library API."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level `cap` parser.

    Dispatch rule: the first positional is a capture target (handled by the capture
    subcommand). Future subcommands (list, show) will be registered on the subparsers
    group, and argparse routes to them when the first positional matches a registered
    name. Capture targets always contain `/`; subcommand names never will — so the two
    never collide.
    """
    parser = argparse.ArgumentParser(
        prog="cap",
        description="Capture GitHub repos locally.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # Capture is the default action: `cap owner/repo` invokes it without a keyword.
    # We register it here so --help lists it, and we also wire it as the fallback
    # below when the user types `cap owner/repo` directly.
    from capxure.cli import capture, list_
    capture.register(subparsers)
    list_.register(subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `cap` console script.

    Returns a process exit code. Accepts an `argv` list for testability.
    """
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        parser.print_help(sys.stderr)
        return 2

    # Dispatch rule: if the first positional contains `/`, it's a capture target.
    # Rewrite the argv to prepend the "capture" subcommand name so argparse can
    # route it through the registered handler.
    if "/" in argv[0]:
        argv = ["capture", *argv]

    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)
