"""NestJS scanners: module wiring the DI container can't catch until runtime.

Text scanners over the package that uses NestJS. Module metadata is read
from every ``controllers: [...]``/``providers: [...]`` array in the package.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.languages._framework.node.js_classes import (
    ClassDecl,
    constructor_params,
    iter_classes,
    matching,
    split_top_level,
)
from desloppify.languages._framework.node.js_text import code_text

logger = logging.getLogger(__name__)

_NON_MODULE_MARKERS = (
    ".spec.",
    ".test.",
    ".e2e-spec.",
    "/__tests__/",
    "/__mocks__/",
    "/test/",
)
_ARRAY_RE = re.compile(r"\b(?P<key>controllers|providers)\s*:\s*\[")
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*\Z")
_USE_CLASS_RE = re.compile(r"\buseClass\s*:\s*([A-Za-z_$][\w$]*)")
_INJECT_PARAM_RE = re.compile(
    r"^\s*@(?:Inject|InjectRepository|InjectModel|InjectConnection|InjectDataSource|InjectEntityManager|InjectQueue|InjectRedis)\b"
)


@dataclass
class _Package:
    classes: list[tuple[str, str, ClassDecl]] = field(
        default_factory=list
    )  # file, code, class
    controllers: set[str] = field(default_factory=set)
    providers: set[str] = field(default_factory=set)
    # A controllers array holding something other than class names (a spread,
    # a call) hides what it registers.
    opaque_controllers: bool = False
    files: int = 0


def _is_test_file(filepath: str) -> bool:
    normalized = "/" + filepath.replace("\\", "/")
    return any(marker in normalized for marker in _NON_MODULE_MARKERS)


def _read(filepath: str) -> str | None:
    full = (
        Path(filepath)
        if Path(filepath).is_absolute()
        else get_project_root() / filepath
    )
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("Skipping unreadable NestJS candidate %s: %s", filepath, exc)
        return None


def _collect(path: Path) -> _Package:
    package = _Package()
    for filepath in find_ts_and_js_files(path):
        if (
            "node_modules" in filepath
            or filepath.endswith(".d.ts")
            or _is_test_file(filepath)
        ):
            continue
        content = _read(filepath)
        if content is None:
            continue
        package.files += 1
        code = code_text(content)
        for cls in iter_classes(content, code):
            package.classes.append((filepath, code, cls))
        for match in _ARRAY_RE.finditer(code):
            open_at = match.end() - 1
            items = split_top_level(code, open_at + 1, matching(code, open_at))
            for start, end in items:
                item = code[start:end].strip()
                if _IDENT_RE.match(item):
                    target = (
                        package.controllers
                        if match.group("key") == "controllers"
                        else package.providers
                    )
                    target.add(item)
                elif match.group("key") == "controllers":
                    package.opaque_controllers = True
                else:
                    package.providers.update(_USE_CLASS_RE.findall(item))
    return package


def scan_unregistered_controllers(path: Path) -> tuple[list[dict], int]:
    """``@Controller`` classes no module lists in ``controllers``: their routes never mount."""
    package = _collect(path)
    if package.opaque_controllers or not package.controllers:
        return [], package.files
    entries = [
        {"file": filepath, "line": cls.line, "name": cls.name}
        for filepath, _code, cls in package.classes
        if cls.has_decorator({"Controller"}) and cls.name not in package.controllers
    ]
    return entries, package.files


def scan_providers_missing_injectable(path: Path) -> tuple[list[dict], int]:
    """Providers with constructor dependencies but no class decorator.

    TypeScript only emits the parameter types Nest resolves dependencies from
    for a decorated class, so the container fails at startup.
    """
    package = _collect(path)
    entries: list[dict] = []
    for filepath, code, cls in package.classes:
        if cls.name not in package.providers or cls.decorators:
            continue
        params = constructor_params(code, cls)
        if not params:
            continue
        if all(_INJECT_PARAM_RE.match(code[a:b]) for a, b in params):
            continue
        entries.append({"file": filepath, "line": cls.line, "name": cls.name})
    return entries, package.files


__all__ = ["scan_providers_missing_injectable", "scan_unregistered_controllers"]
