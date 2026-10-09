"""Command-wide state and plan locks.

A command loads state (and usually the plan) when it starts and saves them
when it finishes, so the whole command is one read-modify-write. Commands
that may save hold the state lock, then the plan lock, from before the first
load until they return; two such commands run one after the other instead
of losing each other's updates.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path

from desloppify.base.exception_sets import CommandError
from desloppify.base.output.terminal import colorize
from desloppify.engine._plan.persistence import (
    get_plan_file,
    plan_lock,
    plan_path_for_state,
)
from desloppify.engine._state.persistence import hold_state_lock
from desloppify.engine._state.schema import get_state_file

# Commands that never save state or plan. They load without the lock; a
# corrupt-file recovery during their load takes the lock itself.
READ_ONLY_COMMANDS = frozenset(
    {
        "status",
        "show",
        "next",
        "backlog",
        "detect",
        "tree",
        "viz",
        "move",
        "setup",
        "update-skill",
    }
)

# Long enough for a scan or a review import to finish before the next
# command gives up.
COMMAND_LOCK_TIMEOUT = 600.0


def _spawns_desloppify(args: argparse.Namespace) -> bool:
    """True for modes that run desloppify subprocesses and wait for them.

    The children take the locks themselves, so a parent holding them would
    deadlock until the timeout.
    """
    if args.command == "plan" and getattr(args, "plan_action", None) == "triage":
        return bool(getattr(args, "run_stages", False))
    if args.command == "review":
        # --run-batches runs agents; --scan-after-import runs `desloppify scan`.
        return bool(getattr(args, "run_batches", False)) or bool(
            getattr(args, "scan_after_import", False)
        )
    return False


def command_needs_lock(args: argparse.Namespace) -> bool:
    """Return True when the command may save state or plan under the lock."""
    command = getattr(args, "command", None)
    if not command or command in READ_ONLY_COMMANDS:
        return False
    return not _spawns_desloppify(args)


def _lock_targets(args: argparse.Namespace) -> tuple[Path, list[Path]]:
    state_arg = getattr(args, "state", None)
    state_file = Path(state_arg) if state_arg else get_state_file()
    # Some commands save the plan next to --state, others to the default
    # plan file; lock both, in a fixed order.
    plan_files = {
        Path(p).absolute() for p in (plan_path_for_state(state_file), get_plan_file())
    }
    return state_file, sorted(plan_files)


def _report_wait() -> None:
    print(
        colorize(
            "  Waiting for another desloppify command to finish...", "dim"
        ),
        file=sys.stderr,
    )


@contextlib.contextmanager
def command_lock(args: argparse.Namespace) -> Iterator[None]:
    """Hold the state lock, then the plan lock(s), if the command needs them."""
    if not command_needs_lock(args):
        yield
        return
    state_file, plan_files = _lock_targets(args)
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(
                hold_state_lock(
                    state_file, timeout=COMMAND_LOCK_TIMEOUT, on_wait=_report_wait
                )
            )
            for plan_file in plan_files:
                stack.enter_context(
                    plan_lock(
                        plan_file, timeout=COMMAND_LOCK_TIMEOUT, on_wait=_report_wait
                    )
                )
        except TimeoutError as exc:
            raise CommandError(
                f"another desloppify command has held the state lock for over "
                f"{int(COMMAND_LOCK_TIMEOUT)}s; try again when it finishes"
            ) from exc
        yield


__all__ = ["COMMAND_LOCK_TIMEOUT", "READ_ONLY_COMMANDS", "command_lock", "command_needs_lock"]
