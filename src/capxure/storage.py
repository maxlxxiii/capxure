"""Local filesystem persistence for repo metadata and READMEs."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Any


class DeduplicationResult(StrEnum):
    """Outcome of checking a repo against local storage."""

    NEW = "new"
    ALREADY_UP_TO_DATE = "already_up_to_date"
    UPDATED = "updated"
    LOCAL_IS_NEWER = "local_is_newer"


def _resolve_default_data_dir() -> Path:
    """Resolve the default data directory per the project principle.

    Order:
      1. $CAPXURE_DATA_DIR environment variable (if set and non-empty)
      2. platformdirs.user_data_dir("capxure")
      3. RuntimeError if neither yields a usable path
    """
    env = os.environ.get("CAPXURE_DATA_DIR", "").strip()
    if env:
        return Path(env)
    from platformdirs import user_data_dir
    resolved = user_data_dir("capxure")
    if resolved:
        return Path(resolved)
    raise RuntimeError(
        "Cannot resolve default data dir: set $CAPXURE_DATA_DIR "
        "or ensure platformdirs is working"
    )


class Storage:
    """Manages metadata.json and readme files on disk."""

    def __init__(self, data_dir: Path | None = None) -> None:
        """Initialize storage at a data directory.

        Args:
            data_dir: Where to store metadata.json and readmes/.
                Library consumers (e.g., future CLI, Cortex integration,
                custom scripts) should pass an explicit Path — this is the
                clean contract for embedding capxure in other tools.

                The no-argument default is intended for CLI/TUI end-users
                and resolves via, in order:
                  1. $CAPXURE_DATA_DIR environment variable
                  2. platformdirs.user_data_dir("capxure")
                  3. RuntimeError if neither is usable
        """
        if data_dir is None:
            data_dir = _resolve_default_data_dir()
        self._data_dir = data_dir
        self._metadata_path = data_dir / "metadata.json"
        self._readmes_dir = data_dir / "readmes"

    def ensure_directories(self) -> None:
        """Create data/ and data/readmes/ if they don't exist."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._readmes_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(owner: str, repo: str) -> str:
        """Build the 'owner--repo' storage key."""
        return f"{owner}--{repo}"

    def load_metadata(self) -> dict[str, Any]:
        """Read and return the full metadata dict. Returns {} if missing or corrupt."""
        if not self._metadata_path.exists():
            return {}
        try:
            text = self._metadata_path.read_text(encoding="utf-8")
            return json.loads(text) if text.strip() else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save_metadata(self, metadata: dict[str, Any]) -> None:
        """Atomically write the full metadata dict (write tmp, then rename)."""
        self.ensure_directories()
        tmp_path = self._metadata_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(self._metadata_path)

    def check_dedup(
        self, metadata: dict[str, Any], new_entry: dict
    ) -> DeduplicationResult:
        """Check if a repo (by GitHub numeric id) already exists locally."""
        new_id = new_entry["id"]
        new_pushed = new_entry["pushed_at"]

        for existing_entry in metadata.values():
            if existing_entry.get("id") == new_id:
                existing_pushed = existing_entry["pushed_at"]
                if existing_pushed == new_pushed:
                    return DeduplicationResult.ALREADY_UP_TO_DATE
                elif existing_pushed < new_pushed:
                    return DeduplicationResult.UPDATED
                else:
                    return DeduplicationResult.LOCAL_IS_NEWER

        return DeduplicationResult.NEW

    def find_key_by_id(
        self, metadata: dict[str, Any], github_id: int
    ) -> str | None:
        """Find the storage key for a repo by its GitHub numeric id."""
        for key, entry in metadata.items():
            if entry.get("id") == github_id:
                return key
        return None

    def save_readme(self, owner: str, repo: str, content: str) -> Path:
        """Write README content to data/readmes/{owner}--{repo}.md."""
        self.ensure_directories()
        path = self._readmes_dir / f"{self.make_key(owner, repo)}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def count_repos(self) -> int:
        """Return the number of repos in local storage."""
        return len(self.load_metadata())

    def upsert_entry(
        self, owner: str, repo: str, entry: dict, readme: str
    ) -> DeduplicationResult:
        """Full save operation: check dedup, then save metadata + readme if needed."""
        metadata = self.load_metadata()
        result = self.check_dedup(metadata, entry)

        if result in (
            DeduplicationResult.ALREADY_UP_TO_DATE,
            DeduplicationResult.LOCAL_IS_NEWER,
        ):
            return result

        # Handle rename: remove old key if the GitHub id exists under a different key
        old_key = self.find_key_by_id(metadata, entry["id"])
        if old_key is not None:
            del metadata[old_key]

        key = self.make_key(owner, repo)
        metadata[key] = entry
        self.save_metadata(metadata)
        self.save_readme(owner, repo, readme)
        return result
