"""Unit tests for the `cap ls` subcommand — parser and validation layers."""
from __future__ import annotations

import pytest

from capxure.cli import build_parser, main


# --- Parser-level tests -----------------------------------------------------


def test_parser_accepts_bare_ls():
    parser = build_parser()
    args = parser.parse_args(["ls"])
    assert args.subcommand == "ls"
    assert args.subject == "repos"
    assert args.sort is None
    assert args.reverse is False
    assert args.topics == []
    assert args.limit is None
    assert args.format is None


def test_parser_accepts_ls_repos_explicit():
    parser = build_parser()
    args = parser.parse_args(["ls", "repos"])
    assert args.subject == "repos"


def test_parser_accepts_ls_topics():
    parser = build_parser()
    args = parser.parse_args(["ls", "topics"])
    assert args.subject == "topics"


def test_parser_rejects_unknown_subject():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["ls", "owners"])
    assert exc_info.value.code == 2


@pytest.mark.parametrize("flag", ["-s", "--sort"])
@pytest.mark.parametrize("value", ["synced", "captured", "stars"])
def test_parser_accepts_all_sort_values(flag, value):
    parser = build_parser()
    args = parser.parse_args(["ls", flag, value])
    assert args.sort == value


def test_parser_rejects_unknown_sort_value():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["ls", "-s", "name"])
    assert exc_info.value.code == 2


@pytest.mark.parametrize("flag", ["-r", "--reverse"])
def test_parser_accepts_reverse_flag(flag):
    parser = build_parser()
    args = parser.parse_args(["ls", flag])
    assert args.reverse is True


def test_parser_accepts_multiple_topics():
    parser = build_parser()
    args = parser.parse_args(["ls", "-t", "ml", "-t", "nlp"])
    assert args.topics == ["ml", "nlp"]


@pytest.mark.parametrize("flag", ["-l", "--limit"])
def test_parser_accepts_limit(flag):
    parser = build_parser()
    args = parser.parse_args(["ls", flag, "25"])
    assert args.limit == 25


def test_parser_rejects_non_integer_limit():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["ls", "-l", "nope"])
    assert exc_info.value.code == 2


@pytest.mark.parametrize("value", ["pretty", "plain"])
def test_parser_accepts_format_override(value):
    parser = build_parser()
    args = parser.parse_args(["ls", "--format", value])
    assert args.format == value


def test_parser_rejects_unknown_format():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["ls", "--format", "json"])
    assert exc_info.value.code == 2


def test_parser_combined_short_flags_rs():
    """`-rs stars` parses as `-r -s stars` since -r is boolean."""
    parser = build_parser()
    args = parser.parse_args(["ls", "-rs", "stars"])
    assert args.reverse is True
    assert args.sort == "stars"


# --- main() validation tests ------------------------------------------------


def test_main_ls_topics_with_topic_flag_returns_2(capsys):
    assert main(["ls", "topics", "-t", "ml"]) == 2
    err = capsys.readouterr().err
    assert "--topic" in err


def test_main_ls_topics_with_sort_flag_returns_2(capsys):
    assert main(["ls", "topics", "-s", "stars"]) == 2
    err = capsys.readouterr().err
    assert "--sort" in err


def test_main_ls_limit_zero_returns_2(capsys):
    assert main(["ls", "-l", "0"]) == 2
    err = capsys.readouterr().err
    assert "--limit" in err


def test_main_ls_limit_negative_returns_2(capsys):
    assert main(["ls", "-l", "-5"]) == 2


# --- Pretty renderer tests --------------------------------------------------

from capxure.cli.list_ import (
    _clip_description,
    _format_pretty_repos,
    _format_pretty_topics,
    _resolve_format,
    _truncate,
)
from capxure.storage import Repo


def _mk_repo(
    *,
    id: int = 1,
    github_id: int = 1,
    owner: str = "octocat",
    name: str = "hello",
    stars: int = 0,
    description: str | None = "A test repo.",
    last_synced_at: str = "2026-04-20T12:00:00",
    captured_at: str = "2026-04-20T12:00:00",
    pushed_at: str | None = "2026-04-15T00:00:00Z",
    language: str | None = "Python",
    topics: tuple[str, ...] = (),
) -> Repo:
    return Repo(
        id=id,
        github_id=github_id,
        owner=owner,
        name=name,
        full_name=f"{owner}/{name}",
        url=f"https://github.com/{owner}/{name}",
        default_branch="main",
        description=description,
        language=language,
        stars=stars,
        forks=0,
        pushed_at=pushed_at,
        is_fork=False,
        is_archived=False,
        topics=topics,
        readme_content=None,
        readme_sha=None,
        captured_at=captured_at,
        last_synced_at=last_synced_at,
    )


def test_truncate_short_value_unchanged():
    assert _truncate("abc", 10) == "abc"


def test_truncate_long_value_uses_ellipsis():
    assert _truncate("abcdefghij", 5) == "abcd…"


def test_clip_description_none_returns_empty_lines():
    assert _clip_description(None, width=20) == [""]


def test_clip_description_fits_in_one_line():
    assert _clip_description("Hello world.", width=20) == ["Hello world."]


def test_clip_description_fits_in_two_lines():
    text = "A small wrapping description here."
    lines = _clip_description(text, width=15)
    assert len(lines) <= 2
    assert all(len(l) <= 15 for l in lines)


def test_clip_description_clipped_to_two_with_ellipsis():
    text = "one two three four five six seven eight nine ten eleven twelve thirteen"
    lines = _clip_description(text, width=12)
    assert len(lines) == 2
    assert lines[1].endswith("…")


def test_format_pretty_repos_empty_sends_stderr_message(capsys):
    _format_pretty_repos([], terminal_width=80)
    out = capsys.readouterr()
    assert out.out == ""
    assert "No repos" in out.err


def test_format_pretty_repos_has_header_row(capsys):
    repo = _mk_repo(owner="oct", name="hello", stars=42, last_synced_at="2026-04-01T00:00:00")
    _format_pretty_repos([repo], terminal_width=120)
    out = capsys.readouterr().out
    first_line = out.splitlines()[0]
    # Header ordering
    for header in ("last_synced", "owner", "name", "stars", "description"):
        assert header in first_line.lower()


def test_format_pretty_repos_renders_synced_as_date(capsys):
    repo = _mk_repo(last_synced_at="2026-04-22T18:30:15")
    _format_pretty_repos([repo], terminal_width=120)
    out = capsys.readouterr().out
    assert "2026-04-22" in out
    assert "18:30" not in out


def test_format_pretty_repos_shows_owner_right_aligned_and_name_left_aligned(capsys):
    repo = _mk_repo(owner="a", name="longname", stars=1, description="")
    _format_pretty_repos([repo], terminal_width=120)
    body = capsys.readouterr().out.splitlines()[2]  # header, rule, first data row
    # Owner should end just before a space-gap before the name; name starts right after.
    # We verify by substring order.
    owner_idx = body.find("a")
    name_idx = body.find("longname")
    assert 0 <= owner_idx < name_idx


def test_format_pretty_topics_shows_count_and_topic(capsys):
    _format_pretty_topics([("ml", 5), ("nlp", 2)], terminal_width=80)
    out = capsys.readouterr().out
    assert "5" in out and "ml" in out
    assert "2" in out and "nlp" in out


def test_format_pretty_topics_empty_sends_stderr_message(capsys):
    _format_pretty_topics([], terminal_width=80)
    out = capsys.readouterr()
    assert out.out == ""
    assert "No topics" in out.err


def test_format_pretty_repos_header_and_rule_same_width_as_data(capsys):
    """Regression guard: header, rule, and data rows must share a common width.

    The previous bug rendered `last_synced` (11 chars) into a 10-wide date column,
    making the header 1 char wider than the rule and data rows, which shifted every
    column to the right of it out of alignment.
    """
    repo = _mk_repo(
        owner="oct",
        name="hello",
        stars=42,
        last_synced_at="2026-04-01T00:00:00",
        description="desc",
    )
    _format_pretty_repos([repo], terminal_width=120)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) >= 3
    header, rule, data = lines[0], lines[1], lines[2]
    # Same width guarantees columns line up. Before the fix, header was 1 char longer.
    assert len(header) == len(rule)
    assert len(rule) == len(data)


# --- _resolve_format tests --------------------------------------------------


class _FakeStream:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_resolve_format_explicit_wins_over_tty():
    assert _resolve_format("plain", _FakeStream(is_tty=True)) == "plain"
    assert _resolve_format("pretty", _FakeStream(is_tty=False)) == "pretty"


def test_resolve_format_auto_picks_pretty_for_tty():
    assert _resolve_format(None, _FakeStream(is_tty=True)) == "pretty"


def test_resolve_format_auto_picks_plain_for_pipe():
    assert _resolve_format(None, _FakeStream(is_tty=False)) == "plain"
