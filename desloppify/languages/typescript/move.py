"""TypeScript move helpers for import replacement computation.

Importers are found by resolving their actual import specifiers with the
same resolver as the dependency graph, so every specifier form TypeScript
accepts (relative, tsconfig ``paths`` aliases, ``.js`` suffixes, directory
index imports) is recognised. Each rewrite keeps the specifier's style.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.languages.typescript.detectors.deps.imports import (
    DYNAMIC_PREFIX,
    GLOB,
    ImportExtractor,
)
from desloppify.languages.typescript.detectors.deps.resolver import (
    ModuleResolver,
    project_resolver,
)

VERIFY_HINT = "npx tsc --noEmit"
# Every importer of a moved TS file must have its specifier rewritten; an
# importer with no rewrite would be left broken, so the move aborts instead.
# This also makes the move command build its graph from the project root.
REQUIRES_ALL_IMPORTERS_REWRITTEN = True
# ESM/NodeNext code imports TS files with runtime extensions ("./x.js").
_SPECIFIER_SUFFIXES = ("", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
_JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs")
_QUOTES = ("'", '"', "`")
logger = logging.getLogger(__name__)


def _dedup(replacements: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Deduplicate replacement tuples while preserving order."""
    seen: set[tuple[str, str]] = set()
    result = []
    for pair in replacements:
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def _strip_ts_ext(path: str) -> str:
    """Strip .ts/.tsx/.js/.jsx extension from an import path."""
    for ext in (".tsx", ".ts", ".jsx", ".js"):
        if path.endswith(ext):
            return path[: -len(ext)]
    return path


def _read(path: str, context: str) -> str | None:
    try:
        return Path(path).read_text()
    except (OSError, UnicodeDecodeError) as exc:
        log_best_effort_failure(logger, f"{context} {path}", exc)
        return None


def _resolver() -> ModuleResolver:
    return project_resolver(get_project_root())


def _specifiers(content: str) -> list[str]:
    """Static specifiers in *content* (patterns can't be rewritten)."""
    seen: dict[str, None] = {}
    for ref in ImportExtractor().extract_text(content):
        if ref.kind not in (GLOB, DYNAMIC_PREFIX):
            seen.setdefault(ref.specifier)
    return list(seen)


def _is_index(path: str) -> bool:
    return Path(path).stem == "index" and Path(path).suffix in _TS_SUFFIXES


def _styled(base: str, old_spec: str, old_target: str, new_target: str) -> str:
    """Path *base* (new target, extension stripped) in the style of *old_spec*."""
    old_suffix = next((s for s in (*_TS_SUFFIXES, *_JS_SUFFIXES) if old_spec.endswith(s)), "")
    directory_form = _is_index(old_target) and not _strip_ts_ext(
        old_spec.removesuffix(old_suffix)
    ).endswith("index")
    if directory_form and _is_index(new_target):
        return base.removesuffix("/index") or "."
    if old_suffix in _TS_SUFFIXES:
        return base + Path(new_target).suffix  # allowImportingTsExtensions
    return base + old_suffix


def _without_suffix(path: str) -> str:
    stem, suffix = os.path.splitext(path)
    return stem if suffix in _TS_SUFFIXES else path


def rewrite_specifier(
    spec: str,
    old_from: str,
    old_target: str,
    new_from: str,
    new_target: str,
    resolver: ModuleResolver,
) -> str | None:
    """*spec* rewritten for the moved file(s), or None when it can't be.

    Relative specifiers stay relative; an alias stays an alias when one
    covers the new location (else becomes relative). Workspace package
    specifiers name the package's public entry and can't be rewritten.
    """
    if not spec.startswith("."):
        prefix = resolver.alias_prefix(spec, old_from)
        if prefix is None:
            return None
        alias = resolver.alias_specifier(new_target, new_from, prefer=prefix)
        if alias is not None:
            return _styled(_without_suffix(alias), spec, old_target, new_target)
        # No alias covers the new location: fall back to a relative path.
    return _styled(_relative(new_from, new_target), spec, old_target, new_target)


def _relative(from_file: str, target: str) -> str:
    relative = os.path.relpath(_without_suffix(target), Path(from_file).parent)
    relative = relative.replace(os.sep, "/")
    return relative if relative.startswith(".") else "./" + relative


def _quoted(content: str, old_spec: str, new_spec: str) -> list[tuple[str, str]]:
    return [
        (f"{q}{old_spec}{q}", f"{q}{new_spec}{q}")
        for q in _QUOTES
        if f"{q}{old_spec}{q}" in content
    ]


def find_replacements(
    source_abs: str,
    dest_abs: str,
    graph: dict,
) -> dict[str, list[tuple[str, str]]]:
    """Compute all import string replacements needed for a TS file move."""
    changes: dict[str, list[tuple[str, str]]] = {}
    entry = graph.get(source_abs)
    if not entry:
        return changes
    resolver = _resolver()

    for importer in entry.get("importers", set()):
        if importer == source_abs:
            continue
        content = _read(importer, "read importer for TypeScript move")
        if content is None:
            continue
        replacements = []
        for spec in _specifiers(content):
            if resolver.resolve(spec, importer) != source_abs:
                continue
            new_spec = rewrite_specifier(spec, importer, source_abs, importer, dest_abs, resolver)
            if new_spec is not None and new_spec != spec:
                replacements.extend(_quoted(content, spec, new_spec))
        if replacements:
            changes[importer] = _dedup(replacements)

    return changes


def find_self_replacements(
    source_abs: str,
    dest_abs: str,
    graph: dict,
) -> list[tuple[str, str]]:
    """Compute replacements for the moved file's own relative imports."""
    if not graph.get(source_abs):
        return []
    content = _read(source_abs, "read moved TypeScript source")
    if content is None:
        return []
    resolver = _resolver()

    replacements = []
    for spec in _specifiers(content):
        if not spec.startswith("."):
            continue  # aliases and packages don't depend on the file's location
        target = resolver.resolve(spec, source_abs)
        if target is None or target == source_abs:
            continue
        new_spec = rewrite_specifier(spec, source_abs, target, dest_abs, target, resolver)
        if new_spec is not None and new_spec != spec:
            replacements.extend(_quoted(content, spec, new_spec))
    return _dedup(replacements)


def _is_relative_replacement(replacement: tuple[str, str]) -> bool:
    return replacement[0][1:2] == "."


def _relative_target_base(source_file: str, quoted_spec: str) -> str:
    """Absolute path (extension stripped) that a quoted relative specifier names."""
    spec = quoted_spec[1:-1]
    for suffix in _SPECIFIER_SUFFIXES[1:]:
        if spec.endswith(suffix):
            spec = spec[: -len(suffix)]
            break
    return os.path.normpath(os.path.join(os.path.dirname(source_file), spec))


def filter_intra_package_importer_changes(
    source_file: str,
    replacements: list[tuple[str, str]],
    moving_files: set[str],
) -> list[tuple[str, str]]:
    """Drop relative rewrites between files that move together.

    Their relative layout is unchanged, so "./sibling" stays correct; only
    location-independent alias specifiers ("@/feature/x") need rewriting.
    """
    del source_file, moving_files
    return [r for r in replacements if not _is_relative_replacement(r)]


def filter_directory_self_changes(
    source_file: str,
    self_changes: list[tuple[str, str]],
    moving_files: set[str],
) -> list[tuple[str, str]]:
    """Keep self-import rewrites only for targets that are not moving too."""
    moving_bases = set()
    for moving in moving_files:
        base = _strip_ts_ext(moving)
        moving_bases.add(base)
        if base.endswith(f"{os.sep}index"):
            moving_bases.add(base[: -len("/index")])
    return [
        r
        for r in self_changes
        if not (
            _is_relative_replacement(r)
            and _relative_target_base(source_file, r[0]) in moving_bases
        )
    ]
