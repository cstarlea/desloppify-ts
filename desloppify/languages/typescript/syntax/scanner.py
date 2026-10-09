"""String- and comment-aware TypeScript character scanner."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Generator, Iterator
from functools import cached_property
from pathlib import Path

from desloppify.languages._framework.node.js_text import (
    blank_spans,
    code_text,
    literal_spans,
)
from desloppify.languages._framework.treesitter import PARSE_INIT_ERRORS
from desloppify.languages._framework.treesitter.parsing import _make_query, _run_query
from desloppify.languages.typescript.syntax.lines import (
    line_at,
    line_starts,
    split_lines,
)
from desloppify.languages.typescript.syntax.tree import (
    grammar_for,
    parse_text,
    parsed_file,
)

_JSX_TEXT_QUERIES: dict[int, object] = {}


def scan_code(
    text: str, start: int = 0, end: int | None = None
) -> Generator[tuple[int, str, bool], None, None]:
    """Yield ``(index, char, in_string)`` for ``text[start:end]``.

    ``in_string`` is True for anything that isn't code: string, template and
    regex literal text, and comments (see ``js_text.code_text``).
    """
    limit = end if end is not None else len(text)
    chunk = text[start:limit]
    code = code_text(chunk)
    for offset, ch in enumerate(chunk):
        yield (start + offset, ch, code[offset] != ch)


def jsx_text_spans(text: str, path: str | Path | None) -> list[tuple[int, int]]:
    """The ``(start, end)`` offsets in ``text`` of each JSX text run, from the syntax tree.

    Empty for ``.ts`` files (no JSX), without a path, or without tree-sitter.
    ``path`` picks the grammar; its parse is reused when it holds ``text``.
    """
    if path is None or grammar_for(path) != "tsx" or ("</" not in text and "/>" not in text):
        return []
    source = text.encode("utf-8")
    parsed = parsed_file(path)
    if parsed is None or parsed.source != source:
        parsed = parse_text(text, path)
    if parsed is None:
        return []
    language = parsed.tree.language
    query = _JSX_TEXT_QUERIES.get(id(language))
    if query is None:
        try:
            query = _JSX_TEXT_QUERIES[id(language)] = _make_query(language, "(jsx_text) @text")
        except PARSE_INIT_ERRORS:
            return []
    nodes = sorted(
        (node for _pattern, captures in _run_query(query, parsed.root) for node in captures["text"]),
        key=lambda node: node.start_byte,
    )
    if len(source) == len(text):
        return [(node.start_byte, node.end_byte) for node in nodes]
    offsets = [0] * (len(source) + 1)  # byte offset -> character offset
    byte = 0
    for index, ch in enumerate(text):
        width = len(ch.encode("utf-8", "surrogatepass"))
        offsets[byte : byte + width] = [index] * width
        byte += width
    offsets[byte] = len(text)
    return [(offsets[node.start_byte], offsets[node.end_byte]) for node in nodes]


def file_code_text(text: str, path: str | Path | None) -> str:
    """``code_text`` for a file's contents, its JSX text blanked too."""
    return code_text(text, jsx_text_spans(text, path))


class SourceText:
    """A file's lines, with each offset known to be code or inside a comment or literal.

    ``lines`` and ``code_lines`` line up one to one; ``code_lines`` has every
    comment and string, template and regex literal blanked to spaces, the code
    inside ``${...}`` kept. Given the file's ``path``, JSX text in a ``.tsx`` or
    ``.jsx`` file is blanked too (kind ``jsx``).
    """

    def __init__(self, text: str, path: str | Path | None = None) -> None:
        self.text = text
        self.spans = list(literal_spans(text, jsx_text_spans(text, path)))
        self._span_starts = [start for start, _end, _kind in self.spans]
        self.lines = split_lines(text)
        self.line_starts = line_starts(text)[: len(self.lines)]

    @cached_property
    def code(self) -> str:
        return blank_spans(self.text, self.spans)

    @cached_property
    def code_lines(self) -> list[str]:
        return self._split(self.code)

    @cached_property
    def uncommented(self) -> str:
        """The text with only comments blanked: strings and templates kept."""
        return blank_spans(self.text, [span for span in self.spans if span[2] == "comment"])

    @cached_property
    def uncommented_lines(self) -> list[str]:
        return self._split(self.uncommented)

    def _split(self, text: str) -> list[str]:
        """``text`` (the same length as the source) cut where the source's lines are."""
        return [text[start : start + len(line)] for start, line in zip(self.line_starts, self.lines)]

    def line_of(self, offset: int) -> int:
        """The 1-based line holding ``offset``."""
        return line_at(self.line_starts, offset)

    def kind_at(self, offset: int) -> str | None:
        """``comment``, ``string``, ``template``, ``regex`` or ``jsx`` when ``offset`` is inside one, None for code."""
        index = bisect_right(self._span_starts, offset) - 1
        if index >= 0:
            start, end, kind = self.spans[index]
            if start <= offset < end:
                return kind
        return None

    def starts(self, offset: int) -> str | None:
        """The kind of comment or literal that begins exactly at ``offset``, else None."""
        index = bisect_right(self._span_starts, offset) - 1
        if index >= 0 and self.spans[index][0] == offset:
            return self.spans[index][2]
        return None

    def line_matches(self, pattern: str | re.Pattern[str], anchor: str = "code") -> Iterator[tuple[int, re.Match[str]]]:
        """``(line index, match)`` for the first anchored match of ``pattern`` on each line."""
        regex = re.compile(pattern) if isinstance(pattern, str) else pattern
        for index in range(len(self.lines)):
            match = self.search(regex, index, anchor)
            if match is not None:
                yield index, match

    def search(self, pattern: str | re.Pattern[str], index: int, anchor: str = "code") -> re.Match[str] | None:
        """The first match of ``pattern`` on line ``index`` whose first
        non-blank character is where ``anchor`` says.

        ``code``: in code. ``literal``: the opening quote of a string or
        template literal. ``comment``: in a comment. ``uncommented``: anywhere
        but a comment.
        """
        regex = re.compile(pattern) if isinstance(pattern, str) else pattern
        line = self.lines[index]
        position = 0
        while (match := regex.search(line, position)) is not None:
            text = match.group()
            offset = self.line_starts[index] + match.start() + len(text) - len(text.lstrip())
            if self._anchored(offset, anchor):
                return match
            position = match.start() + 1
            if position > len(line):  # search() clamps a later start, so an empty match would repeat
                break
        return None

    def _anchored(self, offset: int, anchor: str) -> bool:
        if anchor == "code":
            return self.kind_at(offset) is None
        if anchor == "uncommented":
            return self.kind_at(offset) != "comment"
        if anchor == "literal":
            return self.starts(offset) in ("string", "template")
        return self.kind_at(offset) == "comment"
