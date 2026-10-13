"""A scan with no source files is an error, not a perfect score."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import desloppify.cli as cli_mod
from desloppify.app.commands.helpers.state import require_issue_inventory
from desloppify.engine._state.schema import scan_found_no_files


def _empty_scan_state(scan_path: str = ".") -> dict:
    return {
        "last_scan": "2026-10-12T00:00:00+00:00",
        "scan_count": 1,
        "scan_path": scan_path,
        "overall_score": 100.0,
        "objective_score": 100.0,
        "strict_score": 100.0,
        "work_items": {},
        "codebase_metrics": {"typescript": {"total_files": 0, "total_loc": 0}},
    }


def _run(monkeypatch, cwd: Path, *argv: str) -> int:
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("sys.argv", ["desloppify", *argv])
    try:
        cli_mod.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_scan_found_no_files():
    assert scan_found_no_files(_empty_scan_state()) is True
    with_files = _empty_scan_state()
    with_files["codebase_metrics"]["typescript"]["total_files"] = 3
    assert scan_found_no_files(with_files) is False
    assert scan_found_no_files({}) is False


def test_require_issue_inventory_refuses_an_empty_scan(capsys):
    assert require_issue_inventory(_empty_scan_state("src")) is False
    out = capsys.readouterr().out
    assert "no TypeScript or JavaScript files under `src`" in out
    assert "no score" in out


def test_scan_of_an_empty_project_exits_non_zero(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "README.md").write_text("# docs only\n")
    monkeypatch.setenv("DESLOPPIFY_ROOT", str(project))
    state = tmp_path / "state" / "state.json"

    code = _run(
        monkeypatch, project, "scan", "--path", str(project), "--state", str(state)
    )

    assert code != 0
    captured = capsys.readouterr()
    assert "no TypeScript/JavaScript files found under" in captured.out + captured.err
    assert not state.exists()


def test_scan_with_a_missing_project_root_exits_non_zero(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.ts").write_text("export const a = 1;\n")
    monkeypatch.setenv("DESLOPPIFY_ROOT", str(tmp_path / "missing"))
    state = tmp_path / "state" / "state.json"

    code = _run(monkeypatch, project, "scan", "--path", ".", "--state", str(state))

    assert code != 0
    captured = capsys.readouterr()
    assert "does not exist" in captured.out + captured.err
    assert not state.exists()


@pytest.mark.parametrize("command", ["status", "next"])
def test_status_and_next_explain_an_empty_scan(tmp_path, monkeypatch, capsys, command):
    state = tmp_path / "state" / "state.json"
    state.parent.mkdir()
    state.write_text(json.dumps(_empty_scan_state()))

    _run(monkeypatch, tmp_path, command, "--state", str(state))

    out = capsys.readouterr().out
    assert "no TypeScript or JavaScript files" in out
    assert "100" not in out


def test_status_json_flags_an_empty_scan(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state" / "state.json"
    state.parent.mkdir()
    state.write_text(json.dumps(_empty_scan_state()))

    _run(monkeypatch, tmp_path, "status", "--json", "--state", str(state))

    assert json.loads(capsys.readouterr().out)["no_source_files"] is True
