"""Resolution helpers for TypeScript dependency graph extraction."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from desloppify.base.output.fallbacks import log_best_effort_failure

_RESOLVE_EXTENSIONS = ("", ".ts", ".tsx", "/index.ts", "/index.tsx")
_JS_SPECIFIER_EXTENSIONS = {".js", ".mjs", ".cjs"}
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


def iter_resolve_candidates(target: Path) -> Iterator[Path]:
    """Yield filesystem candidates for a module specifier target."""
    seen: set[str] = set()

    def _emit(candidate: Path) -> Iterator[Path]:
        key = str(candidate)
        if key in seen:
            return
        seen.add(key)
        yield candidate

    if target.suffix in {".ts", ".tsx"}:
        yield from _emit(target)
        return

    if target.suffix in _JS_SPECIFIER_EXTENSIONS:
        stem = target.with_suffix("")
        yield from _emit(Path(str(stem) + ".ts"))
        yield from _emit(Path(str(stem) + ".tsx"))
        yield from _emit(Path(str(stem) + "/index.ts"))
        yield from _emit(Path(str(stem) + "/index.tsx"))
        yield from _emit(target)
        return

    for ext in _RESOLVE_EXTENSIONS:
        yield from _emit(Path(str(target) + ext))


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


def resolve_module(
    module_path: str,
    filepath: str,
    tsconfig_paths: dict[str, str],
    project_root: Path,
    graph: dict[str, dict[str, Any]],
    source_resolved: str,
    *,
    source_root: Path | None = None,
) -> None:
    """Resolve an import specifier and add edges to the graph."""
    target: Path | None = None
    if module_path.startswith("."):
        relative_root = source_root or project_root
        source_dir = (
            Path(filepath).parent
            if Path(filepath).is_absolute()
            else (relative_root / filepath).parent
        )
        target = (source_dir / module_path).resolve()
    else:
        target = resolve_alias(module_path, tsconfig_paths, project_root)

    if target is None:
        return

    for candidate in iter_resolve_candidates(target):
        if candidate.is_file():
            target_resolved = str(candidate)
            graph[source_resolved]["imports"].add(target_resolved)
            graph[target_resolved]["importers"].add(source_resolved)
            break
