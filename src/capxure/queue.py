"""Persistent processing queue. No TUI dependencies."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capxure.storage import DEFAULT_DATA_DIR


class Queue:
    """Manages data/queue.json as an ordered list."""

    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR) -> None:
        self._data_dir = data_dir
        self._queue_path = data_dir / "queue.json"

    def _load(self) -> list[dict[str, Any]]:
        """Read queue from disk. Returns [] if missing or corrupt."""
        if not self._queue_path.exists():
            return []
        try:
            text = self._queue_path.read_text(encoding="utf-8")
            data = json.loads(text) if text.strip() else []
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        """Atomic write: tmp file + rename."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._queue_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(self._queue_path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def add(
        self,
        url: str,
        owner: str,
        repo: str,
        *,
        priority: str = "normal",
        source: str = "manual",
    ) -> bool:
        """Add a single entry. Returns True if added, False if duplicate."""
        entries = self._load()

        # Dedup by URL
        for entry in entries:
            if entry["url"] == url:
                return False

        new_entry = {
            "key": f"{owner}--{repo}",
            "url": url,
            "status": "pending",
            "priority": priority,
            "source": source,
            "added_at": self._now(),
            "last_attempt": None,
            "error": None,
        }

        if priority == "manual":
            entries.insert(0, new_entry)
        else:
            entries.append(new_entry)

        self._save(entries)
        return True

    def bulk_add(
        self,
        items: list[tuple[str, str, str]],
        *,
        source: str,
    ) -> tuple[int, int]:
        """Add multiple (owner, repo, url) entries. Returns (added, skipped)."""
        entries = self._load()
        existing_urls = {e["url"] for e in entries}

        added = 0
        for owner, repo, url in items:
            if url in existing_urls:
                continue
            entries.append({
                "key": f"{owner}--{repo}",
                "url": url,
                "status": "pending",
                "priority": "normal",
                "source": source,
                "added_at": self._now(),
                "last_attempt": None,
                "error": None,
            })
            existing_urls.add(url)
            added += 1

        self._save(entries)
        return added, len(items) - added

    def pop_next(self) -> dict[str, Any] | None:
        """Return first actionable entry (pending/failed/rate_limited), mark as processing."""
        entries = self._load()
        for entry in entries:
            if entry["status"] in ("pending", "failed", "rate_limited"):
                entry["status"] = "processing"
                entry["last_attempt"] = self._now()
                self._save(entries)
                return entry
        return None

    def update_status(
        self,
        key: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        """Update status of an entry by key."""
        entries = self._load()
        for entry in entries:
            if entry["key"] == key:
                entry["status"] = status
                entry["last_attempt"] = self._now()
                entry["error"] = error
                break
        self._save(entries)

    def mark_all_pending_as(self, status: str) -> None:
        """Batch-update all 'pending' entries to the given status."""
        entries = self._load()
        for entry in entries:
            if entry["status"] == "pending":
                entry["status"] = status
        self._save(entries)

    def get_stats(self) -> dict[str, int]:
        """Return count breakdown by status."""
        entries = self._load()
        stats: dict[str, int] = {
            "total": len(entries),
            "pending": 0,
            "done": 0,
            "failed": 0,
            "rate_limited": 0,
            "processing": 0,
        }
        for entry in entries:
            s = entry.get("status", "pending")
            if s in stats:
                stats[s] += 1
        return stats

    def get_pending_preview(self, n: int = 5) -> list[dict[str, Any]]:
        """Return first N pending entries for display."""
        entries = self._load()
        result = []
        for entry in entries:
            if entry["status"] == "pending" and len(result) < n:
                result.append(entry)
        return result

    def pending_count(self) -> int:
        """Count of pending entries (for status bar)."""
        entries = self._load()
        return sum(1 for e in entries if e["status"] == "pending")
