"""Tests for desloppify.languages.typescript.detectors.exports — dead exports detection."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import desloppify.languages.typescript.detectors.exports as exports_mod

# ── detect_dead_exports ─────────────────────────────────────────────


def test_detect_dead_exports_reports_reduced_coverage_when_knip_unavailable():
    """Knip not running is a coverage gap, not a clean result."""
    with patch.object(
        exports_mod,
        "detect_with_knip_result",
        return_value=(None, "knip_not_installed"),
    ):
        entries, total, coverage = exports_mod.detect_dead_exports_result(
            Path("/tmp/fake")
        )
    assert entries == []
    assert total == 0
    assert coverage is not None
    assert coverage.status == "reduced"
    assert coverage.reason == "knip_not_installed"
    assert "npm i -D knip" in coverage.remediation


def test_detect_dead_exports_potential_counts_all_exports(tmp_path, set_project_root):
    """The potential is the export population, not the failure count."""
    (tmp_path / "a.ts").write_text("export const a = 1;\nexport function b() {}\n")
    (tmp_path / "c.ts").write_text("export type C = string;\nconst internal = 1;\n")
    fake_entries = [{"file": "a.ts", "name": "a", "line": 1, "kind": "export"}]
    with patch.object(
        exports_mod, "detect_with_knip_result", return_value=(fake_entries, None)
    ):
        entries, total, coverage = exports_mod.detect_dead_exports_result(tmp_path)
    assert entries == fake_entries
    assert total == 3
    assert coverage is None


def test_detect_dead_exports_compat_wrapper():
    with patch.object(exports_mod, "detect_with_knip_result", return_value=([], None)):
        entries, total = exports_mod.detect_dead_exports(Path("/tmp/fake-nonexistent"))
    assert entries == []
    assert total == 0


# ── cmd_exports ─────────────────────────────────────────────────────


def _make_args(path: str = "/tmp/fake", json_output: bool = False, top: int = 10):
    return SimpleNamespace(path=path, json=json_output, top=top)


def test_cmd_exports_json_output(capsys):
    """--json flag produces valid JSON output with count and entries."""
    fake_entries = [
        {"file": "src/utils.ts", "name": "unused1", "line": 10, "kind": "export"},
    ]
    with patch.object(
        exports_mod, "detect_dead_exports", return_value=(fake_entries, 1)
    ):
        exports_mod.cmd_exports(_make_args(json_output=True))

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["count"] == 1
    assert len(parsed["entries"]) == 1
    assert parsed["entries"][0]["name"] == "unused1"


def test_cmd_exports_no_dead_exports(capsys):
    """Prints 'No dead exports found.' when result is empty."""
    with patch.object(exports_mod, "detect_dead_exports", return_value=([], 0)):
        exports_mod.cmd_exports(_make_args())

    out = capsys.readouterr().out
    assert "No dead exports found" in out


def test_cmd_exports_table_output(capsys):
    """Prints a table grouped by file when dead exports are found."""
    fake_entries = [
        {"file": "src/utils.ts", "name": "unusedA", "line": 10, "kind": "export"},
        {"file": "src/utils.ts", "name": "unusedB", "line": 20, "kind": "export"},
        {"file": "src/api.ts", "name": "oldFetch", "line": 5, "kind": "export"},
    ]
    with (
        patch.object(
            exports_mod, "detect_dead_exports", return_value=(fake_entries, 3)
        ),
        patch.object(exports_mod, "rel", side_effect=lambda p: p),
        patch.object(exports_mod, "print_table") as mock_table,
    ):
        exports_mod.cmd_exports(_make_args())

    out = capsys.readouterr().out
    assert "Dead exports: 3 across 2 files" in out

    # Verify print_table was called with correct structure
    mock_table.assert_called_once()
    headers, rows, widths = mock_table.call_args[0]
    assert headers == ["File", "Count", "Exports"]
    assert len(rows) == 2
    # Files sorted by count descending — src/utils.ts (2) comes first
    assert rows[0][1] == "2"  # count column for utils.ts
    assert "unusedA" in rows[0][2]
    assert "unusedB" in rows[0][2]


def test_cmd_exports_table_respects_top_limit(capsys):
    """The --top flag limits how many files appear in the table."""
    fake_entries = [
        {"file": f"src/file{i}.ts", "name": f"export{i}", "line": i, "kind": "export"}
        for i in range(5)
    ]
    with (
        patch.object(
            exports_mod, "detect_dead_exports", return_value=(fake_entries, 5)
        ),
        patch.object(exports_mod, "rel", side_effect=lambda p: p),
        patch.object(exports_mod, "print_table") as mock_table,
    ):
        exports_mod.cmd_exports(_make_args(top=2))

    mock_table.assert_called_once()
    _, rows, _ = mock_table.call_args[0]
    assert len(rows) == 2


def test_cmd_exports_truncates_export_names_over_five(capsys):
    """When a file has more than 5 dead exports, names are truncated."""
    fake_entries = [
        {"file": "src/big.ts", "name": f"exp{i}", "line": i, "kind": "export"}
        for i in range(8)
    ]
    with (
        patch.object(
            exports_mod, "detect_dead_exports", return_value=(fake_entries, 8)
        ),
        patch.object(exports_mod, "rel", side_effect=lambda p: p),
        patch.object(exports_mod, "print_table") as mock_table,
    ):
        exports_mod.cmd_exports(_make_args())

    mock_table.assert_called_once()
    _, rows, _ = mock_table.call_args[0]
    assert len(rows) == 1
    names_col = rows[0][2]
    # First 5 names shown, then truncation indicator
    assert "exp0" in names_col
    assert "exp4" in names_col
    assert "(+3)" in names_col


def test_cmd_exports_prints_scanning_message(capsys):
    """Prints 'Scanning exports via Knip...' to stderr."""
    with patch.object(exports_mod, "detect_dead_exports", return_value=([], 0)):
        exports_mod.cmd_exports(_make_args())

    err = capsys.readouterr().err
    assert "Scanning exports via Knip" in err
