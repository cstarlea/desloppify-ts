"""Syntax checks that stop fixers and ``move`` from writing broken code.

A rewrite is rejected when the new text has more tree-sitter parse errors
(ERROR and MISSING nodes) than the original. Comparing counts instead of
requiring zero errors keeps files that already fail to parse fixable.

This catches edits that break syntax, not edits that parse but change
behaviour; those need the fixers themselves to be correct.
"""

from __future__ import annotations

from pathlib import Path

from desloppify.base.discovery.sfc import is_sfc, sfc_code
from desloppify.languages.typescript.syntax.tree import (
    get_parser,
    grammar_for,
    parse_text,
)


def count_syntax_errors(text: str, path: str | Path) -> int | None:
    """Count ERROR and MISSING nodes, or None when tree-sitter can't parse.

    ``text`` is the file's content; a component's script code is what's parsed.
    """
    if is_sfc(path):
        component = sfc_code(text, path)
        parser = get_parser(component.grammar)
        if parser is None:
            return None
        root = parser.parse(component.view.encode("utf-8")).root_node
    else:
        parsed = parse_text(text, path)
        if parsed is None:
            return None
        root = parsed.root
    if not root.has_error:
        return 0
    count = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_error or node.is_missing:
            count += 1
        if node.has_error:
            stack.extend(node.children)
    return count


def syntax_regression(path: str | Path, before: str, after: str) -> str | None:
    """Describe how ``after`` breaks syntax that ``before`` had, or None.

    Returns None when the rewrite is fine or tree-sitter is unavailable;
    callers that need to know which use ``count_syntax_errors`` directly.
    """
    errors_after = count_syntax_errors(after, path)
    if not errors_after:
        return None
    errors_before = count_syntax_errors(before, path) or 0
    if errors_after <= errors_before:
        return None
    added = errors_after - errors_before
    return f"the rewrite would add {added} syntax error{'s' if added != 1 else ''}"


__all__ = ["count_syntax_errors", "grammar_for", "syntax_regression"]
