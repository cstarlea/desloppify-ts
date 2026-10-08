"""Type-safety TypeScript smells: ``any``, unsafe casts, non-null assertions and suppressions."""

from __future__ import annotations

import re

from desloppify.languages.typescript.syntax.queries import descendants
from desloppify.languages.typescript.syntax.tree import ParsedSource, parse_text, parsed_file

from .detector_core import _emit
from .helpers import _regex_line_matches

TYPE_SAFETY_SMELLS = (
    "any_type",
    "as_any_cast",
    "double_cast",
    "non_null_assert",
    "ts_ignore",
    "ts_expect_error_undocumented",
)

# The comment-directive rules from TypeScript's scanner: a line comment must
# start with the directive; a block comment's last line must.
_LINE_DIRECTIVE = re.compile(r"^///?\s*@(ts-expect-error|ts-ignore)")
_BLOCK_DIRECTIVE = re.compile(r"^(?:/|\*)*\s*@(ts-expect-error|ts-ignore)")
_DESCRIPTION = re.compile(r"\w")

_CASTS = frozenset({"as_expression", "type_assertion"})
_NODE_TYPES = _CASTS | {"non_null_expression", "predefined_type", "comment"}

# Without tree-sitter: one match per line, outside strings and block comments.
_FALLBACK_PATTERNS = {
    "any_type": r":\s*any\b|<\s*any\b|,\s*any\b(?=\s*(?:,|>))",
    "as_any_cast": r"\bas\s+any\b",
    "double_cast": r"\bas\s+unknown\s+as\b",
    "non_null_assert": r"\w+!\.",
    "ts_ignore": r"//\s*@ts-ignore",
    "ts_expect_error_undocumented": r"//\s*@ts-expect-error\s*(?:[:\-]+\s*)?$",
}


def _detect_type_safety(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find type-safety escape hatches, one match per occurrence."""
    parsed = parsed_file(ctx.filepath) or parse_text(ctx.content, ctx.filepath)
    if parsed is None:
        for smell_id, pattern in _FALLBACK_PATTERNS.items():
            for index, line in _regex_line_matches(ctx, pattern):
                _emit(smell_counts, smell_id, ctx, index + 1, line.strip()[:100])
        return
    for smell_id, node in _type_safety_nodes(parsed):
        _emit(smell_counts, smell_id, ctx, node.start_point[0] + 1, _line_text(parsed, node))


def _type_safety_nodes(parsed: ParsedSource):
    for node in descendants(parsed.root, _NODE_TYPES):
        kind = node.type
        if kind == "non_null_expression":
            yield "non_null_assert", node
        elif kind in _CASTS:
            inner = _cast_operand(node)
            if inner is not None and inner.type in _CASTS and _is_predefined(parsed, _cast_type(inner), "unknown"):
                yield "double_cast", node
        elif kind == "predefined_type":
            if parsed.text(node) == "any":
                smell = _any_kind(node)
                if smell is not None:
                    yield smell, node
        elif (directive := _directive(parsed.text(node))) is not None:
            if directive == "ts-ignore":
                yield "ts_ignore", node
            elif not _documented(parsed, node):
                yield "ts_expect_error_undocumented", node


def _cast_type(node):
    """The target type of ``x as T`` or ``<T>x``."""
    if node.type == "as_expression":
        return node.named_children[-1] if node.named_children else None
    args = node.named_children[0] if node.named_children else None
    if args is None or args.type != "type_arguments" or not args.named_children:
        return None
    return args.named_children[0]


def _cast_operand(node):
    """The expression being cast, parentheses removed."""
    named = node.named_children
    operand = named[0] if node.type == "as_expression" else named[-1] if len(named) > 1 else None
    while operand is not None and operand.type == "parenthesized_expression" and operand.named_children:
        operand = operand.named_children[0]
    return operand


def _is_predefined(parsed: ParsedSource, node, name: str) -> bool:
    return node is not None and node.type == "predefined_type" and parsed.text(node) == name


def _any_kind(node) -> str | None:
    """``as_any_cast`` when ``any`` (or ``any[]``) is a cast's target type,
    None for ``keyof any``, else ``any_type``."""
    target = node
    parent = node.parent
    while parent is not None and parent.type == "array_type":
        target, parent = parent, parent.parent
    if parent is None:
        return "any_type"
    if parent.type == "index_type_query":  # ``keyof any`` is PropertyKey
        return None
    if parent.type == "as_expression" and _same(_cast_type(parent), target):
        return "as_any_cast"
    if parent.type == "type_arguments" and parent.parent is not None and parent.parent.type == "type_assertion":
        return "as_any_cast"
    return "any_type"


def _same(a, b) -> bool:
    return a is not None and a.start_byte == b.start_byte and a.end_byte == b.end_byte


def _directive(text: str) -> str | None:
    if text.startswith("//"):
        match = _LINE_DIRECTIVE.match(text)
    else:
        match = _BLOCK_DIRECTIVE.match(text.rsplit("\n", 1)[-1])
    return match.group(1) if match else None


def _documented(parsed: ParsedSource, comment) -> bool:
    """Whether an ``@ts-expect-error`` explains itself, after the directive or in the comment above it."""
    text = parsed.text(comment)
    last = text.rsplit("\n", 1)[-1]
    match = (_LINE_DIRECTIVE if text.startswith("//") else _BLOCK_DIRECTIVE).match(last)
    rest = last[match.end() :] if match else ""
    if not text.startswith("//"):
        rest = rest.removesuffix("*/")
        if "\n" in text:  # a multi-line block comment's earlier lines
            return True
    if _DESCRIPTION.search(rest):
        return True
    previous = comment.prev_named_sibling
    return (
        previous is not None
        and previous.type == "comment"
        and previous.end_point[0] == comment.start_point[0] - 1
        and _directive(parsed.text(previous)) is None
    )


def _line_text(parsed: ParsedSource, node) -> str:
    source = parsed.source
    start = source.rfind(b"\n", 0, node.start_byte) + 1
    end = source.find(b"\n", node.start_byte)
    return source[start : len(source) if end == -1 else end].decode("utf-8", "replace").strip()[:100]


__all__ = ["TYPE_SAFETY_SMELLS", "_detect_type_safety"]
