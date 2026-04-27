"""Note-domain dataclass and store. Single-file package — note has no client/processor."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = ["Note", "NoteStore"]


@dataclass(frozen=True)
class Note:
    id: int
    content: str
    annotation: str | None
    source: str | None
    source_locator: str | None
    kind_hint: str | None
    captured_at: str


class NoteStore:
    """Note-domain queries. Construct over a connection from `Database`."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def add(
        self,
        content: str,
        *,
        annotation: str | None = None,
        source: str | None = None,
        source_locator: str | None = None,
        kind_hint: str | None = None,
    ) -> Note:
        """Insert a note. Strips content; raises ValueError if empty after strip.
        Returns the inserted Note (with assigned id and DB-generated captured_at)."""
        stripped = content.strip()
        if not stripped:
            raise ValueError("content cannot be empty")
        cur = self._connection.execute(
            "INSERT INTO notes "
            "(content, annotation, source, source_locator, kind_hint) "
            "VALUES (?, ?, ?, ?, ?)",
            (stripped, annotation, source, source_locator, kind_hint),
        )
        return self._fetch_one(cur.lastrowid)

    def list_notes(self, *, limit: int | None = None) -> list[Note]:
        """All notes, newest-first by captured_at then id desc."""
        sql = "SELECT * FROM notes ORDER BY captured_at DESC, id DESC"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [self._row_to_note(r) for r in self._connection.execute(sql, params)]

    def count_notes(self) -> int:
        return self._connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

    def _fetch_one(self, note_id: int) -> Note:
        row = self._connection.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return self._row_to_note(row)

    def _row_to_note(self, row: sqlite3.Row) -> Note:
        return Note(
            id=row["id"],
            content=row["content"],
            annotation=row["annotation"],
            source=row["source"],
            source_locator=row["source_locator"],
            kind_hint=row["kind_hint"],
            captured_at=row["captured_at"],
        )
