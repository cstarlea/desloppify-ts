"""The exports a published package exposes through its manifest entry points.

A package's ``exports``, ``main``, ``module`` and ``types`` files are its
public API, and so is everything they re-export, however many barrels sit
between. ``public_exports`` walks those re-export chains by name and returns
every (file, exported name) on them: the entry's own name, each barrel's, and
the declaration's. Nothing inside the repo needs to import these for them to
be used.

Private packages (``"private": true``) publish nothing. Type exports count.
Needs tree-sitter; without it nothing is followed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path

from desloppify.base.discovery.file_paths import rel
from desloppify.languages.typescript.detectors.deps.packages import Package, package_entries
from desloppify.languages.typescript.detectors.deps.reexports import NAMESPACE, module_exports
from desloppify.languages.typescript.detectors.deps.resolver import project_resolver

logger = logging.getLogger(__name__)

_ALL = "*all*"  # every name, ``default`` included (an entry, a namespace)
_STAR = "*star*"  # every name but ``default`` (``export * from``)
Resolve = Callable[[str, str], "str | None"]


def published_entry_files(packages: Iterable[Package], candidates: list[str]) -> set[str]:
    """Absolute public entry files of the packages that aren't private."""
    files: set[str] = set()
    for package in packages:
        if package.manifest.get("private") is True:
            continue
        files |= package_entries(package, candidates).public
    return files


def public_exports(entry_files: Iterable[str], resolve: Resolve) -> set[tuple[str, str]]:
    """Every (absolute file, exported name) the entry files expose."""
    found: set[tuple[str, str]] = set()
    seen: set[tuple[str, str]] = set()
    stack: list[tuple[str, str]] = [(path, _ALL) for path in entry_files]
    while stack:
        path, wanted = stack.pop()
        if (path, wanted) in seen:
            continue
        seen.add((path, wanted))
        summary = module_exports(path, types=True)
        if summary is None:
            continue

        if wanted in (_ALL, _STAR):
            names = summary.local | set(summary.forwarded)
            if wanted == _STAR:
                names.discard("default")
            for spec in summary.stars:
                target = resolve(spec, path)
                if target is not None:
                    stack.append((target, _STAR))
        else:
            names = {wanted}
            if wanted not in summary.local and wanted not in summary.forwarded and wanted != "default":
                for spec in summary.stars:
                    target = resolve(spec, path)
                    if target is not None:
                        stack.append((target, wanted))

        for name in names:
            found.add((path, name))
            forward = summary.forwarded.get(name)
            if forward is None:
                continue
            spec, original = forward
            target = resolve(spec, path)
            if target is not None:
                stack.append((target, _ALL if original == NAMESPACE else original))
    return found


def public_export_names(candidates: list[str], project_root: Path) -> set[tuple[str, str]]:
    """(project-relative file, name) pairs the project's published packages expose."""
    resolver = project_resolver(project_root)

    def resolve(spec: str, from_file: str) -> str | None:
        try:
            return resolver.resolve(spec, from_file)
        except OSError as exc:
            logger.debug("resolve %s from %s: %s", spec, from_file, exc)
            return None

    entries = published_entry_files(resolver.packages, candidates)
    return {(rel(path), name) for path, name in public_exports(entries, resolve)}


__all__ = ["public_export_names", "public_exports", "published_entry_files"]
