"""TypeScript smell helper utilities — block parsing and code projection."""

from __future__ import annotations

from typing import NamedTuple

from desloppify.base.text_utils import strip_c_style_comments
from desloppify.languages._framework.node.js_text import code_text as _code_text
from desloppify.languages.typescript.syntax.lines import line_number, split_lines
from desloppify.languages.typescript.syntax.scanner import SourceText, scan_code


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class _FileContext(NamedTuple):
    """Per-file data bundle passed to all smell detectors."""

    filepath: str
    content: str
    lines: list[str]
    source: SourceText


def _file_context(filepath: str, content: str) -> _FileContext:
    source = SourceText(content)
    return _FileContext(filepath, content, source.lines, source)


# ---------------------------------------------------------------------------
# Comment / string helpers
# ---------------------------------------------------------------------------


def _strip_ts_comments(text: str) -> str:
    """Strip // and /* */ comments while preserving strings."""
    return strip_c_style_comments(text)


def _regex_line_matches(ctx: _FileContext, pattern: str, anchor: str = "code"):
    """``(index, line)`` for each line with a match of ``pattern`` that starts
    in code (or starts a literal or comment: see ``SourceText.line_matches``)."""
    for index, _match in ctx.source.line_matches(pattern, anchor):
        yield index, ctx.lines[index]


# ---------------------------------------------------------------------------
# Block parsing helpers (formerly _smell_helpers_blocks.py)
# ---------------------------------------------------------------------------


def _track_brace_body(
    lines: list[str], start_line: int, *, max_scan: int = 2000
) -> int | None:
    """Find the closing brace matching the first opening brace from start_line."""
    depth = 0
    found_open = False
    for line_idx in range(start_line, min(start_line + max_scan, len(lines))):
        for _, ch, in_string in scan_code(lines[line_idx]):
            if in_string:
                continue
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return line_idx
    return None


def _find_block_end(content: str, brace_start: int, max_scan: int = 5000) -> int | None:
    """Find the closing brace position in a content string from an opening brace."""
    depth = 0
    for ci, ch, in_s in scan_code(
        content, brace_start, min(brace_start + max_scan, len(content))
    ):
        if in_s:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return ci
    return None


def _extract_block_body(
    content: str, brace_start: int, max_scan: int = 5000
) -> str | None:
    """Return text between ``{`` at brace_start and its matching ``}``."""
    end = _find_block_end(content, brace_start, max_scan)
    if end is None:
        return None
    return content[brace_start + 1 : end]


def _content_line_info(content: str, pos: int) -> tuple[int, str]:
    """Return ``(line_no, stripped snippet[:100])`` for a position in content."""
    line_no = line_number(content, pos)
    return line_no, split_lines(content)[line_no - 1].strip()[:100]


__all__ = [
    "_FileContext",
    "_code_text",
    "_content_line_info",
    "_extract_block_body",
    "_file_context",
    "_find_block_end",
    "_regex_line_matches",
    "_strip_ts_comments",
    "_track_brace_body",
    "scan_code",
]
