"""Stars subcommand. Bulk-captures a user's starred GitHub repos."""
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
    Storage,
    process_repo,
)


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
    # Preflight: token required (same convention as `cap capture`).
    token = _resolve_token()
    if token is None:
        print("error: GITHUB_TOKEN or GH_TOKEN must be set", file=sys.stderr)
        return 1

    db_path = _resolve_db_path(args.data_dir)
    storage = Storage(db_path=db_path) if db_path is not None else Storage()

    user_label = args.user if args.user else "the authenticated user"

    async def _run() -> tuple[int, int, int]:
        async with GitHubClient(token=token) as github:
            starred = await github.list_starred(user=args.user, limit=args.limit)

            urls = [t[2] for t in starred]
            already = storage.existing_urls(urls)
            new_items = [t for t in starred if t[2] not in already]

            if not _confirm(
                total=len(starred),
                already=len(already),
                new=len(new_items),
                yes=args.yes,
                quiet=args.quiet,
                user_label=user_label,
                limit=args.limit,
            ):
                return (0, len(already), 0)

            captured = 0
            failed = 0
            for owner, name, url in new_items:
                result = await process_repo(
                    url,
                    github=github,
                    storage=storage,
                    on_status=_silent_status,
                )
                if result.outcome is not None:
                    captured += 1
                    if not args.quiet:
                        print(f"✓ {owner}/{name}", file=sys.stderr, flush=True)
                else:
                    failed += 1
                    if not args.quiet:
                        tag = result.error or "error"
                        print(f"✗ {owner}/{name} ({tag})", file=sys.stderr, flush=True)

            return (captured, len(already), failed)

    try:
        captured, already_count, failed = asyncio.run(_run())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    print(
        f"captured: {captured}, already had: {already_count}, failed: {failed}",
        file=sys.stderr,
    )
    return 0 if failed == 0 else 1


# --- helpers ---

def _resolve_token() -> str | None:
    """Return the resolved GitHub token, or None if neither env var is set.

    Empty-string env values are treated as unset.
    """
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _resolve_db_path(data_dir_flag: str | None) -> Path | None:
    if data_dir_flag is None:
        return None
    return (Path(data_dir_flag).expanduser().resolve()) / "capxure.db"


def _silent_status(message: str, severity: Severity) -> None:
    """StatusCallback that drops everything — per-repo log lines come from this handler,
    not from process_repo's internal status events."""
    return None


def _confirm(
    *,
    total: int,
    already: int,
    new: int,
    yes: bool,
    quiet: bool,
    user_label: str,
    limit: int | None,
) -> bool:
    """Show the diff breakdown and get user confirmation.

    Returns True to proceed, False to abort. Honors --yes and refuses non-TTY input.
    """
    if yes:
        if not quiet:
            print(f"running with --yes ({new} to capture)", file=sys.stderr)
        return True

    if not sys.stdin.isatty():
        print(
            "error: refusing to run interactively without --yes (no TTY)",
            file=sys.stderr,
        )
        return False

    limit_note = f" (limited to {limit})" if limit is not None else ""
    print(f"Found {total} starred repos for {user_label}{limit_note}.", file=sys.stderr)
    print(f"  · {already} already captured", file=sys.stderr)
    print(f"  · {new} new", file=sys.stderr)
    answer = input(f"Capture {new} repos? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer")
    if n <= 0:
        raise argparse.ArgumentTypeError(f"{value!r} must be a positive integer")
    return n
