"""Tests for RepoStore — repo queries against an injected connection."""
from __future__ import annotations

import pytest

from capxure.db import Database
from capxure.git.store import (
    DuplicateRepoNameError,
    Repo,
    RepoStore,
    UpsertOutcome,
)
from capxure.git.store import _sha256_hex  # noqa: F401  — re-export check


def _store(db_path):
    db = Database(db_path)
    return RepoStore(db.connection), db


def test_upsert_new_returns_new_outcome(db_path, claude_mem_metadata):
    store, db = _store(db_path)
    try:
        outcome = store.upsert(claude_mem_metadata, readme_content="# Hello")
        assert outcome is UpsertOutcome.NEW

        repo = store.get_repo_by_github_id(claude_mem_metadata["id"])
        assert isinstance(repo, Repo)
        assert repo.owner == claude_mem_metadata["owner"]["login"]
    finally:
        db.close()


def test_upsert_unchanged_on_repeat(db_path, claude_mem_metadata):
    store, db = _store(db_path)
    try:
        store.upsert(claude_mem_metadata, readme_content="# Hello")
        outcome = store.upsert(claude_mem_metadata, readme_content="# Hello")
        assert outcome is UpsertOutcome.UNCHANGED
    finally:
        db.close()


def test_list_repos_returns_inserted(db_path, claude_mem_metadata):
    store, db = _store(db_path)
    try:
        store.upsert(claude_mem_metadata, readme_content="# Hello")
        repos = store.list_repos()
        assert len(repos) == 1
        assert repos[0].owner == claude_mem_metadata["owner"]["login"]
    finally:
        db.close()


def test_count_repos_starts_at_zero(db_path):
    store, db = _store(db_path)
    try:
        assert store.count_repos() == 0
    finally:
        db.close()


def test_duplicate_repo_name_raises(db_path, claude_mem_metadata):
    store, db = _store(db_path)
    try:
        store.upsert(claude_mem_metadata, readme_content="# Hello")
        twin = dict(claude_mem_metadata)
        twin["id"] = claude_mem_metadata["id"] + 1  # Different github_id, same owner/name.
        with pytest.raises(DuplicateRepoNameError):
            store.upsert(twin, readme_content="# Hello")
    finally:
        db.close()
