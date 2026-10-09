"""Astro scanners: ``Astro.glob``, client directives on Astro components, server env in client scripts.

Text scanners over the ``.astro`` components of the package that depends
on ``astro``, read through their code view (the frontmatter and the
``<script>`` tags); the markup is read only for component tags.
"""

from __future__ import annotations

import re
from pathlib import Path

from desloppify.base.discovery.sfc import ScriptBlock

from ..component_sources import SourceFile, dependency_major, imports, package_sources

_ASTRO_GLOB_RE = re.compile(r"(?<![\w$.])Astro\.glob\s*\(")
_DEFAULT_IMPORT_RE = re.compile(r"^\s*(?:type\s+)?([A-Z][\w$]*)\s*(?:,|$)")
_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_CLIENT_DIRECTIVE_RE = re.compile(r"(?<![\w:-])client:(load|idle|visible|media|only)\b")
_ENV_READ_RE = re.compile(
    r"(?<![\w$.])import\.meta\.env\s*(?:\?\.|\.)\s*([A-Za-z_$][\w$]*)"
)
# What Vite and Astro put in import.meta.env on the client.
_BUILTIN_ENV = frozenset(
    {"MODE", "DEV", "PROD", "SSR", "BASE_URL", "SITE", "ASSETS_PREFIX"}
)
_SERVER_ENV_MODULES = ("astro:env/server",)
_CONFIG_NAMES = tuple(
    f"astro.config.{ext}" for ext in ("mjs", "ts", "mts", "js", "cjs", "cts")
)


def _astro_components(path: Path):
    for source in package_sources(path, scripts=False):
        if source.name.endswith(".astro") and source.component is not None:
            yield source


def scan_astro_glob(path: Path) -> tuple[list[dict], int]:
    """``Astro.glob()``, deprecated in Astro 5 for ``import.meta.glob`` or content collections."""
    major = dependency_major(path, "astro")
    if major is not None and major < 5:
        return [], 0
    entries: list[dict] = []
    scanned = 0
    for source in _astro_components(path):
        scanned += 1
        matches = list(_ASTRO_GLOB_RE.finditer(source.code))
        if matches:
            entries.append(
                {
                    "file": source.path,
                    "line": source.line(matches[0].start()),
                    "count": len(matches),
                }
            )
    return entries, scanned


def _tag_end(text: str, i: int) -> int:
    """Index after the ``>`` closing the tag whose attributes start at ``i``."""
    depth = 0
    quote = ""
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'`" and depth == 0:
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == ">" and depth == 0:
            return i + 1
        i += 1
    return n


def _markup_spans(text: str, blocks: tuple[ScriptBlock, ...]) -> list[tuple[int, int]]:
    spans = []
    pos = 0
    for block in sorted(blocks, key=lambda b: b.start):
        if block.kind == "frontmatter":
            pos = max(pos, text.find("---", block.end) + 3)
            continue
        spans.append((pos, block.start))
        pos = max(
            pos, text.find(">", block.end) + 1 if block.src is None else block.end
        )
    spans.append((pos, len(text)))
    return spans


def scan_client_directives_on_astro_components(path: Path) -> tuple[list[dict], int]:
    """A ``client:*`` directive on an Astro component.

    Astro components render to HTML on the server and never hydrate; the
    directive only applies to framework components (React, Vue, Svelte...),
    so here it does nothing, and the interactivity it promises isn't there.
    """
    entries: list[dict] = []
    scanned = 0
    for source in _astro_components(path):
        component = source.component
        front = next((b for b in component.blocks if b.kind == "frontmatter"), None)
        if front is None:
            continue
        scanned += 1
        names = set()
        for ref in imports(source, front.start, front.end):
            if ref.module.endswith(".astro") and not ref.type_only:
                match = _DEFAULT_IMPORT_RE.match(ref.clause)
                if match:
                    names.add(match.group(1))
        if not names:
            continue
        tag_re = re.compile(rf"<({'|'.join(map(re.escape, sorted(names)))})(?=[\s/>])")
        comments = [m.span() for m in _HTML_COMMENT_RE.finditer(source.text)]
        for start, end in _markup_spans(source.text, component.blocks):
            for tag in tag_re.finditer(source.text, start, end):
                if any(a <= tag.start() < b for a, b in comments):
                    continue
                close = _tag_end(source.text, tag.end())
                directive = _CLIENT_DIRECTIVE_RE.search(source.text, tag.end(), close)
                if directive:
                    entries.append(
                        {
                            "file": source.path,
                            "line": source.line(directive.start()),
                            "component": tag.group(1),
                            "directive": directive.group(0),
                        }
                    )
    return entries, scanned


def _custom_env_prefix(path: Path) -> bool:
    for name in _CONFIG_NAMES:
        try:
            if "envPrefix" in (path / name).read_text(
                encoding="utf-8", errors="replace"
            ):
                return True
        except OSError:
            continue
    return False


def _client_scripts(source: SourceFile) -> list[ScriptBlock]:
    return [
        block
        for block in source.component.blocks
        if block.kind == "client"
        and block.src is None
        and "is:inline" not in block.attrs
    ]


def scan_server_env_in_client_scripts(path: Path) -> tuple[list[dict], int]:
    """A client ``<script>`` reading a server-only env var.

    Astro bundles ``<script>`` tags for the browser, where only
    ``PUBLIC_``-prefixed variables (and Vite's built-ins) are defined: any
    other ``import.meta.env.X`` is undefined there, and ``astro:env/server``
    can't be imported at all. Skipped when the config sets its own
    ``envPrefix``.
    """
    if _custom_env_prefix(path):
        return [], 0
    entries: list[dict] = []
    scanned = 0
    for source in _astro_components(path):
        blocks = _client_scripts(source)
        if not blocks:
            continue
        scanned += 1
        seen: set[str] = set()
        for block in blocks:
            for match in _ENV_READ_RE.finditer(source.code, block.start, block.end):
                name = match.group(1)
                if name.startswith("PUBLIC_") or name in _BUILTIN_ENV or name in seen:
                    continue
                seen.add(name)
                entries.append(
                    {
                        "file": source.path,
                        "line": source.line(match.start(1)),
                        "name": name,
                    }
                )
            for ref in imports(source, block.start, block.end):
                if ref.module in _SERVER_ENV_MODULES and ref.module not in seen:
                    seen.add(ref.module)
                    entries.append(
                        {
                            "file": source.path,
                            "line": source.line(ref.offset),
                            "name": ref.module,
                        }
                    )
    return entries, scanned


__all__ = [
    "scan_astro_glob",
    "scan_client_directives_on_astro_components",
    "scan_server_env_in_client_scripts",
]
