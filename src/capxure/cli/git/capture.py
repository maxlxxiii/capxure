"""Capture subcommand. Wraps capxure.process_repo with stderr progress reporting."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from capxure import (
    GitHubClient,
    ProcessResult,
    Severity,
    parse_github_url,
    process_repo,
)
from capxure.db import Database


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("capture", help="Capture a GitHub repo.")
    p.add_argument("target", help="GitHub URL or owner/repo.")
    p.add_argument(
        "--data-dir",
        default=None,
        help="Directory to store capxure.db (defaults to platformdirs location).",
    )
    p.set_defaults(handler=command)


def command(args: argparse.Namespace) -> int:
    # Preflight: token required. Keep the check above any network/IO.
    token = _resolve_token()
    if token is None:
        print("error: GITHUB_TOKEN or GH_TOKEN must be set", file=sys.stderr)
        return 1

    # Preflight: parse target so malformed input gets a distinct exit code.
    try:
        parse_github_url(args.target)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    db_path = _resolve_db_path(args.data_dir)
    with (Database(db_path=db_path) if db_path is not None else Database()) as db:
        async def _run() -> ProcessResult:
            async with GitHubClient(token=token) as github:
                return await process_repo(
                    args.target,
                    github=github,
                    repos=db.repos,
                    on_status=_print_status,
                )

        try:
            result = asyncio.run(_run())
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return 130

        return _exit_code_for(result)


# --- helpers ---

def _resolve_token() -> str | None:
    """Return the resolved GitHub token, or None if neither env var is set.

    Empty-string env values are treated as unset so an empty `GITHUB_TOKEN=` in a
    user's shell profile doesn't silently produce a bogus Authorization header.
    """
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _resolve_db_path(data_dir_flag: str | None) -> Path | None:
    """Map the --data-dir flag to a Database db_path, or None to use the library default."""
    if data_dir_flag is None:
        return None
    return (Path(data_dir_flag).expanduser().resolve()) / "capxure.db"


def _print_status(message: str, severity: Severity) -> None:
    """StatusCallback implementation. One line per event, stderr, flushed."""
    print(f"{severity.value}: {message}", file=sys.stderr, flush=True)


def _exit_code_for(result: ProcessResult) -> int:
    """Map a ProcessResult to a process exit code.

    Any non-None outcome (including dedup-skips) is success. outcome=None means the
    library caught an internal failure — exit 1. Finer distinctions aren't available
    at this boundary (see spec §5).
    """
    return 0 if result.outcome is not None else 1
