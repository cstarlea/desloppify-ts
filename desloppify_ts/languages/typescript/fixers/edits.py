"""Byte-range edits shared by the syntax-tree fixers.

Fixers collect ``(start, end)`` byte ranges from tree-sitter nodes and apply
them in one pass. Deletions may overlap and are merged; replacements must not
overlap.
"""

from __future__ import annotations


def apply_edits(source: bytes, edits: list[tuple[int, int]]) -> bytes:
    """Delete every range, merging ranges that overlap or touch."""
    merged: list[list[int]] = []
    for start, end in sorted(edits):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for start, end in reversed(merged):
        source = source[:start] + source[end:]
    return source


def apply_replacements(
    source: bytes, replacements: list[tuple[int, int, bytes]]
) -> bytes:
    """Replace each ``source[start:end]`` with its bytes. Ranges must not overlap."""
    for start, end, text in sorted(replacements, reverse=True):
        source = source[:start] + text + source[end:]
    return source


def comma_list_edits(items: list, remove: set[int]) -> list[tuple[int, int]]:
    """Byte ranges that delete ``items[i]`` for each ``i`` in ``remove``.

    ``items`` are the nodes of one comma-separated list. A run of removed
    items followed by a kept one is deleted up to that kept item's start; a
    trailing run is deleted from the previous kept item's end, which also
    takes the separating comma. Removing every item is the caller's job.
    """
    edits: list[tuple[int, int]] = []
    index = 0
    while index < len(items):
        if index not in remove:
            index += 1
            continue
        run_start = index
        while index < len(items) and index in remove:
            index += 1
        if index < len(items):
            edits.append((items[run_start].start_byte, items[index].start_byte))
        elif run_start > 0:
            edits.append((items[run_start - 1].end_byte, items[index - 1].end_byte))
    return edits


def whole_statement_range(
    source: bytes, statement, *, leading_jsdoc: bool = False
) -> tuple[int, int]:
    """The statement's bytes, widened to whole lines when it has them to itself.

    A trailing ``//`` comment goes with the statement. With ``leading_jsdoc``,
    a ``/** ... */`` block on the lines directly above goes too. One blank
    line is dropped as well when the statement sat between two of them.
    """
    start, end = statement.start_byte, statement.end_byte
    line_start = source.rfind(b"\n", 0, start) + 1
    newline = source.find(b"\n", end)
    line_end = len(source) if newline == -1 else newline
    rest = source[end:line_end].strip()
    if source[line_start:start].strip() or (rest and not rest.startswith(b"//")):
        # Shares its line with other code: remove just the statement and the
        # whitespace that separated it from its neighbour.
        if rest:
            while end < line_end and source[end : end + 1] in (b" ", b"\t"):
                end += 1
        else:
            while start > line_start and source[start - 1 : start] in (b" ", b"\t"):
                start -= 1
        return start, end

    if leading_jsdoc:
        line_start = _jsdoc_start(source, statement, line_start)
    remove_end = len(source) if newline == -1 else newline + 1
    previous_blank = line_start == 0 or source[:line_start].endswith(b"\n\n")
    next_newline = source.find(b"\n", remove_end)
    next_line = source[remove_end : len(source) if next_newline == -1 else next_newline]
    if previous_blank and remove_end < len(source) and not next_line.strip():
        remove_end = len(source) if next_newline == -1 else next_newline + 1
    return line_start, remove_end


def _jsdoc_start(source: bytes, statement, line_start: int) -> int:
    """Where a JSDoc block directly above the statement's line starts, if any."""
    comment = statement.prev_sibling
    if comment is None or comment.type != "comment":
        return line_start
    if not source[comment.start_byte : comment.end_byte].startswith(b"/**"):
        return line_start
    if source[comment.end_byte : line_start].strip():
        return line_start  # code between the comment and the statement
    if source[comment.end_byte : line_start].count(b"\n") != 1:
        return line_start  # a blank line separates them
    comment_line_start = source.rfind(b"\n", 0, comment.start_byte) + 1
    if source[comment_line_start : comment.start_byte].strip():
        return line_start  # the comment trails other code
    return comment_line_start


__all__ = [
    "apply_edits",
    "apply_replacements",
    "comma_list_edits",
    "whole_statement_range",
]
