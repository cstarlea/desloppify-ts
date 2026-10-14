"""scan → next → autofix → next → manual resolve, at the state level.

Regression tests: autofix resolved a whole cluster in state but left the
living plan alone, so `next` showed the emptied cluster as "N remaining"
beside "Focus: 0/N items remaining" and "N stale tracked", the scan step's
footer said to `plan resolve`, and commit tracking ignored the autofix work
and labelled a later manual fix "autofix".
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import desloppify.app.commands.autofix.apply_flow as apply_flow_mod
import desloppify.app.commands.resolve.render as resolve_render_mod
import desloppify.app.commands.scan.plan_reconcile as reconcile_mod
import desloppify.cli as cli_mod
from desloppify import state as state_mod
from desloppify.base.git_context import GitContext
from desloppify.base.subjective_dimensions import DISPLAY_NAMES
from desloppify.engine._plan.operations.lifecycle import set_focus
from desloppify.engine._plan.schema import empty_plan
from desloppify.engine.plan_state import (
    add_uncommitted_issues,
    load_plan,
    plan_path_for_state,
    record_commit,
    save_plan,
    suggest_commit_message,
)
from desloppify.languages._framework.base.types import FixerConfig

UNUSED_IDS = [
    f"unused::src/mod{n}.ts::{name}:1"
    for n in (1, 2, 3)
    for name in ("alpha", "beta", "gamma")
]
DIMS = ("naming_quality", "logic_clarity")
TSCONFIG_ID = "tsconfig_health::tsconfig.json::strictFlag"
TEMPLATE = "desloppify: {status} {count} issue(s) — {summary}"
ATTEST = "I have actually fixed this and I am not gaming the score."


def _issue(issue_id: str, detector: str, file: str, summary: str) -> dict:
    return {
        "id": issue_id,
        "detector": detector,
        "file": file,
        "tier": 1,
        "confidence": "high",
        "summary": summary,
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


def _scan_state() -> dict:
    """State after the first scan of a repo that never ran a review."""
    work_items = {
        issue_id: _issue(
            issue_id, "unused", issue_id.split("::")[1], "Unused imports: x"
        )
        for issue_id in UNUSED_IDS
    }
    work_items[TSCONFIG_ID] = {
        **_issue(TSCONFIG_ID, "tsconfig_health", "tsconfig.json", "strict is off"),
        "tier": 2,
        "confidence": "medium",
        "detail": {"option": "strict"},
        "zone": "config",
    }
    work_items.update(
        {f"subjective_review::.::{dim}": _assessment_request(dim) for dim in DIMS}
    )
    return {
        "work_items": work_items,
        "issues": work_items,
        "scan_count": 1,
        "last_scan": "2026-01-01T00:00:00+00:00",
        "scan_metadata": {"source": "scan"},
        "overall_score": 80.0,
        "objective_score": 80.0,
        "strict_score": 80.0,
        "verified_strict_score": 80.0,
        "dimension_scores": {DISPLAY_NAMES[d]: _placeholder_dim(d) for d in DIMS},
        "subjective_assessments": {},
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A scanned project focused on the unused-imports cluster."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DESLOPPIFY_ROOT", str(tmp_path))
    monkeypatch.setattr(
        resolve_render_mod,
        "detect_git_context",
        lambda: GitContext(available=True, branch="main", head_sha="abc1234"),
    )
    state = _scan_state()
    state_file = tmp_path / ".desloppify" / "state.json"
    plan_file = plan_path_for_state(state_file)
    saved: list[dict] = []
    monkeypatch.setattr(reconcile_mod, "load_plan", lambda _path=None: empty_plan())
    monkeypatch.setattr(
        reconcile_mod, "save_plan", lambda p, _path=None: saved.append(p)
    )
    reconcile_mod.reconcile_plan_post_scan(
        SimpleNamespace(state=state, state_path=state_file, config={})
    )
    plan = saved[-1]
    assert "auto/unused" in plan["clusters"]
    set_focus(plan, "auto/unused")
    state_mod.save_state(state, state_file)
    save_plan(plan, plan_file)
    return SimpleNamespace(state_file=state_file, plan_file=plan_file)


def _autofix(project, issue_ids: list[str]) -> None:
    """Run the autofix apply step as if the fixer removed every import."""
    entries = [{"file": fid.split("::")[1], "issue_id": fid} for fid in issue_ids]
    fixer = FixerConfig(
        label="unused imports",
        detect=lambda _p: entries,
        fix=lambda _e, **_k: None,
        detector="unused",
    )
    results = [{"file": e["file"], "fixed_issue_ids": [e["issue_id"]]} for e in entries]
    args = argparse.Namespace(state=str(project.state_file), lang=None, path=".")
    with (
        patch.object(apply_flow_mod, "write_query"),
        patch.object(apply_flow_mod, "resolve_lang", return_value=None),
        patch.object(apply_flow_mod.narrative_mod, "compute_narrative"),
    ):
        apply_flow_mod._apply_and_report(
            args,
            Path("."),
            fixer,
            "unused-imports",
            entries,
            results,
            len(entries),
            None,
        )


def _cli(capsys, monkeypatch, *argv: str) -> str:
    capsys.readouterr()
    monkeypatch.setattr("sys.argv", ["desloppify", *argv])
    try:
        cli_mod.main()
    except SystemExit as exc:
        assert not exc.code, capsys.readouterr()
    return capsys.readouterr().out


def test_autofix_updates_the_living_plan(project, capsys):
    _autofix(project, UNUSED_IDS)

    plan = load_plan(project.plan_file)
    assert not set(UNUSED_IDS) & set(plan["queue_order"])
    assert plan["active_cluster"] is None
    assert plan["clusters"]["auto/unused"]["execution_status"] == "done"
    assert plan["uncommitted_issues"] == UNUSED_IDS
    assert plan["uncommitted_autofix"] == UNUSED_IDS
    assert any(
        entry["action"] == "autofix" and entry["actor"] == "autofix"
        for entry in plan["execution_log"]
    )


def test_next_after_autofix_agrees_with_the_plan(project, capsys, monkeypatch):
    before = _cli(capsys, monkeypatch, "next")
    assert "Cluster: auto/unused" in before

    _autofix(project, UNUSED_IDS)
    out = _cli(capsys, monkeypatch, "next")

    assert "Cluster: auto/unused" not in out
    assert "Focus:" not in out
    assert "stale" not in out
    assert "9 resolved issues uncommitted (9 by autofix)" in out


def test_partial_autofix_counts_only_live_cluster_members(project, capsys, monkeypatch):
    _autofix(project, UNUSED_IDS[:6])
    out = _cli(capsys, monkeypatch, "next")

    assert "Cluster: auto/unused (3 remaining" in out
    assert "Focus: `auto/unused` — 3/3 items remaining" in out
    assert "stale" not in out


def test_scan_step_footer_says_scan(project, capsys, monkeypatch):
    _autofix(project, [*UNUSED_IDS, TSCONFIG_ID])
    out = _cli(capsys, monkeypatch, "next")

    assert "(Ready to scan)" in out
    assert "Run `desloppify scan` now" in out
    assert "`desloppify plan resolve`" not in out


def test_manual_resolve_after_autofix_labels_both(project, capsys, monkeypatch):
    _autofix(project, UNUSED_IDS)

    out = _cli(
        capsys,
        monkeypatch,
        "plan",
        "resolve",
        TSCONFIG_ID,
        "--note",
        "turned the strict flag on in tsconfig.json and re-ran the typecheck",
        "--attest",
        ATTEST,
    )

    assert "Uncommitted work: 10 resolved issues (9 by autofix, 1 manual)" in out
    assert '"desloppify: fix 10 issue(s) — 2 detectors (9 autofix, 1 manual)"' in out
    plan = load_plan(project.plan_file)
    assert len(plan["uncommitted_issues"]) == 10
    assert plan["uncommitted_autofix"] == UNUSED_IDS


# ── commit message wording ──────────────────────────────────


def test_commit_message_for_autofix_only():
    plan = empty_plan()
    add_uncommitted_issues(plan, UNUSED_IDS[:3], autofix=True)
    assert (
        suggest_commit_message(plan, TEMPLATE)
        == "desloppify: autofix 3 issue(s) — unused in src/"
    )


def test_commit_message_for_manual_only_is_not_autofix():
    plan = empty_plan()
    add_uncommitted_issues(plan, UNUSED_IDS[:2])
    assert (
        suggest_commit_message(plan, TEMPLATE)
        == "desloppify: fix 2 issue(s) — unused in src/"
    )


def test_commit_message_root_file_is_not_put_in_a_subdirectory():
    plan = empty_plan()
    add_uncommitted_issues(plan, [UNUSED_IDS[0], TSCONFIG_ID])
    assert suggest_commit_message(plan, TEMPLATE) == (
        "desloppify: fix 2 issue(s) — 2 detectors"
    )


def test_commit_message_template_can_use_source_counts():
    plan = empty_plan()
    add_uncommitted_issues(plan, UNUSED_IDS[:2], autofix=True)
    add_uncommitted_issues(plan, [TSCONFIG_ID])
    assert (
        suggest_commit_message(plan, "{autofix_count} auto + {manual_count} by hand")
        == "2 auto + 1 by hand"
    )


def test_manual_resolve_of_an_autofixed_id_makes_it_manual():
    plan = empty_plan()
    add_uncommitted_issues(plan, UNUSED_IDS[:2], autofix=True)
    add_uncommitted_issues(plan, UNUSED_IDS[:1])
    assert plan["uncommitted_autofix"] == UNUSED_IDS[1:2]


def test_recording_a_commit_clears_autofix_tracking():
    plan = empty_plan()
    add_uncommitted_issues(plan, UNUSED_IDS[:2], autofix=True)
    record_commit(plan, "abc1234")
    assert plan["uncommitted_issues"] == []
    assert plan["uncommitted_autofix"] == []
