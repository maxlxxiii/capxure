"""Tests for the `cap stars` subcommand."""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from capxure.cli import build_parser
from capxure.cli.stars import _confirm


class TestStarsParser:
    def test_no_user_arg_defaults_to_none(self):
        parser = build_parser()
        args = parser.parse_args(["stars"])
        assert args.subcommand == "stars"
        assert args.user is None
        assert args.limit is None
        assert args.quiet is False
        assert args.yes is False
        assert args.data_dir is None

    def test_accepts_user_argument(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "max-freeman"])
        assert args.user == "max-freeman"

    def test_limit_flag(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "--limit", "20"])
        assert args.limit == 20

    def test_limit_rejects_zero(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["stars", "--limit", "0"])
        err = capsys.readouterr().err
        assert "positive integer" in err

    def test_limit_rejects_negative(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["stars", "--limit", "-3"])
        err = capsys.readouterr().err
        assert "positive integer" in err

    def test_quiet_flag(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "--quiet"])
        assert args.quiet is True

    def test_yes_flag_long(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "--yes"])
        assert args.yes is True

    def test_yes_flag_short(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "-y"])
        assert args.yes is True

    def test_data_dir_flag(self, tmp_path):
        parser = build_parser()
        args = parser.parse_args(["stars", f"--data-dir={tmp_path}"])
        assert args.data_dir == str(tmp_path)

    def test_combination_of_user_and_flags(self):
        parser = build_parser()
        args = parser.parse_args(["stars", "max-freeman", "--limit", "5", "-y", "--quiet"])
        assert args.user == "max-freeman"
        assert args.limit == 5
        assert args.yes is True
        assert args.quiet is True


class TestConfirm:
    def test_yes_flag_skips_prompt_and_logs_acknowledgement(self, capsys):
        result = _confirm(
            total=10, already=2, new=8,
            yes=True, quiet=False,
            user_label="alice", limit=None,
        )
        assert result is True
        err = capsys.readouterr().err
        assert "running with --yes" in err
        assert "8 to capture" in err

    def test_yes_with_quiet_suppresses_acknowledgement(self, capsys):
        result = _confirm(
            total=10, already=2, new=8,
            yes=True, quiet=True,
            user_label="alice", limit=None,
        )
        assert result is True
        assert capsys.readouterr().err == ""

    def test_non_tty_without_yes_aborts(self, capsys):
        with patch("sys.stdin.isatty", return_value=False):
            result = _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            )
        assert result is False
        err = capsys.readouterr().err
        assert "no TTY" in err

    def test_tty_accepts_lowercase_y(self, capsys):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="y"):
            result = _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            )
        assert result is True

    def test_tty_accepts_yes_full_word(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="yes"):
            assert _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            ) is True

    def test_tty_accepts_uppercase_Y(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="Y"):
            assert _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            ) is True

    def test_tty_rejects_anything_else(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value=""):
            assert _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            ) is False

    def test_tty_rejects_n(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n"):
            assert _confirm(
                total=10, already=2, new=8,
                yes=False, quiet=False,
                user_label="alice", limit=None,
            ) is False

    def test_breakdown_includes_counts_and_user_label(self, capsys):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n"):
            _confirm(
                total=213, already=47, new=166,
                yes=False, quiet=False,
                user_label="max-freeman", limit=None,
            )
        err = capsys.readouterr().err
        assert "213 starred repos for max-freeman" in err
        assert "47 already captured" in err
        assert "166 new" in err

    def test_breakdown_with_limit_includes_limit_note(self, capsys):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n"):
            _confirm(
                total=213, already=5, new=15,
                yes=False, quiet=False,
                user_label="max-freeman", limit=20,
            )
        err = capsys.readouterr().err
        assert "limited to 20" in err
