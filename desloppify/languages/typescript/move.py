"""TypeScript move helpers for import replacement computation."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.discovery.paths import get_src_path

VERIFY_HINT = "npx tsc --noEmit"
# Every importer of a moved TS file must have its specifier rewritten; an
# importer with no rewrite would be left broken, so the move aborts instead.
# This also makes the move command build its graph from the project root.
REQUIRES_ALL_IMPORTERS_REWRITTEN = True
# ESM/NodeNext code imports TS files with runtime extensions ("./x.js").
_SPECIFIER_SUFFIXES = ("", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
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


def _compute_ts_specifiers(from_file: str, to_file: str) -> tuple[str | None, str]:
    """Compute both @/ alias and relative import specifiers for a TS file."""
    to_path = Path(to_file)
    src_path = get_src_path()

    alias = None
    if to_path == src_path or src_path in to_path.parents:
        to_rel_src = to_path.relative_to(src_path)
        alias = "@/" + _strip_ts_ext(str(to_rel_src).replace("\\", "/"))
        if alias.endswith("/index"):
            alias = alias[:-6]
    else:
        logger.debug(
            "Unable to compute TS alias for %s relative to src %s", to_file, src_path
        )

    from_dir = Path(from_file).parent
    relative = os.path.relpath(to_file, from_dir).replace("\\", "/")
    relative = _strip_ts_ext(relative)
    if not relative.startswith("."):
        relative = "./" + relative
    if relative.endswith("/index"):
        relative = relative[:-6]

    return alias, relative


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

    importers = entry.get("importers", set())

    for importer in importers:
        if importer == source_abs:
            continue

        old_alias, old_relative = _compute_ts_specifiers(importer, source_abs)
        new_alias, new_relative = _compute_ts_specifiers(importer, dest_abs)

        replacements = []
        try:
            content = Path(importer).read_text()
        except (OSError, UnicodeDecodeError) as exc:
            log_best_effort_failure(
                logger, f"read importer for TypeScript move {importer}", exc
            )
            continue

        for old_spec, new_spec in [
            (old_alias, new_alias),
            (old_relative, new_relative),
        ]:
            if old_spec is None or new_spec is None or old_spec == new_spec:
                continue
            replacements.extend(_quoted_replacements(content, old_spec, new_spec))

        if replacements:
            changes[importer] = _dedup(replacements)

    return changes


def find_self_replacements(
    source_abs: str,
    dest_abs: str,
    graph: dict,
) -> list[tuple[str, str]]:
    """Compute replacements for the moved file's own relative imports."""
    replacements = []
    entry = graph.get(source_abs)
    if not entry:
        return replacements

    try:
        content = Path(source_abs).read_text()
    except (OSError, UnicodeDecodeError) as exc:
        log_best_effort_failure(
            logger, f"read moved TypeScript source {source_abs}", exc
        )
        return replacements

    for imported_file in entry.get("imports", set()):
        _, old_relative = _compute_ts_specifiers(source_abs, imported_file)
        _, new_relative = _compute_ts_specifiers(dest_abs, imported_file)
        if old_relative == new_relative:
            continue
        replacements.extend(_quoted_replacements(content, old_relative, new_relative))

    return _dedup(replacements)


def _quoted_replacements(content: str, old_spec: str, new_spec: str) -> list[tuple[str, str]]:
    """Replacements for every quoted form of ``old_spec`` present in ``content``."""
    found = []
    for suffix in _SPECIFIER_SUFFIXES:
        for quote in ("'", '"', "`"):
            target = f"{quote}{old_spec}{suffix}{quote}"
            if target in content:
                found.append((target, f"{quote}{new_spec}{suffix}{quote}"))
    return found


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
