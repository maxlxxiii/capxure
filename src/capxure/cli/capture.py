"""Capture subcommand — stub replaced in Task 3."""
from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("capture", help="Capture a GitHub repo.")
    p.add_argument("target", help="GitHub URL or owner/repo.")
    p.add_argument("--data-dir", default=None, help="Directory to store capxure.db.")
    p.set_defaults(handler=lambda args: 0)
