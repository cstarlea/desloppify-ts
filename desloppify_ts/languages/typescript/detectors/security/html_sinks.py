"""Which HTML sinks (``.innerHTML =``, ``dangerouslySetInnerHTML``) only ever
receive escaped or sanitized markup, from the syntax tree."""

from __future__ import annotations

import re

from desloppify_ts.languages.typescript.syntax.nodes import same
from desloppify_ts.languages.typescript.syntax.queries import descendants, jsx_elements
from desloppify_ts.languages.typescript.syntax.tree import (
    ParsedSource,
    parse_text,
    parsed_file,
)

# ``escapeHtml(x)``, ``he.escape(x)``, ``DOMPurify.sanitize(x)``,
# ``getPurifier().sanitize(x)``, ``sanitizeHtml(x)``.
_SAFE_CALLEE_RE = re.compile(r"(?:^|\.)(?i:escape|sanitize)\w*$")
_LITERALS = frozenset({"string", "number", "null", "undefined", "true", "false"})
_WRAPPERS = frozenset(
    {
        "parenthesized_expression",
        "as_expression",
        "satisfies_expression",
        "non_null_expression",
        "await_expression",
    }
)
_FUNCTIONS = frozenset({"arrow_function", "function_expression", "function"})
_SCOPES = _FUNCTIONS | {
    "function_declaration",
    "method_definition",
    "generator_function",
    "generator_function_declaration",
}
# Calls whose result is the result of their callback: ``items.map(i => ...)``,
# ``useMemo(() => ..., deps)``.
_CALLBACK_RESULT = frozenset({"map", "flatMap", "useMemo"})
_MAX_DEPTH = 12


class _SafeHtml:
    """Whether an expression only produces escaped or sanitized markup."""

    def __init__(self, parsed: ParsedSource) -> None:
        self.parsed = parsed
        self._consts: dict[str, list] | None = None

    def __call__(self, node, depth: int = 0) -> bool:
        if node is None or depth > _MAX_DEPTH:
            return False
        kind = node.type
        if kind in _LITERALS:
            return True
        if kind in _WRAPPERS:
            inner = node.named_children
            return len(inner) == 1 and self(inner[0], depth + 1)
        if kind == "template_string":
            return all(
                self(sub.named_children[0], depth + 1)
                for sub in node.named_children
                if sub.type == "template_substitution" and sub.named_children
            )
        if kind == "ternary_expression":
            return self(node.child_by_field_name("consequence"), depth + 1) and self(
                node.child_by_field_name("alternative"), depth + 1
            )
        if kind == "binary_expression":
            operator = node.child_by_field_name("operator")
            return (
                operator is not None
                and operator.type in ("+", "??", "||")
                and self(node.child_by_field_name("left"), depth + 1)
                and self(node.child_by_field_name("right"), depth + 1)
            )
        if kind == "array":
            return all(self(child, depth + 1) for child in node.named_children)
        if kind == "call_expression":
            return self._safe_call(node, depth)
        if kind == "identifier":
            return self._safe_const(self.parsed.text(node), depth)
        return False

    def _safe_call(self, node, depth: int) -> bool:
        function = node.child_by_field_name("function")
        if function is None:
            return False
        callee = self.parsed.text(function)
        if _SAFE_CALLEE_RE.search(callee):
            return True
        args = node.child_by_field_name("arguments")
        values = (
            []
            if args is None
            else [a for a in args.named_children if a.type != "comment"]
        )
        name = callee.rsplit(".", 1)[-1]
        if name in _CALLBACK_RESULT and values:
            return self._safe_return(values[0], depth)
        if function.type == "member_expression" and name == "join":
            return all(a.type in _LITERALS for a in values) and self(
                function.child_by_field_name("object"), depth + 1
            )
        return False

    def _safe_return(self, function, depth: int) -> bool:
        """Whether every value ``function`` returns is safe."""
        if function.type not in _FUNCTIONS:
            return False
        body = function.child_by_field_name("body")
        if body is None:
            return False
        if body.type != "statement_block":
            return self(body, depth + 1)
        returns = [
            r
            for r in descendants(body, ("return_statement",))
            if same(_owner_function(r), function)
        ]
        return bool(returns) and all(
            r.named_children and self(r.named_children[0], depth + 1) for r in returns
        )

    def _safe_const(self, name: str, depth: int) -> bool:
        """A ``const`` declared once in the file, its value safe."""
        if self._consts is None:
            self._consts = {}
            for declarator in descendants(self.parsed.root, ("variable_declarator",)):
                target = declarator.child_by_field_name("name")
                if target is not None and target.type == "identifier":
                    self._consts.setdefault(self.parsed.text(target), []).append(
                        declarator
                    )
        declarators = self._consts.get(name, [])
        if len(declarators) != 1:
            return False
        declarator = declarators[0]
        declaration = declarator.parent
        if declaration is None or declaration.type != "lexical_declaration":
            return False
        if not any(child.type == "const" for child in declaration.children):
            return False
        return self(declarator.child_by_field_name("value"), depth + 1)


def _owner_function(node):
    parent = node.parent
    while parent is not None and parent.type not in _SCOPES:
        parent = parent.parent
    return parent


def _json_stringified(node) -> bool:
    """``JSON.stringify(x)``, possibly chained (``.replace(/</g, ...)``)."""
    while node is not None and node.type == "call_expression":
        function = node.child_by_field_name("function")
        if function is None or function.type != "member_expression":
            return False
        obj = function.child_by_field_name("object")
        if (
            obj is not None
            and obj.type == "identifier"
            and function.text == b"JSON.stringify"
        ):
            return True
        node = obj
    return False


def _html_value(attribute_node):
    """The ``__html`` value of ``dangerouslySetInnerHTML={{ __html: v }}``."""
    named = attribute_node.named_children
    if len(named) < 2 or named[1].type != "jsx_expression":
        return None
    objects = [c for c in named[1].named_children if c.type == "object"]
    if len(objects) != 1:
        return None
    pairs = [c for c in objects[0].named_children if c.type == "pair"]
    for pair in pairs:
        key = pair.child_by_field_name("key")
        if key is not None and key.text.strip(b"'\"") == b"__html":
            return pair.child_by_field_name("value")
    return None


def _is_json_ld(element) -> bool:
    if element.name != "script":
        return False
    script_type = element.attribute("type")
    return (
        script_type is not None
        and (script_type.value or "").strip("'\"{}` ") == "application/ld+json"
    )


def safe_html_sink_lines(parsed: ParsedSource) -> set[int]:
    """Lines whose HTML sinks all receive escaped or sanitized markup.

    An ``.innerHTML =`` whose value is built only from literals and
    ``escape*``/``sanitize*`` calls (template literals, ``.map(...).join('')``
    and conditionals included, a ``const`` followed to its value), and a
    ``dangerouslySetInnerHTML`` whose ``__html`` is such a value, or
    ``JSON.stringify`` output in a ``<script type="application/ld+json">``.
    """
    safe_html = _SafeHtml(parsed)
    safe: set[int] = set()
    unsafe: set[int] = set()
    for node in descendants(parsed.root, ("assignment_expression",)):
        left = node.child_by_field_name("left")
        if left is None or left.type != "member_expression":
            continue
        prop = left.child_by_field_name("property")
        if prop is None or prop.text != b"innerHTML":
            continue
        line = parsed.line(prop)
        (safe if safe_html(node.child_by_field_name("right")) else unsafe).add(line)
    for element in jsx_elements(parsed):
        attribute = element.attribute("dangerouslySetInnerHTML")
        if attribute is None:
            continue
        value = _html_value(attribute.node)
        is_safe = value is not None and (
            safe_html(value) or (_is_json_ld(element) and _json_stringified(value))
        )
        (safe if is_safe else unsafe).add(parsed.line(attribute.node))
    return safe - unsafe


def safe_html_lines(text: str, path: str) -> set[int]:
    """``safe_html_sink_lines`` for a file's code text; empty without
    tree-sitter or without an HTML sink."""
    if "nnerHTML" not in text:  # innerHTML, dangerouslySetInnerHTML
        return set()
    parsed = parsed_file(path)
    if parsed is None or parsed.source != text.encode("utf-8"):
        parsed = parse_text(text, path)
    return set() if parsed is None else safe_html_sink_lines(parsed)


__all__ = ["safe_html_lines", "safe_html_sink_lines"]
