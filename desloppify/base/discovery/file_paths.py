"""Path resolution and exclusion matching helpers."""

from __future__ import annotations

import contextlib
import errno
import fnmatch
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
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
        return (
            rel_path == normalized
            or rel_path.startswith(normalized + "/")
            or rel_path.startswith(normalized + os.sep)
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


def safe_copy_file(src: str | Path, dst: str | Path) -> None:
    """Copy ``src`` over ``dst`` atomically (copy to a temp file, then rename).

    A reader never sees a half-written ``dst``.
    """
    d = Path(dst)
    d.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d.parent, suffix=".tmp")
    os.close(fd)
    try:
        shutil.copy2(str(src), tmp)
        os.replace(tmp, str(d))
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


# ---------------------------------------------------------------------------
# Exclusive file locks
# ---------------------------------------------------------------------------

_LOCK_RETRY_ERRNOS = frozenset(
    {errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", errno.EACCES)}
)


class LockOrderError(RuntimeError):
    """A thread asked for a lock ranked below one it already holds."""


class _HeldLock:
    """Per-process bookkeeping for one lock file."""

    def __init__(self) -> None:
        self.rlock = threading.RLock()
        self.depth = 0
        self.fd: int | None = None
        self.rank = 0


_held_locks: dict[Path, _HeldLock] = {}
_held_locks_guard = threading.Lock()
_thread_held = threading.local()


def _thread_held_ranks() -> dict[Path, int]:
    held = getattr(_thread_held, "ranks", None)
    if held is None:
        held = {}
        _thread_held.ranks = held
    return held


def _try_os_lock(fd: int) -> None:
    """Take a non-blocking exclusive OS lock on ``fd``; raise OSError if busy."""
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return
    try:
        import fcntl
    except ImportError:  # pragma: no cover - no fcntl on this platform
        # No inter-process lock available; the in-process lock still applies.
        return
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    try:
        import fcntl
    except ImportError:  # pragma: no cover - no fcntl on this platform
        return
    fcntl.flock(fd, fcntl.LOCK_UN)


def _acquire_os_lock(
    lock_path: Path,
    deadline: float | None,
    on_wait: Callable[[], None] | None,
) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    waited = False
    try:
        while True:
            try:
                _try_os_lock(fd)
                return fd
            except OSError as exc:
                if exc.errno not in _LOCK_RETRY_ERRNOS:
                    raise
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"Could not acquire lock {lock_path.name}")
            if not waited and on_wait is not None:
                on_wait()
            waited = True
            time.sleep(0.05)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise


@contextlib.contextmanager
def exclusive_file_lock(
    lock_path: Path,
    *,
    timeout: float | None,
    rank: int = 0,
    on_wait: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Hold an exclusive lock on ``lock_path`` across processes and threads.

    - Re-entrant: a thread that already holds the lock enters again at once,
      so a locked caller can call helpers that lock the same file.
    - Other threads of this process wait on an in-process lock; other
      processes wait on ``flock`` (``msvcrt.locking`` on Windows). Without
      either, only the in-process lock applies.
    - ``timeout`` is in seconds (None waits forever); TimeoutError when it
      runs out. ``on_wait`` is called once if the lock is busy.
    - ``rank`` fixes the lock order: taking a new lock ranked below one this
      thread already holds raises LockOrderError instead of risking a
      deadlock.
    - The lock file stays on disk: deleting it while another process waits
      on it would let two holders in.
    """
    key = Path(os.path.abspath(lock_path))
    held_ranks = _thread_held_ranks()
    if key not in held_ranks:
        higher = [r for r in held_ranks.values() if r > rank]
        if higher:
            raise LockOrderError(
                f"{key.name} (rank {rank}) requested while holding a lock "
                f"of rank {max(higher)}"
            )
    with _held_locks_guard:
        entry = _held_locks.setdefault(key, _HeldLock())

    deadline = None if timeout is None else time.monotonic() + max(timeout, 0.0)
    if not entry.rlock.acquire(blocking=False):
        if on_wait is not None:
            on_wait()
            on_wait = None
        wait = -1.0 if deadline is None else max(deadline - time.monotonic(), 0.0)
        if not entry.rlock.acquire(timeout=wait):
            raise TimeoutError(f"Could not acquire lock {key.name}")
    try:
        if entry.depth == 0:
            entry.fd = _acquire_os_lock(key, deadline, on_wait)
            entry.rank = rank
            held_ranks[key] = rank
        entry.depth += 1
    except BaseException:
        entry.rlock.release()
        raise
    try:
        yield
    finally:
        entry.depth -= 1
        if entry.depth == 0:
            fd, entry.fd = entry.fd, None
            held_ranks.pop(key, None)
            if fd is not None:
                with contextlib.suppress(OSError):
                    _os_unlock(fd)
                with contextlib.suppress(OSError):
                    os.close(fd)
        entry.rlock.release()


__all__ = [
    "LockOrderError",
    "count_lines",
    "exclusive_file_lock",
    "matches_exclusion",
    "normalize_path_separators",
    "rel",
    "resolve_path",
    "resolve_scan_file",
    "safe_copy_file",
    "safe_relpath",
    "safe_write_text",
]
