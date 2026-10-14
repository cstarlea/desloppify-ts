"""Scan-side counts: the detector total and the first-scan headline."""

from __future__ import annotations

from pathlib import Path

from desloppify_ts.engine.planning.scan import _total_line
from desloppify_ts.intelligence.narrative.dimensions import _analyze_dimensions


def _issue(file: str) -> dict:
    return {"file": file, "detector": "unused"}


def test_total_line_splits_off_findings_outside_the_scan_path(tmp_path, monkeypatch):
    """`--path src` still reports root config files; status calls them carried."""
    monkeypatch.setenv("DESLOPPIFY_TS_ROOT", str(tmp_path))
    issues = [
        _issue("src/a.ts"),
        _issue("src/b.ts"),
        _issue("."),
        _issue("tsconfig.json"),
    ]

    line = _total_line(issues, tmp_path / "src")

    assert line == "Total: 4 issues (3 in scope, 1 outside src/)"


def test_total_line_is_plain_when_everything_is_in_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("DESLOPPIFY_TS_ROOT", str(tmp_path))
    issues = [_issue("src/a.ts"), _issue("tsconfig.json")]

    assert _total_line(issues, Path(tmp_path)) == "Total: 2 issues"


def test_dimension_count_covers_every_dimension_with_open_work():
    """The headline said "across 3 dimensions": the length of a top-3 list."""
    placeholder = {
        "score": 0.0,
        "failing": 0,
        "detectors": {"subjective_assessment": {"placeholder": True}},
    }
    dim_scores = {
        **{f"Mech {n}": {"score": 90.0, "failing": 2} for n in range(5)},
        "Clean": {"score": 100.0, "failing": 0},
        "Naming quality": placeholder,
        "Type safety": placeholder,
    }

    analysis = _analyze_dimensions(dim_scores, [], {"potentials": {}})

    assert len(analysis["lowest_dimensions"]) == 3
    assert analysis["failing_dimension_count"] == 7
