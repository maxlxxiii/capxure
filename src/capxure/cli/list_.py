"""ls subcommand. Lists captured repos (or topic counts) from the local db."""
from __future__ import annotations

import argparse
import re
import sys
import textwrap
from typing import IO, Literal

from capxure.storage import Repo


Format = Literal["pretty", "plain"]


_PRETTY_OWNER_CAP = 20
_PRETTY_NAME_CAP = 30
_MIN_DESCRIPTION_WIDTH = 12

_WHITESPACE_RUN = re.compile(r"\s+")


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


def _resolve_format(explicit: Format | None, stream: IO[str]) -> Format:
    """Pick format: explicit flag wins; otherwise TTY detection."""
    if explicit is not None:
        return explicit
    return "pretty" if stream.isatty() else "plain"


def _truncate(value: str, width: int) -> str:
    """Right-truncate `value` to fit `width`, replacing the last character with … when clipped."""
    if len(value) <= width:
        return value
    if width <= 0:
        return ""
    return value[: width - 1] + "…"


def _clip_description(text: str | None, *, width: int) -> list[str]:
    """Wrap `text` to `width` and clip to at most 2 lines, with … on the second if clipped."""
    if not text:
        return [""]
    if width <= 0:
        return [""]
    wrapped = textwrap.wrap(text, width=width) or [""]
    if len(wrapped) <= 2:
        return wrapped
    # Clip to 2 lines; replace final char of line 2 with ….
    line_two = wrapped[1]
    if len(line_two) >= width:
        line_two = line_two[: width - 1] + "…"
    else:
        line_two = line_two + "…"
    return [wrapped[0], line_two]


def _short_date(iso: str) -> str:
    """Return the `YYYY-MM-DD` portion of an ISO timestamp string."""
    return iso[:10] if iso else ""


def _format_pretty_repos(repos: list[Repo], *, terminal_width: int) -> None:
    """Render a pretty repo table to stdout. Empty → message on stderr."""
    if not repos:
        print("No repos captured.", file=sys.stderr)
        return

    date_w = max(len("last_synced"), 10)  # header is 11 chars; values are YYYY-MM-DD (10)
    owner_vals = [_truncate(r.owner, _PRETTY_OWNER_CAP) for r in repos]
    name_vals = [_truncate(r.name, _PRETTY_NAME_CAP) for r in repos]
    stars_vals = [str(r.stars) for r in repos]

    owner_w = max(len("owner"), max(len(v) for v in owner_vals))
    name_w = max(len("name"), max(len(v) for v in name_vals))
    stars_w = max(len("stars"), max(len(v) for v in stars_vals))

    # Column gutters are single spaces. desc_w absorbs the remainder.
    fixed_width = date_w + 1 + owner_w + 1 + name_w + 1 + stars_w + 1
    desc_w = max(_MIN_DESCRIPTION_WIDTH, terminal_width - fixed_width)

    header = (
        f"{'last_synced':<{date_w}} "
        f"{'owner':>{owner_w}} "
        f"{'name':<{name_w}} "
        f"{'stars':>{stars_w}} "
        f"{'description':<{desc_w}}"
    )
    rule = (
        f"{'-' * date_w} "
        f"{'-' * owner_w} "
        f"{'-' * name_w} "
        f"{'-' * stars_w} "
        f"{'-' * desc_w}"
    )
    print(header)
    print(rule)

    for repo, owner_v, name_v, stars_v in zip(repos, owner_vals, name_vals, stars_vals):
        desc_lines = _clip_description(repo.description, width=desc_w)
        date_v = _short_date(repo.last_synced_at)
        first = (
            f"{date_v:<{date_w}} "
            f"{owner_v:>{owner_w}} "
            f"{name_v:<{name_w}} "
            f"{stars_v:>{stars_w}} "
            f"{desc_lines[0]:<{desc_w}}"
        )
        print(first)
        # Continuation line for wrapped descriptions (if any), indented under desc column.
        for extra in desc_lines[1:]:
            pad = " " * (fixed_width)
            print(f"{pad}{extra:<{desc_w}}")


def _format_pretty_topics(rows: list[tuple[str, int]], *, terminal_width: int) -> None:
    """Render a pretty topic count table to stdout. Empty → message on stderr."""
    if not rows:
        print("No topics captured.", file=sys.stderr)
        return

    count_vals = [str(c) for _, c in rows]
    count_w = max(len("count"), max(len(v) for v in count_vals))
    topic_w = max(len("topic"), max(len(t) for t, _ in rows))

    header = f"{'count':>{count_w}} {'topic':<{topic_w}}"
    rule = f"{'-' * count_w} {'-' * topic_w}"
    print(header)
    print(rule)
    for topic, count in rows:
        print(f"{count:>{count_w}} {topic:<{topic_w}}")


def _scrub_description(text: str | None) -> str:
    """Strip tabs/newlines/CR and collapse any whitespace run to a single space.

    Lossy but safe — plain output is tab-separated, so embedded tabs or newlines
    would break downstream `awk`/`cut`/`fzf` consumers. Callers of this helper
    know the trade-off.
    """
    if not text:
        return ""
    return _WHITESPACE_RUN.sub(" ", text).strip()


def _format_plain_repos(repos: list[Repo]) -> None:
    """One TSV row per repo, no header. Nine fields in spec order."""
    for r in repos:
        fields = [
            str(r.id),
            str(r.github_id),
            r.full_name,
            _scrub_description(r.description),
            r.language or "",
            str(r.stars),
            r.pushed_at or "",
            r.captured_at,
            r.last_synced_at,
        ]
        print("\t".join(fields))


def _format_plain_topics(rows: list[tuple[str, int]]) -> None:
    """One `topic<TAB>count` row per entry, no header."""
    for topic, count in rows:
        print(f"{topic}\t{count}")
