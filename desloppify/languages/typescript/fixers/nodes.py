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
# Nodes whose children are statements that can be deleted outright.
STATEMENT_PARENTS = frozenset({"program", "statement_block", "switch_case", "switch_default"})


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


# A statement starting with one of these continues a previous one with no semicolon.
_ASI_HAZARD_STARTS = frozenset(b"([`+-/<")
# Statements that end in a block, so nothing after them can continue them.
_CLOSED_STATEMENTS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "enum_declaration",
        "internal_module",
        "module",
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "try_statement",
        "switch_statement",
        "statement_block",
        "import_statement",
    }
)


def asi_hazards(source: bytes, statements: dict) -> list:
    """Keys of ``statements`` (key -> node) that must stay so the rest can go.

    Without semicolons, ``a = b`` followed by ``(f)()`` reads as ``a = b(f)()``
    once the statement that separated them is gone. Hazardous statements are
    kept one at a time, last first, since keeping one can make its
    neighbours' removal safe again.
    """
    removing = dict(statements)
    kept = []
    while hazards := [k for k, node in removing.items() if _asi_hazard(source, node, removing)]:
        key = max(hazards)
        removing.pop(key)
        kept.append(key)
    return kept


def _asi_hazard(source: bytes, statement, removing: dict) -> bool:
    """Whether removing ``statement`` lets the next statement continue the previous one.

    Without semicolons, ``a = b`` followed by ``(f)()`` reads as ``a = b(f)()``
    once the statement that separated them is gone. Neighbours in
    ``removing`` are going too, so they're looked past.
    """
    following = _neighbour(statement, removing, forward=True)
    if following is None or source[following.start_byte] not in _ASI_HAZARD_STARTS:
        return False
    preceding = _neighbour(statement, removing, forward=False)
    if preceding is None or preceding.type in _CLOSED_STATEMENTS:
        return False
    return not source[: preceding.end_byte].rstrip().endswith(b";")


def _neighbour(statement, removing: dict, *, forward: bool):
    sibling = statement
    while True:
        sibling = sibling.next_named_sibling if forward else sibling.prev_named_sibling
        if sibling is None or (sibling.type != "comment" and node_key(sibling) not in removing):
            return sibling


__all__ = [
    "asi_hazards",
    "FUNCTIONS",
    "NAME_TYPES",
    "STATEMENT_PARENTS",
    "NameIndex",
    "is_parameter",
    "node_key",
    "same",
    "within",
]
