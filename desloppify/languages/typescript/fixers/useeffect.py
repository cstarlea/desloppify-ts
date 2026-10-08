"""Dead useEffect fixer: removes ``useEffect`` calls whose callback does nothing.

Each smell entry is matched to a ``useEffect`` or ``React.useEffect`` call
that starts on its line. The call is removed only when it is a statement of
its own and its callback is an arrow or function whose block body is empty
(no statements, no comments) or a bare ``return;``. The dependency array is
evaluated on every render, so it must only read values: ``[load()]`` still
calls ``load``, so that effect stays. Comments above the effect stay too; an
import left unused is the imports fixer's job.

Skipped: comment-only or non-empty callbacks, deps that may run code, calls
that are part of a larger expression, and removals that would join
semicolon-less neighbours.

Needs tree-sitter: without it the fixer changes nothing.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any

from desloppify.base.output.terminal import colorize
from desloppify.languages._framework.base.types import FixResult
from desloppify.languages.typescript.syntax.nodes import (
    STATEMENT_PARENTS,
    asi_hazards,
    node_key,
    reads_only,
)
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    get_parser,
    parse_text,
)

from .edits import apply_edits, whole_statement_range
from .fixer_io import apply_fixer

_EFFECT_CALLEES = frozenset({"useEffect", "React.useEffect"})
_CALLBACKS = frozenset({"arrow_function", "function_expression", "function"})


def fix_dead_useeffect(
    entries: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> FixResult:
    """Remove useEffect calls whose callback does nothing."""
    if entries and get_parser("tsx") is None:
        print(
            colorize(
                "  Skip: the dead-useeffect fixer needs tree-sitter (install desloppify[full]).",
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
        new_source, fixed, skipped = remove_dead_effects(parsed, file_entries)
        for reason in skipped:
            skip_reasons[reason] += 1
        if not fixed:
            return lines, []
        return new_source.decode("utf-8").splitlines(keepends=True), fixed

    results = apply_fixer(entries, transform, dry_run=dry_run)
    return FixResult(entries=results, skip_reasons=dict(skip_reasons))


def remove_dead_effects(
    parsed: ParsedSource, file_entries: list[dict]
) -> tuple[bytes, list[dict], list[str]]:
    """Return the edited source, the fixed entries and a skip reason per skipped entry."""
    calls_by_row: dict[int, list] = defaultdict(list)
    stack = [parsed.root]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            if function is not None and parsed.text(function) in _EFFECT_CALLEES:
                calls_by_row[node.start_point[0]].append(node)

    planned: dict[tuple, object] = {}
    entry_keys: list[tuple[dict, tuple]] = []
    skipped: list[str] = []
    for entry in file_entries:
        line = entry.get("line")
        calls = calls_by_row.get(line - 1, []) if isinstance(line, int) else []
        candidates = [c for c in calls if node_key(c.parent) not in planned]
        if not candidates:
            skipped.append("not_found")
            continue
        reasons = [_blocker(parsed, call) for call in candidates]
        if None not in reasons:
            skipped.append(reasons[0])
            continue
        statement = candidates[reasons.index(None)].parent
        planned[node_key(statement)] = statement
        entry_keys.append((entry, node_key(statement)))

    kept = set(asi_hazards(parsed.source, planned))
    fixed: list[dict] = []
    edits: list[tuple[int, int]] = []
    for entry, key in entry_keys:
        if key in kept:
            skipped.append("asi_hazard")
            continue
        fixed.append(entry)
        edits.append(whole_statement_range(parsed.source, planned[key]))
    return apply_edits(parsed.source, edits), fixed, skipped


def _blocker(parsed: ParsedSource, call) -> str | None:
    """Why ``call`` can't be removed, or None."""
    statement = call.parent
    if (
        statement is None
        or statement.type != "expression_statement"
        or statement.parent is None
        or statement.parent.type not in STATEMENT_PARENTS
    ):
        return "not_standalone"
    args = call.child_by_field_name("arguments")
    if args is None or args.type != "arguments" or _has_comment(args):
        return "not_empty"
    values = args.named_children
    if not values or not _is_empty_callback(values[0]):
        return "not_empty"
    if not all(v.type != "spread_element" and reads_only(parsed, v) for v in values[1:]):
        return "side_effects"
    return None


def _is_empty_callback(node) -> bool:
    if node.type not in _CALLBACKS:
        return False
    body = node.child_by_field_name("body")
    if body is None or body.type != "statement_block":
        return False
    statements = body.named_children
    if not statements:
        return True
    return (
        len(statements) == 1
        and statements[0].type == "return_statement"
        and not statements[0].named_children
    )


def _has_comment(node) -> bool:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "comment":
            return True
        stack.extend(current.named_children)
    return False


__all__ = ["fix_dead_useeffect", "remove_dead_effects"]
