"""Awesome-list README parser. Extracts GitHub repo URLs."""

from __future__ import annotations

import re

# Matches https://github.com/{owner}/{repo} with optional .git or trailing slash.
# Lookahead ensures no further path segments (filters /issues, /pulls, /tree, etc.).
_REPO_URL_RE = re.compile(
    r"https?://github\.com/"
    r"([A-Za-z0-9_.-]+)"       # owner
    r"/"
    r"([A-Za-z0-9_.-]+?)"     # repo (lazy)
    r"(?:\.git)?"              # optional .git suffix
    r"/?"                      # optional trailing slash
    r"(?=[)\s\]#,;\"'>]|$)"   # must end at URL boundary
)


def extract_repo_urls(readme_markdown: str) -> list[tuple[str, str, str]]:
    """Extract (owner, repo, url) tuples from markdown content.

    Filters out:
    - Sub-resource links (/issues, /pulls, /wiki, /blob, /tree, etc.)
    - Org-only links (github.com/{org} with no repo)
    - gist.github.com links
    - Duplicates (by normalized owner/repo, first occurrence wins)
    """
    seen: set[tuple[str, str]] = set()
    results: list[tuple[str, str, str]] = []

    for m in _REPO_URL_RE.finditer(readme_markdown):
        owner, repo = m.group(1), m.group(2)
        key = (owner.lower(), repo.lower())
        if key in seen:
            continue
        seen.add(key)
        results.append((owner, repo, f"https://github.com/{owner}/{repo}"))

    return results
