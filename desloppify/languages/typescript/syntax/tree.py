"""Tree-sitter parsing for TypeScript and JavaScript, shared by detectors and fixers.

``.ts``/``.mts``/``.cts`` files use the ``typescript`` grammar, where
angle-bracket casts (``<T>value``) are valid; everything else uses ``tsx``.
All functions return None when tree-sitter or the grammar is unavailable, so
callers decide how to degrade.

``parsed_file`` is the entry point for files on disk: during a scan it goes
through the scan-scoped parse cache, so each file is parsed once per grammar
however many detectors ask. The cache is cleared when a scan starts and ends.
Typed queries over the result live in ``syntax.queries``.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.languages._framework.treesitter import PARSE_INIT_ERRORS, is_available
from desloppify.languages._framework.treesitter.cache import get_or_parse_tree
from desloppify.languages._framework.treesitter.parsing import _get_parser
from desloppify.languages.typescript.syntax.lines import LINE_BREAK_BYTES, byte_line_starts

logger = logging.getLogger(__name__)

_TYPESCRIPT_GRAMMAR_SUFFIXES = frozenset({".ts", ".mts", ".cts"})
_PARSERS: dict[str, object] = {}


def grammar_for(path: str | Path) -> str:
    """The tree-sitter grammar for a TS/JS file: ``typescript`` or ``tsx``."""
    suffix = Path(path).suffix.lower()
    return "typescript" if suffix in _TYPESCRIPT_GRAMMAR_SUFFIXES else "tsx"


def get_parser(grammar: str):
    """A parser for ``grammar``, or None when tree-sitter can't provide one."""
    if grammar in _PARSERS:
        return _PARSERS[grammar]
    if not is_available():
        return None
    try:
        parser, _language = _get_parser(grammar)
    except PARSE_INIT_ERRORS as exc:  # recorded as a grammar load failure
        logger.debug("tree-sitter %s grammar unavailable: %s", grammar, exc)
        return None
    _PARSERS[grammar] = parser
    return parser


@dataclass(frozen=True)
class ParsedSource:
    """A parse tree plus the exact bytes it was parsed from."""

    source: bytes
    tree: object

    @property
    def root(self):
        return self.tree.root_node

    def text(self, node) -> str:
        return self.source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    @cached_property
    def _line_starts(self) -> list[int] | None:
        return byte_line_starts(self.source)

    def line(self, node) -> int:
        """The 1-based line where ``node`` starts (see ``syntax.lines``)."""
        if self._line_starts is None:
            return node.start_point[0] + 1
        return bisect_right(self._line_starts, node.start_byte)

    def end_line(self, node) -> int:
        """The 1-based line where ``node`` ends."""
        if self._line_starts is None:
            return node.end_point[0] + 1
        return bisect_right(self._line_starts, node.end_byte)

    def line_text(self, node) -> str:
        """The text of the line where ``node`` starts, line break left out."""
        if self._line_starts is None:
            start = self.source.rfind(b"\n", 0, node.start_byte) + 1
        else:
            start = self._line_starts[self.line(node) - 1]
        match = LINE_BREAK_BYTES.search(self.source, node.start_byte)
        end = len(self.source) if match is None else match.start()
        return self.source[start:end].decode("utf-8", "replace")


def parse_text(text: str, path: str | Path) -> ParsedSource | None:
    """Parse in-memory source (for example a fixer's working copy)."""
    parser = get_parser(grammar_for(path))
    if parser is None:
        return None
    source = text.encode("utf-8")
    return ParsedSource(source, parser.parse(source))


def parsed_file(path: str | Path) -> ParsedSource | None:
    """Parse a file on disk (relative paths are under the project root), once per scan.

    The tree covers the file's exact bytes. None without tree-sitter or when
    the file can't be read.
    """
    grammar = grammar_for(path)
    parser = get_parser(grammar)
    if parser is None:
        return None
    cached = get_or_parse_tree(resolve_path(str(path)), parser, grammar)
    if cached is None:
        return None
    source, tree = cached
    return ParsedSource(source, tree)


__all__ = [
    "ParsedSource",
    "get_parser",
    "grammar_for",
    "parse_text",
    "parsed_file",
]
