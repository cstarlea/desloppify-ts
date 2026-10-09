"""React Router v7 (framework mode) and Remix scanners.

Lightweight text scanners, run on the package that uses the framework.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.engine.detectors.orphaned import package_dependency_names
from desloppify.languages._framework.node.js_text import strip_js_ts_comments

from .routes_config import ROUTE_MODULE_EXTENSIONS, route_config

logger = logging.getLogger(__name__)

_NON_MODULE_MARKERS = (".test.", ".spec.", ".stories.", "/__tests__/", "/__mocks__/")

# React Router v7 replaced the Remix packages; each maps to its successor.
REMIX_SUCCESSORS: dict[str, str] = {
    "@remix-run/react": "react-router",
    "@remix-run/server-runtime": "react-router",
    "@remix-run/testing": "react-router",
    "@remix-run/router": "react-router",
    "@remix-run/node": "@react-router/node",
    "@remix-run/cloudflare": "@react-router/cloudflare",
    "@remix-run/deno": "react-router",
    "@remix-run/architect": "@react-router/architect",
    "@remix-run/express": "@react-router/express",
    "@remix-run/serve": "@react-router/serve",
    "@remix-run/dev": "@react-router/dev",
    "@remix-run/fs-routes": "@react-router/fs-routes",
    "@remix-run/route-config": "@react-router/dev",
    "@remix-run/routes-option-adapter": "@react-router/remix-routes-option-adapter",
}

_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)(['"])(?P<module>[^'"]+)\1""",
    re.MULTILINE,
)
_HOOKS = {
    "useLoaderData": ("loader", "clientLoader"),
    "useActionData": ("action", "clientAction"),
}
_EXPORT_STAR_RE = re.compile(r"\bexport\s*\*")
_HOOK_CALL_RE = re.compile(r"\b(?P<hook>useLoaderData|useActionData)\s*(?:<[^()]*>)?\s*\(")


def _read(filepath: str) -> str | None:
    full = Path(filepath) if Path(filepath).is_absolute() else get_project_root() / filepath
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("Skipping unreadable React Router candidate %s: %s", filepath, exc)
        return None


def _relative_to(filepath: str, root: Path) -> str | None:
    full = Path(filepath) if Path(filepath).is_absolute() else get_project_root() / filepath
    try:
        return full.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _is_test_file(filepath: str) -> bool:
    normalized = "/" + filepath.replace("\\", "/")
    return any(marker in normalized for marker in _NON_MODULE_MARKERS)


def uses_react_router_v7(package_root: Path) -> bool:
    """Whether the package runs React Router v7 framework mode."""
    return "@react-router/dev" in package_dependency_names(package_root)


def scan_route_config_missing_modules(path: Path) -> tuple[list[dict], int]:
    """Route config entries naming a module file that doesn't exist (a build error)."""
    config = route_config(path)
    if config.config_file is None:
        return [], 0
    config_path = path / config.config_file
    try:
        display = config_path.resolve().relative_to(get_project_root().resolve()).as_posix()
    except ValueError:
        display = config_path.as_posix()
    entries = [
        {"file": display, "line": f.line, "module": f.spec}
        for f in config.files
        if not f.exists
    ]
    return entries, 1


def scan_remix_imports_in_react_router_v7(path: Path) -> tuple[list[dict], int]:
    """Imports of Remix packages in a React Router v7 app.

    v7 replaced them; ``@remix-run/react`` hooks read a different router
    context than the one react-router v7 renders, and the rest are leftovers
    of an unfinished migration.
    """
    if not uses_react_router_v7(path):
        return [], 0
    entries: list[dict] = []
    scanned = 0
    for filepath in find_ts_and_js_files(path):
        if "node_modules" in filepath:
            continue
        content = _read(filepath)
        if content is None:
            continue
        scanned += 1
        code = strip_js_ts_comments(content)
        for match in _IMPORT_RE.finditer(code):
            module = match.group("module")
            package = "/".join(module.split("/")[:2])
            successor = REMIX_SUCCESSORS.get(package)
            if successor is None:
                continue
            entries.append(
                {
                    "file": filepath,
                    "line": code.count("\n", 0, match.start("module")) + 1,
                    "module": package,
                    "successor": successor,
                }
            )
            break
    return entries, scanned


def _route_modules(path: Path) -> set[str]:
    """Files that are route modules, relative to the package root.

    The ones the route config names, plus the file-route conventions every
    file-system router shares: a file directly in ``routes/`` and a
    ``routes/<name>/route.*`` folder module.
    """
    config = route_config(path)
    modules = {f.path for f in config.files if f.exists}
    modules.update(
        f"{config.app_dir}/root{ext}"
        for ext in ROUTE_MODULE_EXTENSIONS
        if (path / config.app_dir / f"root{ext}").is_file()
    )
    routes_dir = Path(config.app_dir) / "routes"
    for ext in ROUTE_MODULE_EXTENSIONS:
        for candidate in (path / routes_dir).glob(f"*{ext}"):
            modules.add((routes_dir / candidate.name).as_posix())
        for candidate in (path / routes_dir).glob(f"*/route{ext}"):
            modules.add((routes_dir / candidate.parent.name / candidate.name).as_posix())
    return modules


def _exports_name(code: str, name: str) -> bool:
    if re.search(
        rf"\bexport\s+(?:async\s+)?(?:function\s*\*?\s*|const\s+|let\s+|var\s+){name}\b", code
    ):
        return True
    for clause in re.finditer(r"\bexport\s*(?:type\s*)?\{([^}]*)\}", code):
        for item in clause.group(1).split(","):
            parts = item.split()
            if parts and parts[-1] == name:
                return True
    return False


def scan_data_hooks_without_export(path: Path) -> tuple[list[dict], int]:
    """Route modules reading loader/action data they never export a function for.

    ``useLoaderData()`` in a route module returns that route's own loader
    data, so without a ``loader``/``clientLoader`` export it is always
    undefined; ``useActionData()`` needs an ``action``/``clientAction``.
    """
    modules = _route_modules(path)
    if not modules:
        return [], 0
    entries: list[dict] = []
    scanned = 0
    for filepath in find_ts_and_js_files(path):
        relative = _relative_to(filepath, path)
        if relative is None or relative not in modules or _is_test_file(relative):
            continue
        content = _read(filepath)
        if content is None:
            continue
        scanned += 1
        code = strip_js_ts_comments(content)
        if _EXPORT_STAR_RE.search(code):
            continue
        reported: set[str] = set()
        for match in _HOOK_CALL_RE.finditer(code):
            hook = match.group("hook")
            if hook in reported or any(_exports_name(code, name) for name in _HOOKS[hook]):
                continue
            reported.add(hook)
            entries.append(
                {
                    "file": filepath,
                    "line": code.count("\n", 0, match.start()) + 1,
                    "hook": hook,
                    "exports": list(_HOOKS[hook]),
                }
            )
    return entries, scanned


__all__ = [
    "REMIX_SUCCESSORS",
    "scan_data_hooks_without_export",
    "scan_remix_imports_in_react_router_v7",
    "scan_route_config_missing_modules",
    "uses_react_router_v7",
]
