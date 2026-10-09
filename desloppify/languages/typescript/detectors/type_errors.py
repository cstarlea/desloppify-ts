"""Type errors reported by tsc, read from the run the unused detector shares.

Only files whose own tsconfig is the one tsc ran with are reported: in a
monorepo the root config is not the config a package is checked with, so
its errors there are artifacts (trpc's root config reports 146 errors in
one Next.js example whose own config reports 35). Errors that come from a
missing dependency rather than from the code are environment noise: the
files they occur in are counted, not reported.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.sfc import is_sfc
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.languages._framework.base.types import DetectorCoverageStatus
from desloppify.languages.typescript.detectors.bounded import Budget
from desloppify.languages.typescript.detectors.deps.packages import discover_packages
from desloppify.languages.typescript.detectors.deps.resolve import (
    find_nearest_tsconfig,
    read_tsconfig,
)
from desloppify.languages.typescript.detectors.tsc import (
    COMPONENTS_REMEDIATION,
    UNUSED_CODES,
    TscDiagnostic,
    TscRun,
    run_tsc,
    unchecked_components_note,
)
from desloppify.languages.typescript.detectors.unused_fallback import (
    should_use_deno_fallback,
)

# Compiler-option and project errors (TS5xxx, TS6xxx, "no inputs"): config, not code.
_CONFIG_CODE_RE = re.compile(r"^TS(5\d{3}|6\d{3}|18003)$")
# A package or its types can't be found: an uninstalled or untyped dependency.
_MODULE_NOT_FOUND = frozenset({"TS2307", "TS2792"})
_ENVIRONMENT_CODES = frozenset(
    {
        "TS2688",  # Cannot find type definition file for 'x'
        "TS7016",  # Could not find a declaration file for module 'x'
        "TS2580",  # Cannot find name 'require'/'process'. Install @types/node
        "TS2591",  # same, with "add 'node' to types"
        "TS2582",  # Cannot find name 'describe'/'it'. Install test-runner types
        "TS2593",  # same, with "add 'jest' or 'mocha' to types"
        "TS2875",  # JSX runtime module not found
        "TS7026",  # JSX element implicitly has type 'any': no JSX types (React's not installed)
    }
)
# Errors whose presence depends on compiler options or ambient types more
# than on the code: strictness (implicit any), index-signature access,
# module-format interop, unknown globals, stale @ts-expect-error.
_MEDIUM_CONFIDENCE_CODES = frozenset(
    {
        "TS7005",
        "TS7006",
        "TS7008",
        "TS7015",
        "TS7031",
        "TS7034",
        "TS7053",
        "TS4111",
        "TS2686",
        "TS1259",
        "TS1479",
        "TS2304",
        "TS2578",
    }
)
_QUOTED_RE = re.compile(r"'([^']+)'")
_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "peerDependencies")


@dataclass(frozen=True)
class TypeErrorResult:
    """``checked_files`` is None when tsc didn't check anything (skipped)."""

    entries: list[dict]
    checked_files: list[str] | None
    coverage: DetectorCoverageStatus | None
    packages: list[PackageCheck] | None = None  # monorepo mode only


# Each package run's own time cap, within the budget's total.
TSC_PACKAGE_TIMEOUT = 300
_INSTALL = "Install the project's dependencies (including `typescript`) and rerun scan."


def _reduced(
    summary: str, *, reason: str, confidence: float, remediation: str = _INSTALL
) -> DetectorCoverageStatus:
    return DetectorCoverageStatus(
        detector="type_error",
        status="reduced",
        confidence=confidence,
        summary=summary,
        impact="Type errors may be under-reported for this scan.",
        remediation=remediation,
        tool="tsc",
        reason=reason,
    )


def _skipped(summary: str, *, reason: str) -> TypeErrorResult:
    return TypeErrorResult([], None, _reduced(summary, reason=reason, confidence=0.0))


def _is_environment(diagnostic: TscDiagnostic) -> bool:
    if diagnostic.code in _ENVIRONMENT_CODES:
        return True
    if diagnostic.code in _MODULE_NOT_FOUND:
        match = _QUOTED_RE.search(diagnostic.message)
        return match is not None and not match.group(1).startswith((".", "/"))
    return False


def _imports_component(diagnostic: TscDiagnostic) -> bool:
    if diagnostic.code not in _MODULE_NOT_FOUND:
        return False
    match = _QUOTED_RE.search(diagnostic.message)
    return match is not None and is_sfc(match.group(1))


def _declares_dependencies(directory: Path) -> bool:
    for candidate in (directory, *directory.parents):
        manifest_path = candidate / "package.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(manifest, dict) and any(
            manifest.get(name) for name in _DEPENDENCY_FIELDS
        )
    return False


def _dependencies_missing(tsconfig: Path) -> bool:
    """True when the project declares dependencies but none are installed anywhere above it."""
    directory = tsconfig.parent
    if any((d / "node_modules").is_dir() for d in (directory, *directory.parents)):
        return False
    return _declares_dependencies(directory)


class _Owners:
    """The tsconfig that owns each file (the nearest one, as for the scan path)."""

    def __init__(self) -> None:
        self._by_dir: dict[Path, Path | None] = {}

    def __call__(self, file: Path) -> Path | None:
        directory = file.parent
        if directory not in self._by_dir:
            self._by_dir[directory] = find_nearest_tsconfig(directory)
        return self._by_dir[directory]


def _within(file: Path, root: Path) -> bool:
    return file == root or root in file.parents


@dataclass
class _Collected:
    """What the tsc runs found, merged: the first run to report a key keeps it."""

    checked: set[Path] = field(default_factory=set)
    grouped: dict[tuple[Path, str, int], list[TscDiagnostic]] = field(default_factory=dict)
    environment: set[Path] = field(default_factory=set)


@dataclass(frozen=True)
class PackageCheck:
    """One package tsconfig in monorepo mode: checked, or the reason it wasn't."""

    tsconfig: Path
    files: int
    skipped: str | None = None
    unbuilt: tuple[str, ...] = ()  # workspace packages it imports that aren't built


def _collect(
    run: TscRun,
    owner_config: Path,
    scan_root: Path,
    owners: _Owners,
    collected: _Collected,
) -> dict[Path, set[Path]]:
    """Add the errors in the files ``owner_config`` owns; return other configs' files."""

    def resolve(name: str) -> Path:
        file = Path(name)
        return (file if file.is_absolute() else run.project_root / file).resolve()

    checked: set[Path] = set()
    foreign: dict[Path, set[Path]] = defaultdict(set)
    for name in run.files:
        file = resolve(name)
        if "node_modules" in file.parts or not _within(file, scan_root):
            continue
        owner = owners(file)
        if owner is not None and owner.resolve() != owner_config:
            foreign[owner.resolve()].add(file)
            continue
        checked.add(file)

    grouped: dict[tuple[Path, str, int], list[TscDiagnostic]] = defaultdict(list)
    for diagnostic in run.diagnostics:
        if diagnostic.file is None:
            continue
        if diagnostic.code in UNUSED_CODES or _CONFIG_CODE_RE.match(diagnostic.code):
            continue
        file = resolve(diagnostic.file)
        if file not in checked:
            continue
        if _imports_component(diagnostic):
            continue  # a shim-less `import X from './X.vue'`: vue-tsc and friends resolve it
        if _is_environment(diagnostic):
            collected.environment.add(file)
            continue
        grouped[(file, diagnostic.code, diagnostic.line)].append(diagnostic)

    collected.checked |= checked
    for key, diagnostics in grouped.items():
        collected.grouped.setdefault(key, diagnostics)
    return foreign


def detect_type_errors_result(
    path: Path,
    *,
    cache: dict[str, Any] | None = None,
    monorepo: Budget | None = None,
) -> TypeErrorResult:
    """Type errors in the scan path from the scan's shared tsc run.

    With ``monorepo`` (a budget), every other tsconfig that owns TypeScript
    files in the scan path gets a tsc run of its own, one at a time.
    """
    scan_root = path.resolve()
    ts_files = find_ts_and_js_files(path)
    if should_use_deno_fallback(path, ts_files):
        return _skipped(
            "Deno/edge TypeScript context: tsc can't resolve URL imports; type errors not checked",
            reason="deno",
        )
    tsconfig = find_nearest_tsconfig(path)
    if tsconfig is None:
        return _skipped("No tsconfig.json found; type errors not checked", reason="no_tsconfig")
    project_root = get_project_root()
    run = run_tsc(project_root, tsconfig, cache=cache)
    if run.failure == "tsc_missing":
        return _skipped(f"tsc unavailable ({run.error}); type errors not checked", reason="tsc_missing")
    if run.failure is not None:
        return _skipped(
            f"tsc did not run correctly ({run.failure}); type errors not checked",
            reason=run.failure,
        )
    if any("TS5083" in line for line in run.config_errors):
        # SvelteKit's and Nuxt's tsconfigs extend one they generate
        # (.svelte-kit/, .nuxt/); without it tsc checks with default options.
        return _skipped(
            "tsconfig extends a file that doesn't exist (generated by `svelte-kit sync` or"
            " `nuxi prepare`?); type errors not checked",
            reason="tsconfig_extends_missing",
        )
    if _dependencies_missing(tsconfig):
        environment = sum(_is_environment(d) for d in run.diagnostics)
        return _skipped(
            "Dependencies are not installed (no node_modules); type errors not checked"
            f" ({environment} missing-module/type errors from tsc)",
            reason="deps_not_installed",
        )

    owners = _Owners()
    collected = _Collected()
    foreign = _collect(run, tsconfig.resolve(), scan_root, owners, collected)
    packages = None
    if monorepo is not None:
        projects = _package_projects(ts_files, project_root, scan_root, tsconfig.resolve(), owners)
        packages = _check_packages(
            projects, project_root, scan_root, owners, collected, cache=cache, budget=monorepo
        )
        foreign = {}

    # A file that can't resolve a package has its other errors in doubt
    # (cascades through the missing types), so none of them are reported.
    environment = collected.environment
    hidden = 0
    entries = []
    for (file, code, line), diagnostics in sorted(collected.grouped.items()):
        if file in environment:
            hidden += 1
            continue
        first = diagnostics[0]
        entries.append(
            {
                "file": str(file),
                "line": line,
                "cols": sorted({d.col for d in diagnostics}),
                "code": code,
                "message": first.message,
                "count": len(diagnostics),
                "confidence": "medium" if code in _MEDIUM_CONFIDENCE_CODES else "high",
            }
        )
    checked_files = sorted(str(file) for file in collected.checked - environment)
    coverage = _coverage(
        foreign, environment, hidden, tsconfig, unchecked_components_note(path), packages
    )
    return TypeErrorResult(entries, checked_files, coverage, packages)


# ── monorepo mode ────────────────────────────────────────────

_TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
_SKIP_REASONS = {
    "time_budget": "time budget spent",
    "timeout": "tsc timed out",
    "memory": "over the memory limit",
    "deps_not_installed": "dependencies not installed",
    "tsconfig_extends_missing": "extends a missing (generated?) tsconfig",
    "deno": "Deno project",
    "tsc_missing": "tsc not found",
    "workspace_unbuilt": "imports workspace packages that aren't built",
}


def _package_projects(
    ts_files: list[str],
    project_root: Path,
    scan_root: Path,
    base_config: Path,
    owners: _Owners,
) -> dict[Path, int]:
    """Other tsconfigs owning TypeScript files in the scan path, with their file counts."""
    counts: dict[Path, int] = defaultdict(int)
    for name in ts_files:
        if not name.endswith(_TS_SUFFIXES):
            continue
        file = Path(name)
        file = (file if file.is_absolute() else project_root / file).resolve()
        if "node_modules" in file.parts or not _within(file, scan_root):
            continue
        owner = owners(file)
        if owner is not None and owner.resolve() != base_config:
            counts[owner.resolve()] += 1
    return dict(sorted(counts.items()))


def _project_configs(tsconfig: Path) -> list[Path]:
    """The configs to run for a project: a solution config's (``"files": []``)
    references, else the config itself."""
    data = read_tsconfig(tsconfig) or {}
    references = data.get("references")
    if data.get("files") != [] or not isinstance(references, list):
        return [tsconfig]
    configs = []
    for reference in references:
        target = reference.get("path") if isinstance(reference, dict) else None
        if not isinstance(target, str):
            continue
        candidate = (tsconfig.parent / target).resolve()
        if candidate.is_dir():
            candidate = candidate / "tsconfig.json"
        if candidate.is_file():
            configs.append(candidate)
    return configs or [tsconfig]


def _unbuilt_imports(run: TscRun, workspace_names: frozenset[str]) -> set[str]:
    """Workspace packages the run can't find: their manifests point at build output
    that isn't there, so every type that flows from them is ``any``."""
    missing = set()
    for diagnostic in run.diagnostics:
        if diagnostic.code not in _MODULE_NOT_FOUND:
            continue
        match = _QUOTED_RE.search(diagnostic.message)
        if match is None:
            continue
        parts = match.group(1).split("/")
        name = "/".join(parts[:2]) if parts[0].startswith("@") else parts[0]
        if name in workspace_names:
            missing.add(name)
    return missing


def _unusable(run: TscRun, tsconfig: Path) -> str | None:
    """Why a package run can't be read (a reason code), or None."""
    if run.failure is not None:
        return run.failure
    if any("TS5083" in line for line in run.config_errors):
        return "tsconfig_extends_missing"
    if _dependencies_missing(tsconfig):
        return "deps_not_installed"
    return None


def _check_packages(
    projects: dict[Path, int],
    project_root: Path,
    scan_root: Path,
    owners: _Owners,
    collected: _Collected,
    *,
    cache: dict[str, Any] | None,
    budget: Budget,
) -> list[PackageCheck]:
    workspace_names = frozenset(
        package.name for package in discover_packages(scan_root, project_root) if package.name
    )
    checks = []
    for owner_config, files in projects.items():
        skipped = "deno" if should_use_deno_fallback(owner_config.parent, []) else None
        unbuilt: set[str] = set()
        runs = []
        for config in () if skipped else _project_configs(owner_config):
            limits = budget.limits(TSC_PACKAGE_TIMEOUT)
            if limits is None:
                skipped = "time_budget"
                break
            run = run_tsc(project_root, config, cache=cache, limits=limits)
            skipped = _unusable(run, config)
            if skipped is not None:
                break
            unbuilt |= _unbuilt_imports(run, workspace_names)
            runs.append(run)
        if skipped is None and unbuilt:
            skipped = "workspace_unbuilt"
        if skipped is None:
            for run in runs:
                _collect(run, owner_config, scan_root, owners, collected)
        checks.append(PackageCheck(owner_config, files, skipped, tuple(sorted(unbuilt))))
    return checks


def _shown(configs: list[str]) -> str:
    return ", ".join(configs[:3]) + (f" and {len(configs) - 3} more" if len(configs) > 3 else "")


def _packages_note(packages: list[PackageCheck]) -> str | None:
    skipped = [p for p in packages if p.skipped is not None]
    if not skipped:
        return None
    root = get_project_root()
    by_reason: dict[str, list[str]] = defaultdict(list)
    for package in skipped:
        by_reason[package.skipped or ""].append(_display(package.tsconfig, root))
    unbuilt = sorted({name for package in skipped for name in package.unbuilt})

    def label(reason: str) -> str:
        text = _SKIP_REASONS.get(reason, reason)
        return f"{text} ({_shown(unbuilt)})" if reason == "workspace_unbuilt" else text

    reasons = "; ".join(
        f"{label(reason)}: {_shown(configs)}" for reason, configs in sorted(by_reason.items())
    )
    files = sum(p.files for p in skipped)
    return (
        f"{len(skipped)} of {len(packages)} package tsconfigs were not type-checked"
        f" ({files} files; {reasons})"
    )


def _coverage(
    foreign: dict[Path, set[Path]],
    environment: set[Path],
    hidden: int,
    tsconfig: Path,
    components: str | None = None,
    packages: list[PackageCheck] | None = None,
) -> DetectorCoverageStatus | None:
    notes = []
    remediation = []
    if components:
        notes.append(components)
        remediation.append(COMPONENTS_REMEDIATION)
    if packages and (note := _packages_note(packages)):
        notes.append(note)
        remediation.append(
            "Build the workspace packages and install dependencies, raise"
            " languages.typescript.monorepo_budget_seconds or monorepo_max_memory_mb,"
            " or scan the package's directory."
        )
    if foreign:
        files = sum(len(group) for group in foreign.values())
        root = get_project_root()
        shown = _shown(sorted(_display(config, root) for config in foreign))
        notes.append(
            f"{files} files belong to other tsconfigs ({shown}) and were not type-checked"
            f" with {_display(tsconfig, root)}"
        )
        remediation.append("Scan a package's directory to type-check it with its own tsconfig.")
    if environment:
        notes.append(
            f"{len(environment)} files can't resolve a package or its types (dependencies not"
            f" installed?), so their type errors ({hidden} besides the missing modules) were not reported"
        )
        remediation.append("Install the missing dependencies and rerun scan.")
    if not notes:
        return None
    return _reduced(
        "; ".join(notes), reason="partial", confidence=0.7, remediation=" ".join(remediation)
    )


def _display(config: Path, root: Path) -> str:
    try:
        return config.relative_to(root.resolve()).as_posix()
    except ValueError:
        return config.as_posix()


__all__ = ["PackageCheck", "TypeErrorResult", "detect_type_errors_result"]
