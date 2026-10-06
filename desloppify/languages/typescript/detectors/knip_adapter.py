"""Knip adapter — dead exports detection via Knip.

Runs the project's own ``knip --reporter json`` and parses its JSON output
into the entry dicts expected by detect_dead_exports().

Knip understands re-exports, barrel files, dynamic imports, and entry points —
far more accurate than the old grep-based approach.

Knip is only run when it is installed in the project (never fetched with
``npx --yes``). When it cannot run, callers get a reason string so the gap
can be reported as reduced coverage rather than a clean result.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404
from pathlib import Path

from desloppify.base.discovery.file_paths import rel

logger = logging.getLogger(__name__)

_WORKSPACE_MARKERS = ("pnpm-workspace.yaml",)


def _find_package_root(path: Path) -> Path | None:
    """Return the nearest directory at or above ``path`` with a package.json."""
    current = path.resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        if (directory / "package.json").is_file():
            return directory
    return None


def _declares_workspaces(directory: Path) -> bool:
    if any((directory / marker).is_file() for marker in _WORKSPACE_MARKERS):
        return True
    try:
        manifest = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and bool(manifest.get("workspaces"))


def _find_workspace_root(package_root: Path) -> Path | None:
    """Return the monorepo root above ``package_root``, if there is one."""
    for directory in package_root.parents:
        if (directory / "package.json").is_file() and _declares_workspaces(directory):
            return directory
    return None


def _find_knip_bin(*start_dirs: Path) -> Path | None:
    """Find a locally installed knip, walking up to cover hoisted installs."""
    names = ("knip.cmd", "knip") if os.name == "nt" else ("knip",)
    for start in start_dirs:
        for directory in (start, *start.parents):
            for name in names:
                candidate = directory / "node_modules" / ".bin" / name
                if candidate.is_file():
                    return candidate
    return None


def _knip_invocation(path: Path) -> tuple[list[str], Path] | str:
    """Return (argv, cwd) for running knip on ``path``, or a skip reason."""
    package_root = _find_package_root(path)
    if package_root is None:
        return "no_package_json"

    workspace_root = _find_workspace_root(package_root)
    knip_bin = _find_knip_bin(package_root)
    if knip_bin is None:
        return "knip_not_installed"

    argv = [str(knip_bin), "--reporter", "json"]
    if workspace_root is not None:
        # Knip must run from the monorepo root to resolve workspace imports.
        argv += ["--workspace", package_root.relative_to(workspace_root).as_posix()]
        return argv, workspace_root
    return argv, package_root


def run_knip(path: Path, timeout: int = 120) -> tuple[dict | None, Path | None, str | None]:
    """Run knip for ``path``. Returns (json, cwd, failure_reason)."""
    invocation = _knip_invocation(path)
    if isinstance(invocation, str):
        logger.debug("knip: skipped (%s)", invocation)
        return None, None, invocation
    argv, cwd = invocation

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            cwd=str(cwd),
            timeout=timeout,
        )  # nosec B603
    except subprocess.TimeoutExpired:
        logger.debug("knip: timed out after %ds", timeout)
        return None, cwd, "knip_timeout"
    except OSError as exc:
        logger.debug("knip: OSError: %s", exc)
        return None, cwd, "knip_failed"

    stdout = result.stdout.strip()
    if not stdout:
        logger.debug("knip: no output (rc=%s): %s", result.returncode, result.stderr[-500:])
        return None, cwd, "knip_failed"

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        logger.debug("knip: JSON parse error: %s", exc)
        return None, cwd, "knip_bad_output"
    if not isinstance(data, dict):
        return None, cwd, "knip_bad_output"
    return data, cwd, None


def _normalize_path(raw: str, knip_cwd: Path, scan_path: Path) -> str:
    """Return a path relative to PROJECT_ROOT, or "" when outside ``scan_path``."""
    p = Path(raw)
    if not p.is_absolute():
        p = (knip_cwd / p).resolve()
    try:
        p.relative_to(scan_path.resolve())
    except ValueError:
        return ""
    return rel(str(p))


def _issue_line(item: dict) -> int:
    """Knip reports ``line`` directly; ``pos`` is a character offset, not a line."""
    line = item.get("line")
    if isinstance(line, int):
        return line
    raw_pos = item.get("pos")
    if isinstance(raw_pos, dict):
        start = raw_pos.get("start")
        if isinstance(start, dict) and isinstance(start.get("line"), int):
            return start["line"]
    return 0


def detect_with_knip_result(path: Path) -> tuple[list[dict] | None, str | None]:
    """Run Knip and return (export entries, failure reason).

    Each entry: {"file": str, "name": str, "line": int, "kind": "export"|"type"}.
    Entries are None when Knip could not run; the reason says why.
    """
    data, knip_cwd, reason = run_knip(path)
    if data is None or knip_cwd is None:
        return None, reason

    entries: list[dict] = []
    for issue in data.get("issues", []):
        if not isinstance(issue, dict):
            continue
        norm = _normalize_path(issue.get("file", ""), knip_cwd, path)
        if not norm:
            continue
        for key, kind in (("exports", "export"), ("types", "type")):
            for export in issue.get(key, []) or []:
                if not isinstance(export, dict):
                    continue
                name = export.get("name", "")
                if not name:
                    continue
                entries.append(
                    {"file": norm, "name": name, "line": _issue_line(export), "kind": kind}
                )

    logger.debug("knip: %d dead exports", len(entries))
    return entries, None


def detect_with_knip(path: Path) -> list[dict] | None:
    """Run Knip and return export entries, or None if Knip is unavailable."""
    entries, _reason = detect_with_knip_result(path)
    return entries
