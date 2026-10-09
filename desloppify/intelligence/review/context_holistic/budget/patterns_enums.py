"""Enum and union bypass, and the type-strategy census."""

from __future__ import annotations

from desloppify.languages.typescript.syntax.queries import (
    classes,
    descendants,
    string_value,
    type_declarations,
)
from desloppify.languages.typescript.syntax.tree import ParsedSource

_GENERIC_INT_VALUES: frozenset[object] = frozenset({0, 1, 2, 3, -1})
_EQUALITY_OPS = frozenset({"===", "!==", "==", "!="})
_FUNCTION_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "function",
        "generator_function",
        "arrow_function",
        "method_definition",
    }
)
_SCHEMA_INFER_NAMES = frozenset({"infer", "input", "output", "TypeOf", "InferInput", "InferOutput"})


def _collect_enum_defs(
    parsed_files: dict[str, ParsedSource],
) -> dict[tuple[str, str], dict]:
    """Enums with literal member values, and aliases of string-literal unions.

    Each entry has ``kind`` (``enum`` or ``union``) and ``members`` mapping a
    member name (the literal itself for a union) to its value.
    """
    result: dict[tuple[str, str], dict] = {}
    for rpath, parsed in parsed_files.items():
        for node in descendants(parsed.root, ("enum_declaration",)):
            name = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name is None or body is None:
                continue
            members: dict[str, object] = {}
            for child in body.named_children:
                if child.type != "enum_assignment":
                    continue
                key = child.child_by_field_name("name")
                value = _literal_value(parsed, child.child_by_field_name("value"))
                if key is not None and value is not None:
                    members[parsed.text(key)] = value
            if members:
                result[(rpath, parsed.text(name))] = {"file": rpath, "kind": "enum", "members": members}
        for decl in type_declarations(parsed):
            if decl.kind != "alias":
                continue
            literals = _string_union(parsed, decl.value)
            if literals is not None and len(literals) >= 2:
                result[(rpath, decl.name)] = {
                    "file": rpath,
                    "kind": "union",
                    "members": {value: value for value in literals},
                }
    return result


def _find_enum_bypass(
    parsed_files: dict[str, ParsedSource],
    enum_defs: dict[tuple[str, str], dict],
) -> list[dict]:
    """Raw literals compared where an enum or a literal union already names the value.

    An enum value compared outside the enum's file is a bypass whatever the
    other side is (``typeof x === 'object'`` aside). A union value is only a
    bypass when the other side is declared as plain ``string``: comparing a
    union-typed value against its literals is how TypeScript narrows.
    """
    value_to_types: dict[object, list[tuple[str, str, str, str | None]]] = {}
    for (file, type_name), info in enum_defs.items():
        for member, value in info["members"].items():
            if value == "" or (isinstance(value, int) and value in _GENERIC_INT_VALUES):
                continue
            label = member if info["kind"] == "enum" else None
            value_to_types.setdefault(value, []).append((file, info["kind"], type_name, label))
    if not value_to_types:
        return []

    results: list[dict] = []
    for rpath, parsed in parsed_files.items():
        for literal, subject in _literal_comparisons(parsed):
            value = _literal_value(parsed, literal)
            if value is None or value not in value_to_types or subject.type == "unary_expression":
                continue
            string_typed: bool | None = None
            for file, kind, type_name, member in value_to_types[value]:
                if kind == "enum":
                    if file == rpath:
                        continue
                else:
                    if string_typed is None:
                        string_typed = _declared_string(parsed, subject)
                    if not string_typed:
                        continue
                results.append(
                    {
                        "file": rpath,
                        "line": parsed.line(literal),
                        "kind": kind,
                        "type_name": type_name,
                        "member": member,
                        "raw_value": parsed.text(literal),
                        "compared": parsed.text(subject)[:60],
                    }
                )
    results.sort(key=lambda item: (item["file"], item["line"]))
    return results


def _literal_comparisons(parsed: ParsedSource):
    """(literal, other side) for ``x === 'lit'`` comparisons and ``case 'lit':`` labels."""
    for node in descendants(parsed.root, ("binary_expression", "switch_statement")):
        if node.type == "binary_expression":
            operator = node.child_by_field_name("operator")
            if operator is None or operator.type not in _EQUALITY_OPS:
                continue
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is None or right is None:
                continue
            if _is_literal(right) and not _is_literal(left):
                yield right, left
            elif _is_literal(left) and not _is_literal(right):
                yield left, right
            continue
        subject = node.child_by_field_name("value")
        body = node.child_by_field_name("body")
        if subject is None or body is None:
            continue
        while subject.type == "parenthesized_expression" and subject.named_children:
            subject = subject.named_children[0]
        for case in body.named_children:
            value = case.child_by_field_name("value") if case.type == "switch_case" else None
            if value is not None and _is_literal(value):
                yield value, subject


def _is_literal(node) -> bool:
    return node.type in ("string", "number")


def _literal_value(parsed: ParsedSource, node) -> object | None:
    if node is None:
        return None
    if node.type == "string":
        return string_value(parsed, node)
    if node.type == "number":
        try:
            return int(parsed.text(node))
        except ValueError:
            return None
    return None


def _union_members(node) -> list:
    if node.type == "union_type":
        return [m for child in node.named_children for m in _union_members(child)]
    return [node]


def _string_union(parsed: ParsedSource, node) -> list[str] | None:
    """The literals of a union made only of string literal types, else None."""
    if node.type != "union_type":
        return None
    literals = []
    for member in _union_members(node):
        inner = member.named_children[0] if member.type == "literal_type" and member.named_children else None
        if inner is None or inner.type != "string":
            return None
        literals.append(string_value(parsed, inner))
    return literals


def _is_string_type(node) -> bool:
    """``string``, optionally unioned with ``undefined`` or ``null``."""
    if node is None:
        return False
    if node.type == "type_annotation":
        node = node.named_children[0] if node.named_children else None
        if node is None:
            return False
    kinds = []
    for member in _union_members(node):
        if member.type == "predefined_type" and member.text == b"string":
            kinds.append("string")
        elif member.type == "literal_type" and member.named_children and member.named_children[0].type in (
            "undefined",
            "null",
        ):
            kinds.append("nullish")
        else:
            return False
    return "string" in kinds


def _declared_string(parsed: ParsedSource, subject) -> bool:
    """Whether an identifier is a parameter or variable declared as ``string``."""
    if subject.type != "identifier":
        return False
    name = subject.text
    scope = subject.parent
    while scope is not None:
        if scope.type in _FUNCTION_TYPES:
            params = scope.child_by_field_name("parameters")
            for param in params.named_children if params is not None else ():
                pattern = param.child_by_field_name("pattern")
                if pattern is not None and pattern.type == "identifier" and pattern.text == name:
                    return _is_string_type(param.child_by_field_name("type"))
        if scope.type in _FUNCTION_TYPES or scope.parent is None:
            body = scope.child_by_field_name("body") if scope.parent is not None else scope
            for declarator in descendants(body, ("variable_declarator",)) if body is not None else ():
                key = declarator.child_by_field_name("name")
                if key is not None and key.type == "identifier" and key.text == name:
                    return _is_string_type(declarator.child_by_field_name("type"))
        scope = scope.parent
    return False


def _census_type_strategies(
    parsed_files: dict[str, ParsedSource],
) -> dict[str, list[dict]]:
    """Count type definitions by strategy: interface, object alias, schema-inferred
    alias, data-carrying class, enum and string-literal union."""
    strategies: dict[str, list[dict]] = {
        "interface": [],
        "object_type_alias": [],
        "schema_inferred": [],
        "class": [],
        "enum": [],
        "string_union": [],
    }
    for rpath, parsed in parsed_files.items():
        for decl in type_declarations(parsed):
            entry = {"name": decl.name, "file": rpath, "line": decl.line}
            if decl.kind == "interface":
                strategies["interface"].append(entry)
            elif decl.value.type == "object_type":  # type: ignore[attr-defined]
                strategies["object_type_alias"].append(entry)
            elif _is_schema_inferred(parsed, decl.value):
                strategies["schema_inferred"].append(entry)
            elif _string_union(parsed, decl.value) is not None:
                strategies["string_union"].append(entry)
        for info in classes(parsed):
            if info.name and (
                any(m.kind == "field" and not m.is_static for m in info.members) or _has_parameter_properties(info)
            ):
                strategies["class"].append({"name": info.name, "file": rpath, "line": info.line})
        for node in descendants(parsed.root, ("enum_declaration",)):
            name = node.child_by_field_name("name")
            if name is not None:
                strategies["enum"].append({"name": parsed.text(name), "file": rpath, "line": parsed.line(node)})
    return {name: items for name, items in strategies.items() if items}


def _has_parameter_properties(info) -> bool:
    """``constructor(private db: Db)``: parameters that declare fields."""
    constructor = next((m for m in info.members if m.kind == "constructor"), None)
    params = constructor.node.child_by_field_name("parameters") if constructor is not None else None
    return params is not None and any(
        child.type in ("accessibility_modifier", "readonly")
        for param in params.named_children
        for child in param.children
    )


def _is_schema_inferred(parsed: ParsedSource, node) -> bool:
    """``z.infer<typeof S>``, ``z.input<...>``, ``v.InferOutput<...>`` and the like."""
    if node.type != "generic_type":
        return False
    name = node.child_by_field_name("name")
    if name is None or name.type != "nested_type_identifier" or not name.named_children:
        return False
    return parsed.text(name.named_children[-1]) in _SCHEMA_INFER_NAMES


__all__ = [
    "_census_type_strategies",
    "_collect_enum_defs",
    "_find_enum_bypass",
]
