"""Evidence from the project's own tools: tsc errors, linter rules, coverage reports."""

from __future__ import annotations

from collections import Counter, defaultdict

from .accessors import _get_detail, _safe_num


def _build_type_errors(by_detector: dict[str, list[dict]]) -> dict:
    """tsc errors (``type_error``) by code and by file."""
    issues = by_detector.get("type_error", [])
    if not issues:
        return {}
    by_code: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    for issue in issues:
        by_code[str(_get_detail(issue, "code", "?"))] += 1
        by_file[issue.get("file", "")] += 1
    return {
        "total": len(issues),
        "by_code": dict(by_code.most_common(10)),
        "files": [{"file": f, "errors": n} for f, n in by_file.most_common(10)],
    }


def _build_lint_rules(by_detector: dict[str, list[dict]]) -> list[dict]:
    """The project's linter findings (``lint``), grouped by rule."""
    files_by_rule: dict[str, list[str]] = defaultdict(list)
    for issue in by_detector.get("lint", []):
        files_by_rule[str(_get_detail(issue, "rule", "?"))].append(issue.get("file", ""))
    ranked = sorted(files_by_rule.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [
        {"rule": rule, "count": len(files), "files": sorted(set(files))[:3]}
        for rule, files in ranked[:15]
    ]


def _build_coverage_gaps(by_detector: dict[str, list[dict]]) -> dict:
    """Test coverage verdicts, and the measured line coverage when a report was read."""
    issues = by_detector.get("test_coverage", [])
    kinds: Counter[str] = Counter()
    reports: set[str] = set()
    measured: list[dict] = []
    for issue in issues:
        kind = _get_detail(issue, "kind")
        if not kind:
            continue
        kinds[str(kind)] += 1
        if _get_detail(issue, "source") != "coverage_report":
            continue
        reports.add(str(_get_detail(issue, "coverage_report", "")))
        measured.append(
            {"file": issue.get("file", ""), "line_pct": _safe_num(_get_detail(issue, "line_pct", 0))}
        )
    if not kinds:
        return {}
    result: dict = {"by_kind": dict(kinds.most_common())}
    if reports:
        result["measured_by"] = sorted(r for r in reports if r)
        result["lowest_line_coverage"] = sorted(measured, key=lambda m: (m["line_pct"], m["file"]))[:10]
    return result


__all__ = ["_build_coverage_gaps", "_build_lint_rules", "_build_type_errors"]
