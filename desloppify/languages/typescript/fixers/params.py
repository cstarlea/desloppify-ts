"""Unused-params fixer: marks unused parameters as intentional with a ``_`` prefix.

Each tsc finding is matched to the parameter name at its reported line and
column. A plain parameter is renamed (``x`` → ``_x``, ``...rest`` →
``..._rest``); a destructured shorthand keeps its property and gets an alias
(``{ a = 1 }`` → ``{ a: _a = 1 }``), since renaming the property itself would
read a different one. tsc's ``noUnusedParameters`` ignores all of these forms.
Catch bindings are treated the same way.

Skipped: names that aren't parameters (the unused-vars fixer's job),
TypeScript parameter properties (``constructor(private x)``, where the name
is also a class field), parameters named elsewhere in the signature (type
predicates like ``x is T``, ``asserts x``, ``typeof x``), and parameters whose
new name already appears in the function, where the rename would shadow or
collide with it.

Needs tree-sitter: without it the fixer changes nothing.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from desloppify.base.output.terminal import colorize
from desloppify.languages._framework.base.types import FixResult
from desloppify.languages.typescript.syntax.nodes import (
    PARAMETERS,
    NameIndex,
    node_key,
    parameter_owner,
    same,
    within,
)
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    get_parser,
    parse_text,
)

from .edits import apply_replacements
from .fixer_io import apply_fixer

_PROPERTY_MODIFIERS = frozenset({"accessibility_modifier", "override_modifier", "readonly"})


def fix_unused_params(entries: list[dict], *, dry_run: bool = False) -> FixResult:
    """Prefix unused function, callback and catch parameters with ``_``."""
    if entries and get_parser("tsx") is None:
        print(
            colorize(
                "  Skip: the unused-params fixer needs tree-sitter (install desloppify[full]).",
                "yellow",
            ),
            file=sys.stderr,
        )
        return FixResult(entries=[], skip_reasons={"needs_treesitter": len(entries)})

    skip_reasons: dict[str, int] = defaultdict(int)

    def transform(lines: list[str], file_entries: list[dict]) -> tuple[list[str], list[dict]]:
        path = str(file_entries[0].get("file", "")) if file_entries else ""
        parsed = parse_text("".join(lines), path)
        if parsed is None:
            return lines, []
        new_source, fixed, skipped = prefix_unused_params(parsed, file_entries)
        for reason in skipped:
            skip_reasons[reason] += 1
        if not fixed:
            return lines, []
        return new_source.decode("utf-8").splitlines(keepends=True), fixed

    results = apply_fixer(entries, transform, dry_run=dry_run)
    return FixResult(entries=results, skip_reasons=dict(skip_reasons))


def prefix_unused_params(
    parsed: ParsedSource, file_entries: list[dict]
) -> tuple[bytes, list[dict], list[str]]:
    """Return the edited source, the fixed entries and a skip reason per skipped entry."""
    names = NameIndex(parsed)
    replacements: dict[tuple, tuple[int, int, bytes]] = {}
    fixed: list[dict] = []
    skipped: list[str] = []
    for entry in file_entries:
        name, line = entry.get("name"), entry.get("line")
        if not isinstance(name, str) or not isinstance(line, int) or name.startswith("_"):
            skipped.append("not_found")
            continue
        node = names.find(name, line, entry.get("col"))
        if node is None:
            skipped.append("not_found")
            continue
        owner = parameter_owner(node)
        if owner is None:
            skipped.append("not_a_parameter")
            continue
        if _is_parameter_property(node):
            skipped.append("parameter_property")
            continue
        if _named_in_signature(names.get(name), node, owner):
            skipped.append("used_in_signature")
            continue
        new_name = f"_{name}"
        if any(within(other, owner) for other in names.get(new_name)):
            skipped.append("name_taken")
            continue
        text = new_name if node.type == "identifier" else f"{name}: {new_name}"
        replacements[node_key(node)] = (node.start_byte, node.end_byte, text.encode("utf-8"))
        fixed.append(entry)
    return apply_replacements(parsed.source, list(replacements.values())), fixed, skipped


def _named_in_signature(occurrences: list, node, owner) -> bool:
    """Whether the name appears elsewhere in the signature, e.g. ``x is T`` or ``typeof x``.

    tsc doesn't count those as reads, but they'd break if only the parameter
    were renamed. Occurrences in the body are other, shadowing bindings,
    since tsc reported the parameter as never read.
    """
    body = owner.child_by_field_name("body")
    return any(
        not same(other, node) and within(other, owner) and not (body is not None and within(other, body))
        for other in occurrences
    )


def _is_parameter_property(node) -> bool:
    parameter = node.parent
    while parameter is not None and parameter.type not in PARAMETERS:
        parameter = parameter.parent
    return parameter is not None and any(
        child.type in _PROPERTY_MODIFIERS for child in parameter.children
    )


__all__ = ["fix_unused_params", "prefix_unused_params"]
