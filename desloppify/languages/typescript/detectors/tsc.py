"""One tsc run per scan, shared by the unused and type_error detectors.

tsc runs once on the scan's nearest tsconfig with ``noUnusedLocals`` and
``noUnusedParameters`` forced on. Those flags only add the TS6133 family of
diagnostics, so the same output serves both detectors. ``--listFiles`` adds
the program's files, which is what tsc actually checked.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from desloppify.base.discovery.source import find_component_files

logger = logging.getLogger(__name__)
_proc_runtime = subprocess

# Every diagnostic tsc emits for noUnusedLocals / noUnusedParameters.
UNUSED_CODES = frozenset({"TS6133", "TS6138", "TS6192", "TS6196", "TS6198", "TS6199", "TS6205"})
_DIAGNOSTIC_RE = re.compile(r"^(?:(.+)\((\d+),(\d+)\): )?error (TS\d+): (.*)$")
_TS_DIAGNOSTIC_RE = re.compile(r"error TS(\d+):")
# TS5xxx are compiler-option/config errors; TS18003 is "no inputs were found".
_TS_CONFIG_ERROR_RE = re.compile(r"error TS(5\d{3}|18003):")
# Printed by the unrelated `tsc` npm package that npx can fetch by mistake.
_BOGUS_TSC_MARKER = "This is not the tsc command you are looking for"
TSC_TIMEOUT = 120
_CACHE_KEY = "typescript.tsc_runs"


@dataclass(frozen=True)
class TscDiagnostic:
    """One tsc error. ``file`` is None for global errors (no location)."""

    file: str | None
    line: int
    col: int
    code: str
    message: str


@dataclass
class TscRun:
    """The outcome of one tsc run; ``failure`` says why it is unusable, if it is."""

    project_root: Path
    tsconfig: Path
    failure: str | None = None
    error: str = ""
    output_lines: list[str] = field(default_factory=list)
    diagnostics: list[TscDiagnostic] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    config_errors: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.failure is None


def resolve_tsc_command(*start_dirs: Path) -> list[str]:
    """Find a real TypeScript compiler without downloading anything.

    Walks up from each start directory looking for ``node_modules/.bin/tsc``
    (which covers hoisted monorepo installs), then falls back to a global
    ``tsc``. ``npx tsc`` is deliberately avoided: without a local install it
    fetches an unrelated npm package named ``tsc``.
    """
    names = ("tsc.cmd", "tsc") if os.name == "nt" else ("tsc",)
    for start in start_dirs:
        for directory in (start, *start.parents):
            for name in names:
                candidate = directory / "node_modules" / ".bin" / name
                if candidate.is_file():
                    return [str(candidate)]
    tsc_path = shutil.which("tsc")
    if tsc_path:
        return [tsc_path]
    raise OSError("TypeScript compiler not found (install `typescript` in the project)")


def run_tsc_check(
    project_root: Path,
    tsconfig_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run tsc on ``tsconfig_path`` with the unused-symbol checks enabled."""
    cmd = resolve_tsc_command(tsconfig_path.parent, project_root)
    return _proc_runtime.run(  # nosec B603
        [
            *cmd,
            "--project",
            str(tsconfig_path),
            "--noEmit",
            "--noUnusedLocals",
            "--noUnusedParameters",
            "--listFiles",
            "--pretty",
            "false",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=project_root,
        timeout=TSC_TIMEOUT,
    )


def tsc_failure_reason(result: subprocess.CompletedProcess[str]) -> str | None:
    """Return why a tsc run produced no usable results, or None if it is usable."""
    output = f"{result.stdout}\n{result.stderr}"
    if _BOGUS_TSC_MARKER in output:
        return "wrong_tsc_package"
    if result.returncode not in (0, 1, 2) or (
        result.returncode != 0 and not _TS_DIAGNOSTIC_RE.search(output)
    ):
        return "tsc_failed"
    return None


def parse_tsc_output(lines: list[str]) -> tuple[list[TscDiagnostic], list[str]]:
    """Split ``--pretty false`` output into diagnostics and ``--listFiles`` paths.

    A message chain continues on indented lines; they are joined to the
    diagnostic they follow.
    """
    diagnostics: list[TscDiagnostic] = []
    files: list[str] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        if current is not None:
            diagnostics.append(TscDiagnostic(**current))

    for line in lines:
        if not line.strip():
            continue
        match = _DIAGNOSTIC_RE.match(line)
        if match:
            flush()
            filepath, lineno, col, code, message = match.groups()
            current = {
                "file": filepath,
                "line": int(lineno) if lineno else 0,
                "col": int(col) if col else 0,
                "code": code,
                "message": message,
            }
        elif line[0].isspace() and current is not None:
            current["message"] += "\n" + line.strip()
        else:
            flush()
            current = None
            files.append(line.strip())
    flush()
    return diagnostics, files


def run_tsc(
    project_root: Path,
    tsconfig_path: Path,
    *,
    cache: dict[str, Any] | None = None,
) -> TscRun:
    """Run tsc once for ``tsconfig_path``; later calls with the same cache reuse it."""
    runs = cache.setdefault(_CACHE_KEY, {}) if cache is not None else {}
    key = (str(project_root), str(tsconfig_path))
    if key in runs:
        return runs[key]
    run = TscRun(project_root=project_root, tsconfig=tsconfig_path)
    runs[key] = run
    try:
        result = run_tsc_check(project_root, tsconfig_path)
    except (_proc_runtime.SubprocessError, OSError) as exc:
        logger.debug("tsc did not run: %s", exc)
        run.failure, run.error = "tsc_missing", str(exc)
        return run
    failure = tsc_failure_reason(result)
    if failure is not None:
        logger.debug("tsc produced no usable output (%s): %s", failure, result.stderr[-500:])
        run.failure = failure
        return run
    run.output_lines = result.stdout.splitlines() + result.stderr.splitlines()
    run.config_errors = [line for line in run.output_lines if _TS_CONFIG_ERROR_RE.search(line)]
    run.diagnostics, run.files = parse_tsc_output(run.output_lines)
    return run


def unchecked_components_note(path: Path) -> str | None:
    """Why tsc's findings leave out the scan's components, or None without any.

    tsc doesn't read ``.vue``/``.svelte``/``.astro`` files; their framework's
    checker (vue-tsc, svelte-check, astro check) does, and desloppify doesn't
    run those.
    """
    count = len(find_component_files(path))
    if not count:
        return None
    return (
        f"{count} components (.vue/.svelte/.astro) were not type-checked: tsc doesn't read them"
        " (vue-tsc, svelte-check or astro check does)"
    )


COMPONENTS_REMEDIATION = "Run vue-tsc, svelte-check or astro check for the components."


__all__ = [
    "COMPONENTS_REMEDIATION",
    "TSC_TIMEOUT",
    "TscDiagnostic",
    "TscRun",
    "UNUSED_CODES",
    "parse_tsc_output",
    "resolve_tsc_command",
    "run_tsc",
    "run_tsc_check",
    "tsc_failure_reason",
    "unchecked_components_note",
]
