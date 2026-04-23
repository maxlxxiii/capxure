"""CLI tests. Kept layered: parser-level, handler-level, and an end-to-end smoke test."""
from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from capxure.cli import build_parser, main


def test_cli_runs_as_module_with_no_args_exits_2():
    """`python -m capxure.cli` with no args → argparse complains about missing target."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_parser_accepts_capture_subcommand_with_target():
    """`cap capture owner/repo` parses cleanly via the capture subparser."""
    parser = build_parser()
    args = parser.parse_args(["capture", "owner/repo"])
    assert args.subcommand == "capture"
    assert args.target == "owner/repo"
    assert args.data_dir is None


def test_parser_accepts_capture_with_data_dir_flag(tmp_path):
    parser = build_parser()
    args = parser.parse_args(["capture", f"--data-dir={tmp_path}", "owner/repo"])
    assert args.data_dir == str(tmp_path)
    assert args.target == "owner/repo"


def test_main_with_no_args_returns_2(capsys):
    """`cap` (no args) prints help to stderr and returns 2."""
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_main_help_flag_exits_zero(capsys):
    """`cap --help` goes through argparse which raises SystemExit(0)."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    # argparse prints usage + description to stdout on --help
    assert "cap" in out.lower()
