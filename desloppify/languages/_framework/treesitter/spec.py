"""Tree-sitter grammar and query used for TypeScript and JavaScript."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TreeSitterLangSpec:
    """Grammar plus the query that captures named functions and their bodies."""

    grammar: str
    function_query: str


# The tsx grammar parses .ts, .tsx and plain JavaScript alike.
TYPESCRIPT_SPEC = TreeSitterLangSpec(
    grammar="tsx",
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
