"""Public path and snippet helpers used by command/runtime code."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from desloppify.base import text_utils as _text_utils
from desloppify.base.runtime_state import RuntimeContext, resolve_runtime_context


def _default_project_root() -> Path:
    """Resolve default project root from current environment and CWD."""
    return Path(os.environ.get("DESLOPPIFY_ROOT", Path.cwd())).resolve()


def get_project_root(
    *,
    project_root: Path | str | None = None,
    runtime: RuntimeContext | None = None,
) -> Path:
    """Return the active project root.

    Priority order:
    1. Explicit ``project_root`` argument
    2. Explicit/ambient ``RuntimeContext.project_root``
    3. Environment/CWD default
    """
    if project_root is not None:
        return Path(project_root).resolve()

    override = resolve_runtime_context(runtime).project_root
    if override is not None:
        return Path(override).resolve()
    return _default_project_root()


def get_state_dir(*, runtime: RuntimeContext | None = None) -> Path:
    """Return the directory desloppify writes its files to.

    That is ``<project_root>/.desloppify``, unless ``--state`` moved it.
    """
    override = resolve_runtime_context(runtime).state_dir
    if override is not None:
        return Path(override).resolve()
    return get_project_root(runtime=runtime) / ".desloppify"


def state_dir_is_external(*, runtime: RuntimeContext | None = None) -> bool:
    """True when ``--state`` moved the state directory out of the project."""
    return get_state_dir(runtime=runtime) != (
        get_project_root(runtime=runtime) / ".desloppify"
    )


def readable_state_file(name: str, *, runtime: RuntimeContext | None = None) -> Path:
    """Path to read the project setting file ``name`` from.

    With an external state directory that has no copy yet, the project's own
    ``.desloppify/<name>`` is read instead, so committed settings still
    apply; saves go to ``get_state_dir()``.
    """
    own = get_state_dir(runtime=runtime) / name
    if own.exists() or not state_dir_is_external(runtime=runtime):
        return own
    project_copy = get_project_root(runtime=runtime) / ".desloppify" / name
    return project_copy if project_copy.exists() else own


def get_default_path(
    *,
    project_root: Path | str | None = None,
    runtime: RuntimeContext | None = None,
) -> Path:
    """Return the legacy ``src/`` fallback scan path.

    Prefer ``get_default_scan_path()`` when the active language's
    ``default_src`` should be honored.
    """
    return get_default_scan_path(
        project_root=project_root,
        runtime=runtime,
        default_src="src",
    )


def get_default_scan_path(
    *,
    project_root: Path | str | None = None,
    runtime: RuntimeContext | None = None,
    default_src: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the canonical default scan path for the active runtime."""
    return get_project_root(project_root=project_root, runtime=runtime) / Path(
        default_src or "src"
    )


class _PathProxy(os.PathLike[str]):
    """Backwards-compatible dynamic path reference."""

    def __init__(self, resolver: Callable[[], Path]) -> None:
        self._resolver = resolver

    def _path(self) -> Path:
        return self._resolver()

    def __fspath__(self) -> str:
        return str(self._path())

    def __str__(self) -> str:
        return str(self._path())

    def __repr__(self) -> str:
        return repr(self._path())

    def __truediv__(self, other: str | os.PathLike[str]) -> Path:
        return self._path() / other

    def __rtruediv__(self, other: str | os.PathLike[str]) -> Path:
        return Path(other) / self._path()

    def __eq__(self, other: object) -> bool:
        return self._path() == other

    def __hash__(self) -> int:
        return hash(self._path())

    def __getattr__(self, name: str) -> object:
        return getattr(self._path(), name)


# Deprecated compatibility exports: prefer get_project_root/get_default_path.
PROJECT_ROOT = _PathProxy(get_project_root)
DEFAULT_PATH = _PathProxy(get_default_path)


def read_code_snippet(
    filepath: str,
    line: int,
    context: int = 1,
    *,
    project_root: Path | str | None = None,
) -> str | None:
    """Read a snippet around a 1-based line number."""
    return _text_utils.read_code_snippet(
        filepath,
        line,
        context,
        project_root=(
            Path(project_root).resolve()
            if project_root is not None
            else get_project_root()
        ),
    )


def get_area(filepath: str, *, min_depth: int = 2) -> str:
    """Derive an area name from a file path (generic: first 2 components)."""
    text = (filepath or "").strip()
    if not text:
        return "(unknown)"
    parts = Path(text).parts
    if not parts:
        return "(unknown)"
    return "/".join(parts[:2]) if len(parts) >= min_depth else parts[0]


__all__ = [
    "PROJECT_ROOT",
    "DEFAULT_PATH",
    "get_area",
    "get_project_root",
    "get_state_dir",
    "get_default_path",
    "get_default_scan_path",
    "read_code_snippet",
    "readable_state_file",
    "state_dir_is_external",
]
