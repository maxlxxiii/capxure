"""Tests for the `cap stars` subcommand."""
from __future__ import annotations

import argparse

import pytest

from capxure.cli import build_parser


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
