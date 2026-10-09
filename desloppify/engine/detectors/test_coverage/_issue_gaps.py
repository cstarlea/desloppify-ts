"""Issue builders for transitive-only and untested module coverage gaps."""

from __future__ import annotations

from .metrics import _COMPLEXITY_TIER_UPGRADE, _LINE_COVERAGE_TARGET
from .reports import FileCoverage

CoverageIssue = dict[str, object]
IssueDetail = dict[str, int | float | str]


def transitive_coverage_gap_issue(
    *,
    file_path: str,
    loc: int,
    importer_count: int,
    loc_weight: float,
    complexity: float,
) -> CoverageIssue:
    """Build issue payload for modules covered only transitively."""
    is_complex = complexity >= _COMPLEXITY_TIER_UPGRADE
    detail: IssueDetail = {
        "kind": "transitive_only",
        "loc": loc,
        "importer_count": importer_count,
        "loc_weight": loc_weight,
    }
    if is_complex:
        detail["complexity_score"] = complexity
    return {
        "file": file_path,
        "name": "transitive_only",
        "tier": 2 if (importer_count >= 10 or is_complex) else 3,
        "confidence": "medium",
        "summary": (
            f"No direct tests ({loc} LOC, {importer_count} importers) "
            "— covered only via imports from tested modules"
        ),
        "detail": detail,
    }


def untested_module_issue(
    *,
    file_path: str,
    loc: int,
    importer_count: int,
    loc_weight: float,
    complexity: float,
) -> CoverageIssue:
    """Build issue payload for untested production modules."""
    is_complex = complexity >= _COMPLEXITY_TIER_UPGRADE
    if importer_count >= 10 or is_complex:
        detail: IssueDetail = {
            "kind": "untested_critical",
            "loc": loc,
            "importer_count": importer_count,
            "loc_weight": loc_weight,
        }
        if is_complex:
            detail["complexity_score"] = complexity
        return {
            "file": file_path,
            "name": "untested_critical",
            "tier": 2,
            "confidence": "high",
            "summary": (
                f"Untested critical module ({loc} LOC, {importer_count} importers) "
                "— high blast radius"
            ),
            "detail": detail,
        }
    return {
        "file": file_path,
        "name": "untested_module",
        "tier": 3,
        "confidence": "high",
        "summary": f"Untested module ({loc} LOC, {importer_count} importers)",
        "detail": {
            "kind": "untested_module",
            "loc": loc,
            "importer_count": importer_count,
            "loc_weight": loc_weight,
        },
    }


def measured_coverage_issue(
    *,
    file_path: str,
    coverage: FileCoverage,
    loc: int,
    importer_count: int,
    loc_weight: float,
    complexity: float,
) -> CoverageIssue | None:
    """Build issue payload from a coverage report, or None when on target.

    No covered line means the tests never loaded the file, so it keeps the
    graph's ``untested_*`` IDs. A partly covered file below the target fails
    by the share of the target it misses, so its cost falls smoothly to zero.
    """
    line_pct = coverage.line_pct
    if line_pct >= _LINE_COVERAGE_TARGET:
        return None
    covered = len(coverage.hit_lines & coverage.lines)
    measured: IssueDetail = {
        "source": "coverage_report",
        "coverage_report": coverage.report,
        "line_pct": round(line_pct, 1),
        "measured_lines": len(coverage.lines),
        "covered_lines": covered,
    }
    branch_pct = coverage.branch_pct
    if branch_pct is not None:
        measured["branch_pct"] = round(branch_pct, 1)
    if not covered:
        issue = untested_module_issue(
            file_path=file_path,
            loc=loc,
            importer_count=importer_count,
            loc_weight=loc_weight,
            complexity=complexity,
        )
        detail = issue["detail"]
        assert isinstance(detail, dict)
        detail.update(measured)
        issue["summary"] = f"{issue['summary']} — no line ran in the coverage report"
        return issue
    shortfall = 1 - line_pct / _LINE_COVERAGE_TARGET
    branches = f", {branch_pct:.0f}% of branches" if branch_pct is not None else ""
    return {
        "file": file_path,
        "name": "low_coverage",
        "tier": 3,
        "confidence": "high",
        "summary": (
            f"Low test coverage: {line_pct:.0f}% of lines{branches} "
            f"(target {_LINE_COVERAGE_TARGET}%, {loc} LOC)"
        ),
        "detail": {
            "kind": "low_coverage",
            "loc": loc,
            "importer_count": importer_count,
            "loc_weight": round(loc_weight * shortfall, 3),
            **measured,
        },
    }


__all__ = ["measured_coverage_issue", "transitive_coverage_gap_issue", "untested_module_issue"]
