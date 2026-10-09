"""Hono scanners and HonoX / Workers entry points."""

from __future__ import annotations

import json
import re
from pathlib import Path

from desloppify.languages._framework.node.js_functions import statement_calls

from .common import route_handlers, source_files

_MODULES = ("hono",)
_RESPONSE_HELPERS = "json|text|html|body|redirect|notFound|newResponse"
_WRANGLER_CONFIGS = ("wrangler.toml", "wrangler.json", "wrangler.jsonc")
_TOML_MAIN_RE = re.compile(r"""^\s*main\s*=\s*(['"])([^'"]+)\1""", re.MULTILINE)
_JSON_MAIN_RE = re.compile(r""""main"\s*:\s*"([^"]+)\"""")
_VITE_CONFIGS = ("vite.config.ts", "vite.config.js", "vite.config.mts", "vite.config.mjs")
_HONOX_PLUGIN_RE = re.compile(r"""['"]honox/vite['"]""")
_HONOX_ENTRY_STEMS = ("app/client", "app/server")
_SOURCE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".mjs")


def scan_hono_unreturned_responses(path: Path) -> tuple[list[dict], int]:
    """Handlers that build a response (``c.json(...)``) without returning it.

    Hono sends what the handler returns; a response built as a bare
    statement is dropped, and the request ends in "Context is not finalized"
    or falls through to the next branch.
    """
    entries: list[dict] = []
    scanned = 0
    for source in source_files(path, _MODULES):
        scanned += 1
        for _call_at, function in route_handlers(source):
            if function.body is None or not function.params or not function.params[0]:
                continue
            context = re.escape(function.params[0])
            pattern = re.compile(rf"\b{context}\.(?P<helper>{_RESPONSE_HELPERS})\s*\(")
            for match in statement_calls(source.code, function.body, pattern):
                entries.append(
                    {
                        "file": source.path,
                        "line": source.line(match.start()),
                        "helper": match.group("helper"),
                    }
                )
                break
    return entries, scanned


def scan_hono_unawaited_next(path: Path) -> tuple[list[dict], int]:
    """Middleware that calls ``next()`` without awaiting or returning it.

    The handlers downstream then finish after the middleware has returned,
    so its code after ``next()`` runs before the response exists.
    """
    entries: list[dict] = []
    scanned = 0
    for source in source_files(path, _MODULES):
        scanned += 1
        for _call_at, function in route_handlers(source):
            if function.body is None or len(function.params) != 2 or not function.params[1]:
                continue
            pattern = re.compile(rf"\b{re.escape(function.params[1])}\s*\(\s*\)")
            for match in statement_calls(source.code, function.body, pattern):
                entries.append({"file": source.path, "line": source.line(match.start())})
                break
    return entries, scanned


def wrangler_main(package_root: Path) -> frozenset[str]:
    """The Worker entry module a wrangler config names (``main``)."""
    for name in _WRANGLER_CONFIGS:
        try:
            text = (package_root / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name.endswith(".toml"):
            match = _TOML_MAIN_RE.search(text)
            main = match.group(2) if match else None
        else:
            try:
                payload = json.loads(text)
                main = payload.get("main") if isinstance(payload, dict) else None
            except ValueError:  # jsonc: comments, trailing commas
                match = _JSON_MAIN_RE.search(text)
                main = match.group(1) if match else None
        if isinstance(main, str) and (package_root / main).is_file():
            return frozenset({Path(main).as_posix().removeprefix("./")})
        return frozenset()
    return frozenset()


def honox_entries(package_root: Path) -> frozenset[str]:
    """HonoX's file-system entries, for a package whose Vite config uses its plugin.

    The Vite config is the signal rather than the dependency, which a
    workspace often hoists to its root package.
    """
    for name in _VITE_CONFIGS:
        try:
            text = (package_root / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _HONOX_PLUGIN_RE.search(text):
            return frozenset()
        found = {"app/routes/", "app/islands/"}
        found.update(
            f"{stem}{ext}"
            for stem in _HONOX_ENTRY_STEMS
            for ext in _SOURCE_EXTENSIONS
            if (package_root / f"{stem}{ext}").is_file()
        )
        return frozenset(found)
    return frozenset()


__all__ = [
    "honox_entries",
    "scan_hono_unawaited_next",
    "scan_hono_unreturned_responses",
    "wrangler_main",
]
