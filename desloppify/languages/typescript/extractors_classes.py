"""Class metrics for the god-class rules: methods, constructor dependencies, decorators."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from desloppify.base.discovery.source import find_ts_and_js_files, read_file_text
from desloppify.engine.detectors.base import ClassInfo
from desloppify.languages.typescript.syntax.queries import classes
from desloppify.languages.typescript.syntax.tree import ParsedSource, parsed_file

_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function"})


def extract_ts_classes(path: Path, files: Iterable[str] | None = None) -> list[ClassInfo]:
    """Named classes with their god-class metrics; empty without tree-sitter."""
    results: list[ClassInfo] = []
    for filepath in files if files is not None else find_ts_and_js_files(path):
        if filepath.endswith(".d.ts"):
            continue
        content = read_file_text(filepath)
        if not content or "class" not in content:
            continue
        parsed = parsed_file(filepath)
        if parsed is None:
            continue
        for info in classes(parsed):
            if info.name is None:
                continue
            results.append(
                ClassInfo(
                    name=info.name,
                    file=filepath,
                    line=info.line,
                    loc=info.span.end_line - info.span.start_line + 1,
                    metrics=_class_metrics(parsed, info),
                )
            )
    return results


def _class_metrics(parsed: ParsedSource, info) -> dict[str, int]:
    methods = 0
    dependencies = 0
    decorators = _class_decorators(info.node)
    for member in info.members:
        node = member.node
        if member.kind == "method":
            methods += 1
        elif member.kind == "field":
            value = node.child_by_field_name("value")
            if value is not None and value.type in _FUNCTION_VALUES:
                methods += 1
            elif value is not None and _is_inject_call(parsed, value):
                dependencies += 1
        if member.kind == "constructor":
            params = node.child_by_field_name("parameters")
            for param in params.named_children if params is not None else ():
                if param.type in ("required_parameter", "optional_parameter"):
                    dependencies += 1
                    decorators += _count(param, "decorator")
        elif member.kind in ("method", "getter", "setter"):
            params = node.child_by_field_name("parameters")
            for param in params.named_children if params is not None else ():
                decorators += _count(param, "decorator")
    body = info.node.child_by_field_name("body")
    # Method decorators sit in the class body before their method; field decorators
    # (ORM columns, validators, Angular inputs) declare a shape, so they don't count.
    decorators += _count(body, "decorator") if body is not None else 0
    return {"methods": methods, "constructor_deps": dependencies, "decorators": decorators}


def _class_decorators(node) -> int:
    count = _count(node, "decorator")
    parent = node.parent
    if parent is not None and parent.type == "export_statement":
        count += _count(parent, "decorator")
    return count


def _count(node, node_type: str) -> int:
    return sum(1 for child in node.children if child.type == node_type)


def _is_inject_call(parsed: ParsedSource, node) -> bool:
    """``inject(Service)``, Angular's field-injection form of a constructor dependency."""
    if node.type != "call_expression":
        return False
    callee = node.child_by_field_name("function")
    return callee is not None and parsed.text(callee) == "inject"


__all__ = ["extract_ts_classes"]
