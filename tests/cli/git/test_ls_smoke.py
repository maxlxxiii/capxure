"""End-to-end smoke tests for `cap ls` — real Database, real argv path."""
from __future__ import annotations

import subprocess
import sys

import pytest

from capxure.cli import main
from capxure.db import Database


def _seed(db_path, metadata_list):
    db = Database(db_path)
    try:
        for i, md in enumerate(metadata_list):
            db.repos.upsert(md, f"readme-{i}")
    finally:
        db.close()


def test_cli_ls_help_exits_zero():
    """`cap git ls --help` prints usage and exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "capxure.cli", "git", "ls", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "ls" in result.stdout.lower()
    # Every documented flag shows up in the help text.
    for flag in ("--sort", "--reverse", "--topic", "--limit", "--format"):
        assert flag in result.stdout


def test_cli_ls_plain_output_shape(
    monkeypatch, tmp_path, capsys,
    claude_mem_metadata, awesome_nodejs_metadata, chunky_metadata,
):
    """`cap ls --format plain` emits 9-field TSV rows, one per repo, in synced DESC order."""
    db_dir = tmp_path / "capxure-data"
    db_dir.mkdir()
    monkeypatch.setenv("CAPXURE_DATA_DIR", str(db_dir))

    _seed(db_dir / "capxure.db", [claude_mem_metadata, awesome_nodejs_metadata, chunky_metadata])

    assert main(["git", "ls", "--format", "plain"]) == 0
    out_lines = capsys.readouterr().out.rstrip("\n").splitlines()
    assert len(out_lines) == 3
    for line in out_lines:
        assert len(line.split("\t")) == 9


def test_cli_ls_pretty_output_contains_repos(
    monkeypatch, tmp_path, capsys,
    claude_mem_metadata, awesome_nodejs_metadata,
):
    """Forced pretty format produces a header + one row per repo on stdout."""
    db_dir = tmp_path / "capxure-data"
    db_dir.mkdir()
    monkeypatch.setenv("CAPXURE_DATA_DIR", str(db_dir))

    _seed(db_dir / "capxure.db", [claude_mem_metadata, awesome_nodejs_metadata])

    assert main(["git", "ls", "--format", "pretty"]) == 0
    out = capsys.readouterr().out
    # Header + separator + 2 data rows (at minimum; description wraps may add more).
    assert "owner" in out and "name" in out and "stars" in out
    assert claude_mem_metadata["owner"]["login"] in out
    assert awesome_nodejs_metadata["owner"]["login"] in out


def test_cli_ls_topics_plain(
    monkeypatch, tmp_path, capsys,
    claude_mem_metadata, awesome_nodejs_metadata, chunky_metadata,
):
    db_dir = tmp_path / "capxure-data"
    db_dir.mkdir()
    monkeypatch.setenv("CAPXURE_DATA_DIR", str(db_dir))
    _seed(db_dir / "capxure.db", [claude_mem_metadata, awesome_nodejs_metadata, chunky_metadata])

    assert main(["git", "ls", "topics", "--format", "plain"]) == 0
    lines = capsys.readouterr().out.rstrip("\n").splitlines()
    assert len(lines) >= 1
    for line in lines:
        parts = line.split("\t")
        assert len(parts) == 2
        assert parts[1].isdigit()


def test_cli_ls_empty_repos_pretty_writes_stderr_message(
    monkeypatch, tmp_path, capsys,
):
    db_dir = tmp_path / "capxure-data"
    db_dir.mkdir()
    monkeypatch.setenv("CAPXURE_DATA_DIR", str(db_dir))

    assert main(["git", "ls", "--format", "pretty"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No repos" in captured.err


def test_cli_ls_empty_repos_plain_is_silent(
    monkeypatch, tmp_path, capsys,
):
    db_dir = tmp_path / "capxure-data"
    db_dir.mkdir()
    monkeypatch.setenv("CAPXURE_DATA_DIR", str(db_dir))

    assert main(["git", "ls", "--format", "plain"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
