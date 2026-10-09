"""Fastify scanners and ``@fastify/autoload`` entry points."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.languages._framework.node.js_functions import (
    FunctionLiteral,
    function_at,
)

from .common import SourceFile, source_files

logger = logging.getLogger(__name__)

_MODULES = ("fastify", "fastify-plugin")
_ASYNC_RE = re.compile(r"\basync\b")
_AUTOLOAD_IMPORT_RE = re.compile(r"""['"]@fastify/autoload['"]""")
# dir: path.join(__dirname, 'plugins', 'external'), join(import.meta.dirname, 'routes')
_AUTOLOAD_DIR_RE = re.compile(
    r"""\bdir\s*:\s*(?:path\.)?(?:join|resolve)\s*\(\s*(?:__dirname|import\.meta\.dirname|dirname\s*\([^)]*\))\s*,\s*(?P<parts>(?:['"][^'"]+['"]\s*,?\s*)+)\)"""
)
_PART_RE = re.compile(r"""['"]([^'"]+)['"]""")


def _async_functions(source: SourceFile) -> Iterator[FunctionLiteral]:
    for match in _ASYNC_RE.finditer(source.code):
        function = function_at(source.code, match.start())
        if function is not None and function.is_async and function.body is not None:
            yield function


def scan_fastify_async_with_done(path: Path) -> tuple[list[dict], int]:
    """Async plugins and hooks that also take a ``done`` callback.

    Fastify rejects the mix (FST_ERR_PLUGIN_INVALID_ASYNC_HANDLER and the hook
    equivalent): an async function signals completion by resolving.
    """
    entries: list[dict] = []
    scanned = 0
    for source in source_files(path, _MODULES):
        scanned += 1
        for function in _async_functions(source):
            if len(function.params) >= 2 and function.params[-1] == "done":
                entries.append(
                    {"file": source.path, "line": source.line(function.start)}
                )
    return entries, scanned


def autoload_dirs(package_root: Path) -> frozenset[str]:
    """Directories ``@fastify/autoload`` loads every file from, relative to the package."""
    root = package_root.resolve()
    found: set[str] = set()
    for filepath in find_ts_and_js_files(package_root):
        full = (
            Path(filepath)
            if Path(filepath).is_absolute()
            else get_project_root() / filepath
        )
        if "node_modules" in full.parts:
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _AUTOLOAD_IMPORT_RE.search(text):
            continue
        for match in _AUTOLOAD_DIR_RE.finditer(text):
            parts = [
                p
                for part in _PART_RE.findall(match.group("parts"))
                for p in part.split("/")
            ]
            directory = full.resolve().parent.joinpath(*parts)
            try:
                found.add(directory.relative_to(root).as_posix() + "/")
            except ValueError:
                logger.debug("autoload dir outside package: %s", directory)
    return frozenset(found)


__all__ = [
    "autoload_dirs",
    "scan_fastify_async_with_done",
]
