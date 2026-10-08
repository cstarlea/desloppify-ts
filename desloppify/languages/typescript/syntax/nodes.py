"""Syntax-tree lookups shared by the unused detector and the fixers."""

from __future__ import annotations

from collections import defaultdict

from desloppify.languages.typescript.syntax.tree import ParsedSource

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
PARAMETERS = frozenset({"required_parameter", "optional_parameter"})
_DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})
# Nodes whose children are statements that can be deleted outright.
STATEMENT_PARENTS = frozenset({"program", "statement_block", "switch_case", "switch_default"})


def byte_offset(source: bytes, line: int, col: int) -> int | None:
    """The byte offset of tsc's 1-based ``line``/``col`` (col in UTF-16 units)."""
    if line < 1 or col < 1:
        return None
    start = 0
    for _ in range(line - 1):
        newline = source.find(b"\n", start)
        if newline == -1:
            return None
        start = newline + 1
    newline = source.find(b"\n", start)
    text = source[start : len(source) if newline == -1 else newline].decode("utf-8", "replace")
    units = 0
    for index, char in enumerate(text):
        if units >= col - 1:
            return start + len(text[:index].encode("utf-8"))
        units += 2 if ord(char) > 0xFFFF else 1
    return start + len(text.encode("utf-8")) if units == col - 1 else None


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


_SAFE_LEAVES = frozenset(
    {
        "string",
        "number",
        "true",
        "false",
        "null",
        "undefined",
        "identifier",
        "this",
        "regex",
        "arrow_function",
        "function_expression",
        "function",
    }
)
_SAFE_WRAPPERS = frozenset(
    {"parenthesized_expression", "as_expression", "satisfies_expression", "non_null_expression"}
)
# Built-ins that only read their arguments.
_PURE_CALLS = frozenset(
    {
        "JSON.stringify",
        "Object.keys",
        "Object.values",
        "Object.entries",
        "Array.isArray",
        "String",
        "Number",
        "Boolean",
    }
)
_PURE_METHODS = frozenset({"toFixed", "toString", "toISOString"})


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


def reads_only(parsed: ParsedSource, node) -> bool:
    """Whether evaluating ``node`` only reads values.

    Property reads and string conversion are assumed side-effect free; calls
    are allowed only to a few built-ins that just read their arguments.
    """
    kind = node.type
    if kind in _SAFE_LEAVES:
        return True
    if kind == "template_string":
        return all(
            bool(sub.named_children) and reads_only(parsed, sub.named_children[0])
            for sub in node.named_children
            if sub.type == "template_substitution"
        )
    if kind in _SAFE_WRAPPERS:
        return bool(node.named_children) and reads_only(parsed, node.named_children[0])
    if kind == "member_expression":
        obj = node.child_by_field_name("object")
        return obj is not None and reads_only(parsed, obj)
    if kind == "subscript_expression":
        return all(reads_only(parsed, c) for c in node.named_children)
    if kind == "unary_expression":
        operator = node.child_by_field_name("operator")
        argument = node.child_by_field_name("argument")
        return (
            operator is not None
            and operator.type != "delete"
            and argument is not None
            and reads_only(parsed, argument)
        )
    if kind in ("binary_expression", "ternary_expression"):
        return all(reads_only(parsed, c) for c in node.named_children)
    if kind == "array":
        return all(c.type != "spread_element" and reads_only(parsed, c) for c in node.named_children)
    if kind == "object":
        for child in node.named_children:
            if child.type == "shorthand_property_identifier":
                continue
            if child.type != "pair":
                return False
            key, value = child.child_by_field_name("key"), child.child_by_field_name("value")
            if key is None or key.type == "computed_property_name" or value is None:
                return False
            if not reads_only(parsed, value):
                return False
        return True
    if kind == "call_expression":
        return _is_pure_call(parsed, node)
    return False


def _is_pure_call(parsed: ParsedSource, call) -> bool:
    function = call.child_by_field_name("function")
    args = call.child_by_field_name("arguments")
    if function is None or args is None:
        return False
    if not all(
        a.type != "spread_element" and reads_only(parsed, a)
        for a in args.named_children
        if a.type != "comment"
    ):
        return False
    if parsed.text(function) in _PURE_CALLS:
        return True
    if function.type == "member_expression":
        prop = function.child_by_field_name("property")
        obj = function.child_by_field_name("object")
        return (
            prop is not None
            and parsed.text(prop) in _PURE_METHODS
            and obj is not None
            and reads_only(parsed, obj)
        )
    return False


def parameter_owner(node):
    """The function or catch clause whose parameter ``node`` binds, or None."""
    if node.type not in ("identifier", "shorthand_property_identifier_pattern"):
        return None
    if _is_binding(node) and is_parameter(node):
        owner = node.parent
        while owner is not None and owner.type not in FUNCTIONS:
            owner = owner.parent
        return owner
    clause = node.parent
    while clause is not None and clause.type != "catch_clause":
        clause = clause.parent
    parameter = clause.child_by_field_name("parameter") if clause is not None else None
    if parameter is not None and within(node, parameter) and _is_binding(node):
        return clause
    return None


def _is_binding(node) -> bool:
    """Whether ``node`` is a name a parameter binds, not a default value or type."""
    parent = node.parent
    if node.type == "shorthand_property_identifier_pattern":
        return True
    if parent.type in (*PARAMETERS, "assignment_pattern"):
        return same(parent.child_by_field_name("pattern" if parent.type in PARAMETERS else "left"), node)
    if parent.type == "pair_pattern":
        return same(parent.child_by_field_name("value"), node)
    if parent.type == "arrow_function":
        return same(parent.child_by_field_name("parameter"), node)
    return parent.type in ("array_pattern", "rest_pattern", "catch_clause")


__all__ = [
    "asi_hazards",
    "FUNCTIONS",
    "NAME_TYPES",
    "STATEMENT_PARENTS",
    "PARAMETERS",
    "NameIndex",
    "byte_offset",
    "is_parameter",
    "parameter_owner",
    "node_key",
    "reads_only",
    "same",
    "within",
]
