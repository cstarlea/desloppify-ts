"""Tree-sitter parsing and function query used for TypeScript and JavaScript."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class TreeSitterLangSpec:
    """How to parse a file, plus the query that captures named functions.

    ``parse_file`` returns ``(source_bytes, tree)`` or None; the query is
    compiled for whichever grammar produced the tree.
    """

    parse_file: Callable[[str], tuple[bytes, object] | None]
    function_query: str


def _parse_typescript(path: str) -> tuple[bytes, object] | None:
    """Parse through ``syntax.tree.parsed_file``, sharing the scan's parse."""
    from desloppify.languages.typescript.syntax.tree import parsed_file

    parsed = parsed_file(path)
    return None if parsed is None else (parsed.source, parsed.tree)


TYPESCRIPT_SPEC = TreeSitterLangSpec(
    parse_file=_parse_typescript,
    function_query="""
        (function_declaration
            name: (identifier) @name
            body: (statement_block) @body) @func
        (method_definition
            name: (property_identifier) @name
            body: (statement_block) @body) @func
        (variable_declarator
            name: (identifier) @name
            value: (arrow_function
                body: (statement_block) @body)) @func
    """,
)


__all__ = ["TYPESCRIPT_SPEC", "TreeSitterLangSpec"]
