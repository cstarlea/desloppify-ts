"""The headline score is objective, marked provisional, until a review."""

from __future__ import annotations

from desloppify.intelligence.review.dimensions.data import load_dimensions_for_lang
from desloppify.state import MergeScanOptions, empty_state, merge_scan
from desloppify.state_scoring import headline_score, subjective_unassessed

_NOW = "2026-01-01T00:00:00+00:00"


def _issue(name: str) -> dict:
    return {
        "id": f"unused::src/a.ts::{name}",
        "detector": "unused",
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


def _scanned(assessed: list[str]) -> dict:
    state = empty_state()
    state["subjective_assessments"] = {
        key: {"score": 80.0, "assessed_at": _NOW} for key in assessed
    }
    merge_scan(
        state,
        [_issue("x")],
        MergeScanOptions(lang="typescript", scan_path=".", potentials={"unused": 10}),
    )
    return state


def test_first_scan_headlines_objective_as_provisional():
    state = _scanned([])

    assert subjective_unassessed(state) is True
    assert state["overall_score"] <= 25.0
    headline = headline_score(state)
    assert headline.name == "objective"
    assert headline.provisional is True
    assert headline.score == state["objective_score"]


def test_any_assessment_restores_the_overall_headline():
    one_key = load_dimensions_for_lang("typescript")[0][0]
    state = _scanned([one_key])

    assert subjective_unassessed(state) is False
    assert headline_score(state) == ("overall", state["overall_score"], False)


def test_state_without_subjective_dimensions_is_not_provisional():
    state = empty_state()
    assert subjective_unassessed(state) is False
    assert headline_score(state).name == "overall"


def test_scan_summary_prints_the_provisional_headline(capsys):
    from desloppify.app.commands.scan.reporting.summary import (
        _print_provisional_headline,
    )

    state = _scanned([])
    _print_provisional_headline(state)
    out = capsys.readouterr().out
    assert f"Score: {state['objective_score']:.1f}/100 objective (provisional" in out

    _print_provisional_headline(
        _scanned([load_dimensions_for_lang("typescript")[0][0]])
    )
    assert capsys.readouterr().out == ""
