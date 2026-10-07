"""Workspace packages: name → directory, package entry points, workspace imports.

A monorepo's packages import each other by name (``import { Button } from
'@acme/ui'``). Node resolves those through ``node_modules`` symlinks; this
module resolves them straight to the source files using each package's
``package.json`` (``exports``, ``main``/``types``/...), mapping build output
(``dist/index.js``) back to source (``src/index.ts``) through the package's
tsconfig ``outDir``/``rootDir``.

The same manifests name the files that tooling runs directly: the package's
public entries (``exports``/``main``/...), its ``bin`` scripts and files named
in ``scripts``. Those have no importers by design.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from desloppify.languages.typescript.detectors.deps.resolve import (
    _resolve_extends,
    read_tsconfig,
    resolve_target,
)

_SKIP_DIRS = frozenset({"node_modules", ".git", ".next", ".turbo", "dist", "build", "coverage"})
_MANIFEST_ENTRY_FIELDS = ("types", "typings", "source", "module", "main", "browser")
# Directories build tools commonly emit into, tried when no tsconfig names one.
_OUTPUT_DIRS = ("dist", "distribution", "build", "lib", "out", "output", "esm", "cjs", "types")
_SOURCE_DIRS = ("src", "source", "lib", "")
_DECLARATION_TO_SPECIFIER = ((".d.ts", ".js"), (".d.mts", ".mjs"), (".d.cts", ".cjs"))
_JS_OUTPUT_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx")
_SCRIPT_FILE_RE = re.compile(r"^[\w@.][\w@./-]*$")
_SOURCE_EXTENSION_RE = re.compile(r"\.[cm]?[jt]sx?\Z")
_SCRIPT_SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_MAX_WALK_DEPTH = 8


@dataclass
class Package:
    name: str | None
    directory: Path
    manifest: dict[str, Any]
    # (outDir, rootDir) pairs from the package's tsconfig files.
    output_roots: list[tuple[Path, Path | None]] = field(default_factory=list)


@dataclass
class PackageEntries:
    """Files a package exposes or runs, as absolute resolved paths."""

    public: set[str] = field(default_factory=set)  # exports / main / types / ...
    run: set[str] = field(default_factory=set)  # bin / scripts

    @property
    def all(self) -> set[str]:
        return self.public | self.run


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


# ── workspace discovery ──────────────────────────────────────


def _pnpm_workspace_patterns(path: Path) -> list[str] | None:
    """``packages:`` from pnpm-workspace.yaml, without needing a YAML parser."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    patterns: list[str] = []
    in_packages = False
    for raw in lines:
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace() and not line.startswith("-"):
            key, _, rest = line.partition(":")
            in_packages = key.strip() == "packages"
            rest = rest.strip()
            if in_packages and rest.startswith("["):
                patterns.extend(
                    item.strip().strip("'\"") for item in rest.strip("[]").split(",") if item.strip()
                )
                in_packages = False
            continue
        stripped = line.strip()
        if in_packages and stripped.startswith("-"):
            patterns.append(stripped[1:].strip().strip("'\""))
    return patterns


def _workspace_patterns(directory: Path) -> list[str] | None:
    pnpm = directory / "pnpm-workspace.yaml"
    if pnpm.is_file():
        patterns = _pnpm_workspace_patterns(pnpm)
        if patterns is not None:
            return patterns
    manifest = _read_json(directory / "package.json")
    workspaces = manifest.get("workspaces") if manifest else None
    if isinstance(workspaces, dict):  # yarn: {"packages": [...]}
        workspaces = workspaces.get("packages")
    if isinstance(workspaces, list):
        return [w for w in workspaces if isinstance(w, str)]
    return None


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    for part in pattern.strip("/").split("/"):
        if part == "**":
            out.append("(?:[^/]+/)*")
            continue
        piece = re.escape(part).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        out.append(piece + "/")
    return re.compile("".join(out) + r"\Z")


def _expand_workspace_patterns(root: Path, patterns: list[str]) -> list[Path]:
    includes = [_pattern_regex(p.removeprefix("./")) for p in patterns if not p.startswith("!")]
    excludes = [_pattern_regex(p[1:].removeprefix("./")) for p in patterns if p.startswith("!")]
    if not includes:
        return []
    deep = any("**" in p for p in patterns)
    max_depth = _MAX_WALK_DEPTH if deep else max(p.strip("/").count("/") + 1 for p in patterns)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        relative = os.path.relpath(dirpath, root).replace(os.sep, "/")
        depth = 0 if relative == "." else relative.count("/") + 1
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS] if depth < max_depth else []
        if relative == "." or "package.json" not in filenames:
            continue
        key = relative + "/"
        if any(r.match(key) for r in includes) and not any(r.match(key) for r in excludes):
            found.append(Path(dirpath))
    return sorted(found)


def _output_roots(directory: Path) -> list[tuple[Path, Path | None]]:
    """(outDir, rootDir) declared by the tsconfig files in a package directory."""
    roots: list[tuple[Path, Path | None]] = []
    for config in sorted(directory.glob("tsconfig*.json")):
        out_dir = _compiler_dir(config, "outDir")
        if out_dir is not None:
            roots.append((out_dir, _compiler_dir(config, "rootDir")))
    return roots


def _compiler_dir(config: Path, option: str, depth: int = 0) -> Path | None:
    """A path-valued compilerOption, resolved against the config that set it."""
    if depth > 16:
        return None
    data = read_tsconfig(config)
    if data is None:
        return None
    options = data.get("compilerOptions")
    value = options.get(option) if isinstance(options, dict) else None
    if isinstance(value, str):
        return Path(os.path.normpath(config.parent / value))
    extends = data.get("extends")
    specs = [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []
    for spec in reversed(specs):  # later entries win
        if isinstance(spec, str):
            parent = _resolve_extends(spec, config.parent)
            if parent is not None:
                found = _compiler_dir(parent, option, depth + 1)
                if found is not None:
                    return found
    return None


def _load_package(directory: Path) -> Package | None:
    manifest = _read_json(directory / "package.json")
    if manifest is None:
        return None
    name = manifest.get("name")
    return Package(
        name=name if isinstance(name, str) and name else None,
        directory=directory.resolve(),
        manifest=manifest,
        output_roots=_output_roots(directory),
    )


def discover_packages(scan_path: Path, project_root: Path) -> list[Package]:
    """Packages relevant to a scan: workspace members plus enclosing packages.

    The workspace root is the nearest directory at or above the scan path
    (up to the project root) that declares workspaces. Without one, the
    package that contains the scan path is the only package.
    """
    scan = scan_path.resolve()
    root = project_root.resolve()
    chain = [d for d in (scan, *scan.parents) if d.is_relative_to(root)] or [scan]

    directories: list[Path] = []
    for directory in chain:
        if (directory / "package.json").is_file():
            directories.append(directory)
        patterns = _workspace_patterns(directory)
        if patterns:
            directories.extend(_expand_workspace_patterns(directory, patterns))
            break
        if directory == root:
            break

    packages: list[Package] = []
    seen: set[Path] = set()
    for directory in directories:
        resolved = directory.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        package = _load_package(resolved)
        if package is not None:
            packages.append(package)
    return packages


# ── output → source mapping ──────────────────────────────────


def _as_specifier(path: str) -> str:
    """``dist/index.d.ts`` names the same module as ``dist/index.js``."""
    for declaration, specifier in _DECLARATION_TO_SPECIFIER:
        if path.endswith(declaration):
            return path[: -len(declaration)] + specifier
    return path


def _source_candidates(package: Package, target: str) -> Iterator[Path]:
    """Places the source of a manifest path may live, most specific first."""
    relative = _as_specifier(target.removeprefix("./"))
    directory = package.directory
    absolute = Path(os.path.normpath(directory / relative))
    yield absolute
    for out_dir, root_dir in package.output_roots:
        if absolute.is_relative_to(out_dir):
            rest = absolute.relative_to(out_dir)
            if root_dir is not None:
                yield root_dir / rest
            else:
                for source_dir in _SOURCE_DIRS:
                    yield directory / source_dir / rest
    parts = Path(relative).parts
    skip = 0
    while skip < len(parts) - 1 and parts[skip] in _OUTPUT_DIRS:
        skip += 1
    if skip:
        rest = Path(*parts[skip:])
        for source_dir in _SOURCE_DIRS:
            yield directory / source_dir / rest


def _with_any_source_extension(candidate: Path) -> Iterator[Path]:
    """Bundlers emit ``.mjs``/``.cjs``/``.js`` from any TS source extension."""
    yield candidate
    if candidate.suffix in _JS_OUTPUT_SUFFIXES:
        yield candidate.with_suffix("")


def resolve_package_path(package: Package, target: str) -> str | None:
    """Source file for a path named in a package manifest, or None."""
    for candidate in _source_candidates(package, target):
        for option in _with_any_source_extension(candidate):
            found = resolve_target(option)
            if found is not None:
                return found
    return None


# ── exports ──────────────────────────────────────────────────


def _leaves(value: Any) -> Iterator[str]:
    """String targets of an exports value, in condition order."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)


def _subpath_map(exports: Any) -> dict[str, Any]:
    """Normalize ``exports`` to ``{"./sub": target}``."""
    if isinstance(exports, dict) and any(k.startswith(".") for k in exports):
        return {k: v for k, v in exports.items() if k.startswith(".")}
    if exports is None:
        return {}
    return {".": exports}


def _match_subpath(subpaths: dict[str, Any], subpath: str) -> tuple[Any, str | None] | None:
    """Exports target for a subpath, with the ``*`` substitution if a pattern matched."""
    if subpath in subpaths:
        return subpaths[subpath], None
    best: tuple[int, Any, str | None] | None = None
    for key, value in subpaths.items():
        if "*" in key:
            head, _, tail = key.partition("*")
            if subpath.startswith(head) and subpath.endswith(tail) and len(subpath) >= len(key) - 1:
                match = subpath[len(head) : len(subpath) - len(tail)]
                if best is None or len(head) > best[0]:
                    best = (len(head), value, match)
        elif key.endswith("/") and subpath.startswith(key):  # legacy folder mapping
            if best is None or len(key) > best[0]:
                rest = subpath[len(key) :]
                best = (len(key), [f"{t}{rest}" for t in _leaves(value)], None)
    if best is None:
        return None
    return best[1], best[2]


def resolve_package_subpath(package: Package, subpath: str) -> str | None:
    """Resolve ``"."`` or ``"./button"`` of a package to a source file."""
    exports = package.manifest.get("exports")
    if exports is not None:
        matched = _match_subpath(_subpath_map(exports), subpath)
        if matched is None:
            return None
        value, star = matched
        for leaf in _leaves(value):
            target = leaf.replace("*", star) if star else leaf
            found = resolve_package_path(package, target)
            if found is not None:
                return found
        return None
    if subpath == ".":
        for field_name in _MANIFEST_ENTRY_FIELDS:
            value = package.manifest.get(field_name)
            if isinstance(value, str) and value:
                found = resolve_package_path(package, value)
                if found is not None:
                    return found
        return resolve_target(package.directory / "index")
    return resolve_package_path(package, subpath)


class WorkspaceResolver:
    """Resolve bare specifiers that name a workspace package."""

    def __init__(self, packages: list[Package]) -> None:
        self._by_name = {p.name: p for p in packages if p.name}
        self._names = sorted(self._by_name, key=len, reverse=True)

    def __bool__(self) -> bool:
        return bool(self._by_name)

    def resolve(self, specifier: str) -> str | None:
        for name in self._names:
            if specifier == name:
                return resolve_package_subpath(self._by_name[name], ".")
            if specifier.startswith(name + "/"):
                return resolve_package_subpath(
                    self._by_name[name], "./" + specifier[len(name) + 1 :]
                )
        return None


# ── entry points ─────────────────────────────────────────────


def _pattern_entries(package: Package, target: str, candidates: list[str]) -> set[str]:
    """Files a wildcard exports target (``./src/*.ts``) can reach."""
    found: set[str] = set()
    for candidate in _source_candidates(package, target):
        text = str(candidate)
        if "*" not in text:
            continue
        head, _, tail = text.partition("*")
        tail = _SOURCE_EXTENSION_RE.sub("", tail.replace("*", ""))
        tail_re = re.compile(re.escape(tail) + r"(?:\.[cm]?[jt]sx?)?\Z")
        for path in candidates:
            if path.startswith(head) and tail_re.search(path[len(head) :]):
                found.add(path)
        if found:
            break
    return found


def _script_files(command: str) -> Iterator[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if token.startswith("-") or token.endswith("/") or "*" in token or "://" in token:
            continue
        if not _SCRIPT_FILE_RE.match(token):
            continue
        if "/" in token or token.endswith(_SCRIPT_SOURCE_SUFFIXES):
            yield token


def package_entries(package: Package, candidates: list[str]) -> PackageEntries:
    """Entry files for one package. *candidates* are the scanned source files."""
    entries = PackageEntries()
    manifest = package.manifest

    for leaf in _leaves(_subpath_map(manifest.get("exports"))):
        if "*" in leaf:
            entries.public |= _pattern_entries(package, leaf, candidates)
            continue
        found = resolve_package_path(package, leaf)
        if found is not None:
            entries.public.add(found)
    for field_name in _MANIFEST_ENTRY_FIELDS:
        value = manifest.get(field_name)
        if isinstance(value, str) and value:
            found = resolve_package_path(package, value)
            if found is not None:
                entries.public.add(found)

    bin_field = manifest.get("bin")
    bins = [bin_field] if isinstance(bin_field, str) else list(_leaves(bin_field)) if isinstance(bin_field, dict) else []
    for value in bins:
        found = resolve_package_path(package, value)
        if found is not None:
            entries.run.add(found)

    scripts = manifest.get("scripts")
    for command in scripts.values() if isinstance(scripts, dict) else []:
        if not isinstance(command, str):
            continue
        for token in _script_files(command):
            found = resolve_package_path(package, token)
            if found is not None:
                entries.run.add(found)
    return entries


def workspace_entries(packages: list[Package], candidates: list[str]) -> PackageEntries:
    combined = PackageEntries()
    for package in packages:
        entries = package_entries(package, candidates)
        combined.public |= entries.public
        combined.run |= entries.run
    return combined


__all__ = [
    "Package",
    "PackageEntries",
    "WorkspaceResolver",
    "discover_packages",
    "package_entries",
    "resolve_package_path",
    "resolve_package_subpath",
    "workspace_entries",
]
