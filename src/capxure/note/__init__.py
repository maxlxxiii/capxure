"""Note-domain dataclass and store. Single-file package — note has no client/processor."""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["Note", "NoteHit", "NoteStore"]


@dataclass(frozen=True)
class Note:
    id: int
    content: str
    annotation: str | None
    source: str | None
    source_locator: str | None
    kind_hint: str | None
    captured_at: str


@dataclass(frozen=True)
class NoteHit:
    id: int
    snippet: str
    annotation: str | None
    source: str | None
    source_locator: str | None
    captured_at: str
    score: float


class NoteStore:
    """Note-domain queries. Construct over a connection from `Database`."""

    _VALID_SOURCE_ORDERS: dict[str, str] = {
        "count_desc": "c DESC, source ASC",
        "count_asc":  "c ASC, source ASC",
        "source_asc": "source ASC",
    }

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

    def list_source_counts(
        self,
        *,
        prefix: str | None = None,
        min_count: int | None = None,
        max_count: int | None = None,
        order: str = "count_desc",
        limit: int | None = None,
    ) -> list[tuple[str, int]]:
        """Return (source, count) over notes with non-NULL source.

        Mirrors RepoStore.list_topic_counts: prefix + min/max_count + order all
        compose in SQL. NULL sources are always excluded — they're not a "source."
        """
        if order not in self._VALID_SOURCE_ORDERS:
            raise ValueError(
                f"order must be one of {sorted(self._VALID_SOURCE_ORDERS)}, got {order!r}"
            )
        order_clause = self._VALID_SOURCE_ORDERS[order]

        where_parts: list[str] = ["source IS NOT NULL"]
        params: list[Any] = []
        if prefix is not None:
            where_parts.append("LOWER(source) LIKE ?")
            params.append(prefix.lower() + "%")

        having_parts: list[str] = []
        if min_count is not None:
            having_parts.append("c >= ?")
            params.append(min_count)
        if max_count is not None:
            having_parts.append("c <= ?")
            params.append(max_count)

        sql = "SELECT source, COUNT(*) AS c FROM notes"
        sql += " WHERE " + " AND ".join(where_parts)
        sql += " GROUP BY source"
        if having_parts:
            sql += " HAVING " + " AND ".join(having_parts)
        sql += f" ORDER BY {order_clause}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        return [(row["source"], row["c"])
                for row in self._connection.execute(sql, params).fetchall()]

    def search(
        self,
        query: str,
        *,
        sources: Sequence[str] | None = None,
        k: int = 20,
    ) -> list[NoteHit]:
        """FTS5-backed search across content + annotation + source.

        BM25 weights: content 1x, annotation 3x, source 8x — notes attributed
        to a source rank above notes that just mention it in passing.
        Snippets are from notes.content (col 0).
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        k = max(1, min(k, 100))

        sql_parts = [
            "SELECT notes.id, notes.annotation, notes.source,",
            "       notes.source_locator, notes.captured_at,",
            "       COALESCE(snippet(notes_fts, 0, '<<', '>>', '...', 32), '') AS snippet,",
            "       bm25(notes_fts, 1.0, 3.0, 8.0) AS score",
            "FROM notes_fts",
            "JOIN notes ON notes.id = notes_fts.rowid",
            "WHERE notes_fts MATCH ?",
        ]
        params: list[Any] = [query]

        if sources:
            placeholders = ",".join("?" for _ in sources)
            sql_parts.append(f"AND LOWER(notes.source) IN ({placeholders})")
            params.extend(s.lower() for s in sources)

        sql_parts.append("ORDER BY score ASC LIMIT ?")
        params.append(k)

        try:
            rows = self._connection.execute(" ".join(sql_parts), params).fetchall()
        except sqlite3.OperationalError as exc:
            msg = str(exc)
            if "fts5: syntax error" in msg or "unterminated string" in msg:
                raise ValueError(f"invalid FTS5 query: {exc}") from exc
            raise
        return [
            NoteHit(
                id=row["id"],
                snippet=row["snippet"],
                annotation=row["annotation"],
                source=row["source"],
                source_locator=row["source_locator"],
                captured_at=row["captured_at"],
                score=row["score"],
            )
            for row in rows
        ]

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
