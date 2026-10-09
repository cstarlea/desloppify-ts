"""Findings from the project's own linter, run with the project's own config.

The linter is the one configured in the nearest directory at or above the
scan path, run from that directory with its local binary. A directory under
the scan path with its own lint config belongs to another project (a
package in a monorepo): it is left out of the run and named in reduced
coverage, as type_error does for other tsconfigs. When the linter can't run
(not installed, broken config, crash, too large for a type-aware run), the
detector reports no potential, so the Lint dimension is carried forward.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import collect_exclude_dirs, find_ts_and_js_files
from desloppify.languages._framework.base.types import DetectorCoverageStatus
from desloppify.languages.typescript.detectors.lint import eslint as eslint_mod
from desloppify.languages.typescript.detectors.lint.configs import (
    LinterConfig,
    find_lint_configs,
    nested_config_dirs,
)
from desloppify.languages.typescript.detectors.lint.eslint import LinterRun, LintMessage
from desloppify.languages.typescript.detectors.lint.rules import classify

DEFAULT_TYPE_AWARE_MAX_FILES = 400
_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "peerDependencies")
_TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")


@dataclass(frozen=True)
class LintResult:
    """``checked_files`` is None when no linter ran (none configured, or all skipped)."""

    entries: list[dict]
    checked_files: list[str] | None
    coverage: DetectorCoverageStatus | None
    linters: tuple[str, ...] = ()


def _reduced(
    summary: str, *, reason: str, confidence: float, remediation: str, tool: str
) -> DetectorCoverageStatus:
    return DetectorCoverageStatus(
        detector="lint",
        status="reduced",
        confidence=confidence,
        summary=summary,
        impact="Lint findings may be under-reported for this scan.",
        remediation=remediation,
        tool=tool,
        reason=reason,
    )


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(get_project_root().resolve()))
    except ValueError:
        return str(path)


def _dependencies_missing(directory: Path) -> bool:
    """True when the project declares dependencies but no node_modules exists above it."""
    if any((d / "node_modules").is_dir() for d in (directory, *directory.parents)):
        return False
    for candidate in (directory, *directory.parents):
        manifest_path = candidate / "package.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            return isinstance(manifest, dict) and any(manifest.get(f) for f in _DEPENDENCY_FIELDS)
    return False


def _skip_reason(
    config: LinterConfig, files: list[Path], type_aware_max_files: int
) -> tuple[Path | None, str, str, str]:
    """``(binary, reason, summary, remediation)``; ``reason`` is empty when the linter can run."""
    label, name = config.label, _display(config.path)
    binary = eslint_mod.eslint_binary(config)
    if binary is None:
        if _dependencies_missing(config.directory):
            return None, "deps_not_installed", (
                f"Dependencies are not installed (no node_modules); {label} ({name}) not run"
            ), "Install the project's dependencies and rerun scan."
        return None, "linter_missing", (
            f"{label} is configured ({name}) but not installed in node_modules; lint not run"
        ), f"Install {label} in the project and rerun scan."
    sample = next((f for f in files if f.suffix in _TS_SUFFIXES), files[0])
    type_aware, failure, error = eslint_mod.uses_type_information(binary, config, sample)
    if failure is not None:
        return binary, failure, f"{label} could not load {name} ({error}); lint not run", (
            f"Fix the {label} config or its plugins (`{label.lower()} --print-config <file>`) and rerun scan."
        )
    if type_aware and 0 < type_aware_max_files < len(files):
        return binary, "type_aware_too_large", (
            f"{name} uses type information and the scan has {len(files)} files to lint"
            f" (limit {type_aware_max_files} for a type-aware run); lint not run"
        ), (
            "Scan a package directory, or raise languages.typescript.lint_type_aware_max_files"
            " in config.json (0 = no limit)."
        )
    return binary, "", "", ""


def _ignore_patterns(config_dir: Path, scan_root: Path, nested: list[Path]) -> list[str]:
    patterns = []
    excluded = [Path(p) for p in collect_exclude_dirs(scan_root) if Path(p).is_dir()]
    for directory in [*nested, *excluded]:
        relative = os.path.relpath(directory, config_dir).replace(os.sep, "/")
        patterns.append(f"{relative}/**")
    return patterns


def _group(messages: list[LintMessage]) -> list[dict]:
    grouped: dict[tuple[Path, str, int], list[LintMessage]] = defaultdict(list)
    for message in messages:
        grouped[(message.file, message.rule, message.line)].append(message)
    entries = []
    for (file, rule, line), group in sorted(grouped.items()):
        severity = "error" if any(m.severity == "error" for m in group) else "warning"
        first = group[0]
        confidence = classify(rule, severity, first.meta)
        if confidence is None:
            continue
        entries.append(
            {
                "file": str(file),
                "line": line,
                "cols": sorted({m.col for m in group}),
                "rule": rule,
                "severity": severity,
                "message": first.message,
                "count": len(group),
                "fixable": all(m.fixable for m in group),
                "confidence": confidence,
            }
        )
    return entries


def detect_lint_result(
    path: Path, *, type_aware_max_files: int = DEFAULT_TYPE_AWARE_MAX_FILES
) -> LintResult:
    """Lint findings in the scan path from the nearest configured linter."""
    configs = find_lint_configs(path)
    if not configs:
        return LintResult([], None, None)
    scan_root = path.resolve()
    if scan_root.is_file():
        scan_root = scan_root.parent
    project_root = get_project_root()
    config_dir = configs[0].directory
    nested = nested_config_dirs(scan_root, config_dir)

    in_scope: list[Path] = []
    foreign: list[Path] = []
    for name in find_ts_and_js_files(path):
        file = (project_root / name).resolve()
        (foreign if any(d in file.parents for d in nested) else in_scope).append(file)
    notes: list[tuple[str, str, str]] = []  # (reason, summary, remediation)
    if foreign:
        shown = ", ".join(_display(d) for d in nested[:3])
        more = f" and {len(nested) - 3} more" if len(nested) > 3 else ""
        notes.append((
            "partial",
            f"{len(foreign)} files are under other lint configs ({shown}{more}) and were not linted",
            "Scan a package's directory to lint it with its own config.",
        ))
    if not in_scope:
        return LintResult([], [], _coverage(notes, ran=True))

    scoped = set(in_scope)
    runs: list[LinterRun] = []
    for config in configs:
        binary, reason, summary, remediation = _skip_reason(config, in_scope, type_aware_max_files)
        if reason or binary is None:
            notes.append((reason, summary, remediation))
            continue
        relative_root = os.path.relpath(scan_root, config_dir).replace(os.sep, "/")
        run = eslint_mod.run_eslint(
            binary, config, [relative_root], _ignore_patterns(config_dir, scan_root, nested)
        )
        if run.failure is not None:
            notes.append((
                run.failure,
                f"{config.label} did not run correctly ({run.error}); lint not run",
                f"Run `{config.label.lower()}` in {_display(config_dir)} to see the error, fix it and rerun scan.",
            ))
            continue
        runs.append(run)

    if not runs:
        return LintResult([], None, _coverage(notes, ran=False))
    linted = set().union(*(run.files for run in runs)) & scoped
    unparsed = set().union(*(run.unparsed for run in runs)) & linted
    if unparsed:
        notes.append((
            "partial",
            f"{len(unparsed)} files could not be parsed by the linter and were not counted",
            "Run the linter on those files to see the parse errors.",
        ))
    checked = linted - unparsed
    messages = [m for run in runs for m in run.messages if m.file in checked]
    return LintResult(
        _group(messages),
        sorted(str(f) for f in checked),
        _coverage(notes, ran=True),
        tuple(run.config.linter for run in runs),
    )


def _coverage(notes: list[tuple[str, str, str]], *, ran: bool) -> DetectorCoverageStatus | None:
    if not notes:
        return None
    reasons = [reason for reason, _, _ in notes]
    return _reduced(
        "; ".join(summary for _, summary, _ in notes),
        reason=reasons[0] if len(set(reasons)) == 1 else "partial",
        confidence=0.7 if ran else 0.0,
        remediation=" ".join(dict.fromkeys(remediation for _, _, remediation in notes)),
        tool="lint",
    )


__all__ = ["DEFAULT_TYPE_AWARE_MAX_FILES", "LintResult", "detect_lint_result"]
