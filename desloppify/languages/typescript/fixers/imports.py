"""Unused-import fixer that edits syntax-tree ranges.

Each tsc finding is matched to the import statement covering its line and to
the binding with the reported local name. Only that binding's text (plus its
separating comma) is deleted, so formatting, comments elsewhere in the
statement, type modifiers and import attributes are kept. A statement loses
all of its text only when every binding goes; side-effect imports
(``import 'x'``) have no bindings and are never touched.

Needs tree-sitter: without it the fixer changes nothing.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass

from desloppify.base.output.terminal import colorize
from desloppify.languages._framework.base.types import FixResult
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    get_parser,
    parse_text,
)

from .fixer_io import apply_fixer

ENTIRE_IMPORT = "(entire import)"
# Key for the `{ ... }` block among a clause's parts; never a valid identifier.
_NAMED_BLOCK = "{}"


@dataclass(frozen=True)
class _Binding:
    local: str
    node: object  # the node whose text is removed with the binding


def fix_unused_imports(entries: list[dict], *, dry_run: bool = False) -> FixResult:
    """Remove unused import bindings reported by the ``unused`` detector."""
    import_entries = [entry for entry in entries if entry.get("category") == "imports"]
    if import_entries and get_parser("tsx") is None:
        print(
            colorize(
                "  Skip: the unused-imports fixer needs tree-sitter "
                "(install desloppify[full]).",
                "yellow",
            ),
            file=sys.stderr,
        )
        return FixResult(entries=[], skip_reasons={"needs_treesitter": len(import_entries)})

    def transform(lines: list[str], file_entries: list[dict]) -> tuple[list[str], list[str]]:
        text = "".join(lines)
        path = str(file_entries[0].get("file", "")) if file_entries else ""
        parsed = parse_text(text, path)
        if parsed is None:
            return lines, []
        new_source, removed = remove_unused_imports(parsed, file_entries)
        if not removed:
            return lines, []
        return new_source.decode("utf-8").splitlines(keepends=True), removed

    return FixResult(entries=apply_fixer(import_entries, transform, dry_run=dry_run))


def remove_unused_imports(
    parsed: ParsedSource, file_entries: list[dict]
) -> tuple[bytes, list[str]]:
    """Return the edited source and the entry names that were removed."""
    names_by_line: dict[int, set[str]] = {}
    for entry in file_entries:
        line, name = entry.get("line"), entry.get("name")
        if isinstance(line, int) and isinstance(name, str) and name:
            names_by_line.setdefault(line, set()).add(name)

    edits: list[tuple[int, int]] = []
    removed: Counter[str] = Counter()
    for statement in parsed.root.named_children:
        if statement.type != "import_statement":
            continue
        first, last = statement.start_point[0] + 1, statement.end_point[0] + 1
        wanted: set[str] = set()
        for line in range(first, last + 1):
            wanted |= names_by_line.get(line, set())
        if not wanted:
            continue
        statement_edits, statement_removed = _statement_edits(parsed, statement, wanted)
        edits.extend(statement_edits)
        removed.update(statement_removed)

    source = parsed.source
    for start, end in sorted(edits, reverse=True):
        source = source[:start] + source[end:]
    order = [str(e.get("name")) for e in file_entries]
    return source, [name for name in dict.fromkeys(order) if removed[name]]


def _statement_edits(
    parsed: ParsedSource, statement, wanted: set[str]
) -> tuple[list[tuple[int, int]], set[str]]:
    parts, specifiers = _bindings(parsed, statement)
    binding_names = {b.local for b in parts if b.local != _NAMED_BLOCK}
    binding_names |= {s.local for s in specifiers}
    if not binding_names:
        return [], set()  # side-effect import

    remove = wanted & binding_names
    if ENTIRE_IMPORT in wanted or remove == binding_names:
        removed = remove | ({ENTIRE_IMPORT} if ENTIRE_IMPORT in wanted else set())
        return [_whole_statement_range(parsed.source, statement)], removed
    if not remove:
        return [], set()

    edits: list[tuple[int, int]] = []
    part_keys = set(remove)
    if specifiers and all(s.local in remove for s in specifiers):
        part_keys.add(_NAMED_BLOCK)  # drop the whole `{ ... }` with its comma
    else:
        edits.extend(_list_edits(specifiers, remove))
    edits.extend(_list_edits(parts, part_keys))
    return edits, remove


def _clause(statement):
    return next(
        (c for c in statement.named_children if c.type in ("import_clause", "import_require_clause")),
        statement,
    )


def _bindings(parsed: ParsedSource, statement) -> tuple[list[_Binding], list[_Binding]]:
    """Clause-level bindings (default, namespace, named block) and named specifiers."""
    clause = _clause(statement)
    if clause.type == "import_require_clause":
        name = next((c for c in clause.named_children if c.type == "identifier"), None)
        return ([_Binding(parsed.text(name), clause)] if name is not None else []), []
    if clause is statement:
        return [], []

    parts: list[_Binding] = []
    specifiers: list[_Binding] = []
    for child in clause.named_children:
        if child.type == "identifier":
            parts.append(_Binding(parsed.text(child), child))
        elif child.type == "namespace_import":
            ident = next((c for c in child.named_children if c.type == "identifier"), None)
            if ident is not None:
                parts.append(_Binding(parsed.text(ident), child))
        elif child.type == "named_imports":
            parts.append(_Binding(_NAMED_BLOCK, child))
            for spec in child.named_children:
                if spec.type != "import_specifier":
                    continue
                local = spec.child_by_field_name("alias") or spec.child_by_field_name("name")
                if local is not None:
                    specifiers.append(_Binding(parsed.text(local), spec))
    return parts, specifiers


def _list_edits(items: list[_Binding], remove: set[str]) -> list[tuple[int, int]]:
    """Byte ranges that delete removed items from a comma-separated list.

    A run of removed items followed by a kept one is deleted up to that kept
    item's start; a trailing run is deleted from the previous kept item's end,
    which also takes the separating comma.
    """
    edits: list[tuple[int, int]] = []
    index = 0
    while index < len(items):
        if items[index].local not in remove:
            index += 1
            continue
        run_start = index
        while index < len(items) and items[index].local in remove:
            index += 1
        first, last = items[run_start].node, items[index - 1].node
        if index < len(items):
            edits.append((first.start_byte, items[index].node.start_byte))
        elif run_start > 0:
            edits.append((items[run_start - 1].node.end_byte, last.end_byte))
    return edits


def _whole_statement_range(source: bytes, statement) -> tuple[int, int]:
    """The statement's bytes, widened to whole lines when it has them to itself."""
    start, end = statement.start_byte, statement.end_byte
    line_start = source.rfind(b"\n", 0, start) + 1
    newline = source.find(b"\n", end)
    line_end = len(source) if newline == -1 else newline
    rest = source[end:line_end].strip()
    if source[line_start:start].strip() or (rest and not rest.startswith(b"//")):
        # Shares its line with other code: remove just the statement.
        while end < line_end and source[end : end + 1] in (b" ", b"\t"):
            end += 1
        return start, end

    remove_end = len(source) if newline == -1 else newline + 1
    previous_blank = line_start == 0 or source[:line_start].endswith(b"\n\n")
    next_newline = source.find(b"\n", remove_end)
    next_line = source[remove_end : len(source) if next_newline == -1 else next_newline]
    if previous_blank and remove_end < len(source) and not next_line.strip():
        remove_end = len(source) if next_newline == -1 else next_newline + 1
    return line_start, remove_end


__all__ = ["ENTIRE_IMPORT", "fix_unused_imports", "remove_unused_imports"]
