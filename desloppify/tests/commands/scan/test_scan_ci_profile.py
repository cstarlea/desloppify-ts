"""``scan --profile ci``: plain report and the ``--fail-under`` exit code."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import desloppify.app.commands.scan.cmd as scan_cmd_mod
from desloppify.app.cli_support.parser import create_parser
from desloppify.app.commands.scan.reporting.ci import (
    ScoreGate,
    ci_report_lines,
    score_gate_from_args,
)
from desloppify.base.exception_sets import CommandError


def _state(**overrides) -> dict:
    state = {
        "overall_score": 22.9,
        "objective_score": 91.7,
        "strict_score": 22.9,
        "verified_strict_score": 91.7,
        "stats": {"open": 12},
        "dimension_scores": {
            "Code quality": {"score": 86.4, "strict": 86.4, "failing": 12, "detectors": {"smells": {}}},
            "Duplication": {
                "score": 100.0,
                "strict": 100.0,
                "failing": 0,
                "detectors": {"dupes": {}},
                "carried_forward": True,
            },
            "Naming quality": {
                "score": 0.0,
                "strict": 0.0,
                "failing": 0,
                "detectors": {"subjective_assessment": {"placeholder": True}},
            },
        },
    }
    state.update(overrides)
    return state


_DIFF = {"new": 12, "auto_resolved": 1, "reopened": 0}


def test_report_is_plain_and_complete():
    lines = ci_report_lines(_state(), _DIFF, ["Coverage reduced (unused): tsc unavailable"], None)

    assert lines == [
        "desloppify scan (profile ci)",
        "scores: overall 22.9  objective 91.7  strict 22.9  verified 91.7",
        "dimensions:",
        "  Code quality  86.4  strict 86.4  failing 12",
        "  Duplication   100.0  strict 100.0  failing 0  (prior scan)",
        "subjective: 0 of 1 dimensions assessed (overall and strict count them as 0)",
        "issues: 12 open (+12 new, -1 resolved, 0 reopened)",
        "warning: Coverage reduced (unused): tsc unavailable",
    ]
    assert not any("\x1b[" in line for line in lines)


@pytest.mark.parametrize(
    ("gate", "expected"),
    [
        (ScoreGate("objective", 80.0), "gate: objective 91.7 >= 80.0: pass"),
        (ScoreGate("objective", 95.0), "gate: objective 91.7 < 95.0: FAIL"),
        (ScoreGate("strict", 20.0), "gate: strict 22.9 >= 20.0: pass"),
    ],
)
def test_report_ends_with_the_gate(gate, expected):
    assert ci_report_lines(_state(), _DIFF, [], gate)[-1] == expected


def test_missing_score_fails_the_gate():
    passed, value = ScoreGate("verified", 0.0).check(
        SimpleNamespace(overall=None, objective=None, strict=None, verified=None)
    )
    assert (passed, value) == (False, None)


def test_parser_reads_fail_under_and_fail_score():
    parser = create_parser(detector_names=[])
    args = parser.parse_args(
        ["scan", "--profile", "ci", "--fail-under", "80", "--fail-score", "verified"]
    )
    assert score_gate_from_args(args) == ScoreGate("verified", 80.0)

    default = parser.parse_args(["scan", "--fail-under", "70"])
    assert score_gate_from_args(default) == ScoreGate("objective", 70.0)
    assert score_gate_from_args(parser.parse_args(["scan"])) is None


def test_failed_gate_exits_with_status_1():
    with pytest.raises(CommandError) as excinfo:
        scan_cmd_mod._enforce_score_gate(ScoreGate("objective", 95.0), _state())
    assert excinfo.value.exit_code == 1
    assert "objective score 91.7 is below --fail-under 95.0" in excinfo.value.message

    scan_cmd_mod._enforce_score_gate(ScoreGate("objective", 90.0), _state())
    scan_cmd_mod._enforce_score_gate(None, _state())
