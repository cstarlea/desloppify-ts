"""Wrapper, delegation, and facade pattern scanners."""

from __future__ import annotations

from desloppify.languages.typescript.detectors.facade import reexport_sources
from desloppify.languages.typescript.syntax.queries import (
    Definition,
    classes,
    definitions,
    statements,
)
from desloppify.languages.typescript.syntax.tree import ParsedSource


def _single_expression(info) -> object | None:
    """The one expression a function body evaluates: an arrow's expression body,
    or the value of a body's only statement (``return x`` or ``x;``)."""
    body = info.body_node
    if body is None:
        return None
    if info.expression_body:
        value = body
    else:
        stmts = statements(body)
        if len(stmts) != 1 or stmts[0].type not in ("return_statement", "expression_statement"):
            return None
        value = stmts[0].named_children[0] if stmts[0].named_children else None
    while value is not None and value.type in ("await_expression", "parenthesized_expression"):
        value = value.named_children[0] if value.named_children else None
    return value


def _forwarded_call_target(parsed: ParsedSource, definition: Definition) -> str | None:
    """The callee when a function's whole body calls it with exactly its own
    parameters, in order (``(a, ...rest) => f(a, ...rest)``)."""
    info = definition.function
    if info.accessor is not None or info.is_generator:
        return None
    call = _single_expression(info)
    if call is None or call.type != "call_expression":
        return None
    callee = call.child_by_field_name("function")
    args = call.child_by_field_name("arguments")
    if callee is None or args is None or not _is_plain_reference(callee):
        return None
    expected = [("...", p.name) if p.rest else ("", p.name) for p in info.params]
    actual = []
    for arg in args.named_children:
        if arg.type == "comment":
            continue
        if arg.type == "spread_element" and arg.named_children and arg.named_children[0].type == "identifier":
            actual.append(("...", parsed.text(arg.named_children[0])))
        elif arg.type == "identifier":
            actual.append(("", parsed.text(arg)))
        else:
            return None
    target = parsed.text(callee)
    if actual != expected or target == info.name:
        return None
    return target


def _is_plain_reference(node) -> bool:
    """``f``, ``this.inner.f`` or ``api.f``: a callee reached without calling anything."""
    while node.type == "member_expression":
        node = node.child_by_field_name("object")
        if node is None:
            return False
    return node.type in ("identifier", "this")


def _find_passthrough_wrappers(parsed: ParsedSource) -> list[tuple[str, str]]:
    """(wrapper, target) pairs for named functions and methods that only forward
    their arguments. Object-literal members are left out: those are adapters
    satisfying an interface (an observer's ``next``), not layers."""
    wrappers = []
    for definition in definitions(parsed):
        if definition.object_member:
            continue
        target = _forwarded_call_target(parsed, definition)
        if target is not None:
            wrappers.append((definition.name, target))
    return wrappers


def _delegate_member(parsed: ParsedSource, value) -> str | None:
    """``inner`` when *value* is ``this.inner.x``, ``this.inner.x(...)`` or deeper."""
    if value.type == "call_expression":
        value = value.child_by_field_name("function")
    if value is None or value.type != "member_expression":
        return None
    node = value
    while node.type == "member_expression":
        obj = node.child_by_field_name("object")
        if obj is None:
            return None
        if obj.type == "this":
            # ``this.x`` alone is the class's own state, not a delegate.
            return None if node is value else parsed.text(node.child_by_field_name("property"))
        node = obj
    return None


def _find_delegation_heavy_classes(parsed: ParsedSource) -> list[dict]:
    """Classes where most methods forward to one member object."""
    results: list[dict] = []
    for info in classes(parsed):
        methods = [
            m
            for m in info.members
            if m.kind in ("method", "getter", "setter") and m.name is not None
        ]
        if len(methods) <= 3:
            continue
        delegating: dict[str, list[str]] = {}
        for member in methods:
            body = member.node.child_by_field_name("body")  # type: ignore[attr-defined]
            stmts = statements(body) if body is not None else []
            if len(stmts) != 1 or stmts[0].type not in ("return_statement", "expression_statement"):
                continue
            value = stmts[0].named_children[0] if stmts[0].named_children else None
            while value is not None and value.type == "await_expression":
                value = value.named_children[0] if value.named_children else None
            attr = _delegate_member(parsed, value) if value is not None else None
            if attr:
                delegating.setdefault(attr, []).append(str(member.name))
        if not delegating:
            continue
        top_attr = max(delegating, key=lambda a: len(delegating[a]))
        delegate_count = len(delegating[top_attr])
        ratio = delegate_count / len(methods)
        if ratio > 0.5:
            results.append(
                {
                    "class_name": info.name or "(anonymous)",
                    "line": info.line,
                    "delegation_ratio": round(ratio, 2),
                    "method_count": len(methods),
                    "delegate_count": delegate_count,
                    "delegate_target": top_attr,
                    "sample_methods": delegating[top_attr][:5],
                }
            )
    return results


def _find_facade_modules(content: str, parsed: ParsedSource | None, *, loc: int) -> dict | None:
    """A pure re-export module, as the ``facade`` detector defines one."""
    found = reexport_sources(content, parsed) if "export" in content and "from" in content else None
    if not found:
        return None
    sources = list(dict.fromkeys(found))
    return {"imports_from": sources[:10], "source_count": len(sources), "loc": loc}


__all__ = [
    "_delegate_member",
    "_find_delegation_heavy_classes",
    "_find_facade_modules",
    "_find_passthrough_wrappers",
    "_forwarded_call_target",
]
