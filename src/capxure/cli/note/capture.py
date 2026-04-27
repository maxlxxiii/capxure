"""`cap note add` handler — capture content from positional or stdin."""
from __future__ import annotations

import argparse
import sys

from capxure.db import Database
from capxure.note import Note


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "add",
        help="Add a note (or 'cap note <content>' shortcut).",
        description="Add a note. Content from positional arg or piped stdin.",
    )
    p.add_argument(
        "content",
        nargs="?",
        help="Note content. Omit to read from stdin.",
    )
    p.add_argument("-a", "--annotation", help="Your commentary on the captured thing.")
    p.add_argument("-s", "--source", help="Where it came from (e.g. 'twitter').")
    p.add_argument("-L", "--loc", dest="source_locator",
                   help="Position within source (URL, page, timestamp).")
    p.add_argument("-k", "--kind", dest="kind_hint",
                   help="What kind of thing it is (free-form).")
    p.set_defaults(handler=command)


def command(args: argparse.Namespace) -> int:
    content = args.content
    if content is None:
        if sys.stdin.isatty():
            print(
                "error: no content provided (positional or via stdin)",
                file=sys.stderr,
            )
            return 2
        content = sys.stdin.read()
    # else: positional given — ignore stdin per unix convention.

    if not content.strip():
        print("error: content cannot be empty", file=sys.stderr)
        return 2

    try:
        note = _add_via_database(
            content,
            annotation=args.annotation,
            source=args.source,
            source_locator=args.source_locator,
            kind_hint=args.kind_hint,
        )
    except Exception as exc:  # noqa: BLE001 — surface any DB/store error uniformly
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"success: note {note.id} captured", file=sys.stderr)
    return 0


def _add_via_database(content: str, **kwargs) -> Note:
    """Indirection point for tests — open a Database and call notes.add."""
    with Database() as db:
        return db.notes.add(content, **kwargs)
