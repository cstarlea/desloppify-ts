"""`plan skip` keeps state status and plan skip kind in step (2.38)."""

from __future__ import annotations

import argparse

from desloppify import state as state_mod
from desloppify.app.commands.helpers.command_runtime import CommandRuntime
from desloppify.app.commands.plan.override import skip as override_skip
from desloppify.engine._scoring.policy.core import issue_counts_as_failure
from desloppify.engine._work_queue.synthetic_workflow import (
    build_deferred_disposition_item,
)
from desloppify.engine.plan_state import empty_plan, load_plan, save_plan

_ATTEST = "I have actually reviewed this and I am not gaming the score."


def _seed(tmp_path, names: list[str]):
    state = state_mod.empty_state()
    state["last_scan"] = "2026-03-01T00:00:00+00:00"
    state["scan_count"] = 3
    ids = []
    for name in names:
        issue = state_mod.make_issue(
            "unused", "src/a.ts", name, tier=1, confidence="high", summary=name
        )
        state["work_items"][issue["id"]] = issue
        ids.append(issue["id"])
    state_file = tmp_path / "state.json"
    state_mod.save_state(state, state_file)
    plan = empty_plan()
    plan["queue_order"] = list(ids)
    save_plan(plan, tmp_path / "plan.json")
    return state_file, ids


def _skip(state_file, patterns, **flags) -> None:
    runtime = CommandRuntime(
        config={}, state=state_mod.load_state(state_file), state_path=state_file
    )
    args = argparse.Namespace(
        runtime=runtime,
        patterns=patterns,
        reason=flags.get("reason"),
        review_after=None,
        permanent=flags.get("permanent", False),
        false_positive=flags.get("false_positive", False),
        note=flags.get("note"),
        attest=flags.get("attest"),
        confirm=flags.get("confirm", False),
        deferred_only=flags.get("deferred_only", False),
    )
    override_skip.cmd_plan_skip(args)


def _status(state_file, issue_id) -> str:
    return state_mod.load_state(state_file)["work_items"][issue_id]["status"]


def _kind(state_file, issue_id) -> str | None:
    entry = load_plan(state_file.parent / "plan.json")["skipped"].get(issue_id)
    return entry["kind"] if entry else None


def test_permanent_skip_turns_deferred_issue_into_wontfix(tmp_path) -> None:
    state_file, (issue_id,) = _seed(tmp_path, ["x"])
    _skip(state_file, [issue_id], reason="later")
    assert _status(state_file, issue_id) == "deferred"

    _skip(state_file, [issue_id], permanent=True, note="accepted", attest=_ATTEST)

    assert _status(state_file, issue_id) == "wontfix"
    assert _kind(state_file, issue_id) == "permanent"
    state = state_mod.load_state(state_file)
    assert state["work_items"][issue_id]["wontfix_snapshot"]["scan_count"] == 3
    assert not issue_counts_as_failure(state["work_items"][issue_id], "lenient")


def test_false_positive_skip_turns_deferred_issue_into_false_positive(tmp_path) -> None:
    state_file, (issue_id,) = _seed(tmp_path, ["x"])
    _skip(state_file, [issue_id], reason="later")
    _skip(state_file, [issue_id], false_positive=True, attest=_ATTEST)
    assert _status(state_file, issue_id) == "false_positive"
    assert _kind(state_file, issue_id) == "false_positive"


def test_permanent_skip_turns_triaged_out_issue_into_wontfix(tmp_path) -> None:
    state_file, (issue_id,) = _seed(tmp_path, ["x"])
    state = state_mod.load_state(state_file)
    state["work_items"][issue_id]["status"] = "triaged_out"
    state_mod.save_state(state, state_file)

    _skip(state_file, [issue_id], permanent=True, note="accepted", attest=_ATTEST)
    assert _status(state_file, issue_id) == "wontfix"


def test_skip_never_changes_wontfix_or_resolved_issues(tmp_path, capsys) -> None:
    state_file, (wontfix_id, fixed_id) = _seed(tmp_path, ["w", "f"])
    _skip(state_file, [wontfix_id], permanent=True, note="accepted", attest=_ATTEST)
    state = state_mod.load_state(state_file)
    state_mod.resolve_issues(state, fixed_id, "fixed", note="done", attestation=_ATTEST)
    state_mod.save_state(state, state_file)
    capsys.readouterr()

    _skip(state_file, [wontfix_id, fixed_id], reason="later")
    _skip(state_file, [wontfix_id], false_positive=True, attest=_ATTEST)

    assert _status(state_file, wontfix_id) == "wontfix"
    assert _kind(state_file, wontfix_id) == "permanent"
    assert _status(state_file, fixed_id) == "fixed"
    assert _kind(state_file, fixed_id) is None
    assert "Left 2 issue(s) unchanged" in capsys.readouterr().out


def test_deferred_only_wontfixes_just_the_temporary_skips(tmp_path) -> None:
    state_file, (deferred_id, open_id) = _seed(tmp_path, ["d", "o"])
    _skip(state_file, [deferred_id], reason="later")

    _skip(
        state_file,
        ["*"],
        permanent=True,
        note="accepted",
        attest=_ATTEST,
        deferred_only=True,
    )

    assert _status(state_file, deferred_id) == "wontfix"
    assert _status(state_file, open_id) == "open"
    assert _kind(state_file, open_id) is None


def test_deferred_disposition_wontfix_command_is_deferred_only() -> None:
    plan = empty_plan()
    plan["skipped"] = {"unused::src/a.ts::x": {"kind": "temporary"}}
    item = build_deferred_disposition_item(plan)
    assert item is not None
    assert "--deferred-only" in item["detail"]["wontfix_command"]


def test_resolve_issues_from_statuses() -> None:
    state = state_mod.empty_state()
    for name, status in (("o", "open"), ("d", "deferred"), ("w", "wontfix")):
        issue = state_mod.make_issue(
            "unused", "src/a.ts", name, tier=1, confidence="high", summary=name
        )
        issue["status"] = status
        state["work_items"][issue["id"]] = issue

    assert len(state_mod.resolve_issues(state, "unused", "fixed")) == 1
    changed = state_mod.resolve_issues(
        state, "unused", "false_positive", from_statuses=("deferred", "wontfix")
    )
    assert sorted(changed) == ["unused::src/a.ts::d", "unused::src/a.ts::w"]
