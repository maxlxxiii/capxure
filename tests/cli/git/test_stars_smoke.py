"""End-to-end smoke test for `cap stars --help`."""
from __future__ import annotations

import subprocess
import sys


def test_stars_help_exits_zero_and_lists_flags():
    """`python -m capxure.cli git stars --help` prints usage with all flags."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli", "git", "stars", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    out = result.stdout
    # Positional
    assert "user" in out
    # Flags
    assert "--limit" in out
    assert "--quiet" in out
    assert "--yes" in out or "-y" in out
    assert "--data-dir" in out


def test_stars_top_level_help_lists_subcommand():
    """`cap git --help` mentions `stars` alongside the other subcommands."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli", "git", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "stars" in result.stdout
