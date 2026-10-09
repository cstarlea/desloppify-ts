"""Statement-shaped TypeScript smells: empty catches, ``.sort()`` and ``void x;``."""

from __future__ import annotations

from desloppify.languages.typescript.syntax.queries import descendants
from desloppify.languages.typescript.syntax.tree import ParsedSource

from .detector_core import _emit, _node_line, _parsed
from .helpers import _regex_line_matches

# Without tree-sitter: one match per line, starting in code.
_FALLBACK_PATTERNS = {
    "empty_catch": r"catch\s*\([^)]*\)\s*\{\s*\}",
    "sort_no_comparator": r"\.sort\(\s*\)",
    "voided_symbol": r"^\s*void\s+[a-zA-Z_]\w*\s*;?\s*$",
}


def _detect_statement_smells(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find empty catch blocks (comments count as content), ``.sort()`` calls
    without a comparator, and ``void name;`` statements that silence an unused symbol."""
    parsed = _parsed(ctx)
    if parsed is None:
        for smell_id, pattern in _FALLBACK_PATTERNS.items():
            for index, line in _regex_line_matches(ctx, pattern):
                _emit(smell_counts, smell_id, ctx, index + 1, line.strip()[:100])
        return
    for smell_id, node in _statement_smells(parsed):
        _emit(smell_counts, smell_id, ctx, *_node_line(parsed, node))


def _statement_smells(parsed: ParsedSource):
    for node in descendants(
        parsed.root, ("catch_clause", "call_expression", "expression_statement")
    ):
        if node.type == "catch_clause":
            body = node.child_by_field_name("body")
            if body is not None and not body.named_children:
                yield "empty_catch", node
        elif node.type == "call_expression":
            sort = _sort_property(parsed, node)
            args = node.child_by_field_name("arguments")
            if sort is not None and args is not None and not args.named_children:
                yield "sort_no_comparator", sort
        elif _voided_name(node):
            yield "voided_symbol", node


def _sort_property(parsed: ParsedSource, call):
    """The ``sort`` of ``x.sort(...)``, else None."""
    function = call.child_by_field_name("function")
    if function is None or function.type != "member_expression":
        return None
    prop = function.child_by_field_name("property")
    return prop if prop is not None and parsed.text(prop) == "sort" else None


def _voided_name(statement) -> bool:
    named = statement.named_children
    if not named or named[0].type != "unary_expression":
        return False
    unary = named[0]
    operator = unary.child_by_field_name("operator")
    argument = unary.child_by_field_name("argument")
    return (
        operator is not None
        and operator.type == "void"
        and argument is not None
        and argument.type == "identifier"
    )


__all__ = ["_detect_statement_smells"]
