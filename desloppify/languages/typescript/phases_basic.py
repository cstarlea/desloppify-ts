"""Basic TypeScript detector phase runners."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from desloppify.base.discovery.file_paths import rel
from desloppify.base.output.terminal import log
from desloppify.engine._state.filtering import make_issue
from desloppify.engine.policy.zones import EXCLUDED_ZONES, Zone, adjust_potential
from desloppify.languages._framework.base.shared_phases_helpers import record_reduced_coverage
from desloppify.languages._framework.base.types import LangRuntimeContract
from desloppify.languages._framework.issue_factories import make_unused_issues
import desloppify.languages.typescript.detectors.dependencies as dependencies_detector_mod
import desloppify.languages.typescript.detectors.deprecated as deprecated_detector_mod
import desloppify.languages.typescript.detectors.exports as exports_detector_mod
import desloppify.languages.typescript.detectors.lint as lint_detector_mod
import desloppify.languages.typescript.detectors.logs as logs_detector_mod
import desloppify.languages.typescript.detectors.tsconfig_health as tsconfig_health_detector_mod
import desloppify.languages.typescript.detectors.type_errors as type_errors_detector_mod
import desloppify.languages.typescript.detectors.unused as unused_detector_mod
from desloppify.state_io import Issue


def phase_logs(path: Path, lang: LangRuntimeContract) -> tuple[list[Issue], dict[str, int]]:
    log_result = logs_detector_mod.detect_logs(path)
    log_entries = log_result.entries
    total_files = log_result.population_size
    log_groups: dict[tuple, list] = defaultdict(list)
    for entry in log_entries:
        log_groups[(entry["file"], entry["tag"])].append(entry)

    results = []
    for (file, tag), entries in log_groups.items():
        results.append(
            make_issue(
                "logs",
                file,
                tag,
                tier=1,
                confidence="high",
                summary=f"{len(entries)} tagged logs [{tag}]",
                detail={
                    "count": len(entries),
                    "lines": [entry["line"] for entry in entries[:20]],
                },
            )
        )
    log(f"         {len(log_entries)} instances → {len(results)} issues")
    return results, {"logs": adjust_potential(lang.zone_map, total_files)}


def phase_unused(path: Path, lang: LangRuntimeContract) -> tuple[list[Issue], dict[str, int]]:
    entries, total_files, coverage = unused_detector_mod.detect_unused_result(
        path, cache=lang.runtime_cache
    )
    record_reduced_coverage(lang, coverage)
    return make_unused_issues(entries, log), {
        "unused": adjust_potential(lang.zone_map, total_files),
    }


def phase_type_errors(
    path: Path, lang: LangRuntimeContract
) -> tuple[list[Issue], dict[str, int]]:
    result = type_errors_detector_mod.detect_type_errors_result(path, cache=lang.runtime_cache)
    record_reduced_coverage(lang, result.coverage)
    if result.checked_files is None:
        log("         skipped (tsc did not check this scan)")
        return [], {}

    def zone(filepath: str) -> Zone:
        return lang.zone_map.get(rel(filepath)) if lang.zone_map is not None else Zone.PRODUCTION

    results = []
    for entry in result.entries:
        if zone(entry["file"]) in (Zone.GENERATED, Zone.VENDOR):
            continue
        first_line = entry["message"].splitlines()[0]
        results.append(
            make_issue(
                "type_error",
                entry["file"],
                f"{entry['code']}::{entry['line']}",
                tier=2 if entry["confidence"] == "high" else 3,
                confidence=entry["confidence"],
                summary=f"{entry['code']}: {first_line[:200]}",
                detail={
                    "line": entry["line"],
                    "cols": entry["cols"],
                    "code": entry["code"],
                    "message": entry["message"],
                    "count": entry["count"],
                },
            )
        )
    potential = sum(zone(filepath) not in EXCLUDED_ZONES for filepath in result.checked_files)
    log(f"         {len(result.entries)} errors → {len(results)} issues ({potential} files scored)")
    return results, {"type_error": potential}


def phase_lint(path: Path, lang: LangRuntimeContract) -> tuple[list[Issue], dict[str, int]]:
    result = lint_detector_mod.detect_lint_result(
        path,
        type_aware_max_files=lang.runtime_setting(
            "lint_type_aware_max_files", lint_detector_mod.DEFAULT_TYPE_AWARE_MAX_FILES
        ),
    )
    record_reduced_coverage(lang, result.coverage)
    if result.checked_files is None:
        if result.coverage is not None:
            log("         skipped (the project's linter did not run)")
        return [], {}

    def zone(filepath: str) -> Zone:
        return lang.zone_map.get(rel(filepath)) if lang.zone_map is not None else Zone.PRODUCTION

    results = []
    for entry in result.entries:
        if zone(entry["file"]) in (Zone.GENERATED, Zone.VENDOR):
            continue
        first_line = entry["message"].splitlines()[0] if entry["message"] else entry["rule"]
        results.append(
            make_issue(
                "lint",
                entry["file"],
                f"{entry['rule']}::{entry['line']}",
                tier=2 if entry["confidence"] == "high" else 3,
                confidence=entry["confidence"],
                summary=f"{entry['rule']}: {first_line[:200]}",
                detail={
                    "line": entry["line"],
                    "cols": entry["cols"],
                    "rule": entry["rule"],
                    "severity": entry["severity"],
                    "message": entry["message"],
                    "count": entry["count"],
                    "fixable": entry["fixable"],
                },
            )
        )
    potential = sum(zone(filepath) not in EXCLUDED_ZONES for filepath in result.checked_files)
    log(f"         {len(result.entries)} findings → {len(results)} issues ({potential} files scored)")
    potentials = {"lint": potential}
    if "eslint" in result.linters:
        # The retired next_lint detector ran ESLint too; this resolves its open issues.
        potentials["next_lint"] = 0
    return results, potentials


def phase_tsconfig_health(
    path: Path, lang: LangRuntimeContract
) -> tuple[list[Issue], dict[str, int]]:
    result = tsconfig_health_detector_mod.detect_tsconfig_health(path, lang.zone_map)
    results = [
        make_issue(
            "tsconfig_health",
            entry["file"],
            entry["check"],
            tier=entry["tier"],
            confidence=entry["confidence"],
            summary=entry["summary"],
            detail=entry["detail"],
        )
        for entry in result.entries
    ]
    log(f"         {result.population_size} checks → {len(results)} issues")
    return results, {"tsconfig_health": result.population_size}


_EXPORT_SUMMARIES = {
    "type": "Dead export: {name}",
    "export": "Dead export: {name}",
    "enum_member": "Unused enum member: {name}",
    "duplicate": "Duplicate export: {names}",
}


def phase_exports(path: Path, lang: LangRuntimeContract) -> tuple[list[Issue], dict[str, int]]:
    export_entries, total_exports, coverage = exports_detector_mod.detect_dead_exports_result(
        path, cache=lang.runtime_cache
    )
    record_reduced_coverage(lang, coverage)
    results = []
    for entry in export_entries:
        kind = entry.get("kind")
        detail = {"line": entry.get("line"), "kind": kind}
        if kind == "duplicate":
            detail["names"] = entry["names"]
        summary = _EXPORT_SUMMARIES.get(kind, _EXPORT_SUMMARIES["export"])
        results.append(
            make_issue(
                "exports",
                entry["file"],
                entry["name"],
                tier=3 if kind == "duplicate" else 2,
                confidence="medium" if kind == "duplicate" else "high",
                summary=summary.format(name=entry["name"], names=", ".join(entry.get("names", ()))),
                detail=detail,
            )
        )
    log(f"         {len(export_entries)} instances → {len(results)} issues")
    return results, {"exports": total_exports}


_DEPENDENCY_ISSUES = {
    "unused": (2, "Unused dependency: {name}"),
    "unused_dev": (3, "Unused devDependency: {name}"),
    "unlisted": (2, "Unlisted dependency: {name} is imported but not declared"),
    "unlisted_binary": (3, "Unlisted binary: {name} is run by a script but not declared"),
}


def phase_dependencies(
    path: Path, lang: LangRuntimeContract
) -> tuple[list[Issue], dict[str, int]]:
    result = dependencies_detector_mod.detect_dependencies(
        path, lang.zone_map, cache=lang.runtime_cache
    )
    record_reduced_coverage(lang, result.coverage)
    if result.population_size is None:
        log("         skipped (Knip did not run)")
        return [], {}
    results = []
    for entry in result.entries:
        tier, summary = _DEPENDENCY_ISSUES[entry["kind"]]
        detail: dict = {"kind": entry["kind"], "package": entry["name"], "line": entry["line"]}
        if entry.get("importers"):
            detail["importers"] = entry["importers"]
            summary += f" ({entry['importers'][0]})"
        results.append(
            make_issue(
                "dependencies",
                entry["file"],
                f"{entry['kind']}::{entry['name']}",
                tier=tier,
                confidence=entry["confidence"],
                summary=summary.format(name=entry["name"]),
                detail=detail,
            )
        )
    log(f"         {result.population_size} checks → {len(results)} issues")
    return results, {"dependencies": result.population_size}


def phase_deprecated(
    path: Path, lang: LangRuntimeContract
) -> tuple[list[Issue], dict[str, int]]:
    dep_result = deprecated_detector_mod.detect_deprecated_result(path)
    dep_entries = dep_result.entries
    total_deprecated = dep_result.population_size
    results = []
    for entry in dep_entries:
        if entry["kind"] in ("property", "overload"):
            continue
        exported = entry.get("exported", True)
        unused = (
            entry["importers"] == 0 and not exported and not entry.get("same_file_uses")
        )
        if unused:
            note = " → unused, remove it"
        elif exported and entry["importers"] == 0:
            # Zero importers inside the scan does not mean zero callers: an
            # exported symbol may be public API used by other packages.
            note = " → exported; may still be public API"
        else:
            note = ""
        results.append(
            make_issue(
                "deprecated",
                entry["file"],
                entry["symbol"],
                tier=1 if unused else 3,
                confidence="high",
                summary=f"Deprecated: {entry['symbol']} ({entry['importers']} importers){note}",
                detail={
                    "importers": entry["importers"],
                    "line": entry["line"],
                    "exported": exported,
                },
            )
        )
    log(
        f"         {len(dep_entries)} instances → {len(results)} issues (members and overloads suppressed)"
    )
    return results, {"deprecated": total_deprecated}


__all__ = [
    "phase_dependencies",
    "phase_deprecated",
    "phase_exports",
    "phase_lint",
    "phase_logs",
    "phase_type_errors",
    "phase_unused",
]
