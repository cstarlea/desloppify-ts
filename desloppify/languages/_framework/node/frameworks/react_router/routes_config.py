"""React Router v7 route config (``app/routes.ts``): the route modules it names.

Framework mode loads every route module from this config, so the files it
names have no importers. The config is a TypeScript module; the files are
read off its ``route()``/``index()``/``layout()`` calls and ``file:`` keys
without evaluating it, which covers the literal form the docs use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from desloppify.languages._framework.node.js_text import strip_js_ts_comments

ROUTE_MODULE_EXTENSIONS = (".tsx", ".ts", ".jsx", ".js", ".mts", ".mjs")
_CONFIG_NAMES = ("react-router.config.ts", "react-router.config.js", "react-router.config.mjs")
_APP_DIRECTORY_RE = re.compile(r"""\bappDirectory\s*:\s*(['"])([^'"]+)\1""")
_FILE_ARG_RES = (
    # route("path", "file") and route(null, "file")
    re.compile(r"""\broute\s*\(\s*(?:(['"`])[^'"`]*\1|null|undefined)\s*,\s*(['"])(?P<file>[^'"]+)\2"""),
    # index("file"), layout("file", [...])
    re.compile(r"""\b(?:index|layout)\s*\(\s*(['"])(?P<file>[^'"]+)\1"""),
    # { path: "x", file: "file" }
    re.compile(r"""\bfile\s*:\s*(['"])(?P<file>[^'"]+)\1"""),
)


@dataclass(frozen=True)
class RouteFile:
    """One route module the config names."""

    spec: str  # as written, relative to the app directory
    path: str  # relative to the package root
    line: int
    exists: bool


@dataclass(frozen=True)
class RouteConfig:
    app_dir: str  # relative to the package root
    config_file: str | None  # relative to the package root
    files: tuple[RouteFile, ...]


def _app_directory(package_root: Path) -> str:
    for name in _CONFIG_NAMES:
        try:
            text = (package_root / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _APP_DIRECTORY_RE.search(strip_js_ts_comments(text))
        if match:
            return match.group(2).strip("./") or "app"
    return "app"


def _resolve(package_root: Path, app_dir: str, spec: str) -> tuple[str, bool]:
    relative = (Path(app_dir) / spec).as_posix()
    normalized = Path(relative)
    parts: list[str] = []
    for part in normalized.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    relative = "/".join(parts)
    if (package_root / relative).is_file():
        return relative, True
    if not Path(relative).suffix:
        for ext in ROUTE_MODULE_EXTENSIONS:
            if (package_root / f"{relative}{ext}").is_file():
                return f"{relative}{ext}", True
    return relative, False


def route_config(package_root: Path) -> RouteConfig:
    """The route config of the package at *package_root* (empty when it has none)."""
    app_dir = _app_directory(package_root)
    config_file = next(
        (
            f"{app_dir}/routes{ext}"
            for ext in ROUTE_MODULE_EXTENSIONS
            if (package_root / app_dir / f"routes{ext}").is_file()
        ),
        None,
    )
    if config_file is None:
        return RouteConfig(app_dir=app_dir, config_file=None, files=())
    try:
        text = (package_root / config_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return RouteConfig(app_dir=app_dir, config_file=config_file, files=())
    code = strip_js_ts_comments(text)
    found: dict[int, RouteFile] = {}
    for pattern in _FILE_ARG_RES:
        for match in pattern.finditer(code):
            spec = match.group("file")
            path, exists = _resolve(package_root, app_dir, spec)
            line = code.count("\n", 0, match.start("file")) + 1
            found.setdefault(
                match.start("file"),
                RouteFile(spec=spec, path=path, line=line, exists=exists),
            )
    files = tuple(found[k] for k in sorted(found))
    return RouteConfig(app_dir=app_dir, config_file=config_file, files=files)


def declared_route_modules(package_root: Path) -> frozenset[str]:
    """Route modules the config names, relative to the package root."""
    config = route_config(package_root)
    return frozenset(f.path for f in config.files if f.exists)


__all__ = [
    "ROUTE_MODULE_EXTENSIONS",
    "RouteConfig",
    "RouteFile",
    "declared_route_modules",
    "route_config",
]
