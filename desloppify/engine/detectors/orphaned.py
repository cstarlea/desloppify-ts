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

# ---------------------------------------------------------------------------
# React Router / Remix convention files
# ---------------------------------------------------------------------------

# Everything under app/routes/ is a route module, loaded by the framework's
# file-based router and imported by nothing.
_REACT_ROUTER_ROUTE_DIRS: tuple[str, ...] = ("routes",)

# Framework entry points that sit beside the routes directory.
_REACT_ROUTER_CONVENTIONS: set[str] = {
    "root",
    "entry.client",
    "entry.server",
}

_REACT_ROUTER_EXTENSIONS: frozenset[str] = frozenset({".ts", ".tsx", ".js", ".jsx"})

_REACT_ROUTER_CONFIGS: tuple[str, ...] = (
    "react-router.config.js",
    "react-router.config.mjs",
    "react-router.config.ts",
    "remix.config.js",
    "remix.config.mjs",
    "remix.config.ts",
)


def _detect_react_router_project(path: Path) -> bool:
    """Return True if the scan root looks like a React Router or Remix project.

    A config file is the cheap signal. Failing that, the dependency is checked,
    because the framework's own template ships a Vite config rather than a
    react-router.config file.
    """
    for name in _REACT_ROUTER_CONFIGS:
        if (path / name).exists():
            return True

    package_json = path / "package.json"
    if package_json.exists():
        try:
            text = package_json.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return '"@react-router/' in text or '"@remix-run/' in text
    return False


def _is_react_router_convention_entry(rel_path: str) -> bool:
    """Return True if *rel_path* is a React Router / Remix convention file.

    Route modules and the framework entry points have no importers by design —
    the router loads them from the filesystem — so reporting them as orphaned
    is a false positive on every project of this shape.
    """
    p = Path(rel_path)
    if p.suffix not in _REACT_ROUTER_EXTENSIONS:
        return False

    parts = p.parts

    # Any file beneath an app/routes/ (or src/routes/) directory.
    for routes_dir in _REACT_ROUTER_ROUTE_DIRS:
        if routes_dir in parts[:-1]:
            return True

    # root.jsx, entry.client.jsx, entry.server.jsx beside the routes directory.
    # `.stem` only strips the last suffix, so entry.client.jsx stems to
    # "entry.client", which is exactly what is being matched.
    if p.stem in _REACT_ROUTER_CONVENTIONS and len(parts) <= 3:
        return True

    return False


@dataclass(frozen=True)
class EntryConventions:
    """Files a framework loads by file-system convention, so nothing imports them.

    A framework spec declares these (``FrameworkSpec.entry_conventions``) and
    the language passes them in; a matching file is an entry point. Paths are
    matched relative to a package root, and only for packages that have one
    of ``config_files`` at their root.
    """

    config_files: tuple[str, ...]
    extensions: frozenset[str]
    # Stems that are entry points at the package root or one level down (``src/``).
    root_stems: frozenset[str] = frozenset()
    # Stems that are entry points anywhere beneath a ``route_dir`` segment.
    route_dir: str | None = None
    route_stems: frozenset[str] = frozenset()
    # Stems starting with this are entry points beneath ``route_dir`` too
    # (SvelteKit's ``+page``, ``+layout.server``, ``+page@group``).
    route_stem_prefix: str | None = None
    # Every file beneath these package-relative directories is an entry point
    # (Nuxt's auto-imported ``components/``, Astro's ``src/pages/``).
    entry_dirs: tuple[str, ...] = ()
    # Package-relative paths without their extension (``src/content/config``).
    entry_paths: frozenset[str] = frozenset()
    # When set, the package must also list one of these in package.json:
    # a convention of a Vite plugin applies only where the plugin is used.
    dependencies: tuple[str, ...] = ()

    def applies_to(self, package_root: Path) -> bool:
        """Whether the package at *package_root* uses this framework."""
        if not any((package_root / name).exists() for name in self.config_files):
            return False
        return not self.dependencies or bool(
            set(self.dependencies) & _manifest_dependencies(package_root)
        )

    def is_entry(self, rel_path: str) -> bool:
        """Whether *rel_path* (relative to the package root) is a convention file."""
        path = Path(rel_path)
        if path.suffix not in self.extensions:
            return False
        if path.stem in self.root_stems and len(path.parts) <= 2:
            return True
        if self.entry_dirs and rel_path.startswith(tuple(d + "/" for d in self.entry_dirs)):
            return True
        if self.entry_paths and path.with_suffix("").as_posix() in self.entry_paths:
            return True
        if self.route_dir is None or self.route_dir not in path.parts[:-1]:
            return False
        return path.stem in self.route_stems or (
            self.route_stem_prefix is not None and path.stem.startswith(self.route_stem_prefix)
        )


def _manifest_dependencies(package_root: Path) -> set[str]:
    """Every dependency name the package.json at *package_root* declares."""
    try:
        payload = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return set()
    if not isinstance(payload, dict):
        return set()
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = payload.get(key)
        if isinstance(section, dict):
            names.update(str(name) for name in section)
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
    # File-system conventions of the frameworks the language knows (Next.js).
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
        self._detected: dict[Path, tuple[tuple[EntryConventions, ...], bool]] = {}

    def is_entry(self, filepath: str) -> bool:
        file_path = Path(resolve_path(filepath))
        root = next((r for r in self._roots if file_path.is_relative_to(r)), None)
        if root is None:
            return False
        if root not in self._detected:
            self._detected[root] = (
                tuple(c for c in self._conventions if c.applies_to(root)),
                _detect_react_router_project(root),
            )
        conventions, is_react_router = self._detected[root]
        relative = file_path.relative_to(root).as_posix()
        if any(c.is_entry(relative) for c in conventions):
            return True
        return is_react_router and _is_react_router_convention_entry(relative)


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
