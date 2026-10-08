"""Tree-sitter parsing for TypeScript and JavaScript, shared by detectors and fixers.

``.ts``/``.mts``/``.cts`` files use the ``typescript`` grammar, where
angle-bracket casts (``<T>value``) are valid; everything else uses ``tsx``.
All functions return None when tree-sitter or the grammar is unavailable, so
callers decide how to degrade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from desloppify.languages._framework.treesitter import PARSE_INIT_ERRORS, is_available
from desloppify.languages._framework.treesitter.parsing import _get_parser

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


def parse_text(text: str, path: str | Path) -> ParsedSource | None:
    """Parse in-memory source (for example a fixer's working copy)."""
    parser = get_parser(grammar_for(path))
    if parser is None:
        return None
    source = text.encode("utf-8")
    return ParsedSource(source, parser.parse(source))


__all__ = [
    "ParsedSource",
    "get_parser",
    "grammar_for",
    "parse_text",
]
