"""First run without a subjective review: scan → next → fix → resolve → scan.

Regression tests for the field-test dead end where the subjective review
placeholders blocked ``next``, ``plan resolve`` and ``scan`` on a repo that
had never run an LLM review.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import desloppify.app.commands.resolve.cmd as resolve_cmd_mod
import desloppify.app.commands.scan.plan_reconcile as reconcile_mod
from desloppify.app.commands.helpers.queue_progress import (
    QueueBreakdown,
    format_queue_block,
    headline_scores,
    is_first_scan,
    plan_aware_queue_breakdown,
    print_execution_or_reveal,
    print_frozen_score_with_queue_context,
    print_objective_drained_banner,
)
from desloppify.app.commands.helpers.queue_progress_render import (
    format_queue_headline,
)
from desloppify.app.commands.helpers.score_update import print_strict_target_nudge
from desloppify.app.commands.next.options import NextOptions
from desloppify.app.commands.next.queue_flow import _apply_queue_view_transforms
from desloppify.app.commands.next.render_nudges import (
    render_backlog_hint,
    render_deferred_review_hint,
)
from desloppify.app.commands.resolve.plan_load import (
    DegradedPlanWarningState,
    ResolvePlanAccess,
)
from desloppify.app.commands.scan.preflight import scan_queue_preflight
from desloppify.base.subjective_dimensions import DISPLAY_NAMES
from desloppify.engine._plan.operations.lifecycle import purge_ids
from desloppify.engine._plan.schema import empty_plan
from desloppify.engine._work_queue.snapshot import build_queue_snapshot

DIMS = ("naming_quality", "logic_clarity", "type_safety")
UNUSED_IDS = [f"unused::src/a.ts::x{n}:{n}" for n in (1, 2, 3)]
REVIEW_IDS = [f"subjective_review::.::{dim}" for dim in DIMS]
ATTEST = "I have actually removed the import and I am not gaming the score"


def _unused_issue(issue_id: str) -> dict:
    return {
        "id": issue_id,
        "detector": "unused",
        "file": "src/a.ts",
        "tier": 1,
        "confidence": "high",
        "summary": "Unused imports: x",
        "detail": {"category": "imports"},
        "status": "open",
        "work_item_kind": "mechanical_defect",
        "issue_kind": "mechanical_defect",
        "origin": "scan",
        "zone": "production",
    }


def _assessment_request(dim: str) -> dict:
    return {
        "id": f"subjective_review::.::{dim}",
        "detector": "subjective_review",
        "file": ".",
        "tier": 4,
        "confidence": "low",
        "summary": f"{DISPLAY_NAMES[dim]} — no assessment on record",
        "detail": {"reason": "unassessed", "dimension": dim},
        "status": "open",
        "work_item_kind": "assessment_request",
        "issue_kind": "assessment_request",
        "origin": "synthetic_task",
        "zone": "production",
    }


def _placeholder_dim(dim: str) -> dict:
    return {
        "score": 0.0,
        "strict": 0.0,
        "checks": 10,
        "failing": 0,
        "detectors": {
            "subjective_assessment": {"placeholder": True, "dimension_key": dim}
        },
    }


def _first_scan_state() -> dict:
    """State right after the first scan of a repo that never ran a review."""
    work_items = {issue_id: _unused_issue(issue_id) for issue_id in UNUSED_IDS}
    work_items.update(
        {f"subjective_review::.::{d}": _assessment_request(d) for d in DIMS}
    )
    return {
        "work_items": work_items,
        "issues": work_items,
        "scan_count": 1,
        "overall_score": 41.2,
        "objective_score": 95.5,
        "strict_score": 41.2,
        "verified_strict_score": 95.5,
        "dimension_scores": {DISPLAY_NAMES[d]: _placeholder_dim(d) for d in DIMS},
        "subjective_assessments": {},
    }


def _scan_boundary(state: dict, plan: dict, monkeypatch) -> dict:
    saved: list[dict] = []
    monkeypatch.setattr(reconcile_mod, "load_plan", lambda _path=None: plan)
    monkeypatch.setattr(
        reconcile_mod, "save_plan", lambda p, _path=None: saved.append(p)
    )
    reconcile_mod.reconcile_plan_post_scan(
        SimpleNamespace(state=state, state_path=Path("/tmp/fake-state.json"), config={})
    )
    return saved[-1] if saved else plan


def _first_run(monkeypatch) -> tuple[dict, dict]:
    state = _first_scan_state()
    return state, _scan_boundary(state, empty_plan(), monkeypatch)


def _resolve_fixed(state: dict, plan: dict, issue_ids: list[str]) -> None:
    for issue_id in issue_ids:
        state["work_items"][issue_id]["status"] = "fixed"
    purge_ids(plan, issue_ids)


def _run_preflight(state: dict, plan: dict) -> None:
    args = SimpleNamespace(profile=None, force_rescan=False, state=None)
    status = SimpleNamespace(plan=plan, degraded=False, error_kind=None)
    with (
        patch(
            "desloppify.app.commands.scan.preflight.resolve_plan_load_status",
            return_value=status,
        ),
        patch(
            "desloppify.app.commands.scan.preflight.state_path",
            return_value="/tmp/fake-state.json",
        ),
        patch("desloppify.app.commands.scan.preflight.state_mod") as state_mod,
        patch("desloppify.app.commands.scan.preflight._log_preflight"),
    ):
        state_mod.load_state.return_value = state
        scan_queue_preflight(args)


# ── Step 1: scan, then `next` ───────────────────────────────


def test_next_serves_objective_work_before_the_first_review(monkeypatch):
    state, plan = _first_run(monkeypatch)

    snapshot = build_queue_snapshot(state, plan=plan)

    assert snapshot.phase == "execute"
    assert sorted(item["id"] for item in snapshot.execution_items) == UNUSED_IDS
    assert not any(fid.startswith("subjective") for fid in plan["queue_order"])
    # One placeholder per dimension waits in the backlog, not two.
    assert sorted(item["id"] for item in snapshot.deferred_review_items) == sorted(
        REVIEW_IDS
    )
    assert {item["id"] for item in snapshot.backlog_items} == {
        *REVIEW_IDS,
        "workflow::run-scan",
    }


def test_review_hint_waits_until_objective_queue_is_done(monkeypatch, capsys):
    state, plan = _first_run(monkeypatch)

    render_deferred_review_hint(build_queue_snapshot(state, plan=plan))
    assert capsys.readouterr().out == ""

    _resolve_fixed(state, plan, UNUSED_IDS)
    render_deferred_review_hint(build_queue_snapshot(state, plan=plan))
    out = capsys.readouterr().out
    assert "Subjective review pending: 3 dimensions are unscored" in out
    assert "does not block `desloppify scan`" in out
    assert "desloppify review --prepare" in out


# ── Step 2: `plan resolve` on an item that isn't at the head ─


def test_resolve_out_of_queue_order_is_not_refused(monkeypatch, capsys):
    state = _first_scan_state()
    plan = empty_plan()
    plan["queue_order"] = list(UNUSED_IDS)
    access = ResolvePlanAccess(
        plan=plan,
        degraded=False,
        error_kind=None,
        warning_state=DegradedPlanWarningState(),
    )
    monkeypatch.setattr(resolve_cmd_mod, "load_state", lambda _path: state)
    monkeypatch.setattr(resolve_cmd_mod, "state_path", lambda _args: "state.json")
    monkeypatch.setattr(resolve_cmd_mod, "load_resolve_plan_access", lambda: access)
    monkeypatch.setattr(
        resolve_cmd_mod, "require_triage_current_or_exit", lambda **_k: None
    )
    args = argparse.Namespace(
        patterns=[UNUSED_IDS[2]],
        status="fixed",
        note="removed the unused import of x3 from src/a.ts and re-ran tsc to confirm",
        attest=ATTEST,
        force_resolve=False,
    )

    loaded = resolve_cmd_mod._load_state_with_guards(args, attestation=ATTEST)

    assert loaded is not None
    out = capsys.readouterr().out
    assert "resolving out of plan order" in out
    assert f"The current next item is still: {UNUSED_IDS[0]}" in out


# ── Step 3: queue drained → `next` says scan, and scan runs ──


def test_scan_runs_without_force_once_objective_queue_is_done(monkeypatch):
    state, plan = _first_run(monkeypatch)
    _resolve_fixed(state, plan, UNUSED_IDS)

    snapshot = build_queue_snapshot(state, plan=plan)

    assert snapshot.phase == "scan"
    assert [item["id"] for item in snapshot.execution_items] == ["workflow::run-scan"]
    _run_preflight(state, plan)  # must not raise CommandError


def test_scan_gate_ignores_placeholders_left_by_an_older_plan(monkeypatch):
    """Plans written before this fix carry ``subjective::`` IDs mid-cycle."""
    state, plan = _first_run(monkeypatch)
    _resolve_fixed(state, plan, UNUSED_IDS)
    plan["queue_order"] = [f"subjective::{dim}" for dim in DIMS]

    assert build_queue_snapshot(state, plan=plan).phase == "scan"
    _run_preflight(state, plan)


def test_scan_runs_after_one_fix_while_objective_work_remains(monkeypatch):
    """ "Rescan after each fix" must work: detector findings never gate a scan."""
    state, plan = _first_run(monkeypatch)
    _resolve_fixed(state, plan, UNUSED_IDS[:1])

    assert build_queue_snapshot(state, plan=plan).phase == "execute"
    _run_preflight(state, plan)  # must not raise CommandError


def test_mid_cycle_rescan_keeps_line_shifted_items_queued(monkeypatch):
    """Fixing line 1 moves the finding on line 2; it must keep its queue slot."""
    state, plan = _first_run(monkeypatch)
    _resolve_fixed(state, plan, UNUSED_IDS[:1])
    moved_from = UNUSED_IDS[1]  # unused::src/a.ts::x2:2
    moved_to = "unused::src/a.ts::x2:1"
    state["work_items"][moved_from]["status"] = "auto_resolved"
    state["work_items"][moved_to] = _unused_issue(moved_to)
    state["scan_count"] = 2
    order_before = list(plan["queue_order"])

    plan = _scan_boundary(state, plan, monkeypatch)

    expected = [moved_to if fid == moved_from else fid for fid in order_before]
    assert plan["queue_order"] == expected
    assert moved_from not in plan.get("superseded", {})
    for cluster in plan.get("clusters", {}).values():
        assert moved_from not in cluster.get("issue_ids", [])
    snapshot = build_queue_snapshot(state, plan=plan)
    assert moved_to in {item["id"] for item in snapshot.execution_items}


def test_rescan_starts_a_new_objective_cycle(monkeypatch):
    state, plan = _first_run(monkeypatch)
    _resolve_fixed(state, plan, UNUSED_IDS)
    state["work_items"]["unused::src/b.ts::y:1"] = _unused_issue(
        "unused::src/b.ts::y:1"
    )
    state["scan_count"] = 2

    plan = _scan_boundary(state, plan, monkeypatch)
    snapshot = build_queue_snapshot(state, plan=plan)

    assert snapshot.phase == "execute"
    assert [item["id"] for item in snapshot.execution_items] == [
        "unused::src/b.ts::y:1"
    ]


# ── Step 4: counts agree and are labelled ───────────────────


def test_queue_counts_add_up_to_open_issues(monkeypatch):
    state, plan = _first_run(monkeypatch)

    breakdown = plan_aware_queue_breakdown(state, plan)

    assert breakdown.lifecycle_phase == "execute"
    assert breakdown.objective_open == len(UNUSED_IDS)
    assert breakdown.backlog == len(REVIEW_IDS)
    assert breakdown.subjective == 0
    assert format_queue_headline(breakdown).endswith("· Backlog: 3")


def _queue_lines(out: str) -> list[str]:
    return [line.strip() for line in out.splitlines() if "Queue: " in line]


def test_queue_count_agrees_across_status_next_plan_and_scan(monkeypatch, capsys):
    """Every surface reports the same queue size."""
    from desloppify.app.commands.helpers.queue_progress import queue_headline
    from desloppify.app.commands.scan.reporting.agent_context import (
        _print_living_plan_notice,
    )
    from desloppify.app.commands.status.render import show_agent_plan
    from desloppify.engine._plan.schema import ensure_plan_defaults
    from desloppify.engine._work_queue.core import QueueBuildOptions
    from desloppify.engine.planning.queue_policy import build_execution_queue
    from desloppify.engine.planning.render import generate_plan_md

    state, plan = _first_run(monkeypatch)
    # Before any resolve: three items in one collapsed cluster are 3, not 1.
    assert plan_aware_queue_breakdown(state, plan).queue_total == 3

    # `plan resolve` purges the item, focuses its cluster and saves the plan.
    _resolve_fixed(state, plan, UNUSED_IDS[:1])
    plan["active_cluster"] = "auto/unused"
    ensure_plan_defaults(plan)
    expected = "Queue: 2 items"

    breakdown = plan_aware_queue_breakdown(state, plan)
    assert breakdown.queue_total == 2

    # status: the score block and the AGENT PLAN block
    for text, _style in format_queue_block(breakdown):
        print(text)
    show_agent_plan({}, plan=plan, breakdown=breakdown)
    status_out = capsys.readouterr().out
    assert "Focused on: auto/unused (2 items remaining)." in status_out

    # next: the header total, whatever the focus and --count show
    items = build_execution_queue(
        state, options=QueueBuildOptions(status="open", count=None, plan=plan)
    )["items"]
    queue = {"items": list(items)}
    _apply_queue_view_transforms(
        items=items,
        queue=queue,
        opts=NextOptions(count=1),
        plan_for_queue=plan,
        effective_cluster="auto/unused",
        collapse_plan_clusters=True,
    )
    assert queue["total"] == 2

    # plan: queue line, ordered section, cluster description, no repeat
    plan_line = queue_headline(state, plan)
    plan_md = generate_plan_md(state, plan)
    assert "## User-Ordered Queue (2 items)" in plan_md
    assert "> Remove 2 unused issues" in plan_md
    assert "## Open Items" not in plan_md
    for issue_id in UNUSED_IDS[1:]:
        assert plan_md.count(issue_id) == 1

    # scan: the LLM summary's living-plan line
    _print_living_plan_notice({"plan": plan, "active_cluster": None}, state)
    scan_out = capsys.readouterr().out

    lines = [*_queue_lines(status_out), plan_line, *_queue_lines(scan_out)]
    assert len(lines) == 4
    for line in lines:
        assert expected in line, line


def test_drained_banner_does_not_claim_completion_with_planned_work(capsys):
    planned = QueueBreakdown(queue_total=2, workflow=2, objective_planned=5)
    print_objective_drained_banner(80.0, 2, planned)
    out = capsys.readouterr().out
    assert "Objective queue complete" not in out
    assert "5 planned objective items follow" in out

    backlog = QueueBreakdown(queue_total=2, workflow=2, objective_open=7)
    print_objective_drained_banner(80.0, 2, backlog)
    out = capsys.readouterr().out
    assert "Objective queue complete" in out
    assert "7 open objective issues are in the backlog" in out


def test_backlog_hint_when_queue_has_no_objective_work(monkeypatch, capsys):
    state, plan = _first_run(monkeypatch)
    state["work_items"]["unused::src/c.ts::z:1"] = _unused_issue(
        "unused::src/c.ts::z:1"
    )
    _resolve_fixed(state, plan, UNUSED_IDS)
    plan["clusters"]["parked"] = {
        "name": "parked",
        "issue_ids": ["unused::src/c.ts::z:1"],
        "execution_status": "review",
    }

    render_backlog_hint(build_queue_snapshot(state, plan=plan))

    assert "1 open objective issue is in the backlog" in capsys.readouterr().out


def test_next_total_counts_the_whole_queue_not_the_page():
    items = [{"id": issue_id, "kind": "issue"} for issue_id in UNUSED_IDS]
    queue = {"items": list(items), "total": len(items)}

    visible = _apply_queue_view_transforms(
        items=items,
        queue=queue,
        opts=NextOptions(count=1),
        plan_for_queue={},
        effective_cluster=None,
        collapse_plan_clusters=True,
    )

    assert len(visible) == 1
    assert queue["total"] == 3


def test_stale_cluster_focus_does_not_fake_an_empty_queue():
    """A remembered focus on a cluster with nothing queued showed 'Queue cleared!'."""
    items = [{"id": issue_id, "kind": "issue"} for issue_id in REVIEW_IDS]
    plan = {
        "active_cluster": "auto/unused",
        "clusters": {"auto/unused": {"name": "auto/unused", "issue_ids": UNUSED_IDS}},
        "queue_order": [],
    }

    visible = _apply_queue_view_transforms(
        items=items,
        queue={"items": list(items)},
        opts=NextOptions(count=None),
        plan_for_queue=plan,
        effective_cluster="auto/unused",
        collapse_plan_clusters=True,
    )

    assert [item["id"] for item in visible] == REVIEW_IDS


# ── Step 5: the headline score before any review ────────────


def test_headline_is_objective_before_the_first_review(monkeypatch):
    state, plan = _first_run(monkeypatch)

    assert headline_scores(state, plan) == ("objective", 95.5, 95.5)

    block = format_queue_block(
        plan_aware_queue_breakdown(state, plan),
        frozen_score=97.0,
        live_score=97.0,
        score_name="objective",
    )
    assert (
        "  Score (frozen at plan start): objective 95.5/100 (subjective review pending)",
        "cyan",
    ) in block


def test_score_reads_plainly_right_after_the_first_scan(monkeypatch, capsys):
    state, plan = _first_run(monkeypatch)
    breakdown = plan_aware_queue_breakdown(state, plan)

    print_execution_or_reveal(state, None, plan)
    out = capsys.readouterr().out
    assert "  Score: objective 95.5/100 (subjective review pending)" in out
    assert "frozen" not in out

    state["scan_count"] = 2
    print_frozen_score_with_queue_context(
        breakdown,
        frozen_strict=97.0,
        live_score=97.0,
        score_name="objective",
        first_scan=is_first_scan(state),
    )
    assert "Score (frozen at plan start)" in capsys.readouterr().out


def test_headline_is_strict_once_a_dimension_is_assessed(monkeypatch):
    state, plan = _first_run(monkeypatch)
    meta = state["dimension_scores"][DISPLAY_NAMES["naming_quality"]]
    meta["detectors"]["subjective_assessment"]["placeholder"] = False

    assert headline_scores(state, plan) == ("strict", 41.2, 41.2)


def test_strict_target_nudge_is_marked_provisional(capsys):
    print_strict_target_nudge(41.2, 85.0, show_next=False, provisional=True)
    out = capsys.readouterr().out
    assert "Strict 41.2 is provisional" in out
    assert "target" not in out

    print_strict_target_nudge(41.2, 85.0, show_next=False)
    assert "Strict 41.2 (target: 85.0)" in capsys.readouterr().out


def test_never_queued_issues_are_counted_and_labelled(monkeypatch):
    """Queue + backlog + never-queued add up to the open count status shows."""
    state, plan = _first_run(monkeypatch)
    orphan = _unused_issue("orphaned::test/helper.ts")
    orphan.update(detector="orphaned", zone="test", file="test/helper.ts")
    state["work_items"][orphan["id"]] = orphan

    breakdown = plan_aware_queue_breakdown(state, plan)
    block = format_queue_block(breakdown)

    assert breakdown.never_queued == 1
    assert any(text.startswith("  Never queued: 1 open issue ") for text, _ in block)
