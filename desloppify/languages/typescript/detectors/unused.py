"""Unused declarations detection via tsc's noUnusedLocals/noUnusedParameters.

Includes a Deno/edge-functions fallback where `tsc` cannot model URL-based imports.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess  # nosec B404
import sys
from collections import defaultdict
from pathlib import Path

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.base.output.terminal import colorize, print_table
from desloppify.languages._framework.base.types import DetectorCoverageStatus
from desloppify.languages.typescript.detectors.unused_fallback import (
    _contains_deno_markers,
    _extract_import_names,
    _has_deno_import_syntax,
    _identifier_occurrences,
    detect_unused_fallback,
    should_use_deno_fallback,
)
from desloppify.languages.typescript.syntax.nodes import (
    ALL_DESTRUCTURED,
    NameIndex,
    is_parameter,
    parameter_owner,
    pattern_at,
    same,
)
from desloppify.languages.typescript.syntax.tree import parse_text

TS6133_RE = re.compile(
    r"^(.+)\((\d+),(\d+)\): error TS6133: '(\S+)' is declared but its value is never read\."
)
TS6192_RE = re.compile(
    r"^(.+)\((\d+),(\d+)\): error TS6192: All imports in import declaration are unused\."
)
# Every diagnostic tsc emits for noUnusedLocals / noUnusedParameters.
_TS_UNUSED_RE = re.compile(
    r"^(.+)\((\d+),(\d+)\): error (TS6133|TS6138|TS6192|TS6196|TS6198|TS6199|TS6205): (.*)$"
)
_QUOTED_NAME_RE = re.compile(r"'([^']+)'")
_AGGREGATE_NAMES = {
    "TS6192": "(entire import)",
    "TS6198": ALL_DESTRUCTURED,
    "TS6199": "(all variables)",
    "TS6205": "(all type parameters)",
}
ENTIRE_IMPORT = _AGGREGATE_NAMES["TS6192"]
# Statements whose names are imports: `import ...` (including
# `import x = require(...)`) and `import x = N.y`.
_IMPORT_STATEMENTS = frozenset({"import_statement", "import_alias"})
_TS_DIAGNOSTIC_RE = re.compile(r"error TS(\d+):")
# TS5xxx are compiler-option/config errors; TS18003 is "no inputs were found".
_TS_CONFIG_ERROR_RE = re.compile(r"error TS(5\d{3}|18003):")
# Printed by the unrelated `tsc` npm package that npx can fetch by mistake.
_BOGUS_TSC_MARKER = "This is not the tsc command you are looking for"
logger = logging.getLogger(__name__)
_proc_runtime = subprocess

# Compatibility aliases for external callers/tests that imported private names.
_detect_unused_fallback = detect_unused_fallback
_should_use_deno_fallback = should_use_deno_fallback


def _resolve_tsc_command(*start_dirs: Path) -> list[str]:
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


def _run_tsc_unused_check(
    project_root: Path,
    tsconfig_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run tsc with the unused-symbol checks enabled on top of ``tsconfig_path``."""
    cmd = _resolve_tsc_command(tsconfig_path.parent, project_root)
    return _proc_runtime.run(  # nosec B603
        [
            *cmd,
            "--project",
            str(tsconfig_path),
            "--noEmit",
            "--noUnusedLocals",
            "--noUnusedParameters",
            "--pretty",
            "false",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=project_root,
        timeout=120,
    )


def _find_nearest_tsconfig(path: Path) -> Path | None:
    """Return the closest TypeScript config that owns ``path``.

    Prefer an application config when both conventional config names exist in
    the same directory. Walking upward keeps scans of monorepo projects scoped
    to that project's config instead of assuming the repository root is a
    single application.
    """
    current = path.resolve()
    if current.is_file():
        current = current.parent

    for directory in (current, *current.parents):
        for config_name in ("tsconfig.app.json", "tsconfig.json"):
            candidate = directory / config_name
            if candidate.is_file():
                return candidate
    return None


def _reduced(summary: str, *, reason: str, confidence: float) -> DetectorCoverageStatus:
    return DetectorCoverageStatus(
        detector="unused",
        status="reduced",
        confidence=confidence,
        summary=summary,
        impact="Unused imports/declarations may be under-reported for this scan.",
        remediation="Install `typescript` in the project (npm i -D typescript) and rerun scan.",
        tool="tsc",
        reason=reason,
    )


def _tsc_failure_reason(result: subprocess.CompletedProcess[str]) -> str | None:
    """Return why a tsc run produced no usable results, or None if it is usable."""
    output = f"{result.stdout}\n{result.stderr}"
    if _BOGUS_TSC_MARKER in output:
        return "wrong_tsc_package"
    if result.returncode not in (0, 1, 2) or (
        result.returncode != 0 and not _TS_DIAGNOSTIC_RE.search(output)
    ):
        return "tsc_failed"
    return None


def _parse_tsc_unused(line: str) -> tuple[str, int, int, str] | None:
    match = _TS_UNUSED_RE.match(line)
    if not match:
        return None
    filepath, lineno, col, code, message = match.groups()
    if code in _AGGREGATE_NAMES:
        return filepath, int(lineno), int(col), _AGGREGATE_NAMES[code]
    name_match = _QUOTED_NAME_RE.search(message)
    if not name_match:
        return None
    return filepath, int(lineno), int(col), name_match.group(1)


def detect_unused_result(
    path: Path, category: str = "all"
) -> tuple[list[dict], int, DetectorCoverageStatus | None]:
    """Detect unused symbols; also report reduced coverage when tsc is unusable."""
    ts_files = find_ts_and_js_files(path)
    total_files = len(ts_files)
    if _should_use_deno_fallback(path, ts_files):
        entries, total = _detect_unused_fallback(path, category)
        return entries, total, None

    base_tsconfig = _find_nearest_tsconfig(path)
    if base_tsconfig is None:
        entries, total = _detect_unused_fallback(path, category)
        return entries, total, _reduced(
            "No tsconfig.json found; used source-based unused heuristic",
            reason="no_tsconfig",
            confidence=0.5,
        )

    try:
        result = _run_tsc_unused_check(get_project_root(), base_tsconfig)
    except (_proc_runtime.SubprocessError, OSError) as exc:
        logger.debug("Falling back to source-based unused detection: %s", exc)
        entries, total = _detect_unused_fallback(path, category)
        return entries, total, _reduced(
            f"tsc unavailable ({exc}); used source-based unused heuristic",
            reason="tsc_missing",
            confidence=0.5,
        )

    failure = _tsc_failure_reason(result)
    if failure is not None:
        logger.debug("tsc produced no usable output (%s): %s", failure, result.stderr[-500:])
        entries, total = _detect_unused_fallback(path, category)
        return entries, total, _reduced(
            f"tsc did not run correctly ({failure}); used source-based unused heuristic",
            reason=failure,
            confidence=0.5,
        )

    output_lines = result.stdout.splitlines() + result.stderr.splitlines()
    coverage = None
    config_errors = [line for line in output_lines if _TS_CONFIG_ERROR_RE.search(line)]
    if config_errors:
        coverage = _reduced(
            f"tsc reported config errors for {base_tsconfig.name}: {config_errors[0].strip()[:160]}",
            reason="tsconfig_error",
            confidence=0.7,
        )

    entries = []
    for line in output_lines:
        parsed = _parse_tsc_unused(line)
        if parsed is None:
            continue
        filepath, lineno, col, name = parsed
        if name.startswith("_"):
            continue

        try:
            full = Path(resolve_path(filepath))
            if not str(full).startswith(str(path.resolve())):
                continue
        except (OSError, ValueError) as exc:
            logger.debug("Skipping path scope check for %s: %s", filepath, exc)
            continue

        entries.append({"file": filepath, "line": lineno, "col": col, "name": name})
    _categorize_entries(entries)
    if category != "all":
        entries = [entry for entry in entries if entry["category"] == category]
    return entries, total_files, coverage


def detect_unused(path: Path, category: str = "all") -> tuple[list[dict], int]:
    entries, total_files, _coverage = detect_unused_result(path, category)
    return entries, total_files


def _read_source(filepath: str) -> str | None:
    try:
        p = Path(filepath) if Path(filepath).is_absolute() else get_project_root() / filepath
        return p.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("Unable to read %s for unused categorization: %s", filepath, exc)
        return None


def _categorize_entries(entries: list[dict]) -> None:
    """Set each entry's ``category``: ``imports``, ``params`` or ``vars``.

    Each file is read and parsed once. Without tree-sitter, or when the
    reported name can't be found in the tree, the line heuristic decides.
    """
    by_file: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_file[entry["file"]].append(entry)
    for filepath, file_entries in by_file.items():
        text = _read_source(filepath)
        lines = text.splitlines() if text is not None else None
        parsed = parse_text(text, filepath) if text is not None else None
        names = NameIndex(parsed) if parsed is not None else None
        for entry in file_entries:
            category = None
            if entry["name"] == ENTIRE_IMPORT:
                category = "imports"
            elif names is not None:
                category = _syntax_category(names, entry)
            if category is None:
                category = _categorize_line(lines, entry["line"]) if lines is not None else "vars"
            entry["category"] = category


def _syntax_category(names: NameIndex, entry: dict) -> str | None:
    """The category of the name tsc reported, or None when it isn't in the tree."""
    name, line, col = entry["name"], entry["line"], entry["col"]
    if name in _AGGREGATE_NAMES.values() and name != ALL_DESTRUCTURED:
        return "vars"
    node = names.find(name, line, col) if name != ALL_DESTRUCTURED else None
    if node is None:
        pattern = pattern_at(names.parsed, line, col)
        if pattern is None:
            return None
        return "params" if is_parameter(pattern) or _in_catch_parameter(pattern) else "vars"
    if parameter_owner(node) is not None:
        return "params"
    parent = node.parent
    while parent is not None:
        if parent.type in _IMPORT_STATEMENTS:
            return "imports"
        parent = parent.parent
    return "vars"


def _in_catch_parameter(node) -> bool:
    child, parent = node, node.parent
    while parent is not None and parent.type in ("object_pattern", "array_pattern", "pair_pattern"):
        child, parent = parent, parent.parent
    return parent is not None and parent.type == "catch_clause" and same(
        parent.child_by_field_name("parameter"), child
    )


def _categorize_unused(filepath: str, lineno: int) -> str:
    """Categorize one finding from its source line alone (no syntax tree)."""
    text = _read_source(filepath)
    return _categorize_line(text.splitlines(), lineno) if text is not None else "vars"


def _categorize_line(lines: list[str], lineno: int) -> str:
    if lineno <= len(lines):
        src_line = lines[lineno - 1].strip()
        if src_line.startswith("import ") or "from '" in src_line or 'from "' in src_line:
            return "imports"
        if src_line.startswith(
            (
                "const ",
                "let ",
                "var ",
                "export ",
                "function ",
                "class ",
                "type ",
                "interface ",
            )
        ):
            return "vars"
        for back in range(1, 10):
            idx = lineno - 1 - back
            if idx < 0:
                break
            prev = lines[idx].strip()
            if prev.startswith("import "):
                return "imports"
            if not prev or (
                not prev.startswith("{") and not prev.startswith(",") and "," not in prev
            ):
                break
    # Anything not provably part of an import (parameters, destructured
    # bindings, class members) must not be routed to the import fixer.
    return "vars"


def cmd_unused(args: argparse.Namespace) -> None:
    path = Path(args.path)
    if _should_use_deno_fallback(path, find_ts_and_js_files(path)):
        print(
            colorize(
                "Deno/edge TypeScript context detected — using source-based unused scan",
                "dim",
            ),
            file=sys.stderr,
        )
    else:
        print(colorize("Running tsc... (this may take a moment)", "dim"), file=sys.stderr)

    entries, _ = detect_unused(path, args.category)
    if args.json:
        print(json.dumps({"count": len(entries), "entries": entries}, indent=2))
        return

    if not entries:
        print(colorize("No unused declarations found.", "green"))
        return

    by_file: dict[str, list] = defaultdict(list)
    for entry in entries:
        by_file[entry["file"]].append(entry)

    by_cat: dict[str, int] = defaultdict(int)
    for entry in entries:
        by_cat[entry["category"]] += 1

    print(
        colorize(
            f"\nUnused declarations: {len(entries)} across {len(by_file)} files\n",
            "bold",
        )
    )

    print(colorize("By category:", "cyan"))
    for cat, count in sorted(by_cat.items(), key=lambda item: -item[1]):
        print(f"  {cat}: {count}")
    print()

    print(colorize("Top files:", "cyan"))
    sorted_files = sorted(by_file.items(), key=lambda item: -len(item[1]))
    rows = []
    for filepath, file_entries in sorted_files[: args.top]:
        names = ", ".join(entry["name"] for entry in file_entries[:5])
        if len(file_entries) > 5:
            names += f", ... (+{len(file_entries) - 5})"
        rows.append([rel(filepath), str(len(file_entries)), names])
    print_table(["File", "Count", "Names"], rows, [55, 6, 50])


__all__ = [
    "TS6133_RE",
    "TS6192_RE",
    "_categorize_unused",
    "_contains_deno_markers",
    "_detect_unused_fallback",
    "_extract_import_names",
    "_has_deno_import_syntax",
    "_identifier_occurrences",
    "_should_use_deno_fallback",
    "cmd_unused",
    "detect_unused",
    "detect_unused_result",
]
