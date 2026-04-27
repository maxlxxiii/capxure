"""`cap note ls` handler — pretty (cards) on TTY, plain (TSV) when piped."""
from __future__ import annotations

import argparse
import re
import sys
from typing import Literal

from capxure.db import Database
from capxure.note import Note


Format = Literal["pretty", "plain"]

_PLAIN_COLLAPSE = re.compile(r"[\t\n\r]+")


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "ls",
        help="List captured notes, newest first.",
        description="List captured notes. Pretty cards on TTY; TSV when piped.",
    )
    p.add_argument(
        "--format",
        choices=("pretty", "plain"),
        default=None,
        help="Defaults to pretty on TTY, plain when piped.",
    )
    p.set_defaults(handler=command)


def command(args: argparse.Namespace) -> int:
    fmt: Format = args.format or ("pretty" if sys.stdout.isatty() else "plain")
    try:
        with Database() as db:
            notes = db.notes.list_notes()
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if fmt == "pretty":
        _emit_pretty(notes)
    else:
        _emit_plain(notes)
    return 0


def _emit_plain(notes: list[Note]) -> None:
    """TSV. 7 fields per row: id, captured_at, kind_hint, source,
    source_locator, annotation, content. Tabs/newlines in content and
    annotation collapsed to spaces. Null optionals → empty string."""
    for n in notes:
        fields = [
            str(n.id),
            n.captured_at,
            n.kind_hint or "",
            n.source or "",
            n.source_locator or "",
            _scrub(n.annotation),
            _scrub(n.content),
        ]
        print("\t".join(fields))


def _emit_pretty(notes: list[Note]) -> None:
    """Card format. Skip blank metadata lines. Blank line between cards."""
    for i, n in enumerate(notes):
        if i > 0:
            print()
        header = f"[{n.id}] {n.captured_at}"
        if n.kind_hint:
            header += f"  kind={n.kind_hint}"
        if n.source:
            header += f"  source={n.source}"
        print(header)
        if n.source_locator:
            print(f"loc: {n.source_locator}")
        if n.annotation:
            print(f"note: {n.annotation}")
        # n.content is guaranteed non-empty by NoteStore.add (strips + rejects empty).
        for line in n.content.splitlines():
            print(f"> {line}")


def _scrub(text: str | None) -> str:
    """Collapse tab/newline runs to a single space (for TSV safety)."""
    if text is None:
        return ""
    return _PLAIN_COLLAPSE.sub(" ", text)
