"""Typed queries over a parsed TypeScript or JavaScript file.

Get a tree with ``parsed_file`` (parsed once per scan) or ``parse_text``, then
ask for functions, classes, imports, exports, JSX elements or calls. Each
query returns small frozen dataclasses in source order; every record keeps
its tree-sitter ``node`` for callers that need more than the summary.

Lines are 1-based tree-sitter rows, which count ``\\n`` only (see
``nodes.byte_offset`` for tsc's line rules). Without tree-sitter there is no
``ParsedSource``, so callers keep their own regex fallback for that case.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass, field

from desloppify.languages.typescript.syntax.nodes import binding_names
from desloppify.languages.typescript.syntax.tree import ParsedSource

_FUNCTION_KINDS = {
    "function_declaration": "declaration",
    "generator_function_declaration": "declaration",
    "function_expression": "expression",
    "function": "expression",
    "generator_function": "expression",
    "arrow_function": "arrow",
    "method_definition": "method",
}
# Overload signatures and abstract methods: a function without a body.
# ``method_signature`` also appears in interfaces and object types, where it
# isn't a function, so it only counts inside a class body.
_SIGNATURE_TYPES = frozenset({"function_signature", "method_signature", "abstract_method_signature"})
_GENERATOR_TYPES = frozenset({"generator_function_declaration", "generator_function"})
_CLASS_TYPES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
_MODULES = frozenset({"module", "internal_module"})
# Statements that can wrap a module or namespace declaration.
_MODULE_HOLDERS = frozenset({"ambient_declaration", "expression_statement", "export_statement"})
_MODIFIERS = frozenset({"accessibility_modifier", "override_modifier", "decorator"})
_JSX_ELEMENTS = frozenset({"jsx_element", "jsx_self_closing_element"})


@dataclass(frozen=True)
class Span:
    """A node's byte range and 1-based first and last lines."""

    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


def span(node) -> Span:
    return Span(node.start_byte, node.end_byte, node.start_point[0] + 1, node.end_point[0] + 1)


def descendants(root, types: Collection[str] | None = None) -> Iterator:
    """Named descendants of ``root`` (itself included) in source order, optionally filtered by type."""
    stack = [root]
    while stack:
        node = stack.pop()
        if types is None or node.type in types:
            yield node
        stack.extend(reversed(node.named_children))


def statements(block) -> list:
    """The statements of a block, comments left out."""
    return [child for child in block.named_children if child.type != "comment"]


def string_value(parsed: ParsedSource, node) -> str:
    """The contents of a string literal node, quotes removed (escapes kept as written)."""
    return parsed.text(node)[1:-1]


def directive(parsed: ParsedSource, node) -> str | None:
    """The value of a directive-shaped statement (``'use client';``), else None."""
    if node.type != "expression_statement":
        return None
    named = node.named_children
    if len(named) == 1 and named[0].type == "string":
        return string_value(parsed, named[0])
    return None


def module_statements(parsed: ParsedSource) -> Iterator:
    """Top-level statements, plus those inside ``declare module``, ``declare global`` and namespaces."""
    stack = list(reversed(parsed.root.named_children))
    while stack:
        node = stack.pop()
        yield node
        body = _module_body(node)
        if body is not None:
            stack.extend(reversed(body.named_children))


def _module_body(node):
    if node.type in _MODULE_HOLDERS:
        inner = [c for c in node.named_children if c.type in _MODULES or c.type == "statement_block"]
        if len(inner) != 1:
            return None
        node = inner[0]
        if node.type == "statement_block":
            return node
    return node.child_by_field_name("body") if node.type in _MODULES else None


# ── Functions ───────────────────────────────────────────────


@dataclass(frozen=True)
class Param:
    """One formal parameter. ``name`` is the pattern's text when it destructures."""

    name: str
    type: str | None
    optional: bool
    rest: bool
    default: str | None
    node: object = field(compare=False, repr=False)


@dataclass(frozen=True)
class FunctionInfo:
    """A function, method or overload signature.

    ``kind`` is ``declaration``, ``expression``, ``arrow``, ``method`` or
    ``signature`` (overloads and abstract methods, which have no ``body``).
    Anonymous functions take the name they are bound to (variable, property,
    class field or assignment target); callbacks have none. ``owner`` is the
    enclosing class's name for class members.
    """

    name: str | None
    kind: str
    params: tuple[Param, ...]
    span: Span
    body: Span | None
    expression_body: bool
    is_async: bool
    is_generator: bool
    exported: bool
    default_export: bool
    owner: str | None
    accessor: str | None
    is_static: bool
    node: object = field(compare=False, repr=False)

    @property
    def line(self) -> int:
        return self.span.start_line

    @property
    def body_node(self):
        return self.node.child_by_field_name("body")  # type: ignore[attr-defined]


def functions(parsed: ParsedSource, *, signatures: bool = False) -> list[FunctionInfo]:
    """Every function in the file, nested ones included, in source order.

    Overload signatures and abstract methods are left out unless ``signatures``.
    """
    exports = _local_exports(parsed)
    found = []
    for node in descendants(parsed.root, _FUNCTION_KINDS.keys() | _SIGNATURE_TYPES):
        info = _function_info(parsed, node, exports)
        if info is not None and (signatures or info.kind != "signature"):
            found.append(info)
    return found


def function_info(parsed: ParsedSource, node) -> FunctionInfo | None:
    """The ``FunctionInfo`` for a function node (a callback argument, say), else None."""
    return _function_info(parsed, node, None)


def _function_info(parsed: ParsedSource, node, exports: dict[str, bool] | None) -> FunctionInfo | None:
    kind = _FUNCTION_KINDS.get(node.type)
    if kind is None:
        if node.type not in _SIGNATURE_TYPES:
            return None
        if node.type == "method_signature" and (node.parent is None or node.parent.type != "class_body"):
            return None
        kind = "signature"
    tokens = _leading_tokens(node)
    name_node = node.child_by_field_name("name")
    name = parsed.text(name_node) if name_node is not None else _bound_name(parsed, node)
    body = node.child_by_field_name("body")
    exported, default = _export_status(parsed, node, name, exports)
    return FunctionInfo(
        name=name,
        kind=kind,
        params=_params(parsed, node),
        span=span(node),
        body=span(body) if body is not None else None,
        expression_body=body is not None and body.type != "statement_block",
        is_async="async" in tokens,
        is_generator=node.type in _GENERATOR_TYPES or "*" in tokens,
        exported=exported,
        default_export=default,
        owner=_owner_name(parsed, node),
        accessor=next((t for t in ("get", "set") if t in tokens), None),
        is_static="static" in tokens,
        node=node,
    )


@dataclass(frozen=True)
class Definition:
    """A function a reader knows by name: a declaration or named function
    expression, a function bound to a variable or assigned (``exports.f = ...``),
    or a class member (``Owner.name``).

    ``start`` is the byte its statement starts at (``export``, ``const``),
    decorators left out; ``line`` is that byte's line.
    """

    name: str
    start: int
    line: int
    function: FunctionInfo


def definitions(parsed: ParsedSource) -> list[Definition]:
    """Named function definitions, nested ones included, in source order.

    Anonymous callbacks, object-literal members, anonymous default exports
    and overload signatures are left out.
    """
    found = []
    for info in functions(parsed):
        node = info.node
        parent = node.parent  # type: ignore[attr-defined]
        if info.name is None or parent is None:
            continue
        if info.owner is not None:
            name = f"{info.owner}.{info.name}"
            holder = parent if parent.type == "public_field_definition" else node
        elif parent.type == "variable_declarator" and _is_field(parent, "value", node):
            name, holder = _bound_name(parsed, node) or info.name, parent.parent
        elif parent.type == "assignment_expression" and _is_field(parent, "right", node):
            name, holder = _bound_name(parsed, node) or info.name, parent
        elif info.kind in ("declaration", "expression") and node.child_by_field_name("name") is not None:  # type: ignore[attr-defined]
            name, holder = info.name, node
        else:
            continue
        if holder.parent is not None and holder.parent.type == "export_statement":
            holder = holder.parent
        first = next((c for c in holder.children if c.type not in ("decorator", "comment")), holder)
        found.append(Definition(name, first.start_byte, first.start_point[0] + 1, info))
    return found


def _leading_tokens(node) -> set[str]:
    """Anonymous keyword tokens before the name or parameters (``async``, ``static``, ``get``, ``*``)."""
    tokens = set()
    for child in node.children:
        if child.type in _MODIFIERS:
            continue
        if child.is_named:
            break
        tokens.add(child.type)
    return tokens


def _params(parsed: ParsedSource, node) -> tuple[Param, ...]:
    single = node.child_by_field_name("parameter")  # ``x => x``
    if single is not None:
        return (Param(parsed.text(single), None, False, False, None, single),)
    params = node.child_by_field_name("parameters")
    if params is None:
        return ()
    result = []
    for param in params.named_children:
        if param.type not in ("required_parameter", "optional_parameter"):
            continue
        pattern = param.child_by_field_name("pattern")
        rest = pattern is not None and pattern.type == "rest_pattern"
        if rest and pattern is not None and pattern.named_children:
            name = parsed.text(pattern.named_children[0])
        else:
            name = parsed.text(pattern) if pattern is not None else parsed.text(param)
        annotation = param.child_by_field_name("type")
        default = param.child_by_field_name("value")
        result.append(
            Param(
                name=name,
                type=_annotation_text(parsed, annotation),
                optional=param.type == "optional_parameter",
                rest=rest,
                default=parsed.text(default) if default is not None else None,
                node=param,
            )
        )
    return tuple(result)


def _annotation_text(parsed: ParsedSource, annotation) -> str | None:
    if annotation is None:
        return None
    named = annotation.named_children
    return parsed.text(named[0]) if named else None


def _bound_name(parsed: ParsedSource, node) -> str | None:
    """The name an anonymous function or class expression is bound to."""
    parent = node.parent
    if parent is None:
        return None
    if parent.type == "variable_declarator" and _is_field(parent, "value", node):
        target = parent.child_by_field_name("name")
    elif parent.type == "pair" and _is_field(parent, "value", node):
        target = parent.child_by_field_name("key")
    elif parent.type == "public_field_definition" and _is_field(parent, "value", node):
        target = parent.child_by_field_name("name")
    elif parent.type == "assignment_expression" and _is_field(parent, "right", node):
        target = parent.child_by_field_name("left")
    else:
        return None
    if target is None:
        return None
    return string_value(parsed, target) if target.type == "string" else parsed.text(target)


def _is_field(parent, name: str, node) -> bool:
    child = parent.child_by_field_name(name)
    return child is not None and child.start_byte == node.start_byte and child.end_byte == node.end_byte


def _owner_name(parsed: ParsedSource, node) -> str | None:
    """The enclosing class's name for a method, signature or class-field function."""
    parent = node.parent
    if parent is not None and parent.type == "public_field_definition":
        parent = parent.parent
    if parent is None or parent.type != "class_body" or parent.parent is None:
        return None
    cls = parent.parent
    name = cls.child_by_field_name("name")
    return parsed.text(name) if name is not None else _bound_name(parsed, cls)


def _export_status(
    parsed: ParsedSource, node, name: str | None, exports: dict[str, bool] | None
) -> tuple[bool, bool]:
    """(exported, default) for a declaration or a value bound at the top level.

    ``exports`` is ``_local_exports``, computed here when the caller has none.
    """
    holder = node
    parent = node.parent
    if parent is not None and parent.type == "variable_declarator" and _is_field(parent, "value", node):
        holder = parent.parent  # lexical_declaration / variable_declaration
    container = holder.parent if holder is not None else None
    if container is not None and container.type == "ambient_declaration":
        container = container.parent
    if container is not None and container.type == "export_statement":
        return True, _has_token(container, "default")
    if container is not None and container.type == "program" and name is not None:
        if exports is None:
            exports = _local_exports(parsed)
        if name in exports:
            return True, exports[name]
    return False, False


def _has_token(node, token: str) -> bool:
    return any(not child.is_named and child.type == token for child in node.children)


def _local_exports(parsed: ParsedSource) -> dict[str, bool]:
    """Top-level names exported by ``export { a }`` or ``export default a``, mapped to is-default."""
    names: dict[str, bool] = {}
    for node in parsed.root.named_children:
        if node.type != "export_statement" or node.child_by_field_name("source") is not None:
            continue
        info = export_info(parsed, node)
        for binding in info.bindings if info.kind in ("named", "default") else ():
            if binding.name is not None:
                names[binding.name] = names.get(binding.name, False) or binding.exported == "default"
    return names


# ── Classes ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ClassMember:
    """One class body member.

    ``kind`` is ``constructor``, ``method``, ``getter``, ``setter``,
    ``signature`` (an overload or abstract method), ``field``,
    ``index_signature`` or ``static_block`` (which has no name).
    """

    name: str | None
    kind: str
    is_static: bool
    is_abstract: bool
    is_readonly: bool
    is_optional: bool
    accessibility: str | None
    span: Span
    node: object = field(compare=False, repr=False)


@dataclass(frozen=True)
class ClassInfo:
    """A class declaration or expression; ``extends`` keeps type arguments (``Base<T>``)."""

    name: str | None
    kind: str
    is_abstract: bool
    extends: str | None
    implements: tuple[str, ...]
    members: tuple[ClassMember, ...]
    exported: bool
    default_export: bool
    span: Span
    node: object = field(compare=False, repr=False)

    @property
    def line(self) -> int:
        return self.span.start_line

    @property
    def methods(self) -> tuple[ClassMember, ...]:
        return tuple(m for m in self.members if m.kind in ("constructor", "method", "getter", "setter"))


def classes(parsed: ParsedSource) -> list[ClassInfo]:
    """Every class in the file, nested and anonymous ones included, in source order."""
    exports = _local_exports(parsed)
    return [_class_info(parsed, node, exports) for node in descendants(parsed.root, _CLASS_TYPES)]


def _class_info(parsed: ParsedSource, node, exports: dict[str, bool]) -> ClassInfo:
    name_node = node.child_by_field_name("name")
    name = parsed.text(name_node) if name_node is not None else _bound_name(parsed, node)
    extends: str | None = None
    implements: list[str] = []
    heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
    for clause in heritage.named_children if heritage is not None else ():
        if clause.type == "extends_clause":
            value = clause.child_by_field_name("value")
            if value is not None:
                extends = parsed.source[value.start_byte : clause.end_byte].decode("utf-8", "replace")
        elif clause.type == "implements_clause":
            implements.extend(parsed.text(t) for t in clause.named_children)
    body = node.child_by_field_name("body")
    members = tuple(
        member
        for child in (body.named_children if body is not None else ())
        if (member := _class_member(parsed, child)) is not None
    )
    exported, default = _export_status(parsed, node, name, exports)
    return ClassInfo(
        name=name,
        kind="expression" if node.type == "class" else "declaration",
        is_abstract=node.type == "abstract_class_declaration",
        extends=extends,
        implements=tuple(implements),
        members=members,
        exported=exported,
        default_export=default,
        span=span(node),
        node=node,
    )


_MEMBER_KINDS = {
    "method_definition": "method",
    "method_signature": "signature",
    "abstract_method_signature": "signature",
    "public_field_definition": "field",
    "index_signature": "index_signature",
    "class_static_block": "static_block",
}


def _class_member(parsed: ParsedSource, node) -> ClassMember | None:
    kind = _MEMBER_KINDS.get(node.type)
    if kind is None:
        return None
    name_node = node.child_by_field_name("name") if kind != "static_block" else None
    name = parsed.text(name_node) if name_node is not None else None
    tokens = set()
    accessibility = None
    for child in node.children:
        if child.type == "accessibility_modifier":
            accessibility = parsed.text(child)
        elif not child.is_named:
            tokens.add(child.type)
    if kind == "method":
        if name == "constructor":
            kind = "constructor"
        elif "get" in _leading_tokens(node):
            kind = "getter"
        elif "set" in _leading_tokens(node):
            kind = "setter"
    return ClassMember(
        name=name,
        kind=kind,
        is_static="static" in tokens,
        is_abstract="abstract" in tokens,
        is_readonly="readonly" in tokens,
        is_optional="?" in tokens,
        accessibility=accessibility,
        span=span(node),
        node=node,
    )


# ── Imports and exports ─────────────────────────────────────


@dataclass(frozen=True)
class ImportBinding:
    """A name an import binds: ``imported`` is ``default`` or ``*`` for those forms."""

    imported: str
    local: str
    type_only: bool


@dataclass(frozen=True)
class ImportInfo:
    """An import statement. ``kind`` is ``static``, ``side_effect`` (``import './x'``)
    or ``require`` (``import x = require('x')``). ``type_only`` is ``import type``."""

    source: str
    kind: str
    type_only: bool
    bindings: tuple[ImportBinding, ...]
    has_error: bool
    span: Span
    node: object = field(compare=False, repr=False)

    @property
    def line(self) -> int:
        return self.span.start_line


def imports(parsed: ParsedSource) -> list[ImportInfo]:
    """Import statements at module level (``declare module`` bodies included)."""
    found = []
    for node in module_statements(parsed):
        if node.type == "import_statement" and (info := import_info(parsed, node)) is not None:
            found.append(info)
    return found


def import_info(parsed: ParsedSource, node) -> ImportInfo | None:
    """The ``ImportInfo`` for an ``import_statement``; None for ``import x = A.B`` aliases."""
    source = node.child_by_field_name("source")
    require = next((c for c in node.named_children if c.type == "import_require_clause"), None)
    if require is not None:
        source = require.child_by_field_name("source")
    if source is None:
        return None
    clause = next((c for c in node.named_children if c.type == "import_clause"), None)
    bindings: list[ImportBinding] = []
    if require is not None:
        local = next((c for c in require.named_children if c.type == "identifier"), None)
        if local is not None:
            bindings.append(ImportBinding("=", parsed.text(local), False))
    for part in clause.named_children if clause is not None else ():
        if part.type == "identifier":
            bindings.append(ImportBinding("default", parsed.text(part), False))
        elif part.type == "namespace_import":
            bindings.extend(ImportBinding("*", parsed.text(i), False) for i in part.named_children)
        elif part.type == "named_imports":
            for spec in part.named_children:
                name = spec.child_by_field_name("name") if spec.type == "import_specifier" else None
                if name is None:
                    continue
                local = spec.child_by_field_name("alias") or name
                bindings.append(
                    ImportBinding(_name_text(parsed, name), parsed.text(local), _has_token(spec, "type"))
                )
    kind = "require" if require is not None else "static" if clause is not None else "side_effect"
    return ImportInfo(
        source=string_value(parsed, source),
        kind=kind,
        type_only=_has_token(node, "type"),
        bindings=tuple(bindings),
        has_error=node.has_error,
        span=span(node),
        node=node,
    )


def _name_text(parsed: ParsedSource, node) -> str:
    """An import/export name, which may be a string (``export { a as 'b-c' }``)."""
    return string_value(parsed, node) if node.type == "string" else parsed.text(node)


@dataclass(frozen=True)
class ExportBinding:
    """``name`` is the local (or source-module) name, None for an expression;
    ``exported`` is the name importers see."""

    name: str | None
    exported: str
    type_only: bool


@dataclass(frozen=True)
class ExportInfo:
    """An export statement.

    ``kind`` is one of:
    - ``reexport``: ``export ... from 'x'`` (``star`` for ``export *`` and ``export * as ns``);
    - ``named``: ``export { a, b as c }``;
    - ``declaration``: ``export const/function/class/interface/type/enum ...``,
      also ``export default function f() {}``;
    - ``default``: ``export default <expression>``; bound to a name only for an identifier;
    - ``assignment``: ``export = x``;
    - ``namespace``: ``export as namespace Lib``.

    ``has_error`` ignores the ERROR the grammar wraps around ``type`` in
    ``export type * from``, which it doesn't know yet.
    """

    kind: str
    source: str | None
    type_only: bool
    star: bool
    is_default: bool
    bindings: tuple[ExportBinding, ...]
    has_error: bool
    span: Span
    node: object = field(compare=False, repr=False)

    @property
    def line(self) -> int:
        return self.span.start_line


def exports(parsed: ParsedSource) -> list[ExportInfo]:
    """Export statements at module level (``declare module`` bodies included)."""
    return [export_info(parsed, n) for n in module_statements(parsed) if n.type == "export_statement"]


def export_info(parsed: ParsedSource, node) -> ExportInfo:
    """The ``ExportInfo`` for an ``export_statement``."""
    source = node.child_by_field_name("source")
    declaration = node.child_by_field_name("declaration")
    value = node.child_by_field_name("value")
    clause = next((c for c in node.named_children if c.type == "export_clause"), None)
    namespace = next((c for c in node.named_children if c.type == "namespace_export"), None)
    is_default = _has_token(node, "default")
    type_only = _has_token(node, "type") or _is_type_star(node)
    bindings: list[ExportBinding] = []
    star = False
    if clause is not None:
        for spec in clause.named_children:
            name = spec.child_by_field_name("name") if spec.type == "export_specifier" else None
            if name is None:
                continue
            alias = spec.child_by_field_name("alias")
            bindings.append(
                ExportBinding(
                    _name_text(parsed, name),
                    _name_text(parsed, alias or name),
                    type_only or _has_token(spec, "type"),
                )
            )
    if source is not None:
        kind = "reexport"
        star = clause is None
        if namespace is not None:
            alias = namespace.named_children[-1] if namespace.named_children else None
            if alias is not None:
                bindings.append(ExportBinding("*", _name_text(parsed, alias), type_only))
    elif clause is not None:
        kind = "named"
    elif declaration is not None:
        kind = "declaration"
        for declared in _declared_names(parsed, declaration):
            bindings.append(ExportBinding(declared, "default" if is_default else declared, False))
    elif value is not None:
        kind = "default"
        if value.type == "identifier":
            bindings.append(ExportBinding(parsed.text(value), "default", False))
    elif _has_token(node, "namespace"):
        kind = "namespace"
    else:
        kind = "assignment"
    return ExportInfo(
        kind=kind,
        source=string_value(parsed, source) if source is not None else None,
        type_only=type_only,
        star=star,
        is_default=is_default,
        bindings=tuple(bindings),
        has_error=_has_error(node) if source is not None else node.has_error,
        span=span(node),
        node=node,
    )


def _is_type_star(node) -> bool:
    return any(child.type == "ERROR" and _is_type_error(child) for child in node.children)


def _is_type_error(node) -> bool:
    return [c.type for c in node.children] == ["type"]


def _has_error(node) -> bool:
    if not node.has_error:
        return False
    for child in node.children:
        if child.type == "ERROR":
            if not _is_type_error(child):
                return True
        elif child.is_missing or child.has_error:
            return True
    return False


def _declared_names(parsed: ParsedSource, declaration) -> list[str]:
    if declaration.type in ("lexical_declaration", "variable_declaration"):
        names: list[str] = []
        for declarator in declaration.named_children:
            if declarator.type == "variable_declarator":
                bound = binding_names(declarator.child_by_field_name("name"))
                names.extend(parsed.text(n) for n in sorted(bound, key=lambda n: n.start_byte))
        return names
    name = declaration.child_by_field_name("name")
    if name is None and declaration.type == "ambient_declaration":
        inner = declaration.named_children
        return _declared_names(parsed, inner[0]) if inner else []
    return [parsed.text(name)] if name is not None else []


# ── JSX ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class JsxAttribute:
    """``name`` is None for a spread (``{...props}``); ``value`` is the value's
    source text (quotes or braces included), None for a bare boolean attribute.
    For a spread it is the spread expression."""

    name: str | None
    value: str | None
    spread: bool
    node: object = field(compare=False, repr=False)


@dataclass(frozen=True)
class JsxElement:
    """A JSX element or fragment (``name`` is empty for ``<>...</>``)."""

    name: str
    self_closing: bool
    attributes: tuple[JsxAttribute, ...]
    span: Span
    node: object = field(compare=False, repr=False)

    @property
    def line(self) -> int:
        return self.span.start_line

    @property
    def is_fragment(self) -> bool:
        return not self.name

    def attribute(self, name: str) -> JsxAttribute | None:
        return next((a for a in self.attributes if a.name == name), None)


def jsx_elements(parsed: ParsedSource) -> list[JsxElement]:
    """Every JSX element and fragment, nested ones included, in source order."""
    found = []
    for node in descendants(parsed.root, _JSX_ELEMENTS):
        tag = node if node.type == "jsx_self_closing_element" else node.child_by_field_name("open_tag")
        if tag is None:
            continue
        name = tag.child_by_field_name("name")
        found.append(
            JsxElement(
                name=parsed.text(name) if name is not None else "",
                self_closing=node.type == "jsx_self_closing_element",
                attributes=tuple(_jsx_attribute(parsed, a) for a in tag.children_by_field_name("attribute")),
                span=span(node),
                node=node,
            )
        )
    return found


def _jsx_attribute(parsed: ParsedSource, node) -> JsxAttribute:
    if node.type != "jsx_attribute":  # ``{...props}``
        spread = next((c for c in node.named_children if c.type == "spread_element"), None)
        target = spread.named_children[0] if spread is not None and spread.named_children else node
        return JsxAttribute(None, parsed.text(target), True, node)
    named = node.named_children
    value = named[1] if len(named) > 1 else None
    return JsxAttribute(parsed.text(named[0]), parsed.text(value) if value is not None else None, False, node)


# ── Calls ───────────────────────────────────────────────────


@dataclass(frozen=True)
class CallInfo:
    """A call: ``callee`` is the function expression's source text
    (``useEffect``, ``React.useEffect``); ``arguments`` skips comments."""

    callee: str
    arguments: tuple
    span: Span
    node: object = field(compare=False, repr=False)

    @property
    def line(self) -> int:
        return self.span.start_line


def calls(parsed: ParsedSource, callees: Collection[str] | None = None) -> list[CallInfo]:
    """Every call expression in source order, optionally only those whose callee text is in ``callees``."""
    found = []
    for node in descendants(parsed.root, ("call_expression",)):
        function = node.child_by_field_name("function")
        if function is None:
            continue
        callee = parsed.text(function)
        if callees is not None and callee not in callees:
            continue
        args = node.child_by_field_name("arguments")
        values = () if args is None else tuple(a for a in args.named_children if a.type != "comment")
        found.append(CallInfo(callee, values, span(node), node))
    return found


# ── Type declarations ───────────────────────────────────────


@dataclass(frozen=True)
class TypeDeclaration:
    """An ``interface`` (``kind`` ``interface``) or ``type`` alias (``alias``).

    ``value`` is the interface body or the alias's type node; ``extends``
    holds an interface's base type nodes. ``span`` starts at ``export``
    when the declaration is exported.
    """

    name: str
    kind: str
    type_parameters: tuple[str, ...]
    extends: tuple
    value: object
    exported: bool
    span: Span
    node: object = field(compare=False, repr=False)

    @property
    def line(self) -> int:
        return self.span.start_line


def type_declarations(parsed: ParsedSource) -> list[TypeDeclaration]:
    """Every interface and type alias, nested ones included, in source order."""
    found = []
    for node in descendants(parsed.root, ("interface_declaration", "type_alias_declaration")):
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("body" if node.type == "interface_declaration" else "value")
        if name is None or value is None:
            continue
        params = node.child_by_field_name("type_parameters")
        heritage = next((c for c in node.named_children if c.type == "extends_type_clause"), None)
        holder = node.parent if node.parent is not None and node.parent.type == "export_statement" else node
        found.append(
            TypeDeclaration(
                name=parsed.text(name),
                kind="interface" if node.type == "interface_declaration" else "alias",
                type_parameters=tuple(
                    parsed.text(p.child_by_field_name("name"))
                    for p in (params.named_children if params is not None else ())
                    if p.child_by_field_name("name") is not None
                ),
                extends=tuple(heritage.named_children) if heritage is not None else (),
                value=value,
                exported=holder is not node,
                span=span(holder),
                node=node,
            )
        )
    return found


__all__ = [
    "CallInfo",
    "ClassInfo",
    "ClassMember",
    "Definition",
    "ExportBinding",
    "ExportInfo",
    "FunctionInfo",
    "ImportBinding",
    "ImportInfo",
    "JsxAttribute",
    "JsxElement",
    "Param",
    "Span",
    "TypeDeclaration",
    "calls",
    "classes",
    "definitions",
    "descendants",
    "directive",
    "export_info",
    "exports",
    "function_info",
    "functions",
    "import_info",
    "imports",
    "jsx_elements",
    "module_statements",
    "span",
    "statements",
    "string_value",
    "type_declarations",
]
