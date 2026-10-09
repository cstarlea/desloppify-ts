"""The script code of single-file components (``.vue``, ``.svelte``, ``.astro``).

Detectors read a component through its *code view*: the file's text with
everything outside its script blocks replaced by spaces. Line breaks are
kept, so a line or column in the view is the same line and column in the
real file, and issue IDs, ``show``, fixers and review packets all point at
``Component.vue:line`` with no mapping table. Two blocks of one component
(Vue's ``<script>`` and ``<script setup>``, Svelte's module and instance
scripts, Astro's frontmatter and its ``<script>`` tags) share one view.

The scanner reads the markup only as far as it needs to find the blocks:

- Vue: top-level ``<script>`` elements; other top-level blocks
  (``<template>``, ``<style>``, custom blocks such as ``<i18n>``) are
  skipped whole, so a ``<script>`` in a ``<docs>`` block doesn't count.
- Svelte: ``<script>`` elements outside ``<svelte:head>`` and ``<style>``.
- Astro: the ``---`` frontmatter fence and ``<script>`` elements.

Comments are skipped everywhere. Attributes may come in any order, quoted,
unquoted or as ``{expressions}``. A block whose ``lang``/``type`` isn't
JavaScript or TypeScript (``application/ld+json``, ``lang="coffee"``) is not
code; an external ``<script src="...">`` has no code, and its ``src`` is
kept so the import graph can follow it.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path

SFC_SUFFIXES = (".vue", ".svelte", ".astro")

# Characters str.splitlines() breaks on: the view keeps them all, so every
# way of counting lines agrees between the view and the file.
_LINE_BREAKS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85  ")

_TS_LANGS = {
    "ts": "ts",
    "typescript": "ts",
    "tsx": "tsx",
    "js": "js",
    "javascript": "js",
    "jsx": "jsx",
}
_SCRIPT_TYPES = {
    "module": None,
    "text/javascript": "js",
    "application/javascript": "js",
    "text/ecmascript": "js",
    "application/ecmascript": "js",
    "text/typescript": "ts",
    "application/typescript": "ts",
    "text/jsx": "jsx",
    "text/babel": "jsx",
}

_COMMENT_END = "-->"
# Elements whose content is text, not markup.
_RAW_TEXT_ELEMENTS = frozenset({"style", "textarea", "title"})
_TAG_START_RE = re.compile(r"<(!--|/?[A-Za-z][^\s/>]*)")
_FRONTMATTER_OPEN_RE = re.compile(r"\A﻿?\s*---[ \t]*(?:\r\n|\n|\r)")
_FRONTMATTER_CLOSE_RE = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)


@dataclass(frozen=True)
class ScriptBlock:
    """One script block: ``text[start:end]`` is its code."""

    start: int
    end: int
    lang: str  # "ts", "tsx", "js" or "jsx"
    kind: str  # "script", "setup", "module", "frontmatter" or "client"
    src: str | None = None
    attrs: dict[str, str | None] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class SfcCode:
    """A component's script blocks and its code view."""

    text: str
    blocks: tuple[ScriptBlock, ...]

    @cached_property
    def view(self) -> str:
        return code_view(self.text, self.blocks)

    @property
    def grammar(self) -> str:
        """``typescript`` when a block is TS (angle-bracket casts parse), else ``tsx``."""
        langs = {block.lang for block in self.blocks if block.start < block.end}
        return "typescript" if "ts" in langs and not langs & {"tsx", "jsx"} else "tsx"

    @property
    def line_count(self) -> int:
        """Lines of script code (a block's first and last lines count once each)."""
        return sum(
            len(self.text[block.start : block.end].strip("\r\n").splitlines())
            for block in self.blocks
        )

    def covers(self, start: int, end: int) -> bool:
        """Whether ``text[start:end]`` lies inside one block (an empty range may touch its ends)."""
        return any(block.start <= start and end <= block.end for block in self.blocks)


def is_sfc(path: str | Path) -> bool:
    return str(path).lower().endswith(SFC_SUFFIXES)


def _skip_quoted(text: str, i: int) -> int:
    """Index after the string literal (or template literal) starting at ``i``."""
    quote = text[i]
    i += 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        if quote == "`" and text.startswith("${", i):
            i = _skip_braces(text, i + 1)
            continue
        i += 1
    return n


def _skip_braces(text: str, i: int) -> int:
    """Index after the ``{...}`` expression starting at ``i`` (strings and nesting aware)."""
    depth = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            i = _skip_quoted(text, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _parse_tag(text: str, i: int) -> tuple[dict[str, str | None], int, bool]:
    """Attributes of the tag whose name ends just before ``i``.

    Returns ``(attrs, index after '>', self_closing)``; names are lowercased.
    """
    attrs: dict[str, str | None] = {}
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == ">":
            return attrs, i + 1, False
        if text.startswith("/>", i):
            return attrs, i + 2, True
        if ch == "{":  # Svelte/Astro spread or shorthand attribute
            i = _skip_braces(text, i)
            continue
        start = i
        while (
            i < n
            and not text[i].isspace()
            and text[i] not in "=>"
            and not text.startswith("/>", i)
        ):
            if text[i] in "\"'":  # stray quote: treat as part of the name
                i = _skip_quoted(text, i)
                continue
            i += 1
        if i == start:  # a lone "/" or other junk
            i += 1
            continue
        name = text[start:i].lower()
        j = i
        while j < n and text[j].isspace():
            j += 1
        if j < n and text[j] == "=":
            j += 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] in "\"'":
                end = _skip_quoted(text, j)
                attrs[name] = text[j + 1 : max(j + 1, end - 1)]
                i = end
            elif j < n and text[j] == "{":
                end = _skip_braces(text, j)
                attrs[name] = text[j:end]
                i = end
            else:
                start = j
                while j < n and not text[j].isspace() and text[j] != ">":
                    j += 1
                attrs[name] = text[start:j]
                i = j
        else:
            attrs[name] = None
    return attrs, n, False


def _find_close(text: str, name: str, i: int) -> tuple[int, int] | None:
    """``(start, end)`` of the next ``</name>`` from ``i`` (case-insensitive)."""
    match = re.compile(rf"</{re.escape(name)}\s*>", re.IGNORECASE).search(text, i)
    return (match.start(), match.end()) if match else None


def _skip_element(text: str, name: str, i: int) -> int:
    """Index after the close tag matching an element of ``name`` opened before ``i``.

    Same-name elements nest (Vue's ``<template v-if>`` inside ``<template>``);
    comments are skipped. Unclosed runs to the end of the text.
    """
    pattern = re.compile(rf"<!--|<(/?){re.escape(name)}(?=[\s/>])", re.IGNORECASE)
    depth = 1
    n = len(text)
    while i < n:
        match = pattern.search(text, i)
        if match is None:
            return n
        if match.group(0) == "<!--":
            end = text.find(_COMMENT_END, match.end())
            i = n if end == -1 else end + len(_COMMENT_END)
            continue
        if match.group(1):
            close = text.find(">", match.end())
            i = n if close == -1 else close + 1
            depth -= 1
            if depth == 0:
                return i
            continue
        _attrs, i, self_closing = _parse_tag(text, match.end())
        if not self_closing:
            depth += 1
    return n


def _script_lang(attrs: dict[str, str | None], default: str) -> str | None:
    """The block's language, or None when it isn't JavaScript or TypeScript."""
    lang = attrs.get("lang")
    type_ = (attrs.get("type") or "").strip().lower()
    if type_:
        if type_ not in _SCRIPT_TYPES:
            return None
        if _SCRIPT_TYPES[type_] is not None and lang is None:
            return _SCRIPT_TYPES[type_]
    if lang is None:
        return default
    return _TS_LANGS.get(lang.strip().lower())


def _script_kind(attrs: dict[str, str | None], suffix: str) -> str:
    if suffix == ".vue" and "setup" in attrs:
        return "setup"
    if suffix == ".svelte" and (
        "module" in attrs or (attrs.get("context") or "").lower() == "module"
    ):
        return "module"
    if suffix == ".astro":
        return "client"
    return "script"


def _frontmatter(text: str) -> tuple[ScriptBlock | None, int]:
    match = _FRONTMATTER_OPEN_RE.match(text)
    if match is None:
        return None, 0
    close = _FRONTMATTER_CLOSE_RE.search(text, match.end())
    if close is None:
        return None, 0
    return ScriptBlock(match.end(), close.start(), "ts", "frontmatter"), close.end()


def script_blocks(text: str, suffix: str) -> tuple[ScriptBlock, ...]:
    """The script blocks of a ``.vue``/``.svelte``/``.astro`` source, in file order."""
    suffix = suffix.lower()
    blocks: list[ScriptBlock] = []
    i = 0
    if suffix == ".astro":
        front, i = _frontmatter(text)
        if front is not None:
            blocks.append(front)
    n = len(text)
    while i < n:
        match = _TAG_START_RE.search(text, i)
        if match is None:
            break
        token = match.group(1)
        if token == "!--":
            end = text.find(_COMMENT_END, match.end())
            i = n if end == -1 else end + len(_COMMENT_END)
            continue
        if token.startswith("/"):
            i = match.end()
            continue
        name = token.lower()
        attrs, after, self_closing = _parse_tag(text, match.end())
        if name == "script":
            close = None if self_closing else _find_close(text, "script", after)
            content_end = after if close is None else close[0]
            if close is None and not self_closing:
                break  # unterminated: nothing reliable after this point
            # Astro bundles its <script>s as TypeScript; an is:inline one is sent as is.
            default = "ts" if suffix == ".astro" and "is:inline" not in attrs else "js"
            lang = _script_lang(attrs, default)
            if lang is not None:
                src = attrs.get("src")
                blocks.append(
                    ScriptBlock(
                        after,
                        after if src else content_end,
                        lang,
                        _script_kind(attrs, suffix),
                        src or None,
                        attrs,
                    )
                )
            i = after if close is None else close[1]
            continue
        if self_closing:
            i = after
        elif name in _RAW_TEXT_ELEMENTS or (
            suffix == ".svelte" and name == "svelte:head"
        ):
            close = _find_close(text, name, after)
            i = n if close is None else close[1]
        elif suffix == ".vue":
            # Every other top-level element of a Vue SFC is a block of its own.
            i = _skip_element(text, name, after)
        else:
            i = after
    return tuple(blocks)


def code_view(text: str, blocks: tuple[ScriptBlock, ...]) -> str:
    """``text`` with everything outside ``blocks`` blanked; line breaks are kept."""
    out: list[str] = []
    pos = 0
    for block in sorted(blocks, key=lambda b: b.start):
        if block.start < pos:
            continue
        out.append(_blank(text[pos : block.start]))
        out.append(text[block.start : block.end])
        pos = block.end
    out.append(_blank(text[pos:]))
    return "".join(out)


def _blank(chunk: str) -> str:
    if not chunk:
        return ""
    return "".join(ch if ch in _LINE_BREAKS else " " for ch in chunk)


def apply_view_change(
    component: SfcCode, new_view: str, path: str | Path
) -> str | None:
    """The component's text with an edit of its code view carried over.

    A fixer edits the view; each changed span (narrowed to the characters
    that differ) must lie inside one script block, and the result must read
    back as exactly ``new_view``. Otherwise None: the edit would touch the
    markup, so the caller leaves the file alone.
    """
    old_view = component.view
    text = component.text
    old_lines = old_view.splitlines(keepends=True)
    new_lines = new_view.splitlines(keepends=True)
    starts = [0]
    for line in old_lines:
        starts.append(starts[-1] + len(line))
    pieces: list[str] = []
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        start, end = starts[i1], starts[i2]
        if tag == "equal":
            pieces.append(text[start:end])
            continue
        old_chunk = old_view[start:end]
        new_chunk = "".join(new_lines[j1:j2])
        prefix = len(os.path.commonprefix([old_chunk, new_chunk]))
        limit = min(len(old_chunk), len(new_chunk)) - prefix
        suffix = 0
        while suffix < limit and old_chunk[-1 - suffix] == new_chunk[-1 - suffix]:
            suffix += 1
        changed_start, changed_end = start + prefix, end - suffix
        if not component.covers(changed_start, changed_end):
            return None
        pieces.append(text[start:changed_start])
        pieces.append(new_chunk[prefix : len(new_chunk) - suffix])
        pieces.append(text[changed_end:end])
    result = "".join(pieces)
    return result if sfc_code(result, path).view == new_view else None


def sfc_code(text: str, path: str | Path) -> SfcCode:
    """The script blocks of ``text``, a component at ``path``."""
    return SfcCode(text, script_blocks(text, Path(str(path)).suffix))


@lru_cache(maxsize=512)
def _sfc_code_cached(path: str, _mtime_ns: int, _size: int) -> SfcCode | None:
    try:
        text = Path(path).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None
    return sfc_code(text, path)


def read_sfc(path: str | Path) -> SfcCode | None:
    """The component on disk at ``path`` (None when unreadable), cached by mtime."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return _sfc_code_cached(str(path), stat.st_mtime_ns, stat.st_size)


def code_text(text: str, path: str | Path) -> str:
    """What detectors read for a file: the code view of a component, else ``text``."""
    return sfc_code(text, path).view if is_sfc(path) else text


def read_code_text(path: str | Path, *, errors: str = "strict") -> str:
    """Read a source file as detectors see it (see ``code_text``).

    Raises like ``Path.read_text`` (``errors`` as there) so callers keep
    their own handling.
    """
    text = Path(path).read_text(encoding="utf-8", errors=errors)
    return code_text(text, path)


def code_bytes(source: bytes, path: str | Path) -> bytes:
    """``source`` as detectors parse it: a component's code view, encoded."""
    if not is_sfc(path):
        return source
    return code_text(source.decode("utf-8", errors="replace"), path).encode("utf-8")


__all__ = [
    "SFC_SUFFIXES",
    "ScriptBlock",
    "SfcCode",
    "apply_view_change",
    "code_bytes",
    "code_text",
    "code_view",
    "is_sfc",
    "read_code_text",
    "read_sfc",
    "script_blocks",
    "sfc_code",
]
