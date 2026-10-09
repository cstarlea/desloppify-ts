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
from desloppify.languages.typescript.detectors.bounded import Budget
from desloppify.languages.typescript.detectors.deps.packages import discover_packages
from desloppify.languages.typescript.detectors.lint import biome as biome_mod
from desloppify.languages.typescript.detectors.lint import eslint as eslint_mod
from desloppify.languages.typescript.detectors.lint import oxlint as oxlint_mod
from desloppify.languages.typescript.detectors.lint.configs import (
    LinterConfig,
    find_lint_configs,
    find_local_bin,
    nested_config_dirs,
)
from desloppify.languages.typescript.detectors.lint.rules import classify
from desloppify.languages.typescript.detectors.lint.runner import (
    LINT_TIMEOUT,
    LinterRun,
    LintMessage,
    bounded_runs,
)

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
    packages: list[PackageLint] | None = None  # monorepo mode only


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
        return path.relative_to(get_project_root().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


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
            return isinstance(manifest, dict) and any(
                manifest.get(f) for f in _DEPENDENCY_FIELDS
            )
    return False


def _skip_reason(
    config: LinterConfig, files: list[Path], type_aware_max_files: int
) -> tuple[Path | None, str, str, str]:
    """``(binary, reason, summary, remediation)``; ``reason`` is empty when the linter can run."""
    label, name = config.label, _display(config.path)
    binary = find_local_bin(config.linter, config.directory)
    if binary is None:
        if _dependencies_missing(config.directory):
            return (
                None,
                "deps_not_installed",
                (
                    f"Dependencies are not installed (no node_modules); {label} ({name}) not run"
                ),
                "Install the project's dependencies and rerun scan.",
            )
        return (
            None,
            "linter_missing",
            (
                f"{label} is configured ({name}) but not installed in node_modules; lint not run"
            ),
            f"Install {label} in the project and rerun scan.",
        )
    if config.linter not in ("eslint", "xo"):
        return binary, "", "", ""
    sample = next((f for f in files if f.suffix in _TS_SUFFIXES), files[0])
    type_aware, failure, error = eslint_mod.uses_type_information(
        binary, config, sample
    )
    if failure is not None:
        return (
            binary,
            failure,
            f"{label} could not load {name} ({error}); lint not run",
            (
                f"Fix the {label} config or its plugins (`{config.linter} --print-config <file>`) and rerun scan."
            ),
        )
    if type_aware and 0 < type_aware_max_files < len(files):
        return (
            binary,
            "type_aware_too_large",
            (
                f"{name} uses type information and the scan has {len(files)} files to lint"
                f" (limit {type_aware_max_files} for a type-aware run); lint not run"
            ),
            (
                "Scan a package directory, or raise languages.typescript.lint_type_aware_max_files"
                " in config.json (0 = no limit)."
            ),
        )
    return binary, "", "", ""


def _ignore_patterns(
    config_dir: Path, scan_root: Path, nested: list[Path]
) -> list[str]:
    patterns = []
    excluded = [Path(p) for p in collect_exclude_dirs(scan_root) if Path(p).is_dir()]
    for directory in [*nested, *excluded]:
        relative = os.path.relpath(directory, config_dir).replace(os.sep, "/")
        patterns.append(f"{relative}/**")
    return patterns


def _run(
    config: LinterConfig,
    binary: Path,
    scan_root: Path,
    nested: list[Path],
    files: set[Path],
) -> LinterRun:
    targets = [os.path.relpath(scan_root, config.directory).replace(os.sep, "/")]
    ignores = _ignore_patterns(config.directory, scan_root, nested)
    if config.linter == "biome":
        return biome_mod.run_biome(binary, config, targets, files)
    if config.linter == "oxlint":
        return oxlint_mod.run_oxlint(binary, config, targets, ignores, files)
    return eslint_mod.run_eslint(binary, config, targets, ignores)


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
    path: Path,
    *,
    type_aware_max_files: int = DEFAULT_TYPE_AWARE_MAX_FILES,
    monorepo: Budget | None = None,
) -> LintResult:
    """Lint findings in the scan path from the nearest configured linter.

    With ``monorepo`` (a budget), each directory under the scan path with its
    own flat ESLint config is also linted with that config, one at a time;
    and when the scan's own config is type-aware and too large, it runs once
    per workspace package, each under the same file limit.
    """
    configs = find_lint_configs(path)
    if not configs:
        return LintResult([], None, None)
    scan_root = path.resolve()
    if scan_root.is_file():
        scan_root = scan_root.parent
    project_root = get_project_root()
    config_dir = configs[0].directory
    nested = nested_config_dirs(scan_root, config_dir)

    all_files = [(project_root / name).resolve() for name in find_ts_and_js_files(path)]
    in_scope = [f for f in all_files if not any(d in f.parents for d in nested)]
    foreign = len(all_files) - len(in_scope)
    notes: list[tuple[str, str, str]] = []  # (reason, summary, remediation)
    if foreign and monorepo is None:
        shown = ", ".join(_display(d) for d in nested[:3])
        more = f" and {len(nested) - 3} more" if len(nested) > 3 else ""
        notes.append(
            (
                "partial",
                f"{foreign} files are under other lint configs ({shown}{more}) and were not linted",
                "Scan a package's directory to lint it with its own config.",
            )
        )
    if not in_scope and monorepo is None:
        return LintResult([], [], _coverage(notes, ran=True))

    base = _Unit(configs, scan_root, nested, in_scope, scan_root)
    runs: list[tuple[LinterRun, int]] = []  # (run, index of its unit)
    scopes: list[set[Path]] = [set(in_scope)]
    reasons: list[str] = []
    if in_scope:
        base_runs, reasons = _lint(base, type_aware_max_files, notes)
        runs = [(run, 0) for run in base_runs]

    packages: list[PackageLint] | None = None
    if monorepo is not None:
        units = _package_units(all_files, base)
        if "type_aware_too_large" in reasons:
            notes[:] = [n for n in notes if n[0] != "type_aware_too_large"]
            type_aware = [c for c in configs if c.linter in ("eslint", "xo")]
            units = _split_units(base, type_aware, scan_root, project_root) + units
        packages = []
        for unit in units:
            limits = monorepo.limits(LINT_TIMEOUT)
            if limits is None:
                packages.append(PackageLint(unit.label, len(unit.files), "time_budget"))
                continue
            unit_notes: list[tuple[str, str, str]] = []
            with bounded_runs(limits):
                unit_runs, unit_reasons = _lint(unit, type_aware_max_files, unit_notes)
            scopes.append(set(unit.files))
            runs.extend((run, len(scopes) - 1) for run in unit_runs)
            skipped = unit_reasons[0] if unit_reasons and not unit_runs else None
            packages.append(PackageLint(unit.label, len(unit.files), skipped))
        if (note := _packages_note(packages)) is not None:
            notes.append(note)

    if not runs:
        return LintResult([], None, _coverage(notes, ran=False), packages=packages)
    # A file belongs to the first unit that linted it; every linter of that unit counts.
    owner: dict[Path, int] = {}
    for run, unit in runs:
        for file in run.files & scopes[unit]:
            owner.setdefault(file, unit)
    checked = set(owner)
    unparsed = {f for run, unit in runs for f in run.unparsed if owner.get(f) == unit}
    messages = [
        m for run, unit in runs for m in run.messages if owner.get(m.file) == unit
    ]
    if unparsed:
        notes.append(
            (
                "partial",
                f"{len(unparsed)} files could not be parsed by the linter and were not counted",
                "Run the linter on those files to see the parse errors.",
            )
        )
    checked -= unparsed
    messages = [m for m in messages if m.file in checked]
    return LintResult(
        _group(messages),
        sorted(str(f) for f in checked),
        _coverage(notes, ran=True),
        tuple(dict.fromkeys(run.config.linter for run, _unit in runs)),
        packages,
    )


_SKIP_REASONS = {
    "time_budget": "time budget spent",
    "type_aware_too_large": "type-aware and over the file limit",
    "linter_timeout": "timed out",
    "linter_oom": "over the memory limit",
    "deps_not_installed": "dependencies not installed",
    "linter_missing": "linter not installed",
    "linter_failed": "linter failed",
}


def _packages_note(packages: list[PackageLint]) -> tuple[str, str, str] | None:
    skipped = [p for p in packages if p.skipped is not None]
    if not skipped:
        return None
    by_reason: dict[str, list[str]] = defaultdict(list)
    for package in skipped:
        by_reason[package.skipped or ""].append(_display(package.directory) or ".")
    reasons = "; ".join(
        f"{_SKIP_REASONS.get(reason, reason)}: "
        + ", ".join(dirs[:3])
        + (f" and {len(dirs) - 3} more" if len(dirs) > 3 else "")
        for reason, dirs in sorted(by_reason.items())
    )
    return (
        "partial",
        f"{len(skipped)} of {len(packages)} packages were not linted"
        f" ({sum(p.files for p in skipped)} files; {reasons})",
        "Raise languages.typescript.monorepo_budget_seconds, monorepo_max_memory_mb or"
        " lint_type_aware_max_files, or scan the package's directory.",
    )


@dataclass
class _Unit:
    """One linter invocation's scope: run ``configs`` on ``root``, leaving out ``ignored``."""

    configs: list[LinterConfig]
    root: Path
    ignored: list[Path]
    files: list[Path]
    label: Path  # the package directory, for coverage notes


@dataclass(frozen=True)
class PackageLint:
    """One package in monorepo mode: linted, or the reason it wasn't."""

    directory: Path
    files: int
    skipped: str | None = None


def _lint(
    unit: _Unit, type_aware_max_files: int, notes: list[tuple[str, str, str]]
) -> tuple[list[LinterRun], list[str]]:
    """Run every linter of ``unit``; returns the runs and the skip reasons."""
    runs: list[LinterRun] = []
    reasons: list[str] = []
    for config in unit.configs:
        binary, reason, summary, remediation = _skip_reason(
            config, unit.files, type_aware_max_files
        )
        if reason or binary is None:
            notes.append((reason, summary, remediation))
            reasons.append(reason)
            continue
        run = _run(config, binary, unit.root, unit.ignored, set(unit.files))
        if run.failure is not None:
            notes.append(
                (
                    run.failure,
                    f"{config.label} did not run correctly ({run.error}); lint not run",
                    f"Run `{config.linter}` in {_display(config.directory)} to see the error, fix it and rerun scan.",
                )
            )
            reasons.append(run.failure)
            continue
        runs.append(run)
    return runs, reasons


def _files_under(files: list[Path], directory: Path, ignored: list[Path]) -> list[Path]:
    return [
        f
        for f in files
        if directory in f.parents and not any(d in f.parents for d in ignored)
    ]


def _package_units(all_files: list[Path], base: _Unit) -> list[_Unit]:
    """Each directory with its own flat ESLint config, linted with that config from
    that directory (and so on for the configs nested in it)."""
    units: list[_Unit] = []
    pending = list(base.ignored)
    seen: set[Path] = set()
    while pending:
        directory = pending.pop(0)
        if directory in seen:
            continue
        seen.add(directory)
        nested = nested_config_dirs(directory, directory)
        pending.extend(nested)
        files = _files_under(all_files, directory, nested)
        configs = find_lint_configs(directory)
        if files and configs:
            units.append(_Unit(configs, directory, nested, files, directory))
    return units


def _split_units(
    base: _Unit, configs: list[LinterConfig], scan_root: Path, project_root: Path
) -> list[_Unit]:
    """The scan's own type-aware configs, once per workspace package inside the scan
    path and once for the files outside them."""
    packages = sorted(
        package.directory
        for package in discover_packages(scan_root, project_root)
        if scan_root in package.directory.parents
        and not any(
            d == package.directory or d in package.directory.parents
            for d in base.ignored
        )
    )
    units = []
    for directory in packages:
        inner = [p for p in packages if directory in p.parents]
        files = _files_under(base.files, directory, inner)
        if files:
            units.append(
                _Unit(configs, directory, [*base.ignored, *inner], files, directory)
            )
    rest = [f for f in base.files if not any(p in f.parents for p in packages)]
    if rest:
        units.append(
            _Unit(configs, scan_root, [*base.ignored, *packages], rest, scan_root)
        )
    return units


def _coverage(
    notes: list[tuple[str, str, str]], *, ran: bool
) -> DetectorCoverageStatus | None:
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


__all__ = [
    "DEFAULT_TYPE_AWARE_MAX_FILES",
    "LintResult",
    "PackageLint",
    "detect_lint_result",
]
