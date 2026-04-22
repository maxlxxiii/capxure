"""Shared pytest fixtures for capxure tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a GitHub metadata fixture by filename (without .json extension)."""
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def awesome_nodejs_metadata() -> dict:
    return load_fixture("sindresorhus-awesome-nodejs")


@pytest.fixture
def claude_mem_metadata() -> dict:
    return load_fixture("thedotmack-claude-mem")


@pytest.fixture
def chunky_metadata() -> dict:
    return load_fixture("GiovanniPasq-chunky")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Ephemeral SQLite db path; tmp_path is auto-cleaned after the test."""
    return tmp_path / "test.db"
