"""Run the project's own Biome linter and read its JSON reporter.

Biome 1.x locates a diagnostic by a UTF-8 byte span, so the line is counted
from the file; a ``start.line`` is used when the reporter gives one. Biome
doesn't list the files it linted, so the run counts the scan's files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from desloppify.languages.typescript.detectors.lint.configs import LinterConfig
from desloppify.languages.typescript.detectors.lint.runner import (
    LinterRun,
    LintMessage,
    run_linter,
)

_GROUP_META: dict[str, dict[str, Any]] = {
    "correctness": {"type": "problem"},
    "suspicious": {"type": "problem"},
    "security": {"type": "problem"},
    "a11y": {"type": "suggestion"},
    "complexity": {"type": "suggestion"},
    "performance": {"type": "suggestion"},
    "style": {"type": "suggestion", "docs": {"recommended": "stylistic"}},
    "nursery": {"type": "suggestion", "docs": {"recommended": "stylistic"}},
}


class _Lines:
    """Byte offset → 1-based line and column, reading each file once."""

    def __init__(self) -> None:
        self._starts: dict[Path, list[int]] = {}

    def __call__(self, file: Path, offset: int) -> tuple[int, int]:
        if file not in self._starts:
            try:
                data = file.read_bytes()
            except OSError:
                data = b""
            starts = [0]
            starts.extend(i + 1 for i, byte in enumerate(data) if byte == 0x0A)
            self._starts[file] = starts
        starts = self._starts[file]
        low, high = 0, len(starts) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if starts[middle] <= offset:
                low = middle
            else:
                high = middle - 1
        return low + 1, offset - starts[low] + 1


def _path(location: dict[str, Any]) -> str | None:
    path = location.get("path")
    if isinstance(path, dict):
        path = path.get("file")
    return path if isinstance(path, str) and path else None


def parse_biome_output(stdout: str, run: LinterRun, files: set[Path]) -> bool:
    """Fill ``run`` from ``--reporter=json`` output; False if it isn't that or a config error."""
    start = stdout.find("{")
    try:
        data = json.loads(stdout[start:]) if start >= 0 else None
    except ValueError:
        return False
    if not isinstance(data, dict) or not isinstance(data.get("diagnostics"), list):
        return False
    lines = _Lines()
    for diagnostic in data["diagnostics"]:
        if not isinstance(diagnostic, dict):
            continue
        category = str(diagnostic.get("category") or "")
        if category.startswith("configuration"):
            run.error = str(diagnostic.get("description") or category)[:300]
            return False
        location = diagnostic.get("location")
        if not isinstance(location, dict):
            continue
        name = _path(location)
        if name is None or not (category == "parse" or category.startswith("lint/")):
            continue
        file = (run.config.directory / name).resolve()
        if category == "parse":
            run.unparsed.add(file)
            continue
        position = location.get("start")
        span = location.get("span")
        if isinstance(position, dict) and position.get("line"):
            line, col = int(position["line"]), int(position.get("column") or 0)
        elif isinstance(span, list) and span:
            line, col = lines(file, int(span[0]))
        else:
            line, col = 0, 0
        rule = category.removeprefix("lint/")
        tags = diagnostic.get("tags")
        run.messages.append(
            LintMessage(
                file=file,
                line=line,
                col=col,
                rule=rule,
                severity="error" if diagnostic.get("severity") in ("error", "fatal") else "warning",
                message=str(diagnostic.get("description") or ""),
                fixable=isinstance(tags, list) and "fixable" in tags,
                meta=_GROUP_META.get(rule.split("/", 1)[0]),
            )
        )
    summary = data.get("summary")
    linted = (
        sum(summary.get(key) or 0 for key in ("changed", "unchanged"))
        if isinstance(summary, dict)
        else None
    )
    run.files = set(files) if linted != 0 else set()
    return True


def run_biome(binary: Path, config: LinterConfig, targets: list[str], files: set[Path]) -> LinterRun:
    """``biome lint`` from the config's directory on ``targets``; ``files`` are the scan's."""
    cmd = [str(binary), "lint", "--reporter=json", "--max-diagnostics=none", *targets]
    return run_linter(
        cmd, LinterRun(config=config), lambda stdout, run: parse_biome_output(stdout, run, files)
    )


__all__ = ["parse_biome_output", "run_biome"]
