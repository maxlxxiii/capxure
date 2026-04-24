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
