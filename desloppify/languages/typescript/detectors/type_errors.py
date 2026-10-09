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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.sfc import is_sfc
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.languages._framework.base.types import DetectorCoverageStatus
from desloppify.languages.typescript.detectors.deps.resolve import find_nearest_tsconfig
from desloppify.languages.typescript.detectors.tsc import (
    COMPONENTS_REMEDIATION,
    UNUSED_CODES,
    TscDiagnostic,
    run_tsc,
    unchecked_components_note,
)
from desloppify.languages.typescript.detectors.unused_fallback import should_use_deno_fallback

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


def detect_type_errors_result(
    path: Path, *, cache: dict[str, Any] | None = None
) -> TypeErrorResult:
    """Type errors in the scan path from the scan's shared tsc run."""
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

    run_tsconfig = tsconfig.resolve()
    owners = _Owners()

    def resolve(name: str) -> Path:
        file = Path(name)
        return (file if file.is_absolute() else project_root / file).resolve()

    checked: set[Path] = set()
    foreign: dict[Path, set[Path]] = defaultdict(set)
    for name in run.files:
        file = resolve(name)
        if "node_modules" in file.parts or not _within(file, scan_root):
            continue
        owner = owners(file)
        if owner is not None and owner.resolve() != run_tsconfig:
            foreign[owner.resolve()].add(file)
            continue
        checked.add(file)

    grouped: dict[tuple[Path, str, int], list[TscDiagnostic]] = defaultdict(list)
    environment: set[Path] = set()
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
            environment.add(file)
            continue
        grouped[(file, diagnostic.code, diagnostic.line)].append(diagnostic)

    # A file that can't resolve a package has its other errors in doubt
    # (cascades through the missing types), so none of them are reported.
    hidden = 0
    entries = []
    for (file, code, line), diagnostics in sorted(grouped.items()):
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
    checked_files = sorted(str(file) for file in checked - environment)
    return TypeErrorResult(
        entries,
        checked_files,
        _coverage(foreign, environment, hidden, tsconfig, unchecked_components_note(path)),
    )


def _coverage(
    foreign: dict[Path, set[Path]],
    environment: set[Path],
    hidden: int,
    tsconfig: Path,
    components: str | None = None,
) -> DetectorCoverageStatus | None:
    notes = []
    remediation = []
    if components:
        notes.append(components)
        remediation.append(COMPONENTS_REMEDIATION)
    if foreign:
        files = sum(len(group) for group in foreign.values())
        root = get_project_root()
        configs = sorted(_display(config, root) for config in foreign)
        shown = ", ".join(configs[:3]) + (f" and {len(configs) - 3} more" if len(configs) > 3 else "")
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


__all__ = ["TypeErrorResult", "detect_type_errors_result"]
