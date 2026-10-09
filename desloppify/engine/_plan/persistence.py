"""Plan persistence — load/save with atomic writes."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from desloppify.base.discovery.file_paths import (
    LockOrderError,
    exclusive_file_lock,
    safe_copy_file,
    safe_write_text,
    set_aside_corrupted,
)
from desloppify.base.exception_sets import CORRUPT_JSON_FILE_EXCEPTIONS
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.engine._plan.refresh_lifecycle import migrate_legacy_phase
from desloppify.engine._plan.schema import (
    PLAN_VERSION,
    PlanModel,
    QuarantinedPlanEntry,
    empty_plan,
    ensure_plan_defaults,
    merge_quarantined_entries,
    validate_plan,
)
from desloppify.engine._state.schema import (
    get_state_dir,
    json_default,
    utc_now,
)

logger = logging.getLogger(__name__)

_PLAN_FILE_SENTINEL = object()
PLAN_FILE = _PLAN_FILE_SENTINEL
_INITIAL_PLAN_FILE = _PLAN_FILE_SENTINEL


@dataclass(frozen=True)
class PlanLoadStatus:
    """Resolved plan load result with degraded-mode signaling."""

    plan: PlanModel | None
    degraded: bool
    error_kind: str | None = None
    recovery: str | None = None
    quarantined: int = 0


def get_plan_file() -> Path:
    """Return the default plan file for the current runtime context."""
    return get_state_dir() / "plan.json"


def _default_plan_file() -> Path:
    """Resolve the effective default plan path.

    If tests monkeypatch ``PLAN_FILE`` in this module, use the patched value.
    """
    if PLAN_FILE != _INITIAL_PLAN_FILE:
        return Path(PLAN_FILE)
    return get_plan_file()


# Lock order: state (10) before plan (20) before progression (30).
PLAN_LOCK_RANK = 20
# How long a load waits for the lock before recovering a corrupt file in
# memory only (leaving the files on disk alone).
_RECOVERY_LOCK_TIMEOUT = 5.0


def plan_lock_path(plan_path: Path) -> Path:
    """Return the lock file guarding ``plan_path``."""
    return plan_path.with_suffix(".lock")


@contextmanager
def plan_lock(
    path: Path | None = None,
    *,
    timeout: float | None = 30.0,
    on_wait: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Acquire exclusive lock on plan file for read-modify-write safety.

    Re-entrant within a thread. When the state lock is also needed, take it
    first. Raises TimeoutError if the lock is not free within ``timeout``.
    """
    plan_path = path or _default_plan_file()
    with exclusive_file_lock(
        plan_lock_path(plan_path),
        timeout=timeout,
        rank=PLAN_LOCK_RANK,
        on_wait=on_wait,
    ):
        yield


# Plan files whose on-disk content did not load cleanly. ``save_plan`` does
# not rotate such a file over ``.bak``, which may be the last good copy.
_unclean_plan_files: set[Path] = set()
_quarantine_warnings_shown: set[tuple[Path, int, int]] = set()


def _rotation_key(path: Path) -> Path:
    return path.absolute()


def _load_validated_plan(plan_path: Path) -> tuple[PlanModel, int]:
    """Load, normalize, and validate one plan file; return it with its quarantine count.

    Malformed entries are moved to ``quarantined_entries``. Raises on anything
    that makes the file as a whole unusable.
    """
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Plan file root must be a JSON object.")

    quarantined: list[QuarantinedPlanEntry] = []
    ensure_plan_defaults(data, quarantine=quarantined)
    migrate_legacy_phase(cast(PlanModel, data))
    validate_plan(data)
    merge_quarantined_entries(data, quarantined)

    version = data["version"]
    if version > PLAN_VERSION:
        logger.warning("Plan file version %d > supported %d.", version, PLAN_VERSION)
        print(
            f"  Warning: Plan file version {version} is newer than supported "
            f"({PLAN_VERSION}). Some features may not work correctly.",
            file=sys.stderr,
        )
    return cast(PlanModel, data), len(quarantined)


def _warn_quarantined(path: Path, count: int) -> None:
    logger.debug("Quarantined %d malformed plan entry(s) from %s", count, path)
    # Commands may load the same file several times; warn once per version.
    try:
        warning_key = (path.absolute(), path.stat().st_mtime_ns, count)
    except OSError:
        warning_key = (path.absolute(), 0, count)
    if warning_key in _quarantine_warnings_shown:
        return
    _quarantine_warnings_shown.add(warning_key)
    print(
        f"  Warning: {count} malformed plan entry(s) in {path.name} were set aside; "
        "they are kept under 'quarantined_entries' in the plan file.",
        file=sys.stderr,
    )


def resolve_plan_load_status(path: Path | None = None) -> PlanLoadStatus:
    """Load a plan with explicit degraded-mode metadata.

    - Malformed entries are quarantined; the rest of the plan loads and the
      load is not degraded.
    - A file that cannot be used at all is renamed to ``.corrupted`` and the
      ``.bak`` copy is loaded (and copied into its place), else the plan
      starts fresh. Both are degraded loads. The rename and restore happen
      under the plan lock; if the lock stays busy, the backup is loaded
      without touching disk.
    - After anything but a clean load, the next ``save_plan`` to this path
      leaves ``.bak`` alone.
    """
    plan_path = path or _default_plan_file()
    rotation_key = _rotation_key(plan_path)
    if not plan_path.exists():
        _unclean_plan_files.discard(rotation_key)
        return PlanLoadStatus(plan=None, degraded=False, error_kind=None, recovery=None)
    try:
        plan, quarantined = _load_validated_plan(plan_path)
    except OSError as exc:
        # Unreadable is not corrupt: nothing on disk changes.
        _unclean_plan_files.add(rotation_key)
        return _load_plan_after_failure(plan_path, exc, set_aside=False)
    except CORRUPT_JSON_FILE_EXCEPTIONS as exc:
        return _recover_corrupt_plan(plan_path, exc)
    return _finish_plan_load(plan_path, plan, quarantined)


def _finish_plan_load(
    plan_path: Path, plan: PlanModel, quarantined: int
) -> PlanLoadStatus:
    rotation_key = _rotation_key(plan_path)
    if quarantined:
        _unclean_plan_files.add(rotation_key)
        _warn_quarantined(plan_path, quarantined)
    else:
        _unclean_plan_files.discard(rotation_key)
    return PlanLoadStatus(
        plan=plan,
        degraded=False,
        error_kind=None,
        recovery=None,
        quarantined=quarantined,
    )


def _recover_corrupt_plan(plan_path: Path, exc: Exception) -> PlanLoadStatus:
    """Recover from a corrupt plan file, touching disk only under the lock.

    Under the lock the file is read again (a concurrent load may already
    have restored it). If the lock is busy, recover in memory only.
    """
    rotation_key = _rotation_key(plan_path)
    try:
        with plan_lock(plan_path, timeout=_RECOVERY_LOCK_TIMEOUT):
            if not plan_path.exists():
                _unclean_plan_files.add(rotation_key)
                return _load_plan_after_failure(plan_path, exc, set_aside=False)
            try:
                plan, quarantined = _load_validated_plan(plan_path)
            except (*CORRUPT_JSON_FILE_EXCEPTIONS, OSError) as again:
                _unclean_plan_files.add(rotation_key)
                return _load_plan_after_failure(plan_path, again, set_aside=True)
            return _finish_plan_load(plan_path, plan, quarantined)
    except (TimeoutError, LockOrderError) as lock_exc:
        logger.warning(
            "Plan lock unavailable while recovering %s (%s); recovering in memory only",
            plan_path,
            lock_exc,
        )
        _unclean_plan_files.add(rotation_key)
        return _load_plan_after_failure(plan_path, exc, set_aside=False)


def _load_plan_after_failure(
    plan_path: Path, exc: Exception, *, set_aside: bool
) -> PlanLoadStatus:
    """Fall back to ``.bak`` or empty after a failed load.

    With ``set_aside`` (only under the plan lock), a corrupt file is renamed
    to ``.corrupted`` and a good backup is copied into its place.
    """
    moved_to: Path | None = None
    if isinstance(exc, OSError):
        # Unreadable is not corrupt: leave the file where it is.
        problem = f"Plan file could not be read ({exc})"
    else:
        problem = f"Plan file corrupted ({exc})"
    if set_aside and not isinstance(exc, OSError):
        moved_to = set_aside_corrupted(plan_path)
        if moved_to is not None:
            problem += f"; moved to {moved_to.name}"

    backup = plan_path.with_suffix(".json.bak")
    if backup.exists():
        try:
            plan, quarantined = _load_validated_plan(backup)
        except (*CORRUPT_JSON_FILE_EXCEPTIONS, OSError) as backup_exc:
            logger.warning(
                "Plan file and backup both failed for %s: %s / %s",
                plan_path,
                exc,
                backup_exc,
            )
            problem += f". Backup {backup.name} is unusable too ({backup_exc})"
        else:
            logger.warning(
                "Plan file load degraded for %s (%s); recovered from backup %s.",
                plan_path,
                exc,
                backup,
            )
            if moved_to is not None:
                # Put the backup in place so later loads in this run (and the
                # next command, if nothing saves) see it, not a missing file.
                try:
                    safe_copy_file(backup, plan_path)
                except OSError as copy_ex:
                    log_best_effort_failure(logger, "restore plan from backup", copy_ex)
            print(
                f"  Warning: {problem}; recovered from backup {backup.name}.",
                file=sys.stderr,
            )
            if quarantined:
                _warn_quarantined(backup, quarantined)
            return PlanLoadStatus(
                plan=plan,
                degraded=True,
                error_kind=exc.__class__.__name__,
                recovery="backup",
                quarantined=quarantined,
            )

    logger.warning(
        "Plan file load degraded for %s (%s); starting fresh.", plan_path, exc
    )
    print(f"  Warning: {problem}; starting fresh.", file=sys.stderr)
    return PlanLoadStatus(
        plan=empty_plan(),
        degraded=True,
        error_kind=exc.__class__.__name__,
        recovery="fresh_start",
    )


def load_plan(path: Path | None = None) -> PlanModel:
    """Load plan from disk, or return empty plan on missing/corruption."""
    status = resolve_plan_load_status(path)
    return status.plan or empty_plan()


def save_plan(plan: PlanModel | dict, path: Path | None = None) -> None:
    """Validate and save plan to disk atomically."""
    ensure_plan_defaults(plan)
    plan["updated"] = utc_now()
    validate_plan(plan)

    plan_path = path or _default_plan_file()
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    content = json.dumps(plan, indent=2, default=json_default) + "\n"

    rotation_key = _rotation_key(plan_path)
    if plan_path.exists() and rotation_key not in _unclean_plan_files:
        backup = plan_path.with_suffix(".json.bak")
        try:
            safe_copy_file(plan_path, backup)
        except OSError as backup_ex:
            log_best_effort_failure(logger, "create plan backup", backup_ex)

    try:
        safe_write_text(plan_path, content)
    except OSError as ex:
        print(f"  Warning: Could not save plan: {ex}", file=sys.stderr)
        raise
    # The file on disk is now one we wrote; it may rotate on the next save.
    _unclean_plan_files.discard(rotation_key)


def plan_path_for_state(state_path: Path) -> Path:
    """Derive plan.json path from a state file path."""
    return state_path.parent / "plan.json"


def has_living_plan(path: Path | None = None) -> bool:
    """Return True if a plan.json exists and has user intent."""
    plan_path = path or _default_plan_file()
    if not plan_path.exists():
        return False
    plan = load_plan(plan_path)
    return bool(
        plan.get("queue_order") or plan.get("overrides") or plan.get("clusters")
    )


__all__ = [
    "PLAN_FILE",
    "PLAN_LOCK_RANK",
    "PlanLoadStatus",
    "get_plan_file",
    "has_living_plan",
    "load_plan",
    "plan_lock",
    "plan_lock_path",
    "plan_path_for_state",
    "resolve_plan_load_status",
    "save_plan",
]
