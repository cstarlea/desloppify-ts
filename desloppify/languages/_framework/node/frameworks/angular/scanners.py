"""Angular workspace entries and scanners."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.languages._framework.node.js_classes import (
    ClassDecl,
    iter_classes,
    matching,
    split_top_level,
)
from desloppify.languages._framework.node.js_text import code_text

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset({"node_modules", "dist", ".git", ".angular", ".nx", "tmp", "coverage"})
_SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".mts")
_NON_MODULE_MARKERS = (".spec.", ".test.", "/__tests__/", "/__mocks__/")
_DECLARABLES = frozenset({"Component", "Directive", "Pipe"})
# No trailing \s*: the blanked string after the colon is whitespace too.
_RESOURCE_RE = re.compile(r"""\b(?P<key>templateUrl|styleUrl|styleUrls)\s*:""")
_STRING_RE = re.compile(r"""(['"`])([^'"`]+)\1""")
_STANDALONE_RE = re.compile(r"\bstandalone\s*:\s*(true|false)\b")
_ARRAY_RE = re.compile(r"\b(?P<key>declarations|imports)\s*:\s*\[")
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*\Z")


# ── workspace entries ────────────────────────────────────────


def _config_files(package_root: Path) -> Iterator[Path]:
    """angular.json and every Nx project.json in the workspace."""
    angular = package_root / "angular.json"
    if angular.is_file():
        yield angular
    for directory, dirnames, filenames in os.walk(package_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        if "project.json" in filenames:
            yield Path(directory) / "project.json"


def _string_leaves(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _string_leaves(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_leaves(item)


def _target_options(payload: dict) -> Iterator[object]:
    projects = payload.get("projects")
    targets_holders = list(projects.values()) if isinstance(projects, dict) else [payload]
    for project in targets_holders:
        if not isinstance(project, dict):
            continue
        targets = project.get("architect") or project.get("targets")
        if not isinstance(targets, dict):
            continue
        for target in targets.values():
            if not isinstance(target, dict):
                continue
            yield target.get("options")
            configurations = target.get("configurations")
            if isinstance(configurations, dict):
                yield from configurations.values()


def workspace_entries(package_root: Path) -> frozenset[str]:
    """Source files the workspace's build targets name: main, polyfills, server,
    test setup, karma/protractor configs, file replacements."""
    root = package_root.resolve()
    found: set[str] = set()
    for config in _config_files(root):
        try:
            payload = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for options in _target_options(payload):
            for value in _string_leaves(options):
                if not value.endswith(_SOURCE_SUFFIXES) or "*" in value or "://" in value:
                    continue
                relative = value.removeprefix("./").replace("{projectRoot}/", "")
                for base in (root, config.parent):
                    candidate = (base / relative).resolve()
                    if candidate.is_file() and candidate.is_relative_to(root):
                        found.add(candidate.relative_to(root).as_posix())
                        break
    return frozenset(found)


# ── scanners ─────────────────────────────────────────────────


@dataclass(frozen=True)
class _Source:
    path: str
    full: Path
    text: str
    code: str
    classes: tuple[ClassDecl, ...]


def _sources(path: Path) -> Iterator[_Source]:
    for filepath in find_ts_and_js_files(path):
        normalized = "/" + filepath.replace("\\", "/")
        if "/node_modules/" in normalized or filepath.endswith(".d.ts"):
            continue
        if any(marker in normalized for marker in _NON_MODULE_MARKERS):
            continue
        full = Path(filepath) if Path(filepath).is_absolute() else get_project_root() / filepath
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug("Skipping unreadable Angular candidate %s: %s", filepath, exc)
            continue
        if "@angular/" not in text:
            continue
        code = code_text(text)
        yield _Source(filepath, full, text, code, tuple(iter_classes(text, code)))


def _decorator(cls: ClassDecl, names: frozenset[str]):
    return next((d for d in cls.decorators if d.name.rsplit(".", 1)[-1] in names), None)


def scan_missing_component_resources(path: Path) -> tuple[list[dict], int]:
    """``templateUrl``/``styleUrl(s)`` naming a file that doesn't exist (a build error)."""
    entries: list[dict] = []
    scanned = 0
    for source in _sources(path):
        scanned += 1
        for cls in source.classes:
            decorator = _decorator(cls, frozenset({"Component"}))
            if decorator is None or decorator.args is None:
                continue
            start, end = decorator.args
            for match in _RESOURCE_RE.finditer(source.code, start, end):
                value_start = match.end()
                first = value_start
                while first < end and source.code[first].isspace():
                    first += 1
                if source.code.startswith("[", first):
                    value_end = matching(source.code, first)
                else:
                    next_comma = source.code.find(",", value_start, end)
                    value_end = next_comma if next_comma >= 0 else end
                for literal in _STRING_RE.finditer(source.text, value_start, value_end):
                    resource = literal.group(2)
                    if (source.full.parent / resource).exists():
                        continue
                    entries.append(
                        {
                            "file": source.path,
                            "line": source.text.count("\n", 0, literal.start()) + 1,
                            "resource": resource,
                            "key": match.group("key"),
                        }
                    )
    return entries, scanned


def angular_major(package_root: Path) -> int | None:
    try:
        payload = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = payload.get(key) if isinstance(payload, dict) else None
        spec = deps.get("@angular/core") if isinstance(deps, dict) else None
        if isinstance(spec, str):
            match = re.search(r"\d+", spec)
            return int(match.group()) if match else None
    return None


def _standalone(source: _Source, cls: ClassDecl, default: bool | None) -> bool | None:
    decorator = _decorator(cls, _DECLARABLES)
    if decorator is None:
        return None
    if decorator.args is not None:
        match = _STANDALONE_RE.search(source.code, *decorator.args)
        if match:
            return match.group(1) == "true"
    return default


def scan_standalone_mismatches(path: Path) -> tuple[list[dict], int]:
    """Declarables used the wrong way for their standalone flag.

    A standalone component, directive or pipe in an NgModule's
    ``declarations``, or a non-standalone one in an ``imports`` array, is a
    compile error. Angular 19 made standalone the default.
    """
    major = angular_major(path)
    default = None if major is None else major >= 19
    sources = list(_sources(path))
    standalone: dict[str, tuple[bool, str, int]] = {}
    for source in sources:
        for cls in source.classes:
            flag = _standalone(source, cls, default)
            if flag is not None:
                standalone[cls.name] = (flag, source.path, cls.line)
    entries: list[dict] = []
    for source in sources:
        for match in _ARRAY_RE.finditer(source.code):
            open_at = match.end() - 1
            for start, end in split_top_level(source.code, open_at + 1, matching(source.code, open_at)):
                name = source.code[start:end].strip()
                if not _IDENT_RE.match(name) or name not in standalone:
                    continue
                flag, _file, _line = standalone[name]
                key = match.group("key")
                if (key == "declarations") == flag:
                    entries.append(
                        {
                            "file": source.path,
                            "line": source.code.count("\n", 0, start) + 1,
                            "name": name,
                            "key": key,
                            "standalone": flag,
                        }
                    )
    return entries, len(sources)


__all__ = [
    "angular_major",
    "scan_missing_component_resources",
    "scan_standalone_mismatches",
    "workspace_entries",
]
