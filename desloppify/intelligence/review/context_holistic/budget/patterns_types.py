"""dict[str, Any] annotation scanner."""

from __future__ import annotations

import ast

from desloppify.base.discovery.file_paths import rel


def _find_dict_any_annotations(parsed_trees: dict[str, ast.Module]) -> list[dict]:
    """Find parameters/returns annotated as dict[str, Any]."""
    results: list[dict] = []
    for filepath, tree in parsed_trees.items():
        rpath = rel(filepath)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue

            all_args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            if node.args.vararg:
                all_args.append(node.args.vararg)
            if node.args.kwarg:
                all_args.append(node.args.kwarg)

            for arg in all_args:
                if arg.annotation and _is_dict_str_any(arg.annotation):
                    results.append(
                        {"file": rpath, "function": node.name, "param": arg.arg, "line": arg.lineno}
                    )

            if node.returns and _is_dict_str_any(node.returns):
                results.append(
                    {"file": rpath, "function": node.name, "param": "(return)", "line": node.lineno}
                )
    return results


def _is_dict_str_any(annotation: ast.expr) -> bool:
    """Check if annotation is ``dict[str, Any]``."""
    if not isinstance(annotation, ast.Subscript):
        return False
    if not (isinstance(annotation.value, ast.Name) and annotation.value.id == "dict"):
        return False
    sl = annotation.slice
    if isinstance(sl, ast.Tuple) and len(sl.elts) == 2:
        first, second = sl.elts
        if isinstance(first, ast.Name) and first.id == "str":
            if isinstance(second, ast.Name) and second.id == "Any":
                return True
    return False


__all__ = ["_find_dict_any_annotations", "_is_dict_str_any"]
