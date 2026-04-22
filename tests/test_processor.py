"""Integration smoke test for processor.process_repo() over the new storage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from capxure.processor import ProcessResult, Severity, process_repo
from capxure.storage import Storage, UpsertOutcome


@pytest.mark.asyncio
async def test_process_repo_new_then_unchanged(db_path, claude_mem_metadata):
    """First run captures; second run skips the README fetch via diff()."""
    storage = Storage(db_path)
    try:
        github = MagicMock()
        github.fetch_metadata = AsyncMock(return_value=claude_mem_metadata)
        github.fetch_readme = AsyncMock(return_value="# claude-mem\nreadme body\n")

        statuses: list[tuple[str, Severity]] = []

        def on_status(message: str, severity: Severity) -> None:
            statuses.append((message, severity))

        full_name = claude_mem_metadata["full_name"]
        url = f"https://github.com/{full_name}"

        # First call: NEW.
        first = await process_repo(url, github=github, storage=storage, on_status=on_status)
        assert isinstance(first, ProcessResult)
        assert first.outcome == UpsertOutcome.NEW
        assert storage.count_repos() == 1
        assert github.fetch_metadata.await_count == 1
        assert github.fetch_readme.await_count == 1

        # Second call: UNCHANGED — diff() short-circuits, no fetch_readme call.
        second = await process_repo(url, github=github, storage=storage, on_status=on_status)
        assert second.outcome == UpsertOutcome.UNCHANGED
        assert github.fetch_metadata.await_count == 2
        assert github.fetch_readme.await_count == 1, (
            "fetch_readme must NOT be called when diff() returns UNCHANGED"
        )
    finally:
        storage.close()
