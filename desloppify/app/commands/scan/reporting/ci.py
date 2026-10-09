"""Plain scan report for ``scan --profile ci``: no colour, coaching or agent blocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from desloppify import state as state_mod
from desloppify.engine._state.schema import StateModel


@dataclass(frozen=True)
class ScoreGate:
    """``--fail-under`` threshold applied to one of the four scores."""

    score_name: str
    threshold: float

    def check(self, scores: state_mod.ScoreSnapshot) -> tuple[bool, float | None]:
        """Return (passed, score). A missing score fails."""
        value = getattr(scores, self.score_name)
        return (value is not None and value >= self.threshold), value


def score_gate_from_args(args: object) -> ScoreGate | None:
    threshold = getattr(args, "fail_under", None)
    if threshold is None:
        return None
    return ScoreGate(
        str(getattr(args, "fail_score", None) or "objective"), float(threshold)
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _mechanical_rows(dim_scores: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(
        (name, data)
        for name, data in dim_scores.items()
        if isinstance(data, dict)
        and "subjective_assessment" not in data.get("detectors", {})
    )


def _subjective_counts(dim_scores: dict[str, Any]) -> tuple[int, int]:
    total = assessed = 0
    for data in dim_scores.values():
        if not isinstance(data, dict):
            continue
        meta = data.get("detectors", {}).get("subjective_assessment")
        if meta is None:
            continue
        total += 1
        if not (isinstance(meta, dict) and meta.get("placeholder")):
            assessed += 1
    return total, assessed


def ci_report_lines(
    state: StateModel,
    diff: dict[str, Any],
    warnings: list[str],
    gate: ScoreGate | None,
) -> list[str]:
    """Build the plain report printed by ``scan --profile ci``."""
    scores = state_mod.score_snapshot(state)
    stats = state.get("stats", {})
    dim_scores = state.get("dimension_scores") or {}
    lines = [
        "desloppify scan (profile ci)",
        f"scores: overall {_fmt(scores.overall)}  objective {_fmt(scores.objective)}  "
        f"strict {_fmt(scores.strict)}  verified {_fmt(scores.verified)}",
    ]
    rows = _mechanical_rows(dim_scores)
    if rows:
        width = max(len(name) for name, _ in rows)
        lines.append("dimensions:")
        for name, data in rows:
            suffix = "  (prior scan)" if data.get("carried_forward") else ""
            lines.append(
                f"  {name:<{width}}  {_fmt(data.get('score'))}  "
                f"strict {_fmt(data.get('strict'))}  failing {int(data.get('failing', 0) or 0)}"
                f"{suffix}"
            )
    total_subj, assessed = _subjective_counts(dim_scores)
    if total_subj:
        lines.append(
            f"subjective: {assessed} of {total_subj} dimensions assessed"
            + ("" if assessed else " (overall and strict count them as 0)")
        )
    lines.append(
        f"issues: {int(stats.get('open', 0) or 0)} open "
        f"(+{int(diff.get('new', 0) or 0)} new, "
        f"-{int(diff.get('auto_resolved', 0) or 0)} resolved, "
        f"{int(diff.get('reopened', 0) or 0)} reopened)"
    )
    lines.extend(f"warning: {warning}" for warning in warnings)
    if gate is not None:
        passed, value = gate.check(scores)
        relation = ">=" if passed else "<"
        lines.append(
            f"gate: {gate.score_name} {_fmt(value)} {relation} {gate.threshold:.1f}: "
            + ("pass" if passed else "FAIL")
        )
    return lines


__all__ = ["ScoreGate", "ci_report_lines", "score_gate_from_args"]
