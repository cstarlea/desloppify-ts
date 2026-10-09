"""Shared plumbing for the Node server-framework scanners (Express, Hono, Fastify)."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.languages._framework.node.js_functions import (
    FunctionLiteral,
    call_arguments,
    function_at,
)
from desloppify.languages._framework.node.js_text import code_text

logger = logging.getLogger(__name__)

_NON_MODULE_MARKERS = (".spec.", ".test.", ".e2e-spec.", "/__tests__/", "/__mocks__/")
# The methods that register routes and middleware on an app or router.
ROUTE_CALL_RE = re.compile(
    r"\.(?:get|post|put|patch|delete|del|all|use|options|head|on|route)\s*\("
)


@dataclass(frozen=True)
class SourceFile:
    path: str  # as find_ts_and_js_files gives it
    text: str
    code: str  # comments and literals blanked

    def line(self, offset: int) -> int:
        return self.code.count("\n", 0, offset) + 1


def _import_re(modules: tuple[str, ...]) -> re.Pattern[str]:
    names = "|".join(re.escape(m) for m in modules)
    return re.compile(rf"""(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)['"](?:{names})(?:/[^'"]*)?['"]""")


def source_files(path: Path, modules: tuple[str, ...]) -> Iterator[SourceFile]:
    """Non-test source files of the package that import one of *modules*."""
    imports = _import_re(modules)
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
            logger.debug("Skipping unreadable server candidate %s: %s", filepath, exc)
            continue
        if not imports.search(text):
            continue
        yield SourceFile(filepath, text, code_text(text))


def route_handlers(source: SourceFile) -> Iterator[tuple[int, FunctionLiteral]]:
    """Function literals passed straight to a route or middleware registration.

    Yields the offset of the registration call with each one. A function
    wrapped in another call (``asyncHandler(async (req, res) => ...)``) is
    not passed straight, so it isn't yielded.
    """
    for match, args in call_arguments(source.code, ROUTE_CALL_RE):
        for start, _end in args:
            function = function_at(source.code, start)
            if function is not None:
                yield match.start(), function


__all__ = ["ROUTE_CALL_RE", "SourceFile", "route_handlers", "source_files"]
