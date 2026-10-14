"""Tests for scan queue preflight guard (queue-cycle gating)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from desloppify_ts.app.commands.scan.preflight import scan_queue_preflight
from desloppify_ts.base.exception_sets import CommandError


def _plan_status(plan=None, *, degraded=False, error_kind=None):
    return SimpleNamespace(plan=plan, degraded=degraded, error_kind=error_kind)


# ── CI profile bypass ───────────────────────────────────────


def test_ci_profile_always_passes():
    """CI profile bypasses the gate entirely."""
    args = SimpleNamespace(profile="ci")
    # Should not raise or exit
    scan_queue_preflight(args)


# ── No plan = no gate ───────────────────────────────────────


def test_no_plan_file_passes():
    """When no plan exists, scan is allowed."""
    args = SimpleNamespace(profile=None, force_rescan=False)
    with patch(
        "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
        return_value=_plan_status(),
    ):
        scan_queue_preflight(args)


def test_plan_without_start_scores_passes():
    """Plan without plan_start_scores means no active cycle."""
    args = SimpleNamespace(profile=None, force_rescan=False)
    with patch(
        "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
        return_value=_plan_status({}),
    ):
        scan_queue_preflight(args)


# ── Queue clear = scan allowed ──────────────────────────────


def test_queue_clear_allows_scan():
    """When queue has zero remaining items, scan proceeds."""
    from desloppify_ts.app.commands.helpers.queue_progress import QueueBreakdown

    args = SimpleNamespace(profile=None, force_rescan=False, state=None, lang="python")
    plan = {"plan_start_scores": {"strict": 80.0}}
    with (
        patch(
            "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
            return_value=_plan_status(plan),
        ),
        patch(
            "desloppify_ts.app.commands.scan.preflight.state_path",
            return_value="/tmp/test-state.json",
        ),
        patch("desloppify_ts.app.commands.scan.preflight.state_mod") as mock_state_mod,
        patch(
            "desloppify_ts.app.commands.scan.preflight.plan_aware_queue_breakdown",
            return_value=QueueBreakdown(queue_total=0, workflow=0),
        ),
    ):
        mock_state_mod.load_state.return_value = {"issues": {}}
        scan_queue_preflight(args)


def test_queue_drained_with_non_scan_lifecycle_allows_scan():
    """When queue is fully drained but lifecycle phase hasn't advanced to scan,
    scan should still be allowed. Regression test for #441."""
    from desloppify_ts.app.commands.helpers.queue_progress import QueueBreakdown

    args = SimpleNamespace(profile=None, force_rescan=False, state=None, lang="python")
    plan = {"plan_start_scores": {"strict": 80.0}}
    # lifecycle_phase stuck on "review" even though queue_total is 0
    breakdown = QueueBreakdown(queue_total=0, workflow=0, lifecycle_phase="review")
    with (
        patch(
            "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
            return_value=_plan_status(plan),
        ),
        patch(
            "desloppify_ts.app.commands.scan.preflight.state_path",
            return_value="/tmp/test-state.json",
        ),
        patch("desloppify_ts.app.commands.scan.preflight.state_mod") as mock_state_mod,
        patch(
            "desloppify_ts.app.commands.scan.preflight.plan_aware_queue_breakdown",
            return_value=breakdown,
        ),
    ):
        mock_state_mod.load_state.return_value = {"issues": {}}
        scan_queue_preflight(args)


# ── Queue remaining = gate ──────────────────────────────────


def _gate_with_execution_items(items, plan=None):
    """Run the preflight with a non-empty queue whose snapshot holds *items*."""
    from desloppify_ts.app.commands.helpers.queue_progress import QueueBreakdown

    args = SimpleNamespace(profile=None, force_rescan=False, state=None)
    plan = plan or {
        "plan_start_scores": {"strict": 80.0},
        "queue_order": [item["id"] for item in items],
    }
    ctx = SimpleNamespace(snapshot=SimpleNamespace(execution_items=tuple(items)))
    with (
        patch(
            "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
            return_value=_plan_status(plan),
        ),
        patch(
            "desloppify_ts.app.commands.scan.preflight.state_path",
            return_value="/tmp/test-state.json",
        ),
        patch("desloppify_ts.app.commands.scan.preflight.state_mod") as mock_state_mod,
        patch(
            "desloppify_ts.app.commands.scan.preflight.plan_aware_queue_breakdown",
            return_value=QueueBreakdown(queue_total=len(items), workflow=0),
        ),
        patch(
            "desloppify_ts.engine._work_queue.context.queue_context",
            return_value=ctx,
        ),
        patch("desloppify_ts.app.commands.scan.preflight._log_preflight"),
    ):
        mock_state_mod.load_state.return_value = {"issues": {}}
        scan_queue_preflight(args)


def _issue(issue_id, detector, kind):
    return {
        "id": issue_id,
        "kind": "issue",
        "detector": detector,
        "work_item_kind": kind,
    }


def test_queue_of_detector_findings_allows_scan():
    """A rescan verifies detector findings, so they never block it."""
    _gate_with_execution_items(
        [
            _issue("unused::src/a.ts::x:1", "unused", "mechanical_defect"),
            _issue("smells::src/a.ts::magic_number::4", "smells", "mechanical_defect"),
        ]
    )


def test_cluster_of_detector_findings_allows_scan():
    member = _issue("unused::src/a.ts::x:1", "unused", "mechanical_defect")
    _gate_with_execution_items(
        [{"id": "auto/unused", "kind": "cluster", "members": [member]}]
    )


def test_review_findings_in_queue_block_scan():
    """A scan doesn't settle review findings, so they keep the gate."""
    items = [
        _issue("unused::src/a.ts::x:1", "unused", "mechanical_defect"),
        _issue("review::src/a.ts::naming", "review", "review_defect"),
    ]
    with pytest.raises(CommandError) as exc_info:
        _gate_with_execution_items(items)
    message = str(exc_info.value)
    assert "1 item in your queue needs attention first (review findings)" in message
    assert "regenerates issue IDs" not in message
    assert "--force-rescan" in message


def test_triage_stage_in_queue_blocks_scan():
    items = [{"id": "triage::observe", "kind": "workflow_stage"}]
    plan = {
        "plan_start_scores": {"strict": 80.0},
        "queue_order": ["review::src/a.ts::naming"],
    }
    with pytest.raises(CommandError, match=r"attention first \(triage\)"):
        _gate_with_execution_items(items, plan=plan)


def test_queue_remaining_blocks_scan_when_snapshot_fails():
    """Without a snapshot the gate can't tell what is queued, so it holds."""
    from desloppify_ts.app.commands.helpers.queue_progress import QueueBreakdown

    args = SimpleNamespace(profile=None, force_rescan=False, state=None, lang="python")
    plan = {
        "plan_start_scores": {"strict": 80.0},
        "queue_order": ["issue-1", "issue-2"],
    }
    with (
        patch(
            "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
            return_value=_plan_status(plan),
        ),
        patch(
            "desloppify_ts.app.commands.scan.preflight.state_path",
            return_value="/tmp/test-state.json",
        ),
        patch("desloppify_ts.app.commands.scan.preflight.state_mod") as mock_state_mod,
        patch(
            "desloppify_ts.app.commands.scan.preflight.plan_aware_queue_breakdown",
            return_value=QueueBreakdown(queue_total=5, workflow=0),
        ),
        patch(
            "desloppify_ts.engine._work_queue.context.queue_context",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(CommandError) as exc_info,
    ):
        mock_state_mod.load_state.return_value = {"issues": {}}
        scan_queue_preflight(args)
    assert "5 items remaining in your queue" in str(exc_info.value)


def test_queue_with_only_subjective_items_allows_scan():
    """When queue contains only subjective items (synthetic), scan is allowed.

    Subjective items are synthetic — the live planned queue is empty,
    so there's no objective work blocking the scan.
    """
    from desloppify_ts.app.commands.helpers.queue_progress import QueueBreakdown

    args = SimpleNamespace(profile=None, force_rescan=False, state=None, lang="python")
    plan = {
        "plan_start_scores": {"strict": 80.0},
        "queue_order": ["subjective::dim1", "subjective::dim2"],
    }
    breakdown = QueueBreakdown(queue_total=20, subjective=20, workflow=0)
    assert breakdown.objective_actionable == 0  # precondition
    with (
        patch(
            "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
            return_value=_plan_status(plan),
        ),
        patch(
            "desloppify_ts.app.commands.scan.preflight.state_path",
            return_value="/tmp/test-state.json",
        ),
        patch("desloppify_ts.app.commands.scan.preflight.state_mod") as mock_state_mod,
        patch(
            "desloppify_ts.app.commands.scan.preflight.plan_aware_queue_breakdown",
            return_value=breakdown,
        ),
    ):
        mock_state_mod.load_state.return_value = {"issues": {}}
        scan_queue_preflight(args)  # should NOT raise


def test_queue_with_only_workflow_items_allows_scan():
    """When queue contains only workflow items (synthetic), scan is allowed.

    Workflow items are synthetic — the live planned queue is empty,
    so there's no objective work blocking the scan.
    """
    from desloppify_ts.app.commands.helpers.queue_progress import QueueBreakdown

    args = SimpleNamespace(profile=None, force_rescan=False, state=None, lang="python")
    plan = {
        "plan_start_scores": {"strict": 80.0},
        "queue_order": ["workflow::communicate-score"],
    }
    breakdown = QueueBreakdown(queue_total=1, workflow=1)
    assert breakdown.objective_actionable == 0  # precondition
    with (
        patch(
            "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
            return_value=_plan_status(plan),
        ),
        patch(
            "desloppify_ts.app.commands.scan.preflight.state_path",
            return_value="/tmp/test-state.json",
        ),
        patch("desloppify_ts.app.commands.scan.preflight.state_mod") as mock_state_mod,
        patch(
            "desloppify_ts.app.commands.scan.preflight.plan_aware_queue_breakdown",
            return_value=breakdown,
        ),
        patch(
            "desloppify_ts.app.commands.scan.preflight._only_run_scan_workflow_remaining",
            return_value=False,
        ),
    ):
        mock_state_mod.load_state.return_value = {"issues": {}}
        scan_queue_preflight(args)  # should NOT raise — workflow items are synthetic


def test_queue_with_only_run_scan_workflow_allows_scan():
    """The synthetic workflow::run-scan item must not block scan execution."""
    from desloppify_ts.app.commands.helpers.queue_progress import QueueBreakdown

    args = SimpleNamespace(profile=None, force_rescan=False, state=None, lang="python")
    plan = {"plan_start_scores": {"strict": 80.0}}
    breakdown = QueueBreakdown(queue_total=1, workflow=1)
    with (
        patch(
            "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
            return_value=_plan_status(plan),
        ),
        patch(
            "desloppify_ts.app.commands.scan.preflight.state_path",
            return_value="/tmp/test-state.json",
        ),
        patch("desloppify_ts.app.commands.scan.preflight.state_mod") as mock_state_mod,
        patch(
            "desloppify_ts.app.commands.scan.preflight.plan_aware_queue_breakdown",
            return_value=breakdown,
        ),
        patch(
            "desloppify_ts.app.commands.scan.preflight._only_run_scan_workflow_remaining",
            return_value=True,
        ),
    ):
        mock_state_mod.load_state.return_value = {"issues": {}}
        scan_queue_preflight(args)


# ── --force-rescan ──────────────────────────────────────────


def test_force_rescan_without_attest_exits():
    """--force-rescan without proper attestation is rejected."""
    args = SimpleNamespace(profile=None, force_rescan=True, attest=None)
    with pytest.raises(CommandError) as exc_info:
        scan_queue_preflight(args)
    assert exc_info.value.exit_code == 1


def test_force_rescan_with_wrong_attest_exits():
    """--force-rescan with wrong attestation text is rejected."""
    args = SimpleNamespace(profile=None, force_rescan=True, attest="wrong text")
    with pytest.raises(CommandError) as exc_info:
        scan_queue_preflight(args)
    assert exc_info.value.exit_code == 1


def test_force_rescan_with_valid_attest_passes():
    """--force-rescan with correct attestation bypasses the gate without modifying plan."""
    args = SimpleNamespace(
        profile=None,
        force_rescan=True,
        attest="I understand this is not the intended workflow",
    )
    # Preflight no longer clears plan_start_scores — mid-cycle detection
    # is preserved so reconciliation can skip destructive steps.
    scan_queue_preflight(args)


def test_force_rescan_tolerates_missing_plan():
    """--force-rescan with valid attestation works even if no plan file exists."""
    args = SimpleNamespace(
        profile=None,
        force_rescan=True,
        attest="I understand this is not the intended workflow",
    )
    with patch(
        "desloppify_ts.app.commands.scan.preflight.resolve_plan_load_status",
        return_value=_plan_status(),
    ):
        scan_queue_preflight(args)
