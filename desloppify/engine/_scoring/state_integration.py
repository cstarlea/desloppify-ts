"""Bridge between state persistence and scoring computation.

This module owns the score-recomputation step that runs before state is written.
The dependency direction is: _scoring/state_integration -> _state (reads state),
_scoring/state_integration -> _scoring (calls scoring functions).
State persistence calls this module, never the reverse.
"""

from __future__ import annotations

from desloppify.base.enums import issue_status_tokens
from desloppify.engine._scoring.detection import merge_potentials
from desloppify.engine._scoring.policy.core import (
    CARRIED_FORWARD_MAX_SCANS,
    is_wontfix_debt,
)
from desloppify.engine._scoring.results.core import (
    compute_health_score,
    compute_score_bundle,
)
from desloppify.engine._scoring.state_integration_subjective import (
    _apply_subjective_integrity_policy,
    _normalize_integrity_target,
    _subjective_integrity_baseline,
)
from desloppify.engine._scoring.state_coverage import (
    apply_scan_coverage_to_dimension_scores as _apply_scan_coverage_to_dimension_scores,
)
from desloppify.engine._state.scope import path_scoped_issues
from desloppify.engine._state.schema import StateModel, ensure_state_defaults

_EMPTY_COUNTERS = tuple(sorted(issue_status_tokens()))


def _resolve_lang_from_state(state: StateModel) -> str | None:
    """Best-effort language detection from state (scan_history > lang_capabilities)."""
    history = state.get("scan_history")
    if isinstance(history, list):
        for entry in reversed(history):
            if isinstance(entry, dict):
                lang = entry.get("lang")
                if isinstance(lang, str) and lang.strip():
                    return lang.strip().lower()
    capabilities = state.get("lang_capabilities")
    if isinstance(capabilities, dict) and len(capabilities) == 1:
        only_lang = next(iter(capabilities.keys()))
        if isinstance(only_lang, str) and only_lang.strip():
            return only_lang.strip().lower()
    return None


def _count_issues(issues: dict) -> tuple[dict[str, int], dict[int, dict[str, int]]]:
    """Tally per-status counters and per-tier breakdowns."""
    counters = dict.fromkeys(_EMPTY_COUNTERS, 0)
    tier_stats: dict[int, dict[str, int]] = {}

    for issue in issues.values():
        if issue.get("suppressed"):
            continue
        status = issue["status"]
        tier = issue.get("tier", 3)
        counters[status] = counters.get(status, 0) + 1
        tier_counter = tier_stats.setdefault(tier, dict.fromkeys(_EMPTY_COUNTERS, 0))
        tier_counter[status] = tier_counter.get(status, 0) + 1

    return counters, tier_stats


def _count_wontfix_debt(issues: dict) -> tuple[int, dict[str, int]]:
    """Count wontfix issues strict still fails, in total and per tier."""
    total = 0
    by_tier: dict[str, int] = {}
    for issue in issues.values():
        if issue.get("suppressed") or not is_wontfix_debt(issue):
            continue
        total += 1
        tier = str(issue.get("tier", 3))
        by_tier[tier] = by_tier.get(tier, 0) + 1
    return total, dict(sorted(by_tier.items()))


def _aggregate_scores(dim_scores: dict) -> dict[str, float]:
    """Derive the four aggregate scores from dimension-level data."""
    mechanical = {
        n: d
        for n, d in dim_scores.items()
        if "subjective_assessment" not in d.get("detectors", {})
    }
    return {
        "overall_score": compute_health_score(dim_scores),
        "strict_score": compute_health_score(dim_scores, score_key="strict"),
        "objective_score": compute_health_score(mechanical),
        "verified_strict_score": compute_health_score(
            mechanical,
            score_key="verified_strict_score",
        ),
    }


def _set_perfect_scores(state: StateModel) -> None:
    """Set all score fields to 100 when there are no active checks."""
    state["dimension_scores"] = {}
    state["overall_score"] = 100.0
    state["objective_score"] = 100.0
    state["strict_score"] = 100.0
    state["verified_strict_score"] = 100.0


def _resolve_allowed_subjective_dimensions(
    state: StateModel,
) -> set[str] | None:
    """Resolve allowed subjective dimensions from the language config."""
    lang_name = _resolve_lang_from_state(state)
    if not lang_name:
        return None
    try:
        from desloppify.intelligence.review.dimensions.data import (
            load_dimensions_for_lang,
        )

        dims, _, _ = load_dimensions_for_lang(lang_name)
        if dims:
            return set(dims)
    except (ImportError, AttributeError) as exc:
        _ = exc
    return None


def _scan_count(state: StateModel) -> int:
    try:
        return int(state.get("scan_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _carried_since(prev_data: dict, scan_count: int) -> int:
    """Return the first scan *prev_data* was carried in (this one if it wasn't)."""
    since = prev_data.get("carried_forward_since_scan")
    if prev_data.get("carried_forward") and isinstance(since, int) and since <= scan_count:
        return since
    return scan_count


def _materialize_dimension_scores(
    state: StateModel,
    bundle: object,
    potentials: dict[str, int],
) -> None:
    """Write dimension scores from a score bundle into state, carrying forward old dims.

    A dimension missing from the bundle is carried forward only when none of
    its detectors reported a potential this time (the detectors didn't run).
    A detector that ran and reported zero checks has nothing left to fail, so
    its old score is dropped rather than kept forever. A carried score expires
    after ``CARRIED_FORWARD_MAX_SCANS`` scans without its detectors running.
    """
    lenient_scores = bundle.dimension_scores
    strict_scores = bundle.strict_dimension_scores
    verified_strict_scores = bundle.verified_strict_dimension_scores

    prev_dim_scores = dict(state.get("dimension_scores", {}))
    scan_count = _scan_count(state)
    disabled = set(state.get("disabled_detectors") or [])

    state["dimension_scores"] = {
        name: dict(
            score=lenient_scores[name]["score"],
            strict=strict_scores[name]["score"],
            verified_strict_score=verified_strict_scores[name]["score"],
            checks=lenient_scores[name]["checks"],
            failing=lenient_scores[name]["failing"],
            tier=lenient_scores[name]["tier"],
            detectors=lenient_scores[name].get("detectors", {}),
        )
        for name in lenient_scores
    }

    for dim_name, prev_data in prev_dim_scores.items():
        if dim_name in state["dimension_scores"]:
            continue
        if not isinstance(prev_data, dict):
            continue
        prev_detectors = prev_data.get("detectors", {})
        if "subjective_assessment" in prev_detectors:
            continue
        if any(detector in potentials for detector in prev_detectors):
            continue
        # A disabled detector's old score isn't carried: it is out of scoring.
        if any(detector in disabled for detector in prev_detectors):
            continue
        since = _carried_since(prev_data, scan_count)
        if scan_count - since >= CARRIED_FORWARD_MAX_SCANS:
            continue
        carried = {**prev_data, "carried_forward": True, "carried_forward_since_scan": since}
        carried.setdefault("score", 0.0)
        carried.setdefault("strict", carried.get("score", 0.0))
        carried.setdefault(
            "verified_strict_score",
            carried.get("strict", carried.get("score", 0.0)),
        )
        state["dimension_scores"][dim_name] = carried

    _apply_scan_coverage_to_dimension_scores(
        state,
        dimension_scores=state["dimension_scores"],
    )
    state.update(_aggregate_scores(state["dimension_scores"]))


def _update_objective_health(
    state: StateModel,
    issues: dict,
    *,
    subjective_integrity_target: float | None = None,
) -> None:
    """Compute canonical score tuple from current detector issues/potentials."""
    pots = state.get("potentials", {})
    if not pots:
        return

    disabled = set(state.get("disabled_detectors") or [])
    merged = {
        detector: count
        for detector, count in merge_potentials(pots).items()
        if detector not in disabled
    }
    if not merged:
        return

    subjective_assessments = state.get("subjective_assessments") or None
    integrity_target = _normalize_integrity_target(subjective_integrity_target)
    integrity_meta = _subjective_integrity_baseline(integrity_target)
    if subjective_assessments and integrity_target is not None:
        subjective_assessments, integrity_meta = _apply_subjective_integrity_policy(
            subjective_assessments,
            target=integrity_target,
        )
    state["subjective_integrity"] = integrity_meta

    has_active_checks = any((count or 0) > 0 for count in merged.values())
    if not has_active_checks and not subjective_assessments:
        _set_perfect_scores(state)
        return

    allowed_subjective = _resolve_allowed_subjective_dimensions(state)

    bundle = compute_score_bundle(
        issues,
        merged,
        subjective_assessments=subjective_assessments,
        allowed_subjective_dimensions=allowed_subjective,
    )
    _materialize_dimension_scores(state, bundle, merged)


def recompute_stats(
    state: StateModel,
    scan_path: str | None = None,
    *,
    subjective_integrity_target: float | None = None,
) -> None:
    """Recompute stats and canonical health scores from issues."""
    ensure_state_defaults(state)
    issues = path_scoped_issues(state.get("work_items") or state.get("issues", {}), scan_path)
    counters, tier_stats = _count_issues(issues)
    wontfix_debt, wontfix_debt_by_tier = _count_wontfix_debt(issues)
    state["stats"] = {
        "total": sum(counters.values()),
        **counters,
        "wontfix_debt": wontfix_debt,
        "wontfix_debt_by_tier": wontfix_debt_by_tier,
        "by_tier": {
            str(tier): tier_counts for tier, tier_counts in sorted(tier_stats.items())
        },
    }
    _update_objective_health(
        state,
        issues,
        subjective_integrity_target=subjective_integrity_target,
    )


__all__ = [
    "_count_issues",
    "_update_objective_health",
    "recompute_stats",
]
