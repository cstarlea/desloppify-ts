"""Scan preflight guard: gate scan while review, triage or workflow items are queued."""

from __future__ import annotations

import logging

from desloppify import state as state_mod
from desloppify.app.commands.helpers.queue_progress import (
    ScoreDisplayMode,
    get_plan_start_strict,
    plan_aware_queue_breakdown,
    score_display_mode,
)
from desloppify.app.commands.helpers.state import state_path
from desloppify.app.commands.resolve.plan_load import warn_plan_load_degraded_once
from desloppify.base.exception_sets import CommandError
from desloppify.base.output.terminal import colorize
from desloppify.engine._plan.constants import WORKFLOW_RUN_SCAN_ID, is_triage_id
from desloppify.engine._plan.refresh_lifecycle import current_lifecycle_phase
from desloppify.engine._plan.sync.pipeline import live_planned_queue_empty
from desloppify.engine._state.issue_semantics import (
    is_assessment_request,
    is_objective_finding,
    is_review_work_item,
)
from desloppify.engine._state.progression import (
    append_progression_event,
    build_scan_preflight_event,
)
from desloppify.engine._work_queue.context import resolve_plan_load_status
from desloppify.engine._work_queue.core import QueueBuildOptions
from desloppify.engine.planning.queue_policy import build_execution_queue

_logger = logging.getLogger(__name__)


def _only_run_scan_workflow_remaining(state: dict, plan: dict) -> bool:
    result = build_execution_queue(
        state,
        options=QueueBuildOptions(
            status="open",
            count=None,
            plan=plan,
            include_skipped=False,
        ),
    )
    items = result.get("items", [])
    return len(items) == 1 and items[0].get("id") == WORKFLOW_RUN_SCAN_ID


def _log_preflight(
    plan: dict | None, result: str, reason: str, queue_count: int
) -> None:
    """Best-effort append of scan_preflight progression event."""
    try:
        phase = current_lifecycle_phase(plan) if isinstance(plan, dict) else None
        append_progression_event(
            build_scan_preflight_event(
                plan,
                result=result,
                reason=reason,
                queue_count=queue_count,
                phase_before=phase,
            )
        )
    except Exception:
        _logger.warning(
            "Failed to append scan_preflight progression event", exc_info=True
        )


def scan_queue_preflight(args: object) -> None:
    """Gate scan while queued work needs attention that a scan can't give."""
    # CI profile always passes
    if getattr(args, "profile", None) == "ci":
        return

    # --force-rescan with valid attestation bypasses
    if getattr(args, "force_rescan", False):
        attest = getattr(args, "attest", None) or ""
        if "i understand" not in attest.lower():
            raise CommandError(
                '--force-rescan requires --attest "I understand this is not '
                "the intended workflow and I am intentionally skipping queue "
                'completion"'
            )
        print(
            colorize(
                "  --force-rescan: bypassing queue completion check. "
                "Queue-destructive reconciliation steps will be skipped.",
                "yellow",
            )
        )
        _log_preflight(None, "bypassed", "force-rescan with attestation", 0)
        return

    # No plan = no gate (first scan, or user never uses plan). Use the same
    # plan-load contract as the work queue so degraded-plan handling stays
    # consistent across queue rendering and scan preflight.
    plan_status = resolve_plan_load_status()
    if plan_status.degraded:
        _logger.debug(
            "scan preflight plan load degraded: %s",
            plan_status.error_kind,
        )
        warn_plan_load_degraded_once(
            command_label="scan preflight",
            error_kind=plan_status.error_kind,
            behavior="Queue gating is disabled until the living plan can be loaded again.",
        )
        return
    plan = plan_status.plan
    if not isinstance(plan, dict):
        return
    if not plan.get("plan_start_scores"):
        return  # No active cycle

    # A rescan only refreshes detector findings: the plan-start score stays
    # frozen, a mid-cycle reconcile skips cluster regeneration, and a queued
    # finding whose line an edit moved keeps its place.  So scan freely while
    # the queue holds only detector findings the scan itself verifies; gate
    # it while review, triage or workflow items are pending.
    try:
        state = state_mod.load_state(state_path(args))
        breakdown = plan_aware_queue_breakdown(state, plan)
        plan_start_strict = get_plan_start_strict(plan)
        mode = score_display_mode(breakdown, plan_start_strict)
    except OSError:
        _logger.debug("scan preflight queue breakdown skipped", exc_info=True)
        return
    if mode is ScoreDisplayMode.LIVE:
        _log_preflight(plan, "allowed", "queue clear", 0)
        return  # Queue fully clear or no active cycle — scan allowed
    if (
        mode is ScoreDisplayMode.PHASE_TRANSITION
        and breakdown.queue_total == 1
        and breakdown.workflow == 1
        and _only_run_scan_workflow_remaining(state, plan)
    ):
        _log_preflight(plan, "allowed", "only workflow::run-scan remaining", 1)
        return
    # The breakdown may count items from queue_order that the snapshot
    # correctly filters out (stale items, subjective items from before
    # boundary-only sync).  If the snapshot shows no execution items,
    # the user has nothing actionable — allow the scan.
    if live_planned_queue_empty(plan):
        _log_preflight(plan, "allowed", "live planned queue empty", 0)
        return
    from desloppify.engine._work_queue.context import queue_context

    blocking: list[dict] | None = None
    try:
        execution_items = list(queue_context(state, plan=plan).snapshot.execution_items)
    except Exception:
        _logger.debug("scan preflight snapshot failed", exc_info=True)
    else:
        if not execution_items:
            _log_preflight(plan, "allowed", "snapshot execution queue empty", 0)
            return
        blocking = [
            item for item in execution_items if not is_scan_verifiable_item(item)
        ]
        if not blocking:
            count = len(execution_items)
            _log_preflight(
                plan, "allowed", "only scan-verifiable items remaining", count
            )
            return

    remaining = len(blocking) if blocking is not None else breakdown.queue_total
    _log_preflight(plan, "blocked", f"{remaining} item(s) remaining", remaining)
    raise CommandError(_gate_message(remaining, blocking))


def is_scan_verifiable_item(item: dict) -> bool:
    """True for queue work a rescan settles on its own: detector findings.

    Review findings, concerns, subjective dimensions and workflow or triage
    stages are not re-checked by a scan, so they keep the queue gate.
    """
    if item.get("id") == WORKFLOW_RUN_SCAN_ID:
        return True
    if item.get("kind") == "cluster":
        members = item.get("members") or []
        return bool(members) and all(is_scan_verifiable_item(m) for m in members)
    return item.get("kind") == "issue" and is_objective_finding(item)


def _gate_message(remaining: int, blocking: list[dict] | None) -> str:
    noun = f"{remaining} item{'s' if remaining != 1 else ''}"
    if blocking:
        kinds = sorted({_blocking_label(item) for item in blocking})
        verb = "needs" if remaining == 1 else "need"
        head = f"{noun} in your queue {verb} attention first ({', '.join(kinds)})."
    else:
        head = f"{noun} remaining in your queue."
    return (
        f"{head}\n"
        "  A scan doesn't settle review, triage or workflow items, and rescanning\n"
        "  mid-way can leave their plan state stale. Work through them with\n"
        "  `desloppify next`, then scan. Detector findings alone never block a scan.\n\n"
        "  To force a rescan (resets your plan-start score):\n"
        '    desloppify scan --force-rescan --attest "I understand this is not '
        "the intended workflow and I am intentionally skipping queue "
        'completion"'
    )


def _blocking_label(item: dict) -> str:
    kind = item.get("kind")
    if kind in ("workflow_stage", "workflow_action"):
        item_id = str(item.get("id", ""))
        return "triage" if is_triage_id(item_id) else "workflow"
    if kind == "subjective_dimension" or is_assessment_request(item):
        return "subjective review"
    if is_review_work_item(item):
        return "review findings"
    return str(kind or "other")


__all__ = ["is_scan_verifiable_item", "scan_queue_preflight"]
