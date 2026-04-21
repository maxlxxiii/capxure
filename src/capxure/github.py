"""GitHub API client."""

from __future__ import annotations

import importlib.metadata
import re
from dataclasses import dataclass

import httpx


try:
    _VERSION = importlib.metadata.version("capxure")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "unknown"

_USER_AGENT = f"capxure/{_VERSION}"


# ── Exceptions ────────────────────────────────────────────────


class GitHubError(Exception):
    """Base exception for GitHub API errors."""


class AuthenticationError(GitHubError):
    """Raised when GITHUB_TOKEN is missing or invalid (401)."""


class NotFoundError(GitHubError):
    """Raised when a repo or resource does not exist (404)."""


class RateLimitExceededError(GitHubError):
    """Raised when API rate limit is exhausted (403)."""

    def __init__(self, message: str, reset_timestamp: int = 0) -> None:
        super().__init__(message)
        self.reset_timestamp = reset_timestamp


# ── Data types ────────────────────────────────────────────────


@dataclass(frozen=True)
class RateLimitInfo:
    """Snapshot of GitHub API rate limit status."""

    limit: int
    remaining: int
    reset_timestamp: int


# ── URL parsing ───────────────────────────────────────────────

_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def parse_github_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL.

    Raises ValueError if the URL is not a valid GitHub repo URL.
    """
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"Not a valid GitHub repo URL: {url!r}")
    return m.group(1), m.group(2)


# ── Client ────────────────────────────────────────────────────


class GitHubClient:
    """Async GitHub API client using httpx."""

    BASE_API = "https://api.github.com"
    BASE_RAW = "https://raw.githubusercontent.com"

    def __init__(self, token: str) -> None:
        self._token = token
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> GitHubClient:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"token {self._token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": _USER_AGENT,
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GitHubClient must be used as an async context manager")
        return self._client

    async def fetch_rate_limit(self) -> RateLimitInfo:
        """GET /rate_limit -> RateLimitInfo."""
        resp = await self.client.get(f"{self.BASE_API}/rate_limit")
        self._check_auth(resp)
        resp.raise_for_status()
        core = resp.json()["resources"]["core"]
        return RateLimitInfo(
            limit=core["limit"],
            remaining=core["remaining"],
            reset_timestamp=core["reset"],
        )

    async def fetch_metadata(self, owner: str, repo: str) -> dict:
        """GET /repos/{owner}/{repo} -> full JSON dict."""
        resp = await self.client.get(f"{self.BASE_API}/repos/{owner}/{repo}")
        self._check_auth(resp)
        self._check_rate_limit(resp)
        if resp.status_code == 404:
            raise NotFoundError(f"Repository {owner}/{repo} not found")
        resp.raise_for_status()
        return resp.json()

    async def fetch_readme(self, owner: str, repo: str, default_branch: str) -> str:
        """GET raw README.md from the default branch."""
        url = f"{self.BASE_RAW}/{owner}/{repo}/{default_branch}/README.md"
        resp = await self.client.get(url)
        if resp.status_code == 404:
            raise NotFoundError(f"README.md not found for {owner}/{repo}")
        resp.raise_for_status()
        return resp.text

    def _check_auth(self, resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise AuthenticationError("Invalid or expired GITHUB_TOKEN")

    def _check_rate_limit(self, resp: httpx.Response) -> None:
        if resp.status_code == 403:
            remaining = int(resp.headers.get("x-ratelimit-remaining", -1))
            if remaining == 0:
                reset_ts = int(resp.headers.get("x-ratelimit-reset", 0))
                raise RateLimitExceededError(
                    "GitHub API rate limit exceeded", reset_timestamp=reset_ts
                )
