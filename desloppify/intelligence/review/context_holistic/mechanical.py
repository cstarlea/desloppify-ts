"""Aggregate mechanical detector issues into structured evidence clusters.

Reads all state issues and produces signal clusters organized for holistic
review context enrichment.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from desloppify.engine._state.schema import StateModel

from .clusters.complexity import _build_complexity_hotspots
from .clusters.consistency import _build_duplicate_clusters, _build_naming_drift
from .clusters.dependency import (
    _build_boundary_violations,
    _build_dead_code,
    _build_deferred_import_density,
    _build_private_crossings,
)
from .clusters.error_state import _build_error_hotspots, _build_mutable_globals
from .clusters.organization import (
    _build_flat_dir_issues,
    _build_large_file_distribution,
)
from .clusters.security import (
    _build_security_hotspots,
    _build_signal_density,
    _build_systemic_patterns,
)
from .clusters.toolchain import _build_coverage_gaps, _build_lint_rules, _build_type_errors


def _normalize_allowed_files(
    allowed_files: set[str] | list[str] | tuple[str, ...] | None,
) -> set[str] | None:
    """Normalize optional allowed-file scope to slash-normalized relative paths."""
    if allowed_files is None:
        return None
    out: set[str] = set()
    for raw in allowed_files:
        if not isinstance(raw, str):
            continue
        file_path = raw.strip().replace("\\", "/")
        if file_path:
            out.add(file_path)
    return out


def gather_mechanical_evidence(
    state: StateModel,
    *,
    allowed_files: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Aggregate open issues into evidence clusters for holistic review."""
    issues = (state.get("work_items") or state.get("issues", {}))
    if not issues:
        return {}
    allowed_scope = _normalize_allowed_files(allowed_files)

    by_detector: dict[str, list[dict]] = defaultdict(list)
    by_file: dict[str, list[dict]] = defaultdict(list)
    smell_counter: Counter[str] = Counter()
    smell_files: dict[str, list[str]] = defaultdict(list)

    for issue in issues.values():
        if not isinstance(issue, dict):
            continue
        if issue.get("status") != "open":
            continue
        filepath = issue.get("file", "")
        normalized_file = filepath.strip().replace("\\", "/") if isinstance(filepath, str) else ""
        if allowed_scope is not None and normalized_file not in allowed_scope:
            continue
        det = issue.get("detector", "")
        if det:
            by_detector[det].append(issue)
        if normalized_file and normalized_file != ".":
            by_file[normalized_file].append(issue)
        if det == "smells":
            detail = issue.get("detail", {})
            smell_id = detail.get("smell_id", "") if isinstance(detail, dict) else ""
            if smell_id:
                smell_counter[smell_id] += 1
                smell_files[smell_id].append(normalized_file)

    if not by_detector:
        return {}

    evidence: dict[str, Any] = {}

    complexity_hotspots = _build_complexity_hotspots(by_detector, by_file)
    if complexity_hotspots:
        evidence["complexity_hotspots"] = complexity_hotspots

    error_hotspots = _build_error_hotspots(by_detector)
    if error_hotspots:
        evidence["error_hotspots"] = error_hotspots

    mutable_globals = _build_mutable_globals(by_detector)
    if mutable_globals:
        evidence["mutable_globals"] = mutable_globals

    boundary_violations = _build_boundary_violations(by_detector)
    if boundary_violations:
        evidence["boundary_violations"] = boundary_violations

    dead_code = _build_dead_code(by_detector)
    if dead_code:
        evidence["dead_code"] = dead_code

    private_crossings = _build_private_crossings(by_detector)
    if private_crossings:
        evidence["private_crossings"] = private_crossings

    deferred = _build_deferred_import_density(by_file)
    if deferred:
        evidence["deferred_import_density"] = deferred

    duplicate_clusters = _build_duplicate_clusters(by_detector)
    if duplicate_clusters:
        evidence["duplicate_clusters"] = duplicate_clusters

    naming_drift = _build_naming_drift(by_detector)
    if naming_drift:
        evidence["naming_drift"] = naming_drift

    flat_dir_issues = _build_flat_dir_issues(by_detector)
    if flat_dir_issues:
        evidence["flat_dir_issues"] = flat_dir_issues

    large_dist = _build_large_file_distribution(by_detector)
    if large_dist:
        evidence["large_file_distribution"] = large_dist

    security_hotspots = _build_security_hotspots(by_detector)
    if security_hotspots:
        evidence["security_hotspots"] = security_hotspots

    signal_density = _build_signal_density(by_file)
    if signal_density:
        evidence["signal_density"] = signal_density

    systemic = _build_systemic_patterns(smell_counter, smell_files)
    if systemic:
        evidence["systemic_patterns"] = systemic

    type_errors = _build_type_errors(by_detector)
    if type_errors:
        evidence["type_errors"] = type_errors

    lint_rules = _build_lint_rules(by_detector)
    if lint_rules:
        evidence["lint_rules"] = lint_rules

    coverage_gaps = _build_coverage_gaps(by_detector)
    if coverage_gaps:
        evidence["coverage_gaps"] = coverage_gaps

    pkg_census = _build_package_size_census(by_file)
    if pkg_census:
        evidence["package_size_census"] = pkg_census

    return evidence


def _build_package_size_census(
    by_file: dict[str, list[dict]],
) -> list[dict]:
    """Compute LOC per top-level package.  Flag packages >15% of codebase."""
    pkg_loc: dict[str, int] = {}
    for filepath, issues in by_file.items():
        parts = filepath.replace("\\", "/").split("/")
        pkg = parts[0] if parts else filepath
        # Sum file LOC from structural detail or count issues as proxy.
        file_loc = 0
        for issue in issues:
            if issue.get("detector") == "structural":
                detail = issue.get("detail", {})
                if isinstance(detail, dict):
                    loc_val = detail.get("loc", 0)
                    if isinstance(loc_val, int | float) and loc_val > file_loc:
                        file_loc = int(loc_val)
        if file_loc == 0:
            file_loc = 1  # Count the file at minimum
        pkg_loc[pkg] = pkg_loc.get(pkg, 0) + file_loc

    total_loc = sum(pkg_loc.values())
    if total_loc == 0:
        return []

    results = []
    for pkg, loc in sorted(pkg_loc.items(), key=lambda kv: -kv[1]):
        pct = round(100 * loc / total_loc, 1)
        results.append({
            "package": pkg,
            "loc": loc,
            "pct_of_total": pct,
            "disproportionate": pct > 15,
        })
    return results


def type_strictness_evidence(state: StateModel) -> list[dict[str, str]]:
    """The open ``tsconfig_health`` issues: how strictly the code is type-checked.

    Not limited to reviewed files, since the issues are on tsconfig files.
    """
    issues = state.get("work_items") or state.get("issues", {})
    return [
        {"config": str(issue.get("file", "")), "summary": str(issue.get("summary", ""))}
        for issue in issues.values()
        if isinstance(issue, dict) and issue.get("detector") == "tsconfig_health" and issue.get("status") == "open"
    ]


def dependency_manifest_evidence(state: StateModel) -> list[dict[str, str]]:
    """The open ``dependencies`` issues (Knip): unused, unlisted and binary packages per manifest.

    Not limited to reviewed files, since the issues are on package.json files.
    """
    issues = state.get("work_items") or state.get("issues", {})
    found = []
    for issue in issues.values():
        if not isinstance(issue, dict) or issue.get("detector") != "dependencies" or issue.get("status") != "open":
            continue
        detail = issue.get("detail") if isinstance(issue.get("detail"), dict) else {}
        found.append(
            {
                "manifest": str(issue.get("file", "")),
                "kind": str(detail.get("kind", "")),
                "package": str(detail.get("package", "")),
            }
        )
    return sorted(found, key=lambda d: (d["manifest"], d["kind"], d["package"]))[:30]


__all__ = ["dependency_manifest_evidence", "gather_mechanical_evidence", "type_strictness_evidence"]
