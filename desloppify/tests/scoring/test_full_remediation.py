"""Fixing every finding and rescanning reaches 100 in every score mode."""

from __future__ import annotations

import pytest

from desloppify.engine._scoring.detection import detector_stats_by_mode
from desloppify.engine._scoring.policy.core import (
    CARRIED_FORWARD_MAX_SCANS,
    SCORING_MODES,
)
from desloppify.intelligence.review.dimensions.data import load_dimensions_for_lang
from desloppify.state import (
    MergeScanOptions,
    empty_state,
    merge_scan,
    resolve_issues,
)

_POTENTIALS = {"unused": 20, "smells": 20}
_NOW = "2026-01-01T00:00:00+00:00"
_SCORE_KEYS = ("overall_score", "objective_score", "strict_score", "verified_strict_score")


def _raw_issue(detector: str, name: str) -> dict:
    return {
        "id": f"{detector}::src/a.ts::{name}",
        "detector": detector,
        "file": "src/a.ts",
        "tier": 2,
        "confidence": "high",
        "summary": name,
        "detail": {},
        "status": "open",
        "note": None,
        "first_seen": _NOW,
        "last_seen": _NOW,
        "resolved_at": None,
        "reopen_count": 0,
    }


def _scan(
    state: dict,
    issues: list[dict],
    project_root: str,
    potentials: dict[str, int] | None = None,
) -> dict:
    return merge_scan(
        state,
        issues,
        MergeScanOptions(
            lang="typescript",
            scan_path=".",
            potentials=dict(_POTENTIALS if potentials is None else potentials),
            project_root=project_root,
        ),
    )


def _scores(state: dict) -> dict[str, float]:
    return {key: state[key] for key in _SCORE_KEYS}


@pytest.fixture
def scanned(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("export const a = 1;\n")
    state = empty_state()
    # Subjective dimensions are reviewed and clean; only the mechanical
    # findings below need remediation.
    dimension_keys, _, _ = load_dimensions_for_lang("typescript")
    state["subjective_assessments"] = {
        key: {"score": 100.0, "placeholder": False} for key in dimension_keys
    }
    issues = [
        _raw_issue("unused", "left_open"),
        _raw_issue("unused", "marked_fixed"),
        _raw_issue("smells", "false_positive"),
        _raw_issue("smells", "left_open_too"),
        _raw_issue("smells", "deferred"),
        _raw_issue("unused", "wontfix"),
    ]
    _scan(state, issues, str(tmp_path))
    return state, str(tmp_path)


def test_issues_lower_every_mode_before_remediation(scanned):
    state, _root = scanned
    for key, value in _scores(state).items():
        assert value < 100.0, key


def test_fix_and_rescan_reaches_100_in_every_mode(scanned):
    state, root = scanned
    resolve_issues(state, "unused::src/a.ts::marked_fixed", "fixed", note="done")
    resolve_issues(
        state, "smells::src/a.ts::false_positive", "false_positive", note="not a smell"
    )
    resolve_issues(state, "unused::src/a.ts::wontfix", "wontfix", note="accepted")
    # `plan skip` (temporary) sets this status in state.
    state["work_items"]["smells::src/a.ts::deferred"]["status"] = "deferred"

    # The code is fixed: the next scan reports nothing.
    _scan(state, [], root)

    statuses = {issue["summary"]: issue["status"] for issue in state["work_items"].values()}
    assert statuses == {
        "left_open": "auto_resolved",
        "left_open_too": "auto_resolved",
        "deferred": "auto_resolved",
        "marked_fixed": "fixed",
        "false_positive": "false_positive",
        "wontfix": "wontfix",
    }
    assert _scores(state) == {key: 100.0 for key in _SCORE_KEYS}


def test_returning_wontfix_finding_counts_again(scanned):
    state, root = scanned
    wontfix_id = "unused::src/a.ts::wontfix"
    resolve_issues(state, wontfix_id, "wontfix", note="accepted")
    _scan(state, [], root)
    assert state["strict_score"] == 100.0

    _scan(state, [_raw_issue("unused", "wontfix")], root)

    assert state["work_items"][wontfix_id]["status"] == "wontfix"
    assert state["overall_score"] == 100.0
    assert state["strict_score"] < 100.0
    assert state["verified_strict_score"] < 100.0


def test_auto_resolved_passes_in_every_mode():
    issues = {
        "unused::a::x": {
            "id": "unused::a::x",
            "detector": "unused",
            "file": "a.ts",
            "confidence": "high",
            "status": "auto_resolved",
        }
    }
    stats = detector_stats_by_mode("unused", issues, 10)
    for mode in SCORING_MODES:
        assert stats[mode] == (1.0, 0, 0.0), mode


@pytest.mark.parametrize("status", ["fixed", "false_positive"])
def test_verified_strict_credits_manual_resolution_once_scan_confirms(status):
    issue = {
        "id": "unused::a::x",
        "detector": "unused",
        "file": "a.ts",
        "confidence": "high",
        "status": status,
        "resolution_attestation": {"kind": "manual", "scan_verified": False},
    }
    before = detector_stats_by_mode("unused", {issue["id"]: issue}, 10)
    assert before["lenient"][1] == 0
    assert before["strict"][1] == 0
    assert before["verified_strict"][1] == 1

    issue["resolution_attestation"]["scan_verified"] = True
    after = detector_stats_by_mode("unused", {issue["id"]: issue}, 10)
    assert after["verified_strict"] == (1.0, 0, 0.0)


def test_wontfix_passes_strict_and_verified_once_scan_confirms():
    issue = {
        "id": "unused::a::x",
        "detector": "unused",
        "file": "a.ts",
        "confidence": "high",
        "status": "wontfix",
        "resolution_attestation": {"kind": "manual", "scan_verified": False},
    }
    before = detector_stats_by_mode("unused", {issue["id"]: issue}, 10)
    assert [before[mode][1] for mode in SCORING_MODES] == [0, 1, 1]

    issue["resolution_attestation"]["scan_verified"] = True
    after = detector_stats_by_mode("unused", {issue["id"]: issue}, 10)
    for mode in SCORING_MODES:
        assert after[mode] == (1.0, 0, 0.0), mode


def test_dimension_whose_detector_ran_with_no_checks_is_not_carried_forward(scanned):
    """Fixing the last finding can leave a detector with nothing to check.

    The dimension then drops out of the bundle; its old failing score must not
    be carried forward, or the score never reaches 100.
    """
    state, root = scanned
    with_coverage = {**_POTENTIALS, "test_coverage": 3}
    _scan(state, [_raw_issue("test_coverage", "untested")], root, with_coverage)
    assert state["dimension_scores"]["Test health"]["score"] < 100.0

    _scan(state, [], root, {**_POTENTIALS, "test_coverage": 0})

    assert "Test health" not in state["dimension_scores"]
    assert state["objective_score"] == 100.0
    assert state["verified_strict_score"] == 100.0


def test_dimension_whose_detector_did_not_run_is_carried_forward(scanned):
    state, root = scanned
    with_coverage = {**_POTENTIALS, "test_coverage": 3}
    _scan(state, [_raw_issue("test_coverage", "untested")], root, with_coverage)

    # test_coverage reports no potential at all: it didn't run this time.
    _scan(state, [], root, dict(_POTENTIALS))

    assert state["dimension_scores"]["Test health"]["carried_forward"] is True


def test_carried_forward_dimension_expires(scanned):
    state, root = scanned
    with_coverage = {**_POTENTIALS, "test_coverage": 3}
    _scan(state, [_raw_issue("test_coverage", "untested")], root, with_coverage)
    measured_at = state["scan_count"]

    for _ in range(CARRIED_FORWARD_MAX_SCANS):
        _scan(state, [], root, dict(_POTENTIALS))
        carried = state["dimension_scores"]["Test health"]
        assert carried["carried_forward"] is True
        assert carried["carried_forward_since_scan"] == measured_at + 1

    _scan(state, [], root, dict(_POTENTIALS))
    assert "Test health" not in state["dimension_scores"]
    assert state["objective_score"] == 100.0

    # Once the detector runs again the dimension is measured afresh.
    _scan(state, [], root, with_coverage)
    assert state["dimension_scores"]["Test health"].get("carried_forward") is None


def test_saving_between_scans_does_not_age_a_carried_dimension(scanned):
    from desloppify.engine._scoring.state_integration import recompute_stats

    state, root = scanned
    with_coverage = {**_POTENTIALS, "test_coverage": 3}
    _scan(state, [_raw_issue("test_coverage", "untested")], root, with_coverage)
    _scan(state, [], root, dict(_POTENTIALS))

    for _ in range(CARRIED_FORWARD_MAX_SCANS + 1):
        recompute_stats(state, scan_path=".")

    assert state["dimension_scores"]["Test health"]["carried_forward"] is True
