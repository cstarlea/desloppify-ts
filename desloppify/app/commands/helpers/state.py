"""State-path and scan-gating helpers for command modules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from desloppify.base.output.terminal import colorize
from desloppify.engine._state.legacy_lang_state import migrate_legacy_lang_state
from desloppify.engine._state.schema import (
    get_state_dir,
    get_state_file,
    scan_inventory_available,
    scan_metrics_available,
)


def state_path(args: argparse.Namespace) -> Path:
    """State file path: ``--state`` if given, else ``.desloppify/state.json``.

    A legacy ``state-typescript.json`` / ``state-javascript.json`` is adopted
    as ``state.json`` the first time a command runs.
    """
    path_arg = getattr(args, "state", None)
    if path_arg:
        return Path(path_arg)
    state_dir = get_state_dir()
    adopted = migrate_legacy_lang_state(state_dir)
    if adopted is not None:
        print(
            colorize(f"  Migrated {adopted.name} to state.json", "dim"),
            file=sys.stderr,
        )
    return get_state_file()


def require_issue_inventory(state: dict) -> bool:
    """Return True when command consumers can rely on the issue inventory."""
    if not scan_inventory_available(state):
        print(colorize("No scans yet. Run: desloppify scan", "yellow"))
        return False
    return True


def require_scan_metrics(state: dict) -> bool:
    """Return True when real scan-derived metrics are available."""
    if not scan_metrics_available(state):
        print(colorize("No completed scan metrics yet. Run: desloppify scan", "yellow"))
        return False
    return True


__all__ = [
    "require_issue_inventory",
    "require_scan_metrics",
    "state_path",
]
