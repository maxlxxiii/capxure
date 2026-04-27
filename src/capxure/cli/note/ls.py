"""`cap note ls` handler — STUB. Real implementation in Task 5."""
from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("ls", help="List captured notes (stub).")
    p.set_defaults(handler=command)


def command(args: argparse.Namespace) -> int:
    raise NotImplementedError("ls is implemented in Task 5")
