"""A cluster is done once every member has a resolved status (2.40)."""

from __future__ import annotations

import argparse

import pytest

import desloppify.app.commands.resolve.living_plan as living_plan_mod
from desloppify import state as state_mod
from desloppify.engine._plan.operations.cluster import add_to_cluster, create_cluster
from desloppify.engine._plan.scan_issue_reconcile import (
    ReconcileResult,
    _reconcile_active_clusters_by_item_status,
)
from desloppify.engine.plan_ops import append_log_entry
from desloppify.engine.plan_state import empty_plan, load_plan, save_plan

_P = "smells::src/p.ts::any_type"
_Q = "smells::src/q.ts::any_type"


def _plan_with_cluster() -> dict:
    plan = empty_plan()
    create_cluster(plan, "anys")
    add_to_cluster(plan, "anys", [_P, _Q])
    # As the CLI does. Already-resolved members can still be listed (a skip
    # keeps cluster membership), so completion goes by state status.
    append_log_entry(plan, "cluster_add", issue_ids=[_P, _Q], cluster_name="anys")
    return plan


def _state(**statuses: str) -> dict:
    state = state_mod.empty_state()
    for issue_id, status in ((_P, statuses["p"]), (_Q, statuses["q"])):
        state["work_items"][issue_id] = {"id": issue_id, "status": status}
    return state


@pytest.mark.parametrize(
    "status", ["fixed", "false_positive", "wontfix", "auto_resolved"]
)
def test_resolve_completes_cluster_when_other_members_already_resolved(status) -> None:
    plan = _plan_with_cluster()
    state = _state(p=status, q="fixed")
    assert living_plan_mod._completed_cluster_names(plan, [_Q], state) == ["anys"]
    ctx = living_plan_mod.capture_cluster_context(plan, [_Q], state)
    assert ctx.cluster_completed is True
    assert ctx.cluster_remaining == 0


@pytest.mark.parametrize("status", ["open", "deferred", "triaged_out"])
def test_resolve_keeps_cluster_open_while_a_member_is_unresolved(status) -> None:
    plan = _plan_with_cluster()
    state = _state(p=status, q="fixed")
    assert living_plan_mod._completed_cluster_names(plan, [_Q], state) == []
    assert (
        living_plan_mod.capture_cluster_context(plan, [_Q], state).cluster_remaining
        == 1
    )


def test_resolving_cluster_members_one_at_a_time_closes_it(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    plan_file = tmp_path / "plan.json"
    save_plan(_plan_with_cluster(), plan_file)
    args = argparse.Namespace(status="fixed", note="done")

    for issue_id, statuses in (
        (_P, {"p": "fixed", "q": "open"}),
        (_Q, {"p": "fixed", "q": "fixed"}),
    ):
        _plan, ctx = living_plan_mod.update_living_plan_after_resolve(
            args=args,
            all_resolved=[issue_id],
            attestation="attest",
            state_file=state_file,
            state=_state(**statuses),
        )

    assert ctx.cluster_completed is True
    assert load_plan(plan_file)["clusters"]["anys"]["execution_status"] == "done"


@pytest.mark.parametrize(
    "status", ["fixed", "false_positive", "wontfix", "auto_resolved"]
)
def test_scan_reconcile_closes_cluster_of_resolved_members(status) -> None:
    plan = _plan_with_cluster()
    result = ReconcileResult()
    _reconcile_active_clusters_by_item_status(
        plan, _state(p=status, q="fixed"), result=result
    )
    assert result.clusters_completed == ["anys"]
    assert plan["clusters"]["anys"]["execution_status"] == "done"
