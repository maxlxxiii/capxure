"""Tests for capxure.github URL parsing."""
from __future__ import annotations

import pytest

from capxure.github import parse_github_url


class TestParseGithubUrl:
    def test_accepts_full_https_url(self):
        assert parse_github_url("https://github.com/owner/repo") == ("owner", "repo")

    def test_accepts_http_url(self):
        assert parse_github_url("http://github.com/owner/repo") == ("owner", "repo")

    def test_accepts_schemeless_url(self):
        assert parse_github_url("github.com/owner/repo") == ("owner", "repo")

    def test_accepts_bare_shorthand(self):
        assert parse_github_url("owner/repo") == ("owner", "repo")

    def test_accepts_dot_git_suffix(self):
        assert parse_github_url("https://github.com/owner/repo.git") == ("owner", "repo")

    def test_accepts_trailing_slash(self):
        assert parse_github_url("https://github.com/owner/repo/") == ("owner", "repo")

    def test_rejects_single_token(self):
        with pytest.raises(ValueError):
            parse_github_url("owner")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            parse_github_url("")

    def test_rejects_non_github_domain(self):
        # The relaxed regex still rejects gitlab.com URLs — the `github.com/` prefix
        # is only optional, not replaceable.
        with pytest.raises(ValueError):
            parse_github_url("https://gitlab.com/owner/repo")


import json

import httpx

from capxure.github import (
    AuthenticationError,
    GitHubClient,
    NotFoundError,
    RateLimitExceededError,
    _next_link,
)


class TestNextLink:
    def test_none_input_returns_none(self):
        assert _next_link(None) is None

    def test_empty_string_returns_none(self):
        assert _next_link("") is None

    def test_extracts_next_url(self):
        header = '<https://api.github.com/user/starred?page=2>; rel="next", <https://api.github.com/user/starred?page=5>; rel="last"'
        assert _next_link(header) == "https://api.github.com/user/starred?page=2"

    def test_no_next_returns_none(self):
        header = '<https://api.github.com/user/starred?page=1>; rel="prev", <https://api.github.com/user/starred?page=1>; rel="first"'
        assert _next_link(header) is None


def _starred_item(github_id: int, owner: str, name: str) -> dict:
    """Minimal shape of a GitHub starred-list response item."""
    return {
        "id": github_id,
        "name": name,
        "html_url": f"https://github.com/{owner}/{name}",
        "owner": {"login": owner},
    }


def _make_client_with_transport(transport: httpx.MockTransport) -> GitHubClient:
    """Build a GitHubClient whose async client uses the given mock transport."""
    client = GitHubClient(token="fake-token")
    client._client = httpx.AsyncClient(
        transport=transport,
        headers={"Authorization": "token fake-token"},
        timeout=5.0,
    )
    return client


class TestListStarred:
    @pytest.mark.asyncio
    async def test_single_page_authenticated_user(self):
        captured_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_paths.append(request.url.path)
            return httpx.Response(
                200,
                json=[_starred_item(1, "alice", "repo1"), _starred_item(2, "bob", "repo2")],
            )

        client = _make_client_with_transport(httpx.MockTransport(handler))
        try:
            result = await client.list_starred(user=None, limit=None)
        finally:
            await client._client.aclose()

        assert result == [
            ("alice", "repo1", "https://github.com/alice/repo1"),
            ("bob", "repo2", "https://github.com/bob/repo2"),
        ]
        assert captured_paths == ["/user/starred"]

    @pytest.mark.asyncio
    async def test_named_user_uses_users_endpoint(self):
        captured_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_paths.append(request.url.path)
            return httpx.Response(200, json=[_starred_item(1, "alice", "repo1")])

        client = _make_client_with_transport(httpx.MockTransport(handler))
        try:
            await client.list_starred(user="someone", limit=None)
        finally:
            await client._client.aclose()

        assert captured_paths == ["/users/someone/starred"]

    @pytest.mark.asyncio
    async def test_paginates_via_link_header(self):
        page1 = [_starred_item(i, "u", f"r{i}") for i in range(1, 4)]
        page2 = [_starred_item(i, "u", f"r{i}") for i in range(4, 6)]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json=page2)
            return httpx.Response(
                200,
                json=page1,
                headers={
                    "Link": '<https://api.github.com/user/starred?page=2>; rel="next", '
                            '<https://api.github.com/user/starred?page=2>; rel="last"',
                },
            )

        client = _make_client_with_transport(httpx.MockTransport(handler))
        try:
            result = await client.list_starred(user=None, limit=None)
        finally:
            await client._client.aclose()

        assert len(result) == 5
        assert [t[1] for t in result] == ["r1", "r2", "r3", "r4", "r5"]

    @pytest.mark.asyncio
    async def test_limit_truncates_within_first_page(self):
        page1 = [_starred_item(i, "u", f"r{i}") for i in range(1, 11)]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=page1)

        client = _make_client_with_transport(httpx.MockTransport(handler))
        try:
            result = await client.list_starred(user=None, limit=3)
        finally:
            await client._client.aclose()

        assert len(result) == 3
        assert [t[1] for t in result] == ["r1", "r2", "r3"]

    @pytest.mark.asyncio
    async def test_limit_truncates_across_pages(self):
        page1 = [_starred_item(i, "u", f"r{i}") for i in range(1, 4)]  # 3 items
        page2_called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal page2_called
            if request.url.params.get("page") == "2":
                page2_called = True
                return httpx.Response(200, json=[_starred_item(99, "u", "r99")])
            return httpx.Response(
                200,
                json=page1,
                headers={"Link": '<https://api.github.com/user/starred?page=2>; rel="next"'},
            )

        client = _make_client_with_transport(httpx.MockTransport(handler))
        try:
            # limit=3 — exactly the first page; page 2 must not be fetched.
            result = await client.list_starred(user=None, limit=3)
        finally:
            await client._client.aclose()

        assert len(result) == 3
        assert page2_called is False

    @pytest.mark.asyncio
    async def test_empty_starred_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        client = _make_client_with_transport(httpx.MockTransport(handler))
        try:
            result = await client.list_starred(user=None, limit=None)
        finally:
            await client._client.aclose()

        assert result == []

    @pytest.mark.asyncio
    async def test_401_raises_authentication_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Bad credentials"})

        client = _make_client_with_transport(httpx.MockTransport(handler))
        try:
            with pytest.raises(AuthenticationError):
                await client.list_starred(user=None, limit=None)
        finally:
            await client._client.aclose()

    @pytest.mark.asyncio
    async def test_404_raises_not_found_for_named_user(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "Not Found"})

        client = _make_client_with_transport(httpx.MockTransport(handler))
        try:
            with pytest.raises(NotFoundError):
                await client.list_starred(user="ghost", limit=None)
        finally:
            await client._client.aclose()

    @pytest.mark.asyncio
    async def test_403_with_zero_remaining_raises_rate_limit(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"message": "rate limit"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"},
            )

        client = _make_client_with_transport(httpx.MockTransport(handler))
        try:
            with pytest.raises(RateLimitExceededError):
                await client.list_starred(user=None, limit=None)
        finally:
            await client._client.aclose()
