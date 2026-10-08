"""TypeScript facade detection helpers.

A file is a re-export facade when every top-level statement only forwards
another module's bindings, with at least one such statement:

- ``export ... from '...'`` in every form (named, multi-line, ``export *``,
  ``export * as ns``, ``export type { }``, ``export type *``);
- import-then-export: ``import { a } from 'x'; export { a };`` (and
  ``export default a``), when every locally exported name is an imported
  binding;
- comments, a ``#!`` line, ``export {}``, and a directive prologue
  (``'use strict'``) before the first other statement.

A side-effect import (``import './x'``) or any declaration makes the file do
something besides forwarding, so it is not a facade. Files with only
comments or directives are not facades either, and nor are files whose first
statement is ``'use client'`` or ``'use server'``: in Next.js those mark a
client or server boundary, which a re-export alone can carry.

Without tree-sitter a regex fallback recognises the ``export ... from`` forms
and directives but not import-then-export.
"""

from __future__ import annotations

import re
from pathlib import Path

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.languages._framework.facade_common import detect_reexport_facades_common
from desloppify.languages.typescript.syntax.tree import ParsedSource, parse_text

_BOUNDARY_DIRECTIVES = frozenset({"use client", "use server"})


def is_ts_facade(filepath: str) -> dict | None:
    """Check if a TypeScript file is a pure re-export facade."""
    try:
        content = Path(resolve_path(filepath)).read_text()
    except (OSError, UnicodeDecodeError):
        return None

    # Every facade form needs both keywords; skip the parse for most files.
    if "export" not in content or "from" not in content:
        return None

    parsed = parse_text(content, filepath)
    if parsed is None:
        imports_from = _reexport_sources_regex(content)
    else:
        imports_from = _reexport_sources_tree(parsed)
    if not imports_from:
        return None
    return {"imports_from": imports_from, "loc": len(content.splitlines())}


# ── Syntax tree ─────────────────────────────────────────────


def _reexport_sources_tree(parsed: ParsedSource) -> list[str] | None:
    """Module sources the file forwards, or None when it is not a facade."""
    imported: dict[str, str] = {}  # local binding -> module source
    reexported: list[str] = []
    local_exports: list[str] = []
    first = True
    in_prologue = True

    for node in parsed.root.named_children:
        kind = node.type
        if kind in ("comment", "hash_bang_line"):
            continue
        if in_prologue and _is_directive(node):
            if first and _string_value(parsed, node.named_children[0]) in _BOUNDARY_DIRECTIVES:
                return None
            first = False
            continue
        in_prologue = False

        if kind == "import_statement":
            bindings = _import_bindings(parsed, node)
            if bindings is None:
                return None
            imported.update(bindings)
        elif kind == "export_statement":
            source = node.child_by_field_name("source")
            if source is not None:
                if not _is_plain_reexport(node):
                    return None
                reexported.append(_string_value(parsed, source))
                continue
            names = _local_export_names(parsed, node)
            if names is None:
                return None
            local_exports.extend(names)
        else:
            return None

    if any(name not in imported for name in local_exports):
        return None
    forwarded = list(reexported)
    for name in local_exports:
        if imported[name] not in forwarded:
            forwarded.append(imported[name])
    return forwarded or None


def _is_directive(node) -> bool:
    """``'use client';`` — an expression statement holding only a string."""
    if node.type != "expression_statement":
        return False
    named = node.named_children
    return len(named) == 1 and named[0].type == "string"


def _is_plain_reexport(node) -> bool:
    """``export ... from`` without unexpected parse errors.

    The grammar doesn't know ``export type *`` yet and wraps the ``type``
    keyword in an ERROR node; that is the only error accepted.
    """
    for child in node.children:
        if child.type == "ERROR":
            if [c.type for c in child.children] != ["type"]:
                return False
        elif child.is_missing or child.has_error:
            return False
    return True


def _string_value(parsed: ParsedSource, string_node) -> str:
    return parsed.text(string_node)[1:-1]


def _import_bindings(parsed: ParsedSource, node) -> dict[str, str] | None:
    """Local names an import statement binds, mapped to its source.

    None for imports that aren't forwarding material: side-effect imports,
    ``import x = require()`` and anything that failed to parse.
    """
    source = node.child_by_field_name("source")
    clause = next((c for c in node.named_children if c.type == "import_clause"), None)
    if source is None or clause is None or node.has_error:
        return None
    module = _string_value(parsed, source)
    bindings: dict[str, str] = {}
    for part in clause.named_children:
        if part.type == "identifier":  # default import
            bindings[parsed.text(part)] = module
        elif part.type == "namespace_import":
            for ident in part.named_children:
                bindings[parsed.text(ident)] = module
        elif part.type == "named_imports":
            for spec in part.named_children:
                if spec.type != "import_specifier":
                    continue
                local = spec.child_by_field_name("alias") or spec.child_by_field_name("name")
                if local is not None:
                    bindings[parsed.text(local)] = module
    return bindings


def _local_export_names(parsed: ParsedSource, node) -> list[str] | None:
    """Names a source-less export forwards: ``export { a, b as c }`` or
    ``export default a``. None for any export that declares something."""
    if node.has_error:
        return None
    named = node.named_children
    if len(named) == 1 and named[0].type == "export_clause":
        names = []
        for spec in named[0].named_children:
            if spec.type != "export_specifier":
                continue
            name = spec.child_by_field_name("name")
            if name is None:
                return None
            names.append(parsed.text(name))
        return names
    value = node.child_by_field_name("value")
    is_default = any(c.type == "default" for c in node.children)
    if is_default and value is not None and value.type == "identifier" and len(named) == 1:
        return [parsed.text(value)]
    return None


# ── Regex fallback ──────────────────────────────────────────

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# ``//`` not preceded by ``:`` so URLs inside module strings survive.
_LINE_COMMENT_RE = re.compile(r"(^|[^:\\])//.*$", re.MULTILINE)
_HASH_BANG_RE = re.compile(r"\A#![^\n]*")
_DIRECTIVE_RE = re.compile(r"""\s*(['"])([^'"\n]*)\1\s*;?""")
_REEXPORT_RE = re.compile(
    r"""\s*export\s+(?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s*"""
    r"""from\s*(['"])([^'"]+)\1\s*;?"""
)


def _reexport_sources_regex(content: str) -> list[str] | None:
    """Best-effort facade check without a parser.

    Matches the ``export ... from`` forms (multi-line included) after a
    directive prologue that doesn't open with a boundary directive;
    import-then-export files are not recognised.
    """
    code = _HASH_BANG_RE.sub("", content)
    code = _LINE_COMMENT_RE.sub(r"\1", _BLOCK_COMMENT_RE.sub("", code))
    pos = 0
    while match := _DIRECTIVE_RE.match(code, pos):
        if pos == 0 and match.group(2) in _BOUNDARY_DIRECTIVES:
            return None
        pos = match.end()
    sources: list[str] = []
    while match := _REEXPORT_RE.match(code, pos):
        sources.append(match.group(2))
        pos = match.end()
    if code[pos:].strip():
        return None
    return sources or None


def detect_reexport_facades(
    graph: dict,
) -> tuple[list[dict], int]:
    """Detect TypeScript re-export facade files."""
    entries, total_checked = detect_reexport_facades_common(
        graph,
        is_facade_fn=is_ts_facade,
    )

    return sorted(
        entries, key=lambda e: (e["kind"], e["importers"], -e["loc"])
    ), total_checked
