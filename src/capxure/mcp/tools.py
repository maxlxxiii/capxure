"""Pure tool handler functions. Each takes a Database and returns a JSON-serializable dict/list.

Handlers are deliberately thin wrappers over RepoStore / NoteStore so they can
be tested in isolation without spinning up the MCP runtime.
"""
from __future__ import annotations

# Tool handlers will be added in subsequent tasks.
