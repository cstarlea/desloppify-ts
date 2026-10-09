"""Behaviour of the external-review session baseline and the assessment store."""

from __future__ import annotations

from types import SimpleNamespace

from desloppify.app.commands.review.coordinator import (
    build_review_session_baseline,
    evaluate_session_baseline_drift,
)
from desloppify.app.commands.review.state_payloads import subjective_assessment_store


def _state(scan_count: int = 3) -> dict:
    return {"scan_count": scan_count, "issues": {"a": {"status": "open"}}}


def _packet(dims: list[str] | None = None) -> dict:
    return {"dimensions": dims or ["naming_quality"], "files": ["src/a.ts"]}


def _git(head: str, status: str = ""):
    def run(command, **_kwargs):
        out = head if "rev-parse" in command else status
        return SimpleNamespace(returncode=0, stdout=out + "\n", stderr="")

    return run


def test_baseline_outside_git_has_no_git_fingerprint(tmp_path):
    baseline = build_review_session_baseline(
        state=_state(), packet=_packet(), project_root=tmp_path
    )
    assert baseline["scan_count"] == 3
    assert baseline["git_head"] is None and baseline["git_status_sha256"] is None
    assert (
        evaluate_session_baseline_drift(
            expected=baseline, state=_state(), packet=_packet(), project_root=tmp_path
        )
        == []
    )


def test_drift_names_each_change(tmp_path):
    baseline = build_review_session_baseline(
        state=_state(), packet=_packet(), project_root=tmp_path
    )
    reasons = evaluate_session_baseline_drift(
        expected=baseline,
        state=_state(scan_count=4),
        packet=_packet(["logic_clarity"]),
        project_root=tmp_path,
    )
    assert reasons == [
        "scan_count changed (session 3, current 4)",
        "state hash changed",
        "review packet content changed",
    ]


def test_git_drift_compares_head_and_working_tree(tmp_path):
    expected = {"git_head": "abc", "git_status_sha256": "x"}
    same_head = evaluate_session_baseline_drift(
        expected={"git_head": "abc"},
        state=_state(),
        packet=_packet(),
        project_root=tmp_path,
        subprocess_run=_git("abc"),
    )
    assert same_head == []
    moved = evaluate_session_baseline_drift(
        expected=expected,
        state=_state(),
        packet=_packet(),
        project_root=tmp_path,
        subprocess_run=_git("def", " M src/a.ts"),
    )
    assert moved == ["git HEAD changed", "git working tree status changed"]


def test_assessment_store_normalizes_legacy_scores_in_place():
    state = {
        "subjective_assessments": {
            "naming_quality": 72,
            "logic_clarity": {"score": 80.0},
            "bad": "x",
            5: {},
        }
    }
    store = subjective_assessment_store(state)
    assert store == {
        "naming_quality": {"score": 72.0},
        "logic_clarity": {"score": 80.0},
        "bad": {},
    }
    assert state["subjective_assessments"]["naming_quality"] == {"score": 72.0}


def test_assessment_store_creates_a_missing_store():
    state: dict = {}
    assert subjective_assessment_store(state) == {}
    assert state["subjective_assessments"] == {}
