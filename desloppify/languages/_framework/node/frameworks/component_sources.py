"""Source reading shared by the component-framework scanners (SvelteKit, Nuxt, Astro).

A component (``.vue``/``.svelte``/``.astro``) is read through its code view
(``base/discovery/sfc.py``), so offsets and lines are the real file's.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.sfc import SfcCode, is_sfc, sfc_code
from desloppify.base.discovery.source import find_component_files, find_ts_and_js_files
from desloppify.languages._framework.node.js_functions import FunctionLiteral
from desloppify.languages._framework.node.js_text import code_text

logger = logging.getLogger(__name__)

_NON_MODULE_MARKERS = (
    ".spec.",
    ".test.",
    ".stories.",
    ".story.",
    "/__tests__/",
    "/__mocks__/",
    "/node_modules/",
    "/.svelte-kit/",
    "/.astro/",
)
_IMPORT_RE = re.compile(
    r"""\b(?P<kw>import|export)\s+(?P<clause>[^'";]*?)\s*\bfrom\s*(?P<q>['"])(?P<module>[^'"\n]+)(?P=q)"""
    r"""|\bimport\s*(?P<q2>['"])(?P<bare>[^'"\n]+)(?P=q2)"""
    r"""|\bimport\s*\(\s*(?P<q3>['"])(?P<dynamic>[^'"\n]+)(?P=q3)\s*\)"""
)
_TYPE_SPECIFIER_RE = re.compile(r"^\s*type\s+[\w$]")


@dataclass(frozen=True)
class SourceFile:
    path: str  # as the file finder gives it
    full: Path
    text: str
    code: str  # comments and literals blanked; a component's markup blanked too

    @property
    def name(self) -> str:
        return posixpath.basename(self.path.replace("\\", "/"))

    @cached_property
    def component(self) -> SfcCode | None:
        return sfc_code(self.text, self.path) if is_sfc(self.path) else None

    def line(self, offset: int) -> int:
        return self.text.count("\n", 0, offset) + 1


@dataclass(frozen=True)
class ImportRef:
    offset: int
    module: str
    type_only: bool
    clause: str


def package_sources(
    path: Path, *, scripts: bool = True, components: bool = True
) -> Iterator[SourceFile]:
    """Non-test sources of the package at *path*."""
    files: list[str] = []
    if scripts:
        files.extend(find_ts_and_js_files(path))
    if components:
        files.extend(find_component_files(path))
    for filepath in files:
        normalized = "/" + filepath.replace("\\", "/")
        if any(marker in normalized for marker in _NON_MODULE_MARKERS):
            continue
        full = (
            Path(filepath)
            if Path(filepath).is_absolute()
            else get_project_root() / filepath
        )
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug(
                "Skipping unreadable framework candidate %s: %s", filepath, exc
            )
            continue
        view = sfc_code(text, filepath).view if is_sfc(filepath) else text
        yield SourceFile(filepath, full, text, code_text(view))


def imports(
    source: SourceFile, start: int = 0, end: int | None = None
) -> Iterator[ImportRef]:
    """Static and dynamic imports (and re-exports) in ``source`` between the offsets."""
    end = len(source.text) if end is None else end
    for match in _IMPORT_RE.finditer(source.text, start, end):
        at = match.start()
        # The keyword must be code, not part of a comment or a string.
        if source.code[at : at + 6] != source.text[at : at + 6]:
            continue
        module = match.group("module") or match.group("bare") or match.group("dynamic")
        clause = match.group("clause") or ""
        if match.group("kw") == "export" and not re.match(
            r"\s*(?:type\s+)?(?:\*|\{)", clause
        ):
            continue
        yield ImportRef(at, module, _type_only(clause), clause)


def _type_only(clause: str) -> bool:
    clause = clause.strip()
    if clause.startswith("type ") or clause.startswith("type{"):
        return True
    if clause.startswith("{") and clause.endswith("}"):
        names = [part for part in clause[1:-1].split(",") if part.strip()]
        return bool(names) and all(_TYPE_SPECIFIER_RE.match(part) for part in names)
    return False


def body_span(code: str, function: FunctionLiteral) -> tuple[int, int]:
    """Offsets of a function's body: inside its braces, or an arrow's expression."""
    if function.body is not None:
        return function.body
    arrow = code.find("=>", function.start)
    end = arrow + 2
    depth = 0
    while end < len(code):
        ch = code[end]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch in ";," and depth == 0:
            break
        end += 1
    return arrow + 2, end


def dependency_major(package_root: Path, name: str) -> int | None:
    """The major version a package's manifest asks for *name* (None when not a number)."""
    try:
        payload = json.loads(
            (package_root / "package.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = payload.get(key) if isinstance(payload, dict) else None
        spec = deps.get(name) if isinstance(deps, dict) else None
        if isinstance(spec, str):
            match = re.match(r"\s*[\^~>=v]*\s*(\d+)", spec)
            return int(match.group(1)) if match else None
    return None


__all__ = [
    "ImportRef",
    "SourceFile",
    "body_span",
    "dependency_major",
    "imports",
    "package_sources",
]
