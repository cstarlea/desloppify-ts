"""Post-render nudges and resolution hints for the `next` command."""

from __future__ import annotations

from desloppify.app.commands.helpers.queue_progress import (
    QueueBreakdown,
    ScoreDisplayMode,
    format_queue_block,
    headline_scores,
    is_first_scan,
    print_frozen_score_with_queue_context,
    score_display_mode,
)
from desloppify.app.commands.scan.reporting.subjective import (
    build_subjective_followup,
)
from desloppify.base.config import load_config
from desloppify.base.output.terminal import colorize, log
from desloppify.base.output.user_message import print_user_message
from desloppify.engine._scoring.results.core import compute_health_breakdown
from desloppify.engine._state.issue_semantics import is_assessment_request
from desloppify.engine._work_queue.core import ATTEST_EXAMPLE
from desloppify.engine._work_queue.helpers import is_auto_fix_item
from desloppify.engine.plan_state import uncommitted_source_note
from desloppify.intelligence.integrity import (
    unassessed_subjective_dimensions,
)

from .render_support import (
    is_auto_fix_command,
    scorecard_subjective,
    subjective_coverage_breakdown,
)


def render_uncommitted_reminder(plan: dict | None) -> None:
    """Show a subtle reminder if there are uncommitted resolved issues."""
    if plan is None:
        return
    try:
        config = load_config()
        if not config.get("commit_tracking_enabled", True):
            return

        uncommitted = plan.get("uncommitted_issues", [])
        if not uncommitted:
            return

        count = len(uncommitted)
        print(
            colorize(
                f"\n  {count} resolved issue{'s' if count != 1 else ''} uncommitted"
                f"{uncommitted_source_note(plan)}"
                " — `desloppify plan commit-log` to review",
                "dim",
            )
        )
    except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
        log(f"  uncommitted reminder skipped: {exc}")


def render_single_item_resolution_hint(items: list[dict]) -> None:
    if len(items) != 1:
        return
    kind = items[0].get("kind", "issue")
    if kind in ("cluster", "workflow_stage", "workflow_action"):
        return  # These kinds have their own resolution hints
    if kind != "issue":
        return
    item = items[0]
    if is_assessment_request(item):
        dim_key = (item.get("detail") or {}).get("dimension", "")
        primary = item.get("primary_command", "")
        if not primary:
            primary = (
                f"desloppify review --prepare --dimensions {dim_key}"
                if dim_key
                else "desloppify review --prepare"
            )
        print(colorize("\n  Review with:", "dim"))
        print(f"    {primary}")
        print_user_message(
            "Hey — this is a subjective review item. Run"
            " `desloppify review --run-batches --dry-run`"
            " to generate prompt files (one per batch)."
            " Launch one subagent per prompt, all in"
            " parallel. Then import with `--import-run"
            " <run-dir> --scan-after-import`. Batches are"
            " pre-defined — do NOT regroup them yourself."
        )
        return

    primary = item.get("primary_command", "")
    if is_auto_fix_item(item) and is_auto_fix_command(primary):
        print(colorize("\n  Fix with:", "dim"))
        print(f"    {primary}")
        print(colorize("  Or resolve individually:", "dim"))
    else:
        print(colorize("\n  Resolve with:", "dim"))

    print(
        f'    desloppify plan resolve "{item["id"]}" --note "<what you did>" --confirm'
    )
    print(
        f'    desloppify plan skip --permanent "{item["id"]}" --note "<why>" '
        f'--attest "{ATTEST_EXAMPLE}"'
    )


def _render_frozen_queue_status(
    *,
    strict_score: float | None,
    plan_start_strict: float | None,
    breakdown: QueueBreakdown | None,
    score_name: str = "strict",
    plan_start_score: float | None = None,
    first_scan: bool = False,
) -> bool:
    """Render frozen-score status. Returns True if score was rendered as frozen."""
    mode = score_display_mode(breakdown, plan_start_strict)
    if mode is not ScoreDisplayMode.FROZEN:
        return False
    print_frozen_score_with_queue_context(
        breakdown,
        frozen_strict=plan_start_score
        if plan_start_score is not None
        else plan_start_strict,
        live_score=strict_score,
        score_name=score_name,
        first_scan=first_scan,
    )
    return True


def _render_north_star(
    *,
    strict_score: float | None,
    target_strict_score: float,
    objective_score: float | None = None,
) -> None:
    if strict_score is None:
        return
    if objective_score is not None:
        print(
            colorize(
                f"\n  Score: objective {objective_score:.1f}/100 (subjective review "
                f"pending; strict {strict_score:.1f} counts the unassessed "
                "dimensions as 0)",
                "cyan",
            )
        )
        return
    gap = round(float(target_strict_score) - float(strict_score), 1)
    if gap > 0:
        print(
            colorize(
                f"\n  North star: strict {strict_score:.1f}/100 → target {target_strict_score:.1f} (+{gap:.1f} needed)",
                "cyan",
            )
        )
        return
    print(
        colorize(
            f"\n  North star: strict {strict_score:.1f}/100 meets target {target_strict_score:.1f}",
            "green",
        )
    )


def _render_live_queue_block(
    *,
    breakdown: QueueBreakdown | None,
    plan_start_strict: float | None,
) -> None:
    """Show queue block when score is not frozen (LIVE or PHASE_TRANSITION)."""
    if breakdown is None or breakdown.queue_total <= 0:
        return
    mode = score_display_mode(breakdown, plan_start_strict)
    if mode is ScoreDisplayMode.FROZEN:
        return  # frozen path renders its own block
    block = format_queue_block(breakdown)
    for text, style in block:
        print(colorize(text, style))


def _render_subjective_bottleneck(dim_scores: dict) -> None:
    try:
        health_breakdown = compute_health_breakdown(dim_scores)
        subjective_drag = sum(
            float(e.get("overall_drag", 0) or 0)
            for e in health_breakdown.get("entries", [])
            if isinstance(e, dict) and e.get("component") == "subjective"
        )
        mechanical_drag = sum(
            float(e.get("overall_drag", 0) or 0)
            for e in health_breakdown.get("entries", [])
            if isinstance(e, dict) and e.get("component") != "subjective"
        )
        if subjective_drag <= mechanical_drag or subjective_drag <= 5.0:
            return
        print(
            colorize(
                f"\n  Subjective dimensions are the main bottleneck "
                f"(-{subjective_drag:.0f} pts vs -{mechanical_drag:.0f} pts mechanical).",
                "yellow",
            )
        )
        print(
            colorize(
                "  Code fixes alone won't close the gap — run "
                "`desloppify review --prepare` and follow your "
                "skill doc's review workflow to re-score.",
                "yellow",
            )
        )
    except (ImportError, TypeError, ValueError, KeyError) as exc:
        log(f"  subjective bottleneck banner skipped: {exc}")


def _subjective_summary_parts(
    *,
    followup,
    unassessed_subjective: list[str],
    subjective_entries: list[dict],
    issues_scoped: dict,
    coverage_open: int,
) -> list[str]:
    parts: list[str] = []
    low_dims = len(followup.low_assessed)
    unassessed_count = len(unassessed_subjective)
    stale_count = sum(1 for entry in subjective_entries if entry.get("stale"))
    open_review_count = sum(
        1
        for issue in issues_scoped.values()
        if issue.get("status") == "open" and issue.get("detector") == "review"
    )
    if low_dims:
        parts.append(f"{low_dims} dimension{'s' if low_dims != 1 else ''} below target")
    if stale_count:
        parts.append(f"{stale_count} stale")
    if unassessed_count:
        parts.append(f"{unassessed_count} unassessed")
    if open_review_count:
        parts.append(
            f"{open_review_count} review work item{'s' if open_review_count != 1 else ''} open"
        )
    if coverage_open > 0:
        parts.append(
            f"{coverage_open} file{'s' if coverage_open != 1 else ''} need review"
        )
    return parts


def render_deferred_review_hint(snapshot: object) -> None:
    """Point at the first subjective review once the objective queue is done."""
    deferred = getattr(snapshot, "deferred_review_items", ())
    if not deferred or getattr(snapshot, "objective_execution_count", 0):
        return
    count = len(deferred)
    print(
        colorize(
            f"\n  Subjective review pending: {count} dimension"
            f"{'s are' if count != 1 else ' is'} unscored. This is optional and "
            "does not block `desloppify scan`.",
            "cyan",
        )
    )
    print(colorize("  To score them: `desloppify review --prepare`", "dim"))


def render_backlog_hint(snapshot: object) -> None:
    """Say where open objective work went when the queue has none of it."""
    backlog = getattr(snapshot, "objective_backlog_count", 0)
    if not backlog or getattr(snapshot, "objective_execution_count", 0):
        return
    print(
        colorize(
            f"\n  {backlog} open objective issue{'s are' if backlog != 1 else ' is'}"
            " in the backlog, outside the queue.",
            "cyan",
        )
    )
    print(
        colorize(
            "  Browse: `desloppify backlog` · Queue one: `desloppify plan promote <id>`",
            "dim",
        )
    )


def render_followup_nudges(
    state: dict,
    dim_scores: dict,
    issues_scoped: dict,
    *,
    strict_score: float | None,
    target_strict_score: float,
    queue_total: int = 0,
    plan_start_strict: float | None = None,
    breakdown: QueueBreakdown | None = None,
    plan: dict | None = None,
) -> None:
    subjective_threshold = target_strict_score
    score_name, plan_start_score, headline_live = headline_scores(state, plan)
    provisional = score_name == "objective"
    subjective_entries = scorecard_subjective(state, dim_scores)
    followup = build_subjective_followup(
        state,
        subjective_entries,
        threshold=subjective_threshold,
        max_quality_items=3,
        max_integrity_items=5,
    )
    unassessed_subjective = unassessed_subjective_dimensions(dim_scores)
    rendered_frozen = _render_frozen_queue_status(
        strict_score=headline_live if provisional else strict_score,
        plan_start_strict=plan_start_strict,
        breakdown=breakdown,
        score_name=score_name,
        plan_start_score=plan_start_score if provisional else None,
        first_scan=is_first_scan(state),
    )
    if not rendered_frozen:
        _render_north_star(
            strict_score=strict_score,
            target_strict_score=target_strict_score,
            objective_score=headline_live if provisional else None,
        )
    _render_live_queue_block(
        breakdown=breakdown,
        plan_start_strict=plan_start_strict,
    )

    # Subjective bottleneck banner — only shown when the objective queue is
    # clear.  While objective items remain, the queue is the single authority
    # on what to work on next; no need to distract with subjective advice.
    objective_remaining = breakdown.objective_actionable if breakdown else queue_total
    if strict_score is not None and dim_scores and objective_remaining <= 0:
        _render_subjective_bottleneck(dim_scores)

    # Integrity penalty/warn lines preserved (anti-gaming safeguard, must remain visible).
    for style, message in followup.integrity_lines:
        print(colorize(f"\n  {message}", style))

    # Rescan nudge after structural work
    if queue_total > 10:
        print(
            colorize(
                "\n  Tip: after structural fixes (splitting files, moving code), rescan to "
                "let cascade effects settle: `desloppify scan --path .`",
                "dim",
            )
        )

    # Collapsed subjective summary.
    coverage_open, _coverage_reasons, _holistic_reasons = subjective_coverage_breakdown(
        issues_scoped
    )
    parts = _subjective_summary_parts(
        followup=followup,
        unassessed_subjective=unassessed_subjective,
        subjective_entries=subjective_entries,
        issues_scoped=issues_scoped,
        coverage_open=coverage_open,
    )
    if parts:
        print(colorize(f"\n  Subjective: {', '.join(parts)}.", "cyan"))
        print(colorize("  Run `desloppify show subjective` for details.", "dim"))
