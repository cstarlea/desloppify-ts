"""Syntax-tree lookups shared by the fixers that act on unused names."""

from __future__ import annotations

from collections import defaultdict

from desloppify.languages.typescript.syntax.tree import ParsedSource

from .edits import byte_offset

NAME_TYPES = frozenset(
    {
        "identifier",
        "shorthand_property_identifier",
        "shorthand_property_identifier_pattern",
        "type_identifier",
    }
)
FUNCTIONS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "function",
        "generator_function",
        "arrow_function",
        "method_definition",
        "class_static_block",
    }
)
_DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})


def node_key(node) -> tuple[int, int, str]:
    return node.start_byte, node.end_byte, node.type


def same(a, b) -> bool:
    return a is not None and b is not None and node_key(a) == node_key(b)


def within(node, container) -> bool:
    return container.start_byte <= node.start_byte and node.end_byte <= container.end_byte


class NameIndex:
    """Every name node in a file, by text, built on first use."""

    def __init__(self, parsed: ParsedSource) -> None:
        self.parsed = parsed
        self._names: dict[str, list[object]] | None = None

    def get(self, name: str) -> list[object]:
        if self._names is None:
            names: dict[str, list[object]] = defaultdict(list)
            stack = [self.parsed.root]
            while stack:
                node = stack.pop()
                if node.type in NAME_TYPES:
                    names[self.parsed.text(node)].append(node)
                stack.extend(node.children)
            self._names = names
        return self._names.get(name, [])

    def find(self, name: str, line: int, col: object):
        """The name node tsc reported at ``line``/``col``.

        Falls back to the only node with that text on the line, since a
        stale column (or a BOM tsc counted) shouldn't lose an otherwise
        unambiguous match.
        """
        source = self.parsed.source
        offset = byte_offset(source, line, col) if isinstance(col, int) else None
        if offset is not None:
            node = self.parsed.root.named_descendant_for_byte_range(offset, offset)
            if (
                node is not None
                and node.type in NAME_TYPES
                and node.start_byte == offset
                and self.parsed.text(node) == name
            ):
                return node
        matches = [n for n in self.get(name) if n.start_point[0] == line - 1]
        return matches[0] if len(matches) == 1 else None


def is_parameter(node) -> bool:
    """Whether ``node`` sits in a function's parameter list."""
    child, parent = node, node.parent
    while parent is not None and parent.type not in ("statement_block", "program", *_DECLARATIONS):
        if parent.type == "formal_parameters":
            return True
        if parent.type == "arrow_function" and same(parent.child_by_field_name("parameter"), child):
            return True
        child, parent = parent, parent.parent
    return False


__all__ = [
    "FUNCTIONS",
    "NAME_TYPES",
    "NameIndex",
    "is_parameter",
    "node_key",
    "same",
    "within",
]
