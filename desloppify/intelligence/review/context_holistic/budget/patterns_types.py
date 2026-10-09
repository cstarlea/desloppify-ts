"""Loose map annotations: ``Record<string, any>`` and ``{ [key: string]: any }``."""

from __future__ import annotations

from desloppify.languages.typescript.syntax.queries import descendants, functions
from desloppify.languages.typescript.syntax.tree import ParsedSource

_MEMBER_SLOTS = ("property_signature", "public_field_definition")


def _find_dict_any_annotations(parsed_files: dict[str, ParsedSource]) -> list[dict]:
    """Parameters, returns and properties typed as a map of ``any``."""
    results: list[dict] = []
    for rpath, parsed in parsed_files.items():
        for info in functions(parsed):
            owner = info.name or "(anonymous)"
            if info.owner:
                owner = f"{info.owner}.{owner}"
            for param in info.params:
                annotation = param.node.child_by_field_name("type")  # type: ignore[attr-defined]
                if annotation is not None and _has_dict_any(annotation):
                    results.append(_entry(parsed, rpath, owner, param.name, annotation))
            returns = info.node.child_by_field_name("return_type")  # type: ignore[attr-defined]
            if returns is not None and _has_dict_any(returns):
                results.append(_entry(parsed, rpath, owner, "(return)", returns))
        for member in descendants(parsed.root, _MEMBER_SLOTS):
            annotation = member.child_by_field_name("type")
            name = member.child_by_field_name("name")
            if annotation is None or name is None or not _has_dict_any(annotation):
                continue
            results.append(
                _entry(
                    parsed,
                    rpath,
                    _container_name(parsed, member),
                    parsed.text(name),
                    annotation,
                )
            )
    results.sort(key=lambda item: (item["file"], item["line"]))
    return results


def _entry(
    parsed: ParsedSource, rpath: str, symbol: str, slot: str, annotation
) -> dict:
    return {
        "file": rpath,
        "line": parsed.line(annotation),
        "symbol": symbol,
        "slot": slot,
        "type": parsed.text(annotation).lstrip(":").strip()[:80],
    }


def _container_name(parsed: ParsedSource, node) -> str:
    """The interface, type alias or class a member belongs to."""
    parent = node.parent
    while parent is not None:
        if parent.type in (
            "interface_declaration",
            "type_alias_declaration",
            "class_declaration",
            "abstract_class_declaration",
        ):
            name = parent.child_by_field_name("name")
            return parsed.text(name) if name is not None else "(anonymous)"
        parent = parent.parent
    return "(anonymous)"


def _has_dict_any(annotation) -> bool:
    return any(
        _is_dict_any(node)
        for node in descendants(annotation, ("generic_type", "object_type"))
    )


def _is_dict_any(node) -> bool:
    """``Record<K, any>``, or an object type whose only members are ``any`` index signatures."""
    if node.type == "generic_type":
        name = node.child_by_field_name("name")
        args = node.child_by_field_name("type_arguments")
        if name is None or args is None or name.text != b"Record":
            return False
        values = args.named_children
        return len(values) == 2 and _is_any(values[1])
    members = [child for child in node.named_children if child.type != "comment"]
    return bool(members) and all(
        member.type == "index_signature" and _is_any(_index_value(member))
        for member in members
    )


def _index_value(signature):
    annotation = next(
        (c for c in signature.named_children if c.type == "type_annotation"), None
    )
    return (
        annotation.named_children[0]
        if annotation is not None and annotation.named_children
        else None
    )


def _is_any(node) -> bool:
    return node is not None and node.type == "predefined_type" and node.text == b"any"


__all__ = ["_find_dict_any_annotations"]
