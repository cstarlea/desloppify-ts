"""SvelteKit scanners: server/client boundary, load ``fetch`` and redirects.

Text scanners over the package that depends on ``@sveltejs/kit``.
Components are read through their code view.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from pathlib import Path

from desloppify.languages._framework.node.js_classes import matching
from desloppify.languages._framework.node.js_functions import (
    FunctionLiteral,
    function_at,
)

from ..component_sources import SourceFile, body_span, imports, package_sources

_SCRIPT = r"\.(?:ts|js|mts|mjs)"
# Universal load modules run in the browser too; server ones only on the server.
_UNIVERSAL_ROUTE_RE = re.compile(rf"^\+(?:page|layout)(?:@[^.]*)?{_SCRIPT}$")
_SERVER_ROUTE_RE = re.compile(rf"^\+(?:page|layout)(?:@[^.]*)?\.server{_SCRIPT}$")
_CLIENT_HOOKS_RE = re.compile(rf"^hooks\.client{_SCRIPT}$")
_SERVER_MODULES = ("$env/static/private", "$env/dynamic/private", "$app/server")
_SERVER_FILE_RE = re.compile(r"\.server(?:\.(?:ts|js|mts|mjs))?$")

_LOAD_RE = re.compile(
    r"\bexport\s+(?:(?P<fn>(?:async\s+)?function\s+load\s*\()"
    r"|(?:const|let|var)\s+load\b\s*(?::\s*[\w$.<>\[\], |]+?)?\s*=\s*)"
)
_GLOBAL_FETCH_RE = re.compile(r"(?<![\w$.])fetch\s*\(")
_FETCH_BINDING_RE = re.compile(
    r"\b(?:const|let|var)\s*\{[^}]*(?<![\w$])fetch\b[^}]*\}\s*=|(?<![\w$.])fetch\s*=(?!=)"
    r"|\bfunction\s+fetch\b"
)
_RELATIVE_URL_RE = re.compile(r"""\s*['"`]/(?!/)""")

_KIT_IMPORT_RE = re.compile(r"""\bimport\s*\{(?P<names>[^}]*)\}\s*from\s*['"]@sveltejs/kit['"]""")
_TRY_RE = re.compile(r"\btry\s*\{")
_CATCH_RE = re.compile(r"\s*catch\b\s*(?:\([^)]*\))?\s*\{")
_RETHROWS_RE = re.compile(r"\bthrow\b|\bisRedirect\s*\(")


def _is_client_module(source: SourceFile) -> bool:
    name = source.name
    return (
        name.endswith(".svelte")
        or bool(_UNIVERSAL_ROUTE_RE.match(name))
        or bool(_CLIENT_HOOKS_RE.match(name))
    )


def _server_only(source: SourceFile, module: str) -> bool:
    if module in _SERVER_MODULES or module == "$lib/server" or module.startswith("$lib/server/"):
        return True
    if not module.startswith("."):
        return False
    target = posixpath.normpath(posixpath.join(posixpath.dirname(source.path.replace("\\", "/")), module))
    return "/lib/server/" in f"/{target}/" or bool(_SERVER_FILE_RE.search(target))


def scan_server_imports_in_client(path: Path) -> tuple[list[dict], int]:
    """Browser-side modules importing a server-only one.

    Components, universal ``+page``/``+layout`` modules and the client hooks
    are bundled for the browser; ``$env/*/private``, ``$app/server``,
    ``$lib/server/*`` and ``*.server.*`` modules hold what must stay on the
    server, and SvelteKit refuses to build the import. Type-only imports are
    erased, so they don't count.
    """
    entries: list[dict] = []
    scanned = 0
    for source in package_sources(path):
        if not _is_client_module(source):
            continue
        scanned += 1
        for ref in imports(source):
            if ref.type_only or not _server_only(source, ref.module):
                continue
            entries.append({"file": source.path, "line": source.line(ref.offset), "module": ref.module})
    return entries, scanned


def _load_functions(source: SourceFile) -> Iterator[FunctionLiteral]:
    for match in _LOAD_RE.finditer(source.code):
        start = match.start("fn") if match.group("fn") else match.end()
        function = function_at(source.code, start)
        if function is not None:
            yield function


def _params_text(code: str, function: FunctionLiteral) -> str:
    open_at = code.find("(", function.start)
    if open_at == -1 or (function.body is not None and open_at > function.body[0]):
        return " ".join(function.params)
    return code[open_at + 1 : matching(code, open_at)]


def scan_load_global_fetch(path: Path) -> tuple[list[dict], int]:
    """``load`` functions calling the global ``fetch`` instead of the one they're given.

    In a universal ``+page``/``+layout`` load, the global ``fetch`` runs the
    request again in the browser on hydration (SvelteKit warns about it in
    dev); the provided one is inlined into the page. A server load's global
    ``fetch`` can't take a relative URL, so only that is reported there.
    """
    entries: list[dict] = []
    scanned = 0
    for source in package_sources(path, components=False):
        universal = bool(_UNIVERSAL_ROUTE_RE.match(source.name))
        if not universal and not _SERVER_ROUTE_RE.match(source.name):
            continue
        scanned += 1
        for function in _load_functions(source):
            if re.search(r"(?<![\w$])fetch\b", _params_text(source.code, function)):
                continue
            start, end = body_span(source.code, function)
            if _FETCH_BINDING_RE.search(source.code, start, end):
                continue
            for match in _GLOBAL_FETCH_RE.finditer(source.code, start, end):
                if not universal and not _RELATIVE_URL_RE.match(source.text, match.end()):
                    continue
                entries.append(
                    {
                        "file": source.path,
                        "line": source.line(match.start()),
                        "universal": universal,
                    }
                )
                break
    return entries, scanned


def _imported_names(text: str, wanted: str) -> set[str]:
    names: set[str] = set()
    for match in _KIT_IMPORT_RE.finditer(text):
        for part in match.group("names").split(","):
            pieces = part.split()
            if pieces and pieces[0] == wanted:
                names.add(pieces[-1] if len(pieces) == 3 and pieces[1] == "as" else wanted)
    return names


def scan_redirects_in_try(path: Path) -> tuple[list[dict], int]:
    """``redirect()`` called inside a ``try`` whose ``catch`` swallows it.

    ``redirect`` works by throwing; a ``catch`` that neither rethrows nor
    checks ``isRedirect`` turns the redirect into whatever it does with
    errors.
    """
    entries: list[dict] = []
    scanned = 0
    for source in package_sources(path, components=False):
        if "@sveltejs/kit" not in source.text:
            continue
        names = _imported_names(source.text, "redirect")
        if not names:
            continue
        scanned += 1
        call_re = re.compile(rf"(?<![\w$.])(?:{'|'.join(map(re.escape, sorted(names)))})\s*\(")
        code = source.code
        for match in _TRY_RE.finditer(code):
            try_open = match.end() - 1
            try_close = matching(code, try_open)
            catch = _CATCH_RE.match(code, try_close + 1)
            if catch is None:
                continue
            catch_open = catch.end() - 1
            if _RETHROWS_RE.search(code, catch_open, matching(code, catch_open)):
                continue
            call = call_re.search(code, try_open, try_close)
            if call is not None:
                entries.append({"file": source.path, "line": source.line(call.start())})
    return entries, scanned


__all__ = ["scan_load_global_fetch", "scan_redirects_in_try", "scan_server_imports_in_client"]
