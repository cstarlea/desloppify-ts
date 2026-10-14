"""Line numbering shared by the TypeScript detectors and fixers.

Lines end where JavaScript says they do, as tsc counts them: at LF, CR,
CRLF, U+2028 and U+2029. VT, FF and U+0085 (which ``str.splitlines`` also
breaks at) don't end a line. Every reported line uses this rule, so a fixer
looking up a detector's line finds the same place, and so do tsc's own
positions (``nodes.byte_offset``).

Tree-sitter rows count LF only; ``ParsedSource.line`` converts them.
"""

from __future__ import annotations

import re
from bisect import bisect_right

LINE_BREAK = re.compile("\r\n?|\n|[  ]")
LINE_BREAK_BYTES = re.compile(rb"\r\n?|\n|\xe2\x80[\xa8\xa9]")
# A break that tree-sitter's rows don't count.
_NON_LF_BREAK_BYTES = re.compile(rb"\r(?!\n)|\xe2\x80[\xa8\xa9]")


def split_lines(text: str) -> list[str]:
    """The lines of ``text``, breaks removed; like ``str.splitlines`` but with JavaScript's breaks."""
    lines = LINE_BREAK.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def line_starts(text: str) -> list[int]:
    """The offset where each line of ``text`` starts (as many as ``split_lines`` gives, at least one)."""
    starts = [0]
    starts.extend(match.end() for match in LINE_BREAK.finditer(text))
    if len(starts) > 1 and starts[-1] == len(text):
        starts.pop()
    return starts


def line_at(starts: list[int], offset: int) -> int:
    """The 1-based line holding ``offset``, given the ``line_starts`` of its text."""
    return max(1, bisect_right(starts, offset))


def line_number(text: str, offset: int) -> int:
    """The 1-based line of ``text`` holding ``offset``."""
    return 1 + len(LINE_BREAK.findall(text, 0, offset))


def byte_line_starts(source: bytes) -> list[int] | None:
    """The byte offset where each line starts, or None when tree-sitter rows already are lines."""
    if _NON_LF_BREAK_BYTES.search(source) is None:
        return None
    return [0, *(match.end() for match in LINE_BREAK_BYTES.finditer(source))]


__all__ = [
    "LINE_BREAK",
    "LINE_BREAK_BYTES",
    "byte_line_starts",
    "line_at",
    "line_number",
    "line_starts",
    "split_lines",
]
