"""Measured coverage from Istanbul (``coverage-final.json``) and lcov reports.

Reports are found under the project root: ``coverage/`` in the root and in
each package directory, plus any ``reportsDirectory`` (vitest) or
``coverageDirectory`` (jest) a config file names.  A report counts for a file
only when it is newer than the file and its line numbers fit the file; every
other file keeps the import-graph verdict.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root

logger = logging.getLogger(__name__)

ISTANBUL_FILE = "coverage-final.json"
LCOV_FILE = "lcov.info"
_DEFAULT_REPORT_DIR = "coverage"
_PRUNED_DIRS = frozenset({"node_modules", "dist", "build", "out", "coverage"})
_MAX_DEPTH = 6

_CONFIG_NAMES = tuple(
    f"{stem}.config.{ext}"
    for stem in ("vitest", "vite", "jest")
    for ext in ("ts", "mts", "cts", "js", "mjs", "cjs")
)
_CONFIG_DIR_RE = re.compile(
    r"""\b(?:reportsDirectory|coverageDirectory)\s*:\s*['"]([^'"]+)['"]"""
)


@dataclass
class FileCoverage:
    """Line and branch coverage of one production file, merged across reports."""

    lines: set[int] = field(default_factory=set)
    hit_lines: set[int] = field(default_factory=set)
    branches: int = 0
    hit_branches: int = 0
    report: str = ""

    @property
    def line_pct(self) -> float:
        if not self.lines:
            return 100.0
        return 100.0 * len(self.hit_lines & self.lines) / len(self.lines)

    @property
    def branch_pct(self) -> float | None:
        if not self.branches:
            return None
        return 100.0 * self.hit_branches / self.branches


@dataclass
class MeasuredCoverage:
    """Usable per-file coverage plus what was set aside, for the scan log."""

    files: dict[str, FileCoverage] = field(default_factory=dict)
    reports: list[str] = field(default_factory=list)
    stale: set[str] = field(default_factory=set)


@dataclass
class _ReportFile:
    lines: set[int]
    hit_lines: set[int]
    branches: int
    hit_branches: int


def _configured_report_dirs(directory: Path) -> list[Path]:
    """Report directories named by vitest/jest config in *directory*."""
    found: list[Path] = []
    texts: list[str] = []
    for name in _CONFIG_NAMES:
        config = directory / name
        if config.is_file():
            try:
                texts.append(config.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    package_json = directory / "package.json"
    if package_json.is_file():
        try:
            jest = json.loads(package_json.read_text(encoding="utf-8")).get("jest")
        except (OSError, ValueError):
            jest = None
        if isinstance(jest, dict) and isinstance(jest.get("coverageDirectory"), str):
            found.append(directory / jest["coverageDirectory"])
    for text in texts:
        found.extend(directory / match for match in _CONFIG_DIR_RE.findall(text))
    return found


def find_report_files(root: Path) -> list[tuple[Path, Path]]:
    """``(report_file, base_dir)`` pairs; *base_dir* anchors relative lcov paths."""
    candidates: list[tuple[Path, Path]] = []
    root = root.resolve()
    for dirpath, dirnames, _filenames in os.walk(root):
        directory = Path(dirpath)
        depth = len(directory.relative_to(root).parts)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _PRUNED_DIRS and not d.startswith(".") and depth < _MAX_DEPTH
        ]
        for report_dir in [
            directory / _DEFAULT_REPORT_DIR,
            *_configured_report_dirs(directory),
        ]:
            candidates.extend(
                (report_dir / name, directory) for name in (ISTANBUL_FILE, LCOV_FILE)
            )
    seen: set[Path] = set()
    found: list[tuple[Path, Path]] = []
    for report, base in candidates:
        if not report.is_file():
            continue
        resolved = report.resolve()
        if resolved in seen:
            continue
        # Both formats from one run describe the same data; Istanbul has more.
        if report.name == LCOV_FILE and (report.parent / ISTANBUL_FILE).is_file():
            continue
        seen.add(resolved)
        found.append((resolved, base.resolve()))
    return found


def _parse_istanbul(text: str) -> dict[str, _ReportFile]:
    data = json.loads(text)
    out: dict[str, _ReportFile] = {}
    if not isinstance(data, dict):
        return out
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        statements = entry.get("statementMap") or {}
        counts = entry.get("s") or {}
        lines: set[int] = set()
        hit: set[int] = set()
        for sid, loc in statements.items():
            line = (loc.get("start") or {}).get("line")
            if not isinstance(line, int):
                continue
            lines.add(line)
            if counts.get(sid, 0):
                hit.add(line)
        branch_counts = [c for arms in (entry.get("b") or {}).values() for c in arms]
        out[str(entry.get("path") or key)] = _ReportFile(
            lines, hit, len(branch_counts), sum(1 for c in branch_counts if c)
        )
    return out


def _parse_lcov(text: str) -> dict[str, _ReportFile]:
    out: dict[str, _ReportFile] = {}
    current: _ReportFile | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            current = out.setdefault(line[3:], _ReportFile(set(), set(), 0, 0))
        elif current is None:
            continue
        elif line.startswith("DA:"):
            parts = line[3:].split(",")
            try:
                number, count = int(parts[0]), int(float(parts[1]))
            except (IndexError, ValueError):
                continue
            current.lines.add(number)
            if count:
                current.hit_lines.add(number)
        elif line.startswith("BRDA:"):
            taken = line.rsplit(",", 1)[-1]
            current.branches += 1
            if taken not in ("-", "0"):
                current.hit_branches += 1
        elif line == "end_of_record":
            current = None
    return out


def _to_project_path(path: str, base: Path, root: Path) -> str | None:
    """Map a report's path to a project-relative one, or None if it isn't ours."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        return candidate.resolve().relative_to(root).as_posix()
    except ValueError:
        pass
    # Generated on another machine (CI): match the longest suffix under *base*.
    parts = Path(path).parts
    for start in range(1, len(parts)):
        moved = base.joinpath(*parts[start:])
        if moved.is_file():
            try:
                return moved.resolve().relative_to(root).as_posix()
            except ValueError:
                return None
    return None


def _line_count(path: Path) -> int | None:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return None


def load_measured_coverage(
    wanted: set[str], root: Path | None = None
) -> MeasuredCoverage:
    """Fresh measured coverage for the project-relative files in *wanted*."""
    root = (root or get_project_root()).resolve()
    result = MeasuredCoverage()
    for report, base in find_report_files(root):
        try:
            text = report.read_text(encoding="utf-8", errors="replace")
            parsed = (
                _parse_istanbul(text)
                if report.name == ISTANBUL_FILE
                else _parse_lcov(text)
            )
            report_mtime = report.stat().st_mtime
        except (OSError, ValueError) as exc:
            logger.debug("unreadable coverage report %s: %s", report, exc)
            continue
        report_rel = (
            report.relative_to(root).as_posix()
            if report.is_relative_to(root)
            else str(report)
        )
        used = False
        for path, data in parsed.items():
            rel = _to_project_path(path, base, root)
            if rel is None or rel not in wanted:
                continue
            source = root / rel
            try:
                newer_source = source.stat().st_mtime > report_mtime
            except OSError:
                continue
            line_count = _line_count(source)
            if (
                newer_source
                or line_count is None
                or (data.lines and max(data.lines) > line_count)
            ):
                result.stale.add(rel)
                continue
            used = True
            merged = result.files.setdefault(rel, FileCoverage(report=report_rel))
            merged.lines |= data.lines
            merged.hit_lines |= data.hit_lines
            if data.branches and (
                not merged.branches
                or data.hit_branches * merged.branches
                > merged.hit_branches * data.branches
            ):
                merged.branches, merged.hit_branches = data.branches, data.hit_branches
        if used:
            result.reports.append(report_rel)
    # A file one report measured is not stale because another report is old.
    result.stale -= set(result.files)
    return result


__all__ = [
    "FileCoverage",
    "MeasuredCoverage",
    "find_report_files",
    "load_measured_coverage",
]
