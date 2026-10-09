"""Resolution helpers for TypeScript dependency graph extraction."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from desloppify.base.output.fallbacks import log_best_effort_failure

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def load_tsconfig_paths_cached(project_root_str: str) -> dict[str, str]:
    """Return cached tsconfig path mappings for a project root."""
    return parse_tsconfig_paths(Path(project_root_str))


def load_tsconfig_paths(project_root: Path) -> dict[str, str]:
    """Parse tsconfig.json compilerOptions.paths into alias-to-directory mappings."""
    return load_tsconfig_paths_cached(str(project_root.resolve()))


def find_tsconfig_root(scan_path: Path, project_root: Path) -> Path:
    """Find the directory containing tsconfig.json, searching scan_path then project_root.

    When the scan path differs from the project root (e.g. monorepos or
    ``--path`` pointing at a subdirectory), the tsconfig may live alongside
    the scanned code rather than at the workspace root.  We check the scan
    path first, then walk up to (and including) the project root.
    """
    resolved_root = project_root.resolve()
    candidate = scan_path.resolve()

    # Walk from scan_path up to project_root looking for tsconfig.json
    while True:
        for name in ("tsconfig.json", "tsconfig.app.json", "jsconfig.json"):
            if (candidate / name).is_file():
                return candidate
        if candidate == resolved_root or candidate == candidate.parent:
            break
        candidate = candidate.parent

    # Fall back to project_root (load_tsconfig_paths will use its own fallback)
    return resolved_root


_TSCONFIG_NAMES = ("tsconfig.json", "tsconfig.app.json", "jsconfig.json")
_MAX_EXTENDS_DEPTH = 16


def strip_jsonc(text: str) -> str:
    """Remove comments and trailing commas so tsconfig JSONC parses as JSON.

    tsconfig files allow ``//`` and ``/* */`` comments and trailing commas
    (``tsc --init`` generates comments), which ``json.loads`` rejects.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "]}":
                i += 1  # drop trailing comma
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def read_tsconfig(config_path: Path) -> dict[str, Any] | None:
    """Parse a tsconfig/jsconfig file, tolerating JSONC. None if unreadable."""
    try:
        text = config_path.read_text(encoding="utf-8-sig", errors="replace")
        data = json.loads(strip_jsonc(text))
    except (json.JSONDecodeError, OSError) as exc:
        log_best_effort_failure(logger, f"parse TypeScript config file {config_path}", exc)
        return None
    return data if isinstance(data, dict) else None


def _resolve_extends(spec: str, config_dir: Path) -> Path | None:
    """Resolve one ``extends`` entry: a relative path or a package specifier."""
    if spec.startswith((".", "/")):
        candidate = (config_dir / spec).resolve()
        for option in (candidate, Path(f"{candidate}.json"), candidate / "tsconfig.json"):
            if option.is_file():
                return option
        return None
    for directory in (config_dir, *config_dir.parents):
        base = directory / "node_modules" / spec
        for option in (base, Path(f"{base}.json"), base / "tsconfig.json"):
            if option.is_file():
                return option
    return None


def find_nearest_tsconfig(path: Path) -> Path | None:
    """Return the closest TypeScript config that owns ``path``.

    Prefer an application config when both conventional config names exist in
    the same directory. Walking upward keeps scans of monorepo projects scoped
    to that project's config instead of assuming the repository root is a
    single application.
    """
    current = path.resolve()
    if current.is_file():
        current = current.parent

    for directory in (current, *current.parents):
        for config_name in ("tsconfig.app.json", "tsconfig.json"):
            candidate = directory / config_name
            if candidate.is_file():
                return candidate
    return None


def compiler_option(config_path: Path, name: str, depth: int = 0) -> Any:
    """A ``compilerOptions`` value as the config sees it after ``extends``; None if unset."""
    if depth > _MAX_EXTENDS_DEPTH:
        return None
    data = read_tsconfig(config_path)
    if data is None:
        return None
    options = data.get("compilerOptions")
    if isinstance(options, dict) and name in options:
        return options[name]
    extends = data.get("extends")
    specs = [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []
    for spec in reversed(specs):  # later entries override earlier ones
        parent = _resolve_extends(spec, config_path.parent) if isinstance(spec, str) else None
        value = compiler_option(parent, name, depth + 1) if parent is not None else None
        if value is not None:
            return value
    return None


def extends_chain(config_path: Path) -> list[Path]:
    """``config_path`` and the configs it extends, nearest first, through the
    last ``extends`` entry that resolves at each step (the one that wins)."""
    chain = [config_path]
    while len(chain) <= _MAX_EXTENDS_DEPTH:
        data = read_tsconfig(chain[-1])
        extends = data.get("extends") if data is not None else None
        specs = [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []
        parents = [_resolve_extends(spec, chain[-1].parent) for spec in specs if isinstance(spec, str)]
        parent = next((found for found in reversed(parents) if found is not None), None)
        if parent is None or parent in chain:
            break
        chain.append(parent)
    return chain


def traced_compiler_option(
    config_path: Path,
    name: str,
    fallback_bases: Callable[[str], dict[str, Any] | None] | None = None,
    depth: int = 0,
) -> tuple[Any, bool]:
    """``compiler_option``, plus whether the answer is known.

    It isn't known when an ``extends`` the lookup has to read through can't be
    resolved (a package that isn't installed) and ``fallback_bases`` (given the
    specifier, the options that base sets) doesn't know it either.
    """
    if depth > _MAX_EXTENDS_DEPTH:
        return None, False
    data = read_tsconfig(config_path)
    if data is None:
        return None, False
    options = data.get("compilerOptions")
    if isinstance(options, dict) and name in options:
        return options[name], True
    extends = data.get("extends")
    specs = [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []
    for spec in reversed(specs):  # later entries override earlier ones
        if not isinstance(spec, str):
            continue
        parent = _resolve_extends(spec, config_path.parent)
        if parent is not None:
            value, known = traced_compiler_option(parent, name, fallback_bases, depth + 1)
        else:
            base = fallback_bases(spec) if fallback_bases is not None else None
            value, known = (None, False) if base is None else (base.get(name), True)
        if value is not None or not known:
            return value, known
    return None, True


def _effective_paths(
    config_path: Path, depth: int = 0
) -> tuple[dict[str, Any], Path, str | None, Path | None] | None:
    """Return the ``paths`` a config sees after ``extends``.

    Returns (paths, paths_dir, base_url, base_url_dir) where each directory is
    that of the config that defined the setting, as TypeScript resolves them.
    """
    if depth > _MAX_EXTENDS_DEPTH:
        return None
    data = read_tsconfig(config_path)
    if data is None:
        return None

    inherited = None
    extends = data.get("extends")
    specs = [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []
    for spec in specs:  # later entries override earlier ones
        if not isinstance(spec, str):
            continue
        parent = _resolve_extends(spec, config_path.parent)
        if parent is None:
            continue
        parent_result = _effective_paths(parent, depth + 1)
        if parent_result is not None:
            inherited = parent_result if inherited is None else _overlay(inherited, parent_result)

    options = data.get("compilerOptions")
    options = options if isinstance(options, dict) else {}
    own_paths = options.get("paths") if isinstance(options.get("paths"), dict) else None
    own_base = options.get("baseUrl") if isinstance(options.get("baseUrl"), str) else None

    paths, paths_dir, base_url, base_dir = inherited or ({}, config_path.parent, None, None)
    if own_paths is not None:
        paths, paths_dir = own_paths, config_path.parent
    if own_base is not None:
        base_url, base_dir = own_base, config_path.parent
    return paths, paths_dir, base_url, base_dir


def _overlay(base, override):
    paths, paths_dir, base_url, base_dir = base
    o_paths, o_paths_dir, o_base_url, o_base_dir = override
    if o_paths:
        paths, paths_dir = o_paths, o_paths_dir
    if o_base_url is not None:
        base_url, base_dir = o_base_url, o_base_dir
    return paths, paths_dir, base_url, base_dir


def _paths_to_mapping(
    paths: dict[str, Any],
    paths_dir: Path,
    base_url: str | None,
    base_dir: Path | None,
    project_root: Path,
) -> dict[str, str]:
    """Convert tsconfig ``paths`` to alias-prefix → directory (relative to root)."""
    anchor = (base_dir / base_url).resolve() if base_url is not None and base_dir else paths_dir
    result: dict[str, str] = {}
    for alias, targets in paths.items():
        if not isinstance(targets, list):
            continue
        string_targets = [target for target in targets if isinstance(target, str)]
        if not string_targets:
            continue
        resolved = []
        for target in string_targets:
            prefix = target.removesuffix("*")
            absolute = Path(os.path.normpath(anchor / prefix))
            resolved.append((prefix, absolute))
        # TypeScript tries each target in order; prefer one that exists.
        prefix, absolute = next(
            ((p, a) for p, a in resolved if a.exists()),
            resolved[0],
        )
        relative = os.path.relpath(absolute, project_root)
        relative = "" if relative == "." else relative.replace(os.sep, "/")
        if prefix.endswith("/") or prefix in ("", "."):
            relative = f"{relative}/" if relative else ""
        result[alias.removesuffix("*")] = relative
    return result


def _relative_dir(directory: Path, project_root: Path) -> str:
    relative = os.path.relpath(directory, project_root).replace(os.sep, "/")
    return "" if relative == "." else f"{relative}/"


def _config_reference_paths(config_path: Path) -> list[Path]:
    data = read_tsconfig(config_path)
    references = data.get("references") if data else None
    found: list[Path] = []
    for reference in references if isinstance(references, list) else []:
        ref_path = reference.get("path") if isinstance(reference, dict) else None
        if not isinstance(ref_path, str):
            continue
        candidate = (config_path.parent / ref_path).resolve()
        if candidate.is_dir():
            candidate = candidate / "tsconfig.json"
        if candidate.is_file():
            found.append(candidate)
    return found


def parse_tsconfig_paths(project_root: Path) -> dict[str, str]:
    """Parse tsconfig paths from disk. Internal — use ``load_tsconfig_paths``.

    Follows ``extends`` chains (relative and package specifiers) and, for
    solution-style configs, the projects listed in ``references`` (the Vite
    template layout keeps ``paths`` in ``tsconfig.app.json``).
    """
    fallback = {"@/": "src/"}
    root = project_root.resolve()

    for name in _TSCONFIG_NAMES:
        config_path = root / name
        if not config_path.is_file():
            continue
        candidates = [config_path, *_config_reference_paths(config_path)]
        mapping: dict[str, str] = {}
        for candidate in candidates:
            effective = _effective_paths(candidate)
            if effective is None:
                continue
            paths, _paths_dir, base_url, base_dir = effective
            for alias, target in _paths_to_mapping(*effective, root).items():
                mapping.setdefault(alias, target)
            if base_url is not None and base_dir is not None:
                # Bare specifiers also resolve against baseUrl ("components/x").
                # The empty prefix sorts last, so explicit aliases win.
                mapping.setdefault("", _relative_dir((base_dir / base_url).resolve(), root))
        if mapping:
            return mapping

    return fallback


def extract_paths(data: dict[str, Any], base_dir: Path) -> dict[str, str] | None:
    """Extract a paths mapping from one parsed tsconfig (no ``extends``)."""
    compiler_options = data.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        return None
    paths = compiler_options.get("paths")
    if not isinstance(paths, dict):
        return None
    base_url = compiler_options.get("baseUrl")
    base_url = base_url if isinstance(base_url, str) else None
    resolved_dir = base_dir.resolve()
    result = _paths_to_mapping(paths, resolved_dir, base_url, resolved_dir, resolved_dir)
    return result or None


# Extensions a specifier may name, mapped to the sources TypeScript tries for
# it (``moduleResolution: bundler`` / ``nodenext``): ``./a.js`` is ``a.ts``.
_SPECIFIER_SOURCE_MAP: dict[str, tuple[str, ...]] = {
    ".js": (".ts", ".tsx", ".js", ".jsx"),
    ".jsx": (".tsx", ".jsx"),
    ".mjs": (".mts", ".mjs"),
    ".cjs": (".cts", ".cjs"),
}
_TS_SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
# Extensionless specifiers try TypeScript sources first, then JavaScript ones
# (``allowJs``); TypeScript never infers ``.mjs``/``.cjs``.
_EXTENSIONLESS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx")
_INDEX_NAMES = ("index.ts", "index.tsx", "index.mts", "index.cts", "index.js", "index.jsx")
_PACKAGE_ENTRY_FIELDS = ("types", "typings", "source", "module", "main")


def _package_dir_entries(directory: Path) -> Iterator[Path]:
    """Entry files named by ``directory/package.json`` (types/main/...)."""
    manifest = directory / "package.json"
    if not manifest.is_file():
        return
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(data, dict):
        return
    for field in _PACKAGE_ENTRY_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value:
            entry = directory / value
            yield from _file_candidates(entry, allow_directory=False)


def _file_candidates(target: Path, *, allow_directory: bool = True) -> Iterator[Path]:
    suffix = target.suffix
    if suffix in _TS_SOURCE_SUFFIXES:
        yield target
    elif suffix in _SPECIFIER_SOURCE_MAP:
        stem = str(target)[: -len(suffix)]
        for source_suffix in _SPECIFIER_SOURCE_MAP[suffix]:
            yield Path(stem + source_suffix)
    else:
        # ``./styles.css``, ``./data.json`` and extensionless specifiers.
        if suffix:
            yield target
        for source_suffix in _EXTENSIONLESS_SUFFIXES:
            yield Path(str(target) + source_suffix)
    if allow_directory:
        yield from _package_dir_entries(target)
        for name in _INDEX_NAMES:
            yield target / name


def iter_resolve_candidates(target: Path) -> Iterator[Path]:
    """Yield filesystem candidates for a module specifier target, in TS order."""
    seen: set[str] = set()
    for candidate in _file_candidates(target):
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            yield candidate


def resolve_target(target: Path) -> str | None:
    """First existing source file for a specifier target, as an absolute path."""
    for candidate in iter_resolve_candidates(target):
        if candidate.is_file() and not candidate.name.endswith(
            (".d.ts", ".d.mts", ".d.cts")
        ):
            return str(candidate.resolve())
    return None


def resolve_alias(
    module_path: str,
    tsconfig_paths: dict[str, str],
    project_root: Path,
) -> Path | None:
    """Resolve a tsconfig path alias to an absolute path.

    Prefixes are checked longest-first so that ``@components/*`` is preferred
    over ``@/*`` when both could match.
    """
    for prefix in sorted(tsconfig_paths, key=len, reverse=True):
        if module_path.startswith(prefix):
            target_dir = tsconfig_paths[prefix]
            relative = module_path[len(prefix) :]
            return (project_root / target_dir / relative).resolve()
    return None


def specifier_target(
    module_path: str,
    filepath: str,
    tsconfig_paths: dict[str, str],
    project_root: Path,
    *,
    source_root: Path | None = None,
) -> Path | None:
    """Absolute path a specifier points at, before extension/index candidates.

    None for bare package specifiers that no tsconfig alias covers.
    """
    if module_path.startswith("."):
        relative_root = source_root or project_root
        source_dir = (
            Path(filepath).parent
            if Path(filepath).is_absolute()
            else (relative_root / filepath).parent
        )
        return (source_dir / module_path).resolve()
    if module_path.startswith("/"):
        return Path(module_path)
    return resolve_alias(module_path, tsconfig_paths, project_root)
