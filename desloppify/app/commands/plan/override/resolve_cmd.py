"""Resolve command handler for plan overrides."""

from __future__ import annotations

import argparse
import logging
import shlex
import sys
from typing import NoReturn

from desloppify.app.commands.helpers.attestation import (
    _MIN_NOTE_LENGTH,
    attestation_refusal,
    note_length_refusal,
    validate_attestation,
    validate_note_length,
)
from desloppify.app.commands.helpers.command_runtime import command_runtime
from desloppify.app.commands.resolve.cmd import cmd_resolve
from desloppify.base.exception_sets import PLAN_LOAD_EXCEPTIONS, CommandError
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.output.terminal import colorize
from desloppify.engine._work_queue.core import ATTEST_EXAMPLE
from desloppify.engine.plan_ops import append_log_entry
from desloppify.engine.plan_state import (
    load_plan,
    save_plan,
)

from .resolve_helpers import (
    check_cluster_guard,
    split_synthetic_patterns,
)
from .resolve_workflow import resolve_workflow_patterns

logger = logging.getLogger(__name__)


def confirm_command(patterns: list[str], note: str | None = None) -> str:
    """The `plan resolve --confirm` command that works for ``patterns``."""
    targets = " ".join(shlex.quote(pattern) for pattern in patterns) or "<id>"
    note_arg = shlex.quote(note) if note else '"<what you did>"'
    return f"desloppify plan resolve {targets} --note {note_arg} --confirm"


def _print_hint(message: str) -> None:
    print(colorize(message, "dim"), file=sys.stderr)


def _refuse_note(patterns: list[str], note: str | None) -> NoReturn:
    _print_hint(
        f"  Describe the change in at least {_MIN_NOTE_LENGTH} characters, then run:"
    )
    _print_hint(f"    {confirm_command(patterns)}")
    raise CommandError(note_length_refusal(note), exit_code=2)


def _refuse_attestation(
    patterns: list[str], note: str | None, attestation: str | None
) -> NoReturn:
    _print_hint("  Use --confirm to attest with your note:")
    _print_hint(f"    {confirm_command(patterns, note)}")
    _print_hint(
        "  Or pass --attest yourself, with the keywords 'I have actually' and "
        "'not gaming':"
    )
    _print_hint(f'    --attest "{ATTEST_EXAMPLE}"')
    if not attestation:
        raise CommandError("Plan resolve requires --confirm or --attest.", exit_code=2)
    raise CommandError(attestation_refusal("Plan resolve", attestation), exit_code=2)


def cmd_plan_resolve(args: argparse.Namespace) -> None:
    """Mark issues as fixed and delegate to resolve command UX."""
    patterns: list[str] = getattr(args, "patterns", [])
    attestation: str | None = getattr(args, "attest", None)
    note: str | None = getattr(args, "note", None)

    if getattr(args, "confirm", False):
        if not note:
            _print_hint(f"    {confirm_command(patterns)}")
            raise CommandError(
                "--confirm requires --note to describe what you did.", exit_code=2
            )
        attestation = f"I have actually {note} and I am not gaming the score."
        args.attest = attestation

    synthetic_ids, real_patterns = split_synthetic_patterns(patterns)
    if synthetic_ids:
        workflow_outcome = resolve_workflow_patterns(
            args,
            synthetic_ids=synthetic_ids,
            real_patterns=real_patterns,
            note=note,
        )
        if workflow_outcome.status == "blocked":
            raise CommandError(
                f"Not resolved: {', '.join(synthetic_ids)}.", exit_code=2
            )
        if workflow_outcome.status == "handled":
            return
        patterns = workflow_outcome.remaining_patterns
        args.patterns = patterns

    if not validate_note_length(note):
        _refuse_note(patterns, note)

    if not validate_attestation(attestation):
        _refuse_attestation(patterns, note, attestation)

    plan: dict | None = None
    try:
        runtime = command_runtime(args)
        state = runtime.state
        plan = load_plan()
        if check_cluster_guard(patterns, plan, state):
            raise CommandError("Cannot resolve an empty cluster.", exit_code=2)
    except PLAN_LOAD_EXCEPTIONS:
        plan = None

    try:
        if plan is None:
            plan = load_plan()
        clusters = plan.get("clusters", {})
        cluster_name = next(
            (pattern for pattern in patterns if pattern in clusters), None
        )
        append_log_entry(
            plan,
            "done",
            issue_ids=patterns,
            cluster_name=cluster_name,
            actor="user",
            note=note,
        )
        save_plan(plan)
    except PLAN_LOAD_EXCEPTIONS as exc:
        log_best_effort_failure(logger, "append plan resolve log entry", exc)
        print(
            colorize(f"  Note: unable to append plan resolve log entry ({exc}).", "dim")
        )

    resolve_args = argparse.Namespace(
        status="fixed",
        patterns=patterns,
        note=note,
        attest=attestation,
        confirm_batch_wontfix=False,
        force_resolve=bool(getattr(args, "force_resolve", False)),
        state=getattr(args, "state", None),
        path=getattr(args, "path", None),
        exclude=getattr(args, "exclude", None),
    )

    cmd_resolve(resolve_args)


__all__ = ["cmd_plan_resolve", "confirm_command"]
