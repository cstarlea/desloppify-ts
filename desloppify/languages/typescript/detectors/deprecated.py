"""Stale @deprecated symbol detection."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.source import find_ts_and_tsx_files
from desloppify.base.output.terminal import colorize, print_table
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.search.grep import grep_count_files, grep_files
from desloppify.languages.typescript.detectors.contracts import DetectorResult

logger = logging.getLogger(__name__)

_INLINE_PROPERTY_RE = re.compile(r"(\w+)\s*[?:=]")
_INLINE_DECLARATION_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var|function|class|type|interface|enum)\s+(\w+)"
)
_DECLARATION_LINE_RE = re.compile(
    r"(?:export\s+)?(?:declare\s+)?(?:const|let|var|function|class|type|interface|enum)\s+(\w+)"
)
_PROPERTY_LINE_RE = re.compile(r"(\w+)\s*[?:]")
_DEPRECATED_TAG_RE = re.compile(r"@deprecated", re.IGNORECASE)
# The JSDoc tag TypeScript understands. Bare words like "deprecated" in
# strings, identifiers or prose are not deprecation markers.
_JSDOC_DEPRECATED_RE = r"@deprecated\b"


def detect_deprecated_result(path: Path) -> DetectorResult[dict[str, Any]]:
    """Find deprecated symbols with explicit population semantics."""
    ts_files = find_ts_and_tsx_files(path)
    hits = grep_files(_JSDOC_DEPRECATED_RE, ts_files, flags=re.IGNORECASE)

    entries = []
    seen_symbols = set()  # Deduplicate by file+symbol
    for filepath, lineno, content in hits:
        symbol, kind = _extract_deprecated_symbol(
            filepath,
            lineno,
            content,
            scan_root=path,
        )
        if not symbol:
            continue
        # Deduplicate (same symbol in same file, e.g., multiple @deprecated on interface props)
        key = (filepath, symbol)
        if key in seen_symbols:
            continue
        seen_symbols.add(key)
        importers = (
            _count_importers(symbol, filepath, ts_files=ts_files, scan_root=path)
            if kind == "top-level"
            else -1
        )
        exported, same_file_uses = (
            _export_and_local_uses(symbol, filepath, scan_root=path)
            if kind == "top-level"
            else (False, 0)
        )
        entries.append(
            {
                "file": filepath,
                "line": lineno,
                "symbol": symbol,
                "kind": kind,
                "importers": importers,
                "exported": exported,
                "same_file_uses": same_file_uses,
            }
        )
    sorted_entries = sorted(entries, key=lambda e: e["importers"])
    return DetectorResult(
        entries=sorted_entries,
        population_kind="deprecated_symbols",
        population_size=len(sorted_entries),
    )


def _extract_deprecated_symbol(
    filepath: str,
    lineno: int,
    content: str,
    *,
    scan_root: Path | None = None,
) -> tuple[str | None, str]:
    """Extract the deprecated symbol name and its deprecation kind."""
    try:
        p = _resolve_source_file(filepath, scan_root=scan_root)
        lines = p.read_text().splitlines()
        content_stripped = content.strip()

        if "/**" in content_stripped and "*/" in content_stripped:
            inline_jsdoc = content_stripped.split("*/", 1)[1].strip()
            if inline_jsdoc:
                inline_match = _match_inline_deprecated_target(inline_jsdoc)
                if inline_match is not None:
                    return inline_match

        scanned_line = _scan_following_declaration_line(lines, lineno)
        if scanned_line is not None:
            return scanned_line

        inline_comment_symbol = _match_inline_comment_property(content_stripped)
        if inline_comment_symbol is not None:
            return inline_comment_symbol, "property"

    except (OSError, UnicodeDecodeError) as exc:
        log_best_effort_failure(
            logger, f"read deprecated source context {filepath}", exc
        )
    return None, "unknown"


def _match_inline_deprecated_target(line: str) -> tuple[str, str] | None:
    property_match = _INLINE_PROPERTY_RE.match(line)
    if property_match:
        return property_match.group(1), "property"
    declaration_match = _INLINE_DECLARATION_RE.match(line)
    if declaration_match:
        return declaration_match.group(1), "top-level"
    return None


def _scan_following_declaration_line(
    lines: list[str],
    lineno: int,
) -> tuple[str, str] | None:
    for offset in range(1, 8):
        idx = lineno - 1 + offset
        if idx >= len(lines):
            break
        src = lines[idx].strip()
        if not src or src.startswith("*") or src.startswith("//"):
            continue
        declaration_match = _DECLARATION_LINE_RE.match(src)
        if declaration_match:
            return declaration_match.group(1), "top-level"
        property_match = _PROPERTY_LINE_RE.match(src)
        if property_match:
            return property_match.group(1), "property"
        break
    return None


def _match_inline_comment_property(content: str) -> str | None:
    if "//" not in content and "*" not in content:
        return None
    tag_match = _DEPRECATED_TAG_RE.search(content)
    if tag_match is None:
        return None
    line_before = content[: tag_match.start()].strip().rstrip("/*").rstrip("*").strip()
    property_match = _PROPERTY_LINE_RE.search(line_before)
    if property_match:
        return property_match.group(1)
    return None


def _resolve_source_file(filepath: str, *, scan_root: Path | None) -> Path:
    if Path(filepath).is_absolute():
        return Path(filepath)
    if scan_root is not None:
        candidate = scan_root / filepath
        if candidate.exists():
            return candidate
    return Path(resolve_path(filepath))


def _export_and_local_uses(
    name: str, declaring_file: str, *, scan_root: Path
) -> tuple[bool, int]:
    """Return (is exported, uses in the declaring file besides the declaration)."""
    try:
        text = _resolve_source_file(declaring_file, scan_root=scan_root).read_text(
            errors="replace"
        )
    except OSError:
        return True, 0  # unknown: assume it may be public
    escaped = re.escape(name)
    exported = bool(
        re.search(
            r"\bexport\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
            rf"(?:const|let|var|function\*?|class|type|interface|enum|namespace)\s+{escaped}\b",
            text,
        )
        or re.search(rf"\bexport\s*(?:type\s*)?\{{[^}}]*\b{escaped}\b", text)
        or re.search(rf"\bexport\s+default\s+{escaped}\b", text)
    )
    occurrences = len(re.findall(rf"(?<![\w$]){escaped}(?![\w$])", text))
    return exported, max(0, occurrences - 1)


def _count_importers(
    name: str, declaring_file: str, *, ts_files: list[str], scan_root: Path
) -> int:
    if not name:
        return -1
    matching = grep_count_files(name, ts_files, word_boundary=True)
    declaring_resolved = str(
        _resolve_source_file(declaring_file, scan_root=scan_root).resolve()
    )
    count = 0
    for match_file in matching:
        match_resolved = str(_resolve_source_file(match_file, scan_root=scan_root).resolve())
        if match_resolved != declaring_resolved:
            count += 1
    return count


def cmd_deprecated(args: Any) -> None:
    result = detect_deprecated_result(Path(args.path))
    entries = result.entries
    if args.json:
        print(
            json.dumps(
                {
                    "count": len(entries),
                    "entries": entries,
                    "population_size": result.population_size,
                },
                indent=2,
            )
        )
        return

    if not entries:
        print(colorize("No @deprecated annotations found.", "green"))
        return

    # Separate top-level and property deprecations
    top_level = [e for e in entries if e["kind"] == "top-level"]
    properties = [e for e in entries if e["kind"] == "property"]

    print(
        colorize(
            f"\nDeprecated symbols: {len(entries)} ({len(top_level)} top-level, {len(properties)} properties)\n",
            "bold",
        )
    )

    if top_level:
        print(colorize("Top-level (importable):", "cyan"))
        rows = []
        for e in top_level[: args.top]:
            imp = str(e["importers"]) if e["importers"] >= 0 else "?"
            if e.get("exported") and e["importers"] == 0:
                status = "exported, no local importers"
            elif e["importers"] == 0 and not e.get("same_file_uses"):
                status = colorize("unused", "green")
            else:
                status = f"{imp} importers"
            rows.append([e["symbol"], rel(e["file"]), status])
        print_table(["Symbol", "File", "Status"], rows, [30, 55, 20])
        print()

    if properties:
        print(colorize("Properties (inline):", "cyan"))
        rows = []
        for e in properties[: args.top]:
            rows.append([e["symbol"], rel(e["file"]), f"line {e['line']}"])
        print_table(["Property", "File", "Line"], rows, [30, 55, 10])
