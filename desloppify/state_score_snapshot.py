"""Canonical score snapshot helpers for state-derived scoring reads."""

from __future__ import annotations

from typing import NamedTuple

from desloppify.engine._state.schema import StateModel
from desloppify.engine._state.schema_scores import (
    get_objective_score,
    get_overall_score,
    get_strict_score,
    get_verified_strict_score,
)
from desloppify.engine._state.scoring import suppression_metrics


class ScoreSnapshot(NamedTuple):
    """All four canonical scores from a single state dict."""

    overall: float | None
    objective: float | None
    strict: float | None
    verified: float | None


class HeadlineScore(NamedTuple):
    """The score to lead with, and whether it is provisional."""

    name: str  # "overall" or "objective"
    score: float | None
    provisional: bool


def subjective_unassessed(state: StateModel) -> bool:
    """Return True when the score has subjective dimensions and none is assessed."""
    subjective = [
        data["detectors"]["subjective_assessment"]
        for data in (state.get("dimension_scores") or {}).values()
        if isinstance(data, dict)
        and isinstance(data.get("detectors"), dict)
        and "subjective_assessment" in data["detectors"]
    ]
    return bool(subjective) and all(
        isinstance(meta, dict) and meta.get("placeholder") for meta in subjective
    )


def headline_score(state: StateModel) -> HeadlineScore:
    """Return the headline score.

    Overall blends in subjective dimensions at 75%, and an unassessed one
    scores 0, so before the first review overall can't pass 25. Until then
    the objective score leads, marked provisional.
    """
    if subjective_unassessed(state):
        return HeadlineScore("objective", get_objective_score(state), True)
    return HeadlineScore("overall", get_overall_score(state), False)


def score_snapshot(state: StateModel) -> ScoreSnapshot:
    """Load all four canonical scores from `state` in one call."""
    return ScoreSnapshot(
        overall=get_overall_score(state),
        objective=get_objective_score(state),
        strict=get_strict_score(state),
        verified=get_verified_strict_score(state),
    )


__all__ = [
    "HeadlineScore",
    "ScoreSnapshot",
    "get_objective_score",
    "get_overall_score",
    "get_strict_score",
    "get_verified_strict_score",
    "headline_score",
    "score_snapshot",
    "subjective_unassessed",
    "suppression_metrics",
]
