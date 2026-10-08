"""Path resolution and exclusion matching helpers."""

from __future__ import annotations

import fnmatch
import logging
import os
import tempfile
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root

logger = logging.getLogger(__name__)


def matches_exclusion(rel_path: str, exclusion: str) -> bool:
    """Check if a relative path matches an exclusion pattern."""
    parts = Path(rel_path).parts
    if exclusion in parts:
        return True
    if "*" in exclusion:
        if any(fnmatch.fnmatch(part, exclusion) for part in parts):
            return True
        # Full-path glob match for patterns with directory separators
        # (e.g. "Wan2GP/**" should match "Wan2GP/models/rf.py").
        if "/" in exclusion or os.sep in exclusion:
            normalized_path = rel_path.removeprefix("./")
            if fnmatch.fnmatch(normalized_path, exclusion):
                return True
    if "/" in exclusion or os.sep in exclusion:
        normalized = exclusion.rstrip("/").rstrip(os.sep)
        return rel_path == normalized or rel_path.startswith(normalized + "/") or rel_path.startswith(
            normalized + os.sep
        )
    return False


def normalize_path_separators(path: str) -> str:
    return path.replace("\\", "/")


def safe_relpath(path: str | Path, start: str | Path) -> str:
    try:
        return os.path.relpath(str(path), str(start))
    except ValueError:
        return str(Path(path).resolve())


def rel(path: str | Path, *, project_root: str | Path | None = None) -> str:
    """Return a normalized project-relative path when possible.

    A relative *path* is already relative to the project root (as the source
    finders return it), not to the process cwd.
    """
    root = get_project_root(project_root=project_root)
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        return normalize_path_separators(str(resolved.relative_to(root)))
    except ValueError:
        return normalize_path_separators(safe_relpath(resolved, root))


def resolve_path(filepath: str, *, project_root: str | Path | None = None) -> str:
    """Resolve a filepath to absolute, handling both relative and absolute."""
    p = Path(filepath)
    if p.is_absolute():
        return str(p.resolve())
    return str((get_project_root(project_root=project_root) / filepath).resolve())


def resolve_scan_file(
    filepath: str | Path,
    *,
    scan_root: str | Path | None = None,
    project_root: str | Path | None = None,
) -> Path:
    """Resolve a scan file path with explicit scan-root-first semantics.

    Relative file paths are resolved against ``scan_root`` first (when provided)
    and then against the process project root as a fallback.
    """
    p = Path(filepath)
    if p.is_absolute():
        return p.resolve()

    root = get_project_root(project_root=project_root)
    if scan_root is not None:
        scan_root_path = Path(scan_root)
        scan_root_abs = (
            scan_root_path.resolve()
            if scan_root_path.is_absolute()
            else (root / scan_root_path).resolve()
        )
        scan_candidate = (scan_root_abs / p).resolve()
        if scan_candidate.exists():
            return scan_candidate

    return (root / p).resolve()


def safe_write_text(filepath: str | Path, content: str) -> None:
    """Atomically write text to a file using temp+rename."""
    p = Path(filepath)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(p))
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def corrupted_path(path: Path) -> Path:
    """Return a free ``<name>.corrupted[.N]`` path next to ``path``."""
    base = path.with_name(f"{path.name}.corrupted")
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}.{suffix}")
        suffix += 1
    return candidate


def set_aside_corrupted(path: Path) -> Path | None:
    """Rename an unusable file to ``<name>.corrupted[.N]``; return where it went.

    Returns None (and leaves the file in place) if the rename fails.
    """
    target = corrupted_path(path)
    try:
        path.rename(target)
    except OSError as rename_ex:
        logger.debug("Failed to rename corrupted file %s: %s", path, rename_ex)
        return None
    return target


def count_lines(path: Path) -> int:
    """Count lines in a file without loading full contents into memory."""
    count = 0
    try:
        with path.open("rb") as handle:
            for _ in handle:
                count += 1
    except (OSError, UnicodeDecodeError):
        return 0
    return count


__all__ = [
    "count_lines",
    "matches_exclusion",
    "normalize_path_separators",
    "rel",
    "resolve_path",
    "resolve_scan_file",
    "safe_relpath",
    "safe_write_text",
]
