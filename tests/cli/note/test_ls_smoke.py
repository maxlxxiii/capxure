"""End-to-end smoke tests for cap note ls: real Database, tmp_path."""

import pytest

from capxure.cli.note import main


@pytest.fixture
def capxure_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CAPXURE_DATA_DIR", str(tmp_path))
    return tmp_path


def test_ls_after_three_adds_shows_all_three(capxure_data_dir, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)  # plain mode
    main(["add", "first"])
    main(["add", "second"])
    main(["add", "third"])
    capsys.readouterr()  # discard add-side stderr/stdout
    code = main(["ls"])
    assert code == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln]
    assert len(lines) == 3
    for line in lines:
        assert len(line.split("\t")) == 7
    contents = [line.split("\t")[6] for line in lines]
    assert contents == ["third", "second", "first"]


def test_ls_pretty_smoke(capxure_data_dir, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    main(["add", "hello", "-k", "thought"])
    capsys.readouterr()
    code = main(["ls"])
    assert code == 0
    out = capsys.readouterr().out
    assert "kind=thought" in out
    assert "> hello" in out


def test_ls_empty_inbox(capxure_data_dir, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    code = main(["ls"])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
