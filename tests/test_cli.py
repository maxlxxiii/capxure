"""CLI tests. Kept layered: parser-level, handler-level, and an end-to-end smoke test."""
from __future__ import annotations

import subprocess
import sys


def test_cli_runs_as_module_with_no_args_exits_2():
    """`python -m capxure.cli` with no args → argparse complains about missing target."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
