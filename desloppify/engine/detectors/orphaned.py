"""Orphaned file detection: files with zero importers that aren't entry points."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.file_paths import rel
from desloppify.base.discovery.file_paths import count_lines, resolve_path

_DUNDER_ALL_RE = re.compile(r"^__all__\s*[:=]", re.MULTILINE)


@dataclass(frozen=True)
class EntryConventions:
    """Files a framework loads by file-system convention, so nothing imports them.

    A framework spec declares these (``FrameworkSpec.entry_conventions``) and
    the language passes them in; a matching file is an entry point. Paths are
    matched relative to a package root, and only for packages that have one
    of ``config_files`` at their root or one of ``dependencies`` in their
    package.json.
    """

    config_files: tuple[str, ...]
    extensions: frozenset[str]
    # A name ending in "/" matches every package in that scope.
    dependencies: tuple[str, ...] = ()
    # Stems that are entry points near the package root: at most ``root_depth``
    # path parts, so 2 is the root or one directory down (``src/``).
    root_stems: frozenset[str] = frozenset()
    root_depth: int = 2
    # Stems that are entry points anywhere beneath a ``route_dir`` segment.
    route_dir: str | None = None
    route_stems: frozenset[str] = frozenset()
    # Every file beneath one of these directory segments is an entry point.
    entry_dirs: frozenset[str] = frozenset()
    # Entry points a package names in its own files (a route config, a
    # loader's directory), relative to the package root. A path ending in
    # "/" covers everything beneath that directory.
    declared_entries: Callable[[Path], frozenset[str]] | None = None

    def applies_to(self, package_root: Path) -> bool:
        """Whether the package at *package_root* uses this framework."""
        if any((package_root / name).exists() for name in self.config_files):
            return True
        if not self.dependencies:
            return False
        names = package_dependency_names(package_root)
        return any(
            name.startswith(dep) if dep.endswith("/") else name == dep
            for dep in self.dependencies
            for name in names
        )

    def is_entry(self, rel_path: str) -> bool:
        """Whether *rel_path* (relative to the package root) is a convention file."""
        path = Path(rel_path)
        if path.suffix not in self.extensions:
            return False
        if path.stem in self.root_stems and len(path.parts) <= self.root_depth:
            return True
        if self.entry_dirs and not self.entry_dirs.isdisjoint(path.parts[:-1]):
            return True
        return (
            self.route_dir is not None
            and path.stem in self.route_stems
            and self.route_dir in path.parts
        )


def package_dependency_names(package_root: Path) -> set[str]:
    """Every dependency name the package.json at *package_root* declares."""
    try:
        payload = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return set()
    if not isinstance(payload, dict):
        return set()
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = payload.get(key)
        if isinstance(deps, dict):
            names.update(str(name) for name in deps)
    return names


@dataclass
class OrphanedDetectionOptions:
    """Optional behavior flags for orphaned-file detection."""

    extra_entry_patterns: list[str] | None = None
    extra_barrel_names: set[str] | None = None
    dynamic_import_finder: Callable[[Path, list[str]], set[str]] | None = None
    alias_resolver: Callable[[str], str] | None = None
    detect_frameworks: bool = True
    # Absolute paths that are entry points (e.g. named by a package manifest).
    entry_files: set[str] | None = None
    # Package directories inside the scan; framework conventions are detected
    # per package and matched relative to it.
    package_roots: list[Path] | None = None
    # File-system conventions of the frameworks the language knows.
    entry_conventions: tuple[EntryConventions, ...] = ()


class _FrameworkConventions:
    """Framework convention checks, detected for the package owning each file."""

    def __init__(
        self,
        scan_path: Path,
        package_roots: list[Path] | None,
        conventions: tuple[EntryConventions, ...],
    ) -> None:
        roots = {scan_path.resolve(), *(Path(r).resolve() for r in package_roots or ())}
        self._roots = sorted(roots, key=lambda p: len(p.parts), reverse=True)
        self._conventions = conventions
        self._detected: dict[Path, tuple[tuple[EntryConventions, ...], frozenset[str]]] = {}

    def is_entry(self, filepath: str) -> bool:
        file_path = Path(resolve_path(filepath))
        root = next((r for r in self._roots if file_path.is_relative_to(r)), None)
        if root is None:
            return False
        if root not in self._detected:
            applicable = tuple(c for c in self._conventions if c.applies_to(root))
            declared: set[str] = set()
            for convention in applicable:
                if convention.declared_entries is not None:
                    declared |= convention.declared_entries(root)
            self._detected[root] = (applicable, frozenset(declared))
        conventions, declared = self._detected[root]
        relative = file_path.relative_to(root).as_posix()
        if relative in declared or any(
            d.endswith("/") and relative.startswith(d) for d in declared
        ):
            return True
        return any(c.is_entry(relative) for c in conventions)


def _has_dunder_all(filepath: str) -> bool:
    """Return True if the file defines ``__all__``, signaling a public API surface."""
    try:
        text = Path(resolve_path(filepath)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _DUNDER_ALL_RE.search(text) is not None


def _is_dynamically_imported(
    filepath: str,
    dynamic_targets: set[str],
    alias_resolver: Callable[[str], str] | None = None,
) -> bool:
    """Check if a file is referenced by any dynamic/side-effect import."""
    r = rel(filepath)
    stem = Path(filepath).stem
    name_no_ext = str(Path(r).with_suffix(""))

    for target in dynamic_targets:
        resolved = alias_resolver(target) if alias_resolver else target
        resolved = resolved.lstrip("./")
        if resolved == name_no_ext or resolved == r:
            return True
        if name_no_ext.endswith("/" + resolved) or name_no_ext.endswith(resolved):
            return True
        if resolved.endswith("/" + stem) or resolved == stem:
            return True
        if resolved.endswith("/" + Path(filepath).name):
            return True

    return False


def detect_orphaned_files(
    path: Path,
    graph: dict,
    extensions: list[str],
    options: OrphanedDetectionOptions | None = None,
) -> tuple[list[dict], int]:
    """Find files with zero importers that aren't known entry points."""
    resolved_options = options or OrphanedDetectionOptions()
    all_entry_patterns = resolved_options.extra_entry_patterns or []
    all_barrel_names = resolved_options.extra_barrel_names or set()
    dynamic_import_finder = resolved_options.dynamic_import_finder
    alias_resolver = resolved_options.alias_resolver

    entry_files = resolved_options.entry_files or set()
    conventions = (
        _FrameworkConventions(
            path, resolved_options.package_roots, resolved_options.entry_conventions
        )
        if resolved_options.detect_frameworks
        else None
    )

    dynamic_targets = (
        dynamic_import_finder(path, extensions) if dynamic_import_finder else set()
    )

    total_files = len(graph)
    entries = []
    for filepath, entry in graph.items():
        if entry["importer_count"] > 0:
            continue

        # Graphs may carry nodes that are not scored source files (e.g. Razor
        # or cshtml views linked into the C# graph for edge resolution). Only
        # files in the scanned extension set are orphan candidates.
        if not any(filepath.endswith(ext) for ext in extensions):
            continue

        r = rel(filepath)

        if any(p in r for p in all_entry_patterns):
            continue

        basename = Path(filepath).name
        if basename in all_barrel_names:
            continue

        if filepath in entry_files:
            continue

        if conventions is not None and conventions.is_entry(filepath):
            continue

        if dynamic_targets and _is_dynamically_imported(
            filepath, dynamic_targets, alias_resolver
        ):
            continue

        if _has_dunder_all(filepath):
            continue

        try:
            loc = count_lines(Path(resolve_path(filepath)))
        except (OSError, UnicodeDecodeError):
            loc = 0

        if loc < 10:
            continue

        entries.append(
            {
                "file": filepath,
                "loc": loc,
                "import_count": entry.get("import_count", 0),
            }
        )

    return sorted(entries, key=lambda e: -e["loc"]), total_files
