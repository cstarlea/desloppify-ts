"""Knip adapter: one ``knip --reporter json`` run per scan, shared by detectors.

Runs the project's own Knip and parses its JSON report. ``exports`` reads the
unused exports, types, enum members and duplicate exports; ``orphaned`` reads
the unused files and unresolved imports as corroboration; ``dependencies``
reads the unused and unlisted dependencies and unlisted binaries.

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
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import rel

logger = logging.getLogger(__name__)

_WORKSPACE_MARKERS = ("pnpm-workspace.yaml",)
_CACHE_KEY = "typescript.knip_runs"
KNIP_TIMEOUT = 120


@dataclass
class KnipRun:
    """The outcome of one Knip run; ``failure`` says why it is unusable, if it is."""

    cwd: Path | None
    data: dict | None = None
    failure: str | None = None

    @property
    def usable(self) -> bool:
        return self.failure is None and self.data is not None and self.cwd is not None

    def items(self, category: str) -> Iterator[tuple[Path, Any]]:
        """Yield (absolute file, item) for every item Knip reports in ``category``."""
        if self.failure is not None or self.data is None or self.cwd is None:
            return
        for row in self.data.get("issues", []):
            if not isinstance(row, dict) or not isinstance(row.get("file"), str):
                continue
            entries = row.get(category)
            if not isinstance(entries, list):
                continue
            filepath = Path(row["file"])
            if not filepath.is_absolute():
                filepath = self.cwd / filepath
            filepath = filepath.resolve()
            for item in entries:
                yield filepath, item


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


def _parse_report(stdout: str) -> dict | None:
    """Knip's JSON report is one line; plugins loading project config may print
    their own lines to stdout before it."""
    candidates = [
        stdout,
        *(line for line in reversed(stdout.splitlines()) if line.startswith("{")),
    ]
    for text in candidates:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _execute(argv: list[str], cwd: Path, timeout: int) -> KnipRun:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            cwd=str(cwd),
            timeout=timeout,
        )  # nosec B603
    except subprocess.TimeoutExpired:
        logger.debug("knip: timed out after %ds", timeout)
        return KnipRun(cwd, failure="knip_timeout")
    except OSError as exc:
        logger.debug("knip: OSError: %s", exc)
        return KnipRun(cwd, failure="knip_failed")

    stdout = result.stdout.strip()
    if not stdout:
        logger.debug(
            "knip: no output (rc=%s): %s", result.returncode, result.stderr[-500:]
        )
        return KnipRun(cwd, failure="knip_failed")
    data = _parse_report(stdout)
    if data is None:
        logger.debug("knip: no JSON report in output: %s", stdout[-500:])
        return KnipRun(cwd, failure="knip_bad_output")
    return KnipRun(cwd, data=data)


def run_knip(
    path: Path, timeout: int = KNIP_TIMEOUT, *, cache: dict[str, Any] | None = None
) -> KnipRun:
    """Run knip for ``path`` once; later calls with the same cache reuse the run."""
    invocation = _knip_invocation(path)
    if isinstance(invocation, str):
        logger.debug("knip: skipped (%s)", invocation)
        return KnipRun(None, failure=invocation)
    argv, cwd = invocation
    runs = cache.setdefault(_CACHE_KEY, {}) if cache is not None else {}
    key = (tuple(argv), str(cwd))
    if key not in runs:
        runs[key] = _execute(argv, cwd, timeout)
    return runs[key]


def in_scan_path(filepath: Path, scan_path: Path) -> bool:
    try:
        filepath.relative_to(scan_path.resolve())
    except ValueError:
        return False
    return True


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


def _is_deprecated(lines: list[str], line: int) -> bool:
    """Whether the comment block right above 1-based ``line`` says ``@deprecated``."""
    index = line - 2
    comment: list[str] = []
    while 0 <= index < len(lines):
        text = lines[index].strip()
        if not text.startswith(("*", "/*", "//")) and not text.endswith("*/"):
            break
        comment.append(text)
        index -= 1
    return any("@deprecated" in text for text in comment)


def _duplicate_entry(filepath: Path, group: list, unused: set[str]) -> dict | None:
    """One entry for the names exporting the same thing in ``group``.

    A deprecated alias is kept on purpose (``deprecated`` reports it), and an
    alias that is itself an unused export is already reported as one, so
    neither counts; fewer than two names left is no duplicate.
    """
    members = [m for m in group if isinstance(m, dict) and m.get("name")]
    try:
        lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    members = [
        m
        for m in members
        if m["name"] not in unused and not _is_deprecated(lines, _issue_line(m))
    ]
    if len(members) < 2:
        return None
    return {
        "file": rel(str(filepath)),
        "name": "=".join(m["name"] for m in members),
        "line": _issue_line(members[0]),
        "kind": "duplicate",
        "names": [m["name"] for m in members],
    }


def export_entries(run: KnipRun, path: Path) -> list[dict]:
    """Unused exports, types and enum members, and duplicate exports, in ``path``."""
    entries: list[dict] = []
    for category, kind in (("exports", "export"), ("types", "type")):
        for filepath, item in run.items(category):
            if (
                isinstance(item, dict)
                and item.get("name")
                and in_scan_path(filepath, path)
            ):
                entries.append(
                    {
                        "file": rel(str(filepath)),
                        "name": item["name"],
                        "line": _issue_line(item),
                        "kind": kind,
                    }
                )
    for filepath, item in run.items("enumMembers"):
        if not (
            isinstance(item, dict) and item.get("name") and in_scan_path(filepath, path)
        ):
            continue
        enum = item.get("namespace")
        name = f"{enum}.{item['name']}" if enum else item["name"]
        entries.append(
            {
                "file": rel(str(filepath)),
                "name": name,
                "line": _issue_line(item),
                "kind": "enum_member",
            }
        )
    unused_by_file: dict[str, set[str]] = {}
    for entry in entries:
        unused_by_file.setdefault(entry["file"], set()).add(entry["name"])
    for filepath, group in run.items("duplicates"):
        if isinstance(group, list) and in_scan_path(filepath, path):
            unused = unused_by_file.get(rel(str(filepath)), set())
            entry = _duplicate_entry(filepath, group, unused)
            if entry is not None:
                entries.append(entry)
    return entries


def unused_files(run: KnipRun) -> set[str]:
    """Absolute paths of the files Knip reports unused."""
    return {str(filepath) for filepath, _item in run.items("files")}


def unresolved_imports(run: KnipRun) -> dict[str, set[str]]:
    """Absolute importing file → the specifiers Knip could not resolve in it."""
    found: dict[str, set[str]] = {}
    for filepath, item in run.items("unresolved"):
        if isinstance(item, dict) and item.get("name"):
            found.setdefault(str(filepath), set()).add(item["name"])
    return found


def detect_with_knip_result(
    path: Path, *, cache: dict[str, Any] | None = None
) -> tuple[list[dict] | None, str | None]:
    """Run Knip and return (export entries, failure reason).

    Each entry: {"file", "name", "line", "kind"}, kind being "export", "type",
    "enum_member" (name ``Enum.Member``) or "duplicate" (names joined by ``=``).
    Entries are None when Knip could not run; the reason says why.
    """
    run = run_knip(path, cache=cache)
    if not run.usable:
        return None, run.failure or "knip_failed"
    entries = export_entries(run, path)
    logger.debug("knip: %d dead exports", len(entries))
    return entries, None


def detect_with_knip(path: Path) -> list[dict] | None:
    """Run Knip and return export entries, or None if Knip is unavailable."""
    entries, _reason = detect_with_knip_result(path)
    return entries


__all__ = [
    "KNIP_TIMEOUT",
    "KnipRun",
    "detect_with_knip",
    "detect_with_knip_result",
    "export_entries",
    "in_scan_path",
    "run_knip",
    "unresolved_imports",
    "unused_files",
]
