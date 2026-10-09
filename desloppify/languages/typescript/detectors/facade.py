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
comments or directives are not facades either, and nor are files whose
directive prologue holds ``'use client'`` or ``'use server'``: in Next.js
those mark a client or server boundary, which a re-export alone can carry.

Without tree-sitter a regex fallback recognises the ``export ... from`` forms
and directives but not import-then-export.
"""

from __future__ import annotations

import re
from pathlib import Path

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.languages._framework.facade_common import detect_reexport_facades_common
from desloppify.languages.typescript.syntax.queries import (
    directive,
    export_info,
    import_info,
)
from desloppify.languages.typescript.syntax.tree import ParsedSource, parsed_file

_BOUNDARY_DIRECTIVES = frozenset({"use client", "use server"})


def is_ts_facade(filepath: str) -> dict | None:
    """Check if a TypeScript file is a pure re-export facade."""
    try:
        content = Path(resolve_path(filepath)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # Every facade form needs both keywords; skip the parse for most files.
    if "export" not in content or "from" not in content:
        return None

    imports_from = reexport_sources(content, parsed_file(filepath))
    if not imports_from:
        return None
    return {"imports_from": imports_from, "loc": len(content.splitlines())}


def reexport_sources(content: str, parsed: ParsedSource | None) -> list[str] | None:
    """Module sources a facade forwards, or None when the file is not a facade.

    Uses the syntax tree when there is one, else the regex fallback.
    """
    if parsed is None:
        return _reexport_sources_regex(content)
    return _reexport_sources_tree(parsed)


# ── Syntax tree ─────────────────────────────────────────────


def _reexport_sources_tree(parsed: ParsedSource) -> list[str] | None:
    """Module sources the file forwards, or None when it is not a facade."""
    imported: dict[str, str] = {}  # local binding -> module source
    reexported: list[str] = []
    local_exports: list[str] = []
    in_prologue = True

    for node in parsed.root.named_children:
        kind = node.type
        if kind in ("comment", "hash_bang_line"):
            continue
        if in_prologue and (value := directive(parsed, node)) is not None:
            if value in _BOUNDARY_DIRECTIVES:
                return None
            continue
        in_prologue = False

        if kind == "import_statement":
            # Side-effect imports, ``import x = require()`` and broken imports
            # aren't forwarding material.
            imp = import_info(parsed, node)
            if imp is None or imp.kind != "static" or imp.has_error:
                return None
            imported.update((binding.local, imp.source) for binding in imp.bindings)
        elif kind == "export_statement":
            exp = export_info(parsed, node)
            if exp.has_error:
                return None
            if exp.source is not None:
                reexported.append(exp.source)
            elif exp.kind == "named" or (exp.kind == "default" and exp.bindings):
                local_exports.extend(b.name for b in exp.bindings if b.name is not None)
            else:
                return None
        else:
            return None

    if any(name not in imported for name in local_exports):
        return None
    forwarded = list(reexported)
    for name in local_exports:
        if imported[name] not in forwarded:
            forwarded.append(imported[name])
    return forwarded or None


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
    directive prologue without a boundary directive;
    import-then-export files are not recognised.
    """
    code = _HASH_BANG_RE.sub("", content)
    code = _LINE_COMMENT_RE.sub(r"\1", _BLOCK_COMMENT_RE.sub("", code))
    pos = 0
    while match := _DIRECTIVE_RE.match(code, pos):
        if match.group(2) in _BOUNDARY_DIRECTIVES:
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
