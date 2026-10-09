"""State persistence and migration routines."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import cast

from desloppify.base.exception_sets import (
    CORRUPT_JSON_FILE_EXCEPTIONS,
    PLAN_LOAD_EXCEPTIONS,
)
from desloppify.engine._state import _recompute_stats

__all__ = [
    "STATE_LOCK_RANK",
    "hold_state_lock",
    "load_state",
    "save_state",
    "state_lock",
    "state_lock_path",
]

from desloppify.base.discovery.file_paths import (
    LockOrderError,
    exclusive_file_lock,
    safe_copy_file,
    safe_write_text,
)
from desloppify.base.discovery.file_paths import (
    set_aside_corrupted as _set_aside_corrupted,
)
from desloppify.base.text_utils import is_numeric
from desloppify.engine._plan.persistence import load_plan as load_plan_state
from desloppify.engine._plan.persistence import plan_path_for_state
from desloppify.engine._state.recovery import (
    has_saved_plan_without_scan,
    reconstruct_state_from_saved_plan,
)
from desloppify.engine._state.schema import (
    CURRENT_VERSION,
    QuarantinedWorkItem,
    StateModel,
    empty_state,
    ensure_state_defaults,
    get_state_file,
    json_default,
    scan_source,
    validate_state_invariants,
)
from desloppify.engine.plan_state import PlanLoadStatus

logger = logging.getLogger(__name__)

_STATE_FILE_SENTINEL = object()
STATE_FILE = _STATE_FILE_SENTINEL


# Lock order: state (10) before plan (20) before progression (30).
STATE_LOCK_RANK = 10
# How long a load waits for the lock before recovering a corrupt file in
# memory only (leaving the files on disk alone).
_RECOVERY_LOCK_TIMEOUT = 5.0


def _default_state_file() -> Path:
    """Resolve the default state path, honoring runtime context overrides.

    If tests monkeypatch ``STATE_FILE`` in this module, use that override.
    """
    if STATE_FILE is not _STATE_FILE_SENTINEL:
        return Path(STATE_FILE)
    return get_state_file()


def state_lock_path(state_path: Path) -> Path:
    """Return the lock file guarding ``state_path``."""
    return state_path.with_suffix(".json.lock")


@contextlib.contextmanager
def hold_state_lock(
    path: Path | None = None,
    *,
    timeout: float | None = 30.0,
    on_wait: Callable[[], None] | None = None,
) -> Generator[None, None, None]:
    """Hold the state file's exclusive lock (no load or save).

    Re-entrant within a thread. Raises TimeoutError if it cannot be taken
    within ``timeout`` seconds.
    """
    state_path = path or _default_state_file()
    with exclusive_file_lock(
        state_lock_path(state_path),
        timeout=timeout,
        rank=STATE_LOCK_RANK,
        on_wait=on_wait,
    ):
        yield


# State files whose on-disk content did not load cleanly. ``save_state`` does
# not rotate such a file over ``.bak``, which may be the last good copy.
_unclean_state_files: set[Path] = set()
_quarantine_warnings_shown: set[tuple[Path, int, int]] = set()


def _rotation_key(path: Path) -> Path:
    return path.absolute()


def _load_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("state file root must be a JSON object")
    return data


def _normalize_loaded_state(
    data: object,
    quarantine: list[QuarantinedWorkItem] | None = None,
) -> StateModel:
    if not isinstance(data, dict):
        raise ValueError("state file root must be a JSON object")
    ensure_state_defaults(data, quarantine=quarantine)
    normalized = cast(StateModel, data)
    validate_state_invariants(normalized)
    return normalized


def _read_state_file(path: Path) -> tuple[StateModel, int]:
    """Parse and normalize one state file; return it with its quarantine count.

    Malformed work items are moved to ``quarantined_work_items`` (replacing
    an older entry with the same id). Raises on anything that makes the file
    as a whole unusable.
    """
    data = _load_json(path)
    quarantined: list[QuarantinedWorkItem] = []
    state = _normalize_loaded_state(data, quarantined)
    if quarantined:
        new_ids = {entry["id"] for entry in quarantined if entry["id"] is not None}
        kept = [
            entry
            for entry in state.get("quarantined_work_items", [])
            if not (isinstance(entry, dict) and entry.get("id") in new_ids)
        ]
        state["quarantined_work_items"] = kept + quarantined
    version = state["version"]
    if version > CURRENT_VERSION:
        print(
            "  ⚠ State file version "
            f"{version} is newer than supported ({CURRENT_VERSION}). "
            "Some features may not work correctly.",
            file=sys.stderr,
        )
    return state, len(quarantined)


def _warn_quarantined(path: Path, count: int) -> None:
    logger.debug("Quarantined %d malformed work item(s) from %s", count, path)
    # Commands may load the same file several times; warn once per version.
    try:
        warning_key = (path.absolute(), path.stat().st_mtime_ns, count)
    except OSError:
        warning_key = (path.absolute(), 0, count)
    if warning_key in _quarantine_warnings_shown:
        return
    _quarantine_warnings_shown.add(warning_key)
    print(
        f"  ⚠ {count} malformed work item(s) in {path.name} were set aside; "
        "they are kept under 'quarantined_work_items' in the state file.",
        file=sys.stderr,
    )


def _reconstruct_from_saved_plan_if_available(
    state_path: Path,
    state: StateModel,
) -> StateModel:
    plan_status = _saved_plan_load_status(state_path)
    if plan_status.degraded:
        logger.warning(
            "Saved plan load degraded during state recovery for %s: %s",
            state_path,
            plan_status.error_kind,
        )
        if scan_source(state) == "plan_reconstruction":
            return cast(StateModel, _normalize_loaded_state(empty_state()))
        return state
    plan = plan_status.plan
    if plan is None:
        if scan_source(state) == "plan_reconstruction":
            return cast(StateModel, _normalize_loaded_state(empty_state()))
        return state
    if has_saved_plan_without_scan(state, plan):
        reconstructed = reconstruct_state_from_saved_plan(empty_state(), plan)
        return cast(StateModel, _normalize_loaded_state(reconstructed))
    if scan_source(state) == "plan_reconstruction":
        return cast(StateModel, _normalize_loaded_state(empty_state()))
    return state


def _saved_plan_load_status(state_path: Path) -> PlanLoadStatus:
    plan_path = plan_path_for_state(state_path)
    if not plan_path.exists():
        return PlanLoadStatus(plan=None, degraded=False, error_kind=None)
    try:
        return PlanLoadStatus(
            plan=load_plan_state(plan_path),
            degraded=False,
            error_kind=None,
        )
    except PLAN_LOAD_EXCEPTIONS as exc:
        return PlanLoadStatus(
            plan=None,
            degraded=True,
            error_kind=exc.__class__.__name__,
        )


def load_state(path: Path | None = None) -> StateModel:
    """Load state from disk, degrading gracefully on corruption.

    - Malformed work items are quarantined; the rest of the state loads.
    - A file that cannot be used at all is renamed to ``.corrupted`` and the
      ``.bak`` copy is loaded (and copied into its place), else the state
      starts fresh. The rename and restore happen under the state lock; if
      the lock stays busy, the backup is loaded without touching disk.
    - After anything but a clean load, the next ``save_state`` to this path
      leaves ``.bak`` alone.
    """
    state_path = path or _default_state_file()
    rotation_key = _rotation_key(state_path)
    if not state_path.exists():
        _unclean_state_files.discard(rotation_key)
        plan_path = plan_path_for_state(state_path)
        if plan_path.exists():
            print(
                f"  ⚠ State file missing ({state_path.name}); attempting recovery from {plan_path.name}.",
                file=sys.stderr,
            )
        return _reconstruct_from_saved_plan_if_available(state_path, empty_state())

    try:
        state, quarantined = _read_state_file(state_path)
    except OSError as ex:
        # Unreadable is not corrupt: nothing on disk changes.
        _unclean_state_files.add(rotation_key)
        return _load_state_after_failure(state_path, ex, set_aside=False)
    except CORRUPT_JSON_FILE_EXCEPTIONS as ex:
        return _recover_corrupt_state(state_path, ex)
    return _finish_state_load(state_path, state, quarantined)


def _finish_state_load(
    state_path: Path, state: StateModel, quarantined: int
) -> StateModel:
    rotation_key = _rotation_key(state_path)
    if quarantined:
        _unclean_state_files.add(rotation_key)
        _warn_quarantined(state_path, quarantined)
    else:
        _unclean_state_files.discard(rotation_key)
    return _reconstruct_from_saved_plan_if_available(state_path, state)


def _recover_corrupt_state(state_path: Path, ex: Exception) -> StateModel:
    """Recover from a corrupt state file, touching disk only under the lock.

    Two loads of the same corrupt file must not both rename it and restore
    ``.bak``. Under the lock the file is read again (a concurrent load may
    already have restored it). If the lock is busy, recover in memory only.
    """
    rotation_key = _rotation_key(state_path)
    try:
        with hold_state_lock(state_path, timeout=_RECOVERY_LOCK_TIMEOUT):
            if not state_path.exists():
                _unclean_state_files.add(rotation_key)
                return _load_state_after_failure(state_path, ex, set_aside=False)
            try:
                state, quarantined = _read_state_file(state_path)
            except (*CORRUPT_JSON_FILE_EXCEPTIONS, OSError) as again:
                _unclean_state_files.add(rotation_key)
                return _load_state_after_failure(state_path, again, set_aside=True)
            return _finish_state_load(state_path, state, quarantined)
    except (TimeoutError, LockOrderError) as lock_ex:
        logger.warning(
            "State lock unavailable while recovering %s (%s); "
            "recovering in memory only",
            state_path,
            lock_ex,
        )
        _unclean_state_files.add(rotation_key)
        return _load_state_after_failure(state_path, ex, set_aside=False)


def _load_state_after_failure(
    state_path: Path, ex: Exception, *, set_aside: bool
) -> StateModel:
    """Fall back to ``.bak`` or empty after a failed load.

    With ``set_aside`` (only under the state lock), a corrupt file is renamed
    to ``.corrupted`` and a good backup is copied into its place.
    """
    moved_to: Path | None = None
    if isinstance(ex, OSError):
        # Unreadable is not corrupt: leave the file where it is.
        problem = f"State file could not be read ({ex})"
    else:
        problem = f"State file corrupted ({ex})"
    if set_aside and not isinstance(ex, OSError):
        moved_to = _set_aside_corrupted(state_path)
        if moved_to is not None:
            problem += f"; moved to {moved_to.name}"

    backup = state_path.with_suffix(".json.bak")
    if backup.exists():
        logger.warning(
            "Primary state load failed for %s; attempting backup %s: %s",
            state_path,
            backup,
            ex,
        )
        try:
            backup_state, quarantined = _read_state_file(backup)
        except (*CORRUPT_JSON_FILE_EXCEPTIONS, OSError) as backup_ex:
            logger.warning(
                "Backup state load failed from %s after corruption in %s: %s",
                backup,
                state_path,
                backup_ex,
            )
            problem += f". Backup {backup.name} is unusable too ({backup_ex})"
        else:
            logger.warning(
                "Recovered state from backup %s after primary load failure at %s",
                backup,
                state_path,
            )
            if moved_to is not None:
                # Put the backup in place so later loads in this run (and the
                # next command, if nothing saves) see it, not a missing file.
                try:
                    safe_copy_file(backup, state_path)
                except OSError as copy_ex:
                    logger.debug(
                        "Failed to restore %s from %s: %s", state_path, backup, copy_ex
                    )
            print(f"  ⚠ {problem}. Loaded from {backup.name}.", file=sys.stderr)
            if quarantined:
                _warn_quarantined(backup, quarantined)
            return _reconstruct_from_saved_plan_if_available(state_path, backup_state)

    logger.warning(
        "State file load failed for %s and backup recovery was unavailable. "
        "Falling back to empty state: %s",
        state_path,
        ex,
    )
    print(f"  ⚠ {problem}. Starting fresh.", file=sys.stderr)
    return _reconstruct_from_saved_plan_if_available(state_path, empty_state())


def _coerce_integrity_target(value: object) -> float | None:
    if not is_numeric(value):
        return None
    return max(0.0, min(100.0, float(value)))


def _resolve_integrity_target(
    state: StateModel,
    explicit_target: float | None,
) -> float | None:
    target = _coerce_integrity_target(explicit_target)
    if target is not None:
        return target

    integrity = state.get("subjective_integrity")
    if not isinstance(integrity, dict):
        return None
    return _coerce_integrity_target(integrity.get("target_score"))


def save_state(
    state: StateModel,
    path: Path | None = None,
    *,
    subjective_integrity_target: float | None = None,
) -> None:
    """Recompute stats/score and save to disk atomically."""
    ensure_state_defaults(state)
    _recompute_stats(
        state,
        scan_path=state.get("scan_path"),
        subjective_integrity_target=_resolve_integrity_target(
            state,
            subjective_integrity_target,
        ),
    )
    validate_state_invariants(state)

    state_path = path or _default_state_file()
    state_path.parent.mkdir(parents=True, exist_ok=True)

    serialized_state = {
        key: value for key, value in state.items() if key != "issues"
    }
    serialized_state["work_items"] = dict((state.get("work_items") or state.get("issues", {})))
    content = json.dumps(serialized_state, indent=2, default=json_default) + "\n"

    rotation_key = _rotation_key(state_path)
    if state_path.exists() and rotation_key not in _unclean_state_files:
        backup = state_path.with_suffix(".json.bak")
        try:
            safe_copy_file(state_path, backup)
        except OSError as backup_ex:
            logger.debug(
                "Failed to create state backup %s: %s",
                backup,
                backup_ex,
            )

    try:
        safe_write_text(state_path, content)
    except OSError as ex:
        print(f"  Warning: Could not save state: {ex}", file=sys.stderr)
        raise
    # The file on disk is now one we wrote; it may rotate on the next save.
    _unclean_state_files.discard(rotation_key)


@contextlib.contextmanager
def state_lock(
    path: Path | None = None,
    *,
    timeout: float = 30.0,
    subjective_integrity_target: float | None = None,
) -> Generator[StateModel, None, None]:
    """Context manager that locks the state file for exclusive read-modify-write.

    Acquires an exclusive file lock, reloads state from disk (to pick up the
    latest version), yields it for mutation, then saves on clean exit.

    Usage::

        with state_lock(state_file) as state:
            state["work_items"]["foo"] = "fixed"
        # state is saved automatically on clean exit
    """
    state_path = path or _default_state_file()
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(hold_state_lock(state_path, timeout=timeout))
        except TimeoutError:
            raise TimeoutError(
                f"Could not acquire state lock within {timeout}s. "
                "Another desloppify command may be running."
            ) from None
        # Reload state inside the lock to get the latest version.
        state = load_state(state_path)
        yield state
        save_state(
            state,
            state_path,
            subjective_integrity_target=subjective_integrity_target,
        )
