"""Empty if-chain fixer: removes ``if``/``else if``/``else`` chains with nothing in them.

Each smell entry is matched to the ``if`` statement that starts on its line
and heads a chain (it isn't itself an ``else if``). The chain is followed on
the syntax tree, so it is removed only when every branch is an empty block
(no statements, no comments) or a bare ``;``, and every condition only reads
values: ``if (save()) {}`` still calls ``save``, so it stays.

Skipped: chains with any non-empty branch, conditions that may run code,
``if`` statements that are the unbraced body of another statement, and
removals that would join semicolon-less neighbours.

Needs tree-sitter: without it the fixer changes nothing.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any

from desloppify.base.output.terminal import colorize
from desloppify.languages._framework.base.types import FixResult
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    get_parser,
    parse_text,
)

from .edits import apply_edits, whole_statement_range
from .fixer_io import apply_fixer
from desloppify.languages.typescript.syntax.nodes import STATEMENT_PARENTS, asi_hazards, node_key, reads_only


def fix_empty_if_chain(
    entries: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> FixResult:
    """Remove if/else chains where every branch is empty."""
    if entries and get_parser("tsx") is None:
        print(
            colorize(
                "  Skip: the empty-if-chain fixer needs tree-sitter (install desloppify[full]).",
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
        new_source, fixed, skipped = remove_empty_if_chains(parsed, file_entries)
        for reason in skipped:
            skip_reasons[reason] += 1
        if not fixed:
            return lines, []
        return new_source.decode("utf-8").splitlines(keepends=True), fixed

    results = apply_fixer(entries, transform, dry_run=dry_run)
    return FixResult(entries=results, skip_reasons=dict(skip_reasons))


def remove_empty_if_chains(
    parsed: ParsedSource, file_entries: list[dict]
) -> tuple[bytes, list[dict], list[str]]:
    """Return the edited source, the fixed entries and a skip reason per skipped entry."""
    heads_by_row: dict[int, list] = defaultdict(list)
    stack = [parsed.root]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type == "if_statement" and (node.parent is None or node.parent.type != "else_clause"):
            heads_by_row[node.start_point[0]].append(node)

    planned: dict[tuple, object] = {}
    entry_keys: list[tuple[dict, tuple]] = []
    skipped: list[str] = []
    for entry in file_entries:
        line = entry.get("line")
        heads = heads_by_row.get(line - 1, []) if isinstance(line, int) else []
        if not heads:
            skipped.append("not_found")
            continue
        reasons = [_blocker(parsed, head) for head in heads]
        if None not in reasons:
            skipped.append(reasons[0])
            continue
        head = heads[reasons.index(None)]
        planned[node_key(head)] = head
        entry_keys.append((entry, node_key(head)))

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


def _blocker(parsed: ParsedSource, head) -> str | None:
    """Why the chain headed by ``head`` can't be removed, or None."""
    if head.parent is None or head.parent.type not in STATEMENT_PARENTS:
        return "not_standalone"
    node = head
    while True:
        condition = node.child_by_field_name("condition")
        if condition is None or not reads_only(parsed, condition):
            return "side_effects"
        if not _is_empty(node.child_by_field_name("consequence")):
            return "not_empty"
        alternative = node.child_by_field_name("alternative")
        if alternative is None:
            return None
        branches = alternative.named_children
        if len(branches) != 1:
            return "not_empty"  # a comment between `else` and its branch
        if branches[0].type == "if_statement":
            node = branches[0]
            continue
        return None if _is_empty(branches[0]) else "not_empty"


def _is_empty(branch) -> bool:
    if branch is None:
        return False
    if branch.type == "empty_statement":
        return True
    return branch.type == "statement_block" and branch.named_child_count == 0


__all__ = ["fix_empty_if_chain", "remove_empty_if_chains"]
