"""Comparator-less ``.sort()`` calls whose default (string) order is fine."""

from __future__ import annotations

import re

from desloppify.languages.typescript.syntax.nodes import FUNCTIONS
from desloppify.languages.typescript.syntax.queries import descendants
from desloppify.languages.typescript.syntax.tree import ParsedSource

# A field or name that holds one string: ``id``, ``groupId``, ``userName``, ``slug``.
_STRING_FIELD_RE = re.compile(
    r"(?:^|[a-z0-9_])(?:id|name|key|slug|email|title|label|code|path|url|uuid)$",
    re.IGNORECASE,
)
# A name that holds a list of them: ``ownerIds``, ``names``.
_STRING_LIST_RE = re.compile(
    r"(?:^|[a-z0-9_])(?:id|name|key|slug|email|title|label|code|path|url|uuid)s$",
    re.IGNORECASE,
)
_STRING_ARRAY_TYPE_RE = re.compile(
    r"(?:readonly\s+)?string\[\]|(?:Readonly)?(?:Array|Set)<string>|ReadonlySet<string>"
)
_NULLISH_TYPES = frozenset({"undefined", "null"})
# Methods that return an array of the receiver's own elements.
_SAME_ELEMENTS = frozenset({"slice", "filter", "concat", "reverse", "toReversed"})
_KEY_LISTS = frozenset({"Object.keys", "Object.entries", "Object.getOwnPropertyNames"})
_WRAPPERS = frozenset(
    {
        "parenthesized_expression",
        "non_null_expression",
        "as_expression",
        "satisfies_expression",
    }
)
_MAX_DEPTH = 4

# Without tree-sitter: the line shapes the syntax-tree check accepts.
FALLBACK_STRING_SORT_RE = re.compile(
    r"\.sort\(\s*\)\s*\.join\(|\bexpect\s*\("
    r"|\bObject\.(?:keys|entries)\([^()]*\)\.sort\("
    r"|\.split\([^()]*\)\.sort\("
    r"|\.map\(\s*\(?\s*\w+\s*\)?\s*=>\s*\w+\.\w*"
    r"(?:id|name|key|slug|email|title|label|code|path|url|uuid)\s*\)\.sort\(",
    re.IGNORECASE,
)

Declarations = dict[str, list[tuple[object, object]]]


def string_sort(parsed: ParsedSource, call, declarations: Declarations) -> bool:
    """Whether the comparator-less ``x.sort()`` ``call`` sorts strings: it is
    joined into a string, sits inside an ``expect(...)`` assertion, or its
    receiver is plainly a ``string[]``."""
    parent = call.parent
    if (
        parent is not None
        and parent.type == "member_expression"
        and parent.child_by_field_name("object") == call
        and _text(parsed, parent.child_by_field_name("property")) == "join"
    ):
        return True
    if _in_expect(parsed, call):
        return True
    receiver = call.child_by_field_name("function").child_by_field_name("object")
    return _strings(parsed, receiver, declarations, 0)


def declarations(parsed: ParsedSource) -> Declarations:
    """Each plain name the file declares (variables and parameters), with its annotation and value."""
    found: Declarations = {}
    for node in descendants(
        parsed.root,
        ("variable_declarator", "required_parameter", "optional_parameter"),
    ):
        is_variable = node.type == "variable_declarator"
        name = node.child_by_field_name("name" if is_variable else "pattern")
        if name is None or name.type != "identifier":
            continue
        found.setdefault(parsed.text(name), []).append(
            (
                node.child_by_field_name("type"),
                node.child_by_field_name("value") if is_variable else None,
            )
        )
    return found


def _in_expect(parsed: ParsedSource, node) -> bool:
    node = node.parent
    while node is not None:
        if node.type in FUNCTIONS or node.type.endswith("statement"):
            return False
        if node.type == "call_expression" and _callee_root(parsed, node) == "expect":
            return True
        node = node.parent
    return False


def _callee_root(parsed: ParsedSource, call) -> str:
    """``expect`` for ``expect(a)``, ``expect(a).toEqual(b)`` and ``expect.soft(a)``."""
    node = call.child_by_field_name("function")
    while node is not None and node.type in ("call_expression", "member_expression"):
        field = "function" if node.type == "call_expression" else "object"
        node = node.child_by_field_name(field)
    return _text(parsed, node)


def _strings(parsed: ParsedSource, node, decls: Declarations, depth: int) -> bool:
    """Whether ``node`` is plainly an array (or set) of strings."""
    node = _unwrap(node)
    if node is None or depth > _MAX_DEPTH:
        return False
    if node.type == "array":
        return bool(node.named_children) and all(
            _string_element(parsed, element, decls, depth)
            for element in node.named_children
        )
    if node.type == "identifier":
        return _declared_strings(parsed, parsed.text(node), decls, depth)
    if node.type == "member_expression":
        return bool(
            _STRING_LIST_RE.search(_text(parsed, node.child_by_field_name("property")))
        )
    if node.type == "new_expression":
        return _new_string_set(parsed, node, decls, depth)
    if node.type == "call_expression":
        return _string_call(parsed, node, decls, depth)
    return False


def _string_element(parsed: ParsedSource, element, decls: Declarations, depth: int):
    if element.type == "spread_element":
        return bool(element.named_children) and _strings(
            parsed, element.named_children[0], decls, depth + 1
        )
    element = _unwrap(element)
    if element is None:
        return False
    if element.type in ("string", "template_string"):
        return True
    if element.type == "member_expression":
        return _string_field(parsed, element)
    return element.type == "identifier" and bool(
        _STRING_FIELD_RE.search(parsed.text(element))
    )


def _string_call(parsed: ParsedSource, call, decls: Declarations, depth: int) -> bool:
    function = call.child_by_field_name("function")
    if _text(parsed, function) in _KEY_LISTS:
        return True
    if function is None or function.type != "member_expression":
        return False
    method = _text(parsed, function.child_by_field_name("property"))
    if method == "split":
        return True
    if method in _SAME_ELEMENTS:
        return _strings(
            parsed, function.child_by_field_name("object"), decls, depth + 1
        )
    if method != "map":
        return False
    args = call.child_by_field_name("arguments")
    return args is not None and any(
        _maps_to_string_field(parsed, arg) for arg in args.named_children
    )


def _maps_to_string_field(parsed: ParsedSource, callback) -> bool:
    """``(x) => x.groupId``: a callback whose body is a field that holds one string."""
    if callback.type != "arrow_function":
        return False
    body = _unwrap(callback.child_by_field_name("body"))
    return (
        body is not None
        and body.type == "member_expression"
        and _string_field(parsed, body)
    )


def _string_field(parsed: ParsedSource, member) -> bool:
    return bool(
        _STRING_FIELD_RE.search(_text(parsed, member.child_by_field_name("property")))
    )


def _new_string_set(
    parsed: ParsedSource, node, decls: Declarations, depth: int
) -> bool:
    if _text(parsed, node.child_by_field_name("constructor")) != "Set":
        return False
    type_args = node.child_by_field_name("type_arguments")
    if type_args is not None:
        return parsed.text(type_args).replace(" ", "") == "<string>"
    args = node.child_by_field_name("arguments")
    return (
        args is not None
        and len(args.named_children) == 1
        and _strings(parsed, args.named_children[0], decls, depth + 1)
    )


def _declared_strings(
    parsed: ParsedSource, name: str, decls: Declarations, depth: int
) -> bool:
    """A name the file declares once as ``string[]`` (or with a string-array
    value), or, undeclared, one named like a list of ids."""
    found = decls.get(name)
    if not found:
        return bool(_STRING_LIST_RE.search(name))
    if len(found) > 1:
        return False
    annotation, value = found[0]
    if annotation is not None:
        return _string_array_type(parsed.text(annotation).lstrip(":").strip())
    if value is not None and _strings(parsed, value, decls, depth + 1):
        return True
    return bool(_STRING_LIST_RE.search(name))


def _string_array_type(text: str) -> bool:
    members = [part.strip() for part in text.split("|")]
    members = [part for part in members if part not in _NULLISH_TYPES]
    return bool(members) and all(
        _STRING_ARRAY_TYPE_RE.fullmatch(part) for part in members
    )


def _unwrap(node):
    while node is not None and node.type in _WRAPPERS:
        node = node.named_children[0] if node.named_children else None
    return node


def _text(parsed: ParsedSource, node) -> str:
    return parsed.text(node) if node is not None else ""


__all__ = ["FALLBACK_STRING_SORT_RE", "declarations", "string_sort"]
