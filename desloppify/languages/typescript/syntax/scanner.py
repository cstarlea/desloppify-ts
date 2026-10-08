"""String- and comment-aware TypeScript character scanner."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Generator, Iterator
from functools import cached_property

from desloppify.languages._framework.node.js_text import blank_spans, code_text, literal_spans
from desloppify.languages.typescript.syntax.lines import line_at, line_starts, split_lines


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


class SourceText:
    """A file's lines, with each offset known to be code or inside a comment or literal.

    ``lines`` and ``code_lines`` line up one to one; ``code_lines`` has every
    comment and string, template and regex literal blanked to spaces, the code
    inside ``${...}`` kept.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.spans = list(literal_spans(text))
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
        """``comment``, ``string``, ``template`` or ``regex`` when ``offset`` is inside one, None for code."""
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
        return None

    def _anchored(self, offset: int, anchor: str) -> bool:
        if anchor == "code":
            return self.kind_at(offset) is None
        if anchor == "uncommented":
            return self.kind_at(offset) != "comment"
        if anchor == "literal":
            return self.starts(offset) in ("string", "template")
        return self.kind_at(offset) == "comment"
