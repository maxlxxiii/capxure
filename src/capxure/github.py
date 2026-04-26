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
    r"(?:(?:https?://)?github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def parse_github_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL.

    Raises ValueError if the URL is not a valid GitHub repo URL.
    """
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"Not a valid GitHub repo URL: {url!r}")
    return m.group(1), m.group(2)


def _next_link(link_header: str | None) -> str | None:
    """Parse an HTTP Link header and return the URL with rel="next", or None.

    Handles the format: `<url1>; rel="next", <url2>; rel="last"`.
    """
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' in section:
            url_part = section.split(";", 1)[0].strip()
            return url_part.lstrip("<").rstrip(">")
    return None


# ── Client ────────────────────────────────────────────────────


class GitHubClient:
    """Async GitHub API client using httpx."""

    BASE_API = "https://api.github.com"

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

    async def fetch_readme(self, owner: str, repo: str) -> str:
        """GET the repository README via the dedicated GitHub endpoint.

        Returns the raw README text regardless of filename or extension
        (README.md, README.rst, README, etc.).
        """
        resp = await self.client.get(
            f"{self.BASE_API}/repos/{owner}/{repo}/readme",
            headers={"Accept": "application/vnd.github.raw"},
        )
        self._check_auth(resp)
        self._check_rate_limit(resp)
        if resp.status_code == 404:
            raise NotFoundError(f"README not found for {owner}/{repo}")
        resp.raise_for_status()
        return resp.text

    async def list_starred(
        self,
        user: str | None,
        limit: int | None = None,
    ) -> list[tuple[str, str, str]]:
        """List a user's starred repos.

        Returns a list of (owner, name, html_url) tuples in GitHub's default order
        (most-recently-starred first). user=None hits /user/starred (the auth'd user);
        otherwise hits /users/{user}/starred.

        `limit` caps the number of items returned and stops pagination early.
        """
        path = "/user/starred" if user is None else f"/users/{user}/starred"
        url: str | None = f"{self.BASE_API}{path}?per_page=100"
        out: list[tuple[str, str, str]] = []
        while url is not None:
            resp = await self.client.get(url)
            self._check_auth(resp)
            self._check_rate_limit(resp)
            if resp.status_code == 404:
                raise NotFoundError(f"User {user!r} not found")
            resp.raise_for_status()
            for item in resp.json():
                owner = item["owner"]["login"]
                name = item["name"]
                html_url = item["html_url"]
                out.append((owner, name, html_url))
                if limit is not None and len(out) >= limit:
                    return out
            url = _next_link(resp.headers.get("Link"))
        return out

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
