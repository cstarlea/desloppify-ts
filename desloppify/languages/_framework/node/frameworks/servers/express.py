"""Express scanners."""

from __future__ import annotations

import json
import re
from pathlib import Path

from desloppify.engine.detectors.orphaned import package_dependency_names

from .common import route_handlers, source_files

_MODULES = ("express",)
_AWAIT_RE = re.compile(r"\bawait\b")
_HANDLED_RE = re.compile(r"\btry\s*\{|\.catch\s*\(")
_ERROR_PARAM_NAMES = frozenset({"err", "error", "e", "ex", "exception"})
# Packages that make Express 4 forward rejected promises to the error handler.
_PROMISE_AWARE = ("express-async-errors", "express-promise-router", "@awaitjs/express")


def express_major(package_root: Path) -> int | None:
    """Major version of the express dependency the package declares."""
    try:
        payload = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = payload.get(key)
        spec = deps.get("express") if isinstance(deps, dict) else None
        if isinstance(spec, str):
            match = re.search(r"\d+", spec)
            return int(match.group()) if match else None
    return None


def scan_express_unhandled_async_handlers(path: Path) -> tuple[list[dict], int]:
    """Express 4 async handlers that await without catching.

    Express 4 ignores the promise a handler returns, so a rejection never
    reaches the error middleware: the request hangs and the process logs an
    unhandled rejection. Express 5 forwards it.
    """
    major = express_major(path)
    if major is None or major >= 5:
        return [], 0
    if any(dep in package_dependency_names(path) for dep in _PROMISE_AWARE):
        return [], 0
    entries: list[dict] = []
    scanned = 0
    for source in source_files(path, _MODULES):
        scanned += 1
        for call_at, function in route_handlers(source):
            if not function.is_async or function.body is None or len(function.params) < 2:
                continue
            start, end = function.body
            if not _AWAIT_RE.search(source.code, start, end):
                continue
            if _HANDLED_RE.search(source.code, start, end):
                continue
            entries.append({"file": source.path, "line": source.line(call_at)})
    return entries, scanned


def scan_express_misshapen_error_handlers(path: Path) -> tuple[list[dict], int]:
    """``app.use((err, req, res) => ...)``: Express only treats a four-parameter
    function as error middleware, so this one runs as ordinary middleware with
    the request in ``err`` and never sees an error."""
    entries: list[dict] = []
    scanned = 0
    for source in source_files(path, _MODULES):
        scanned += 1
        for call_at, function in route_handlers(source):
            if not source.code.startswith(".use", call_at):
                continue
            if len(function.params) == 3 and function.params[0] in _ERROR_PARAM_NAMES:
                entries.append(
                    {"file": source.path, "line": source.line(call_at), "param": function.params[0]}
                )
    return entries, scanned


__all__ = [
    "express_major",
    "scan_express_misshapen_error_handlers",
    "scan_express_unhandled_async_handlers",
]
