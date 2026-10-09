"""Tests for framework phase builder helpers."""

from __future__ import annotations


from desloppify.languages._framework.base.phase_builders import (
    detector_phase_boilerplate_duplication,
    detector_phase_duplicates,
    detector_phase_security,
    detector_phase_signature,
    detector_phase_subjective_review,
    detector_phase_test_coverage,
    shared_subjective_duplicates_tail,
)
from desloppify.languages._framework.base.types import DetectorPhase

# ── Individual factory functions ──────────────────────────────


def test_detector_phase_test_coverage_returns_correct_label():
    phase = detector_phase_test_coverage()
    assert isinstance(phase, DetectorPhase)
    assert phase.label == "Test coverage"
    assert phase.slow is False


def test_detector_phase_security_returns_correct_label():
    phase = detector_phase_security()
    assert isinstance(phase, DetectorPhase)
    assert phase.label == "Security"
    assert phase.slow is False


def test_detector_phase_signature_returns_correct_label():
    phase = detector_phase_signature()
    assert isinstance(phase, DetectorPhase)
    assert phase.label == "Signature analysis"
    assert phase.slow is False


def test_detector_phase_subjective_review_returns_correct_label():
    phase = detector_phase_subjective_review()
    assert isinstance(phase, DetectorPhase)
    assert phase.label == "Subjective review"
    assert phase.slow is False


def test_detector_phase_duplicates_is_slow():
    phase = detector_phase_duplicates()
    assert isinstance(phase, DetectorPhase)
    assert phase.label == "Duplicates"
    assert phase.slow is True


def test_detector_phase_boilerplate_duplication_is_slow():
    phase = detector_phase_boilerplate_duplication()
    assert isinstance(phase, DetectorPhase)
    assert phase.label == "Boilerplate duplication"
    assert phase.slow is True


# ── All factory functions produce callable run ────────────────


# ── shared_subjective_duplicates_tail ─────────────────────────


def test_shared_tail_default_has_three_phases():
    """Default tail: subjective review, boilerplate duplication, duplicates."""
    phases = shared_subjective_duplicates_tail()
    assert len(phases) == 3
    assert phases[0].label == "Subjective review"
    assert phases[1].label == "Boilerplate duplication"
    assert phases[2].label == "Duplicates"


def test_shared_tail_with_pre_duplicates_inserts_in_middle():
    """Extra phases go between subjective review and boilerplate duplication."""
    custom = DetectorPhase("Custom detector", lambda p, lang: ([], {}))
    phases = shared_subjective_duplicates_tail(pre_duplicates=[custom])
    assert len(phases) == 4
    assert phases[0].label == "Subjective review"
    assert phases[1].label == "Custom detector"
    assert phases[2].label == "Boilerplate duplication"
    assert phases[3].label == "Duplicates"


def test_shared_tail_with_multiple_pre_duplicates():
    """Multiple pre_duplicates are inserted in order."""
    custom_a = DetectorPhase("Alpha", lambda p, lang: ([], {}))
    custom_b = DetectorPhase("Beta", lambda p, lang: ([], {}))
    phases = shared_subjective_duplicates_tail(pre_duplicates=[custom_a, custom_b])
    assert len(phases) == 5
    labels = [p.label for p in phases]
    assert labels == [
        "Subjective review",
        "Alpha",
        "Beta",
        "Boilerplate duplication",
        "Duplicates",
    ]


def test_shared_tail_empty_pre_duplicates_same_as_default():
    """Empty list for pre_duplicates behaves like None."""
    phases = shared_subjective_duplicates_tail(pre_duplicates=[])
    assert len(phases) == 3


def test_shared_tail_slow_flags():
    """Last two phases (boilerplate duplication + duplicates) are slow."""
    phases = shared_subjective_duplicates_tail()
    assert phases[0].slow is False  # subjective review
    assert phases[1].slow is True   # boilerplate duplication
    assert phases[2].slow is True   # duplicates
