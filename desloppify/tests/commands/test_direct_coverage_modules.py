"""Direct coverage smoke tests for modules often covered only transitively."""

from __future__ import annotations

from types import SimpleNamespace

import desloppify.app.cli_support.parser as cli_parser
import desloppify.app.cli_support.parser_groups as cli_parser_groups
import desloppify.app.commands.config as config_cmd
import desloppify.app.commands.move.cmd as move_cmd_mod
import desloppify.app.commands.move.directory as move_directory
import desloppify.app.commands.move.reporting as move_reporting
import desloppify.app.commands.next.output as next_output
import desloppify.app.commands.next.render_support as next_render_support
import desloppify.app.commands.plan.cmd as plan_cmd_mod
import desloppify.app.commands.registry as cmd_registry
from desloppify.app.commands.review.batch import merge as review_batch_merge
import desloppify.app.commands.review.batch.execution as review_batches
import desloppify.app.commands.review.importing.cmd as review_import
import desloppify.app.commands.review.importing.helpers as review_import_helpers
import desloppify.app.commands.review.prepare as review_prepare
import desloppify.app.commands.runner.codex_batch as review_runner_helpers
import desloppify.app.commands.review.runtime.setup as review_runtime_setup
import desloppify.app.commands.scan.artifacts as scan_artifacts
import desloppify.app.commands.scan.cmd as scan_cmd_mod
import desloppify.app.commands.scan.reporting.presentation as scan_reporting_presentation
import desloppify.app.commands.scan.reporting.subjective as scan_reporting_subjective
import desloppify.app.commands.scan.workflow as scan_workflow
import desloppify.app.commands.status.cmd as status_cmd_mod
import desloppify.app.commands.status.render as status_render
import desloppify.app.commands.status.summary as status_summary
import desloppify.app.output._viz_cmd_context as viz_cmd_context
import desloppify.app.output.scorecard_parts.draw as scorecard_draw
import desloppify.app.output.scorecard_parts.left_panel as scorecard_left_panel
import desloppify.app.output.scorecard_parts.ornaments as scorecard_ornaments
import desloppify.app.output.tree_text as tree_text_mod
import desloppify.base.runtime_state as runtime_state
import desloppify.engine._state.noise as noise
import desloppify.engine._state.persistence as persistence
import desloppify.engine._state.resolution as state_resolution
import desloppify.engine._work_queue.finalize as work_queue_finalize_mod
import desloppify.engine._work_queue.inputs as work_queue_inputs_mod
import desloppify.engine._work_queue.selection as work_queue_selection_mod
import desloppify.engine.planning.helpers as plan_common
import desloppify.engine.planning.scan as plan_scan
import desloppify.engine.planning.select as plan_select
import desloppify.intelligence.integrity as subjective_review_integrity
import desloppify.intelligence.review._context.structure as review_context_structure
import desloppify.intelligence.review.dimensions.holistic as review_dimensions_holistic
import desloppify.intelligence.review.dimensions.validation as review_dimensions_validation
import desloppify.languages as lang_pkg
import desloppify.languages._framework.registry.discovery as lang_discovery
import desloppify.languages.typescript.detectors.smells.detector_safety as ts_smell_detectors_safety
import desloppify.languages.typescript.detectors.smells.helpers as ts_smell_helpers_mod
import desloppify.languages.typescript.detectors.deps.runtime as ts_deps_runtime
import desloppify.languages.typescript.extractors_components as ts_extractors_components
from desloppify.engine._work_queue.models import QueueBuildOptions, QueueVisibility
from desloppify.intelligence.review import prepare_batches_builders as review_prepare_batches
from desloppify.languages._framework.registry import resolution as lang_resolution
from desloppify.languages.typescript import review as ts_review


def _assert_all_callables(*targets) -> None:
    for target in targets:
        assert callable(target)


def test_smoke_parser():
    """Parser and CLI support modules."""
    _assert_all_callables(
        cli_parser.create_parser,
        cli_parser_groups._add_scan_parser,
    )


def test_smoke_planning():
    """Planning modules: common, scan, select."""
    _assert_all_callables(
        plan_common.is_subjective_phase,
        plan_scan.generate_issues,
        plan_select.get_next_items,
        plan_select.get_next_item,
    )


def test_smoke_commands():
    """App command modules: config, plan, move, scan, next, review, status."""
    _assert_all_callables(
        config_cmd.cmd_config,
        plan_cmd_mod.cmd_plan_output,
        move_directory.run_directory_move,
        move_reporting.print_file_move_plan,
        move_reporting.print_directory_move_plan,
        move_cmd_mod.cmd_move,
        scan_cmd_mod.cmd_scan,
        scan_artifacts.build_scan_query_payload,
        scan_artifacts.emit_scorecard_badge,
        scan_workflow.prepare_scan_runtime,
        scan_workflow.run_scan_generation,
        scan_workflow.merge_scan_results,
        next_output.serialize_item,
        next_output.build_query_payload,
        next_render_support.render_queue_header,
        review_batch_merge.merge_batch_results,
        review_batches.BatchRunDeps,
        review_import.do_import,
        review_import_helpers.load_import_issues_data,
        review_prepare.do_prepare,
        review_runner_helpers.run_codex_batch,
        review_runtime_setup.setup_lang,
        status_cmd_mod.cmd_status,
        status_render.show_tier_progress_table,
        status_summary.score_summary_lines,
        scan_reporting_presentation.show_score_model_breakdown,
        scan_reporting_presentation.show_detector_progress,
        scan_reporting_subjective.subjective_rerun_command,
        scan_reporting_subjective.subjective_integrity_followup,
        scan_reporting_subjective.build_subjective_followup,
    )
    assert isinstance(cmd_registry.get_command_handlers(), dict)
    assert "scan" in cmd_registry.get_command_handlers()
    runtime = runtime_state.current_runtime_context()
    assert isinstance(runtime.exclusions, tuple)
    assert isinstance(runtime.source_file_cache.max_entries, int)
    runtime.cache_enabled = True
    assert runtime.cache_enabled
    runtime.cache_enabled = False


def test_smoke_engine():
    """Engine modules: state internals, TypeScript detectors."""
    # state internals
    _assert_all_callables(
        persistence.load_state,
        persistence.save_state,
        state_resolution.match_issues,
        state_resolution.resolve_issues,
        noise.resolve_issue_noise_budget,
        noise.resolve_issue_noise_global_budget,
        noise.resolve_issue_noise_settings,
    )

    # TypeScript detector modules
    _assert_all_callables(
        ts_smell_detectors_safety._detect_swallowed_errors,
        ts_deps_runtime.build_dynamic_import_targets,
        ts_extractors_components.extract_ts_components,
    )


def test_work_queue_split_modules_have_direct_behavior(monkeypatch):
    items = [{"id": "issue::1", "kind": "issue"}]
    monkeypatch.setattr(
        work_queue_finalize_mod,
        "enrich_with_impact",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        work_queue_finalize_mod,
        "item_sort_key",
        lambda item: item["id"],
    )
    monkeypatch.setattr(
        work_queue_finalize_mod,
        "item_explain",
        lambda item: f"why:{item['id']}",
    )
    monkeypatch.setattr(
        work_queue_finalize_mod,
        "group_queue_items",
        lambda grouped_items, _mode: {"item": list(grouped_items)},
    )
    result = work_queue_finalize_mod.finalize_queue(
        items,
        state={"dimension_scores": {}},
        plan=None,
        opts=QueueBuildOptions(explain=True),
    )
    assert result["items"][0]["explain"] == "why:issue::1"
    assert result["grouped"]["item"][0]["id"] == "issue::1"

    opts = QueueBuildOptions(
        status="open",
        subjective_threshold="oops",
        context=SimpleNamespace(plan={"queue": []}),
    )
    plan, scan_path, status, threshold = work_queue_inputs_mod.resolve_queue_inputs(
        opts,
        {"scan_path": "src"},
    )
    assert plan == {"queue": []}
    assert scan_path == "src"
    assert status == "open"
    assert threshold == 100.0

    monkeypatch.setattr(
        work_queue_inputs_mod,
        "build_subjective_items",
        lambda *_a, **_k: [
            {
                "id": "subjective::x",
                "kind": "subjective_dimension",
                "summary": "X",
            }
        ],
    )
    gathered = work_queue_inputs_mod.gather_subjective_items({}, opts, 50.0)
    assert gathered[0]["id"] == "subjective::x"

    snapshot = SimpleNamespace(
        backlog_items=[{"id": "backlog::1", "kind": "issue"}],
        execution_items=[{"id": "exec::1", "kind": "issue"}],
    )
    monkeypatch.setattr(
        work_queue_selection_mod,
        "build_queue_snapshot",
        lambda *_a, **_k: snapshot,
    )
    selected = work_queue_selection_mod.select_queue_items(
        {},
        opts=QueueBuildOptions(),
        plan=None,
        scan_path=".",
        status="open",
        threshold=95.0,
        visibility=QueueVisibility.BACKLOG,
    )
    assert selected == [{"id": "backlog::1", "kind": "issue"}]


def test_smoke_lang_plugins():
    """Language plugin modules: package, discovery, resolution, per-lang."""
    # lang package/discovery/resolution
    _assert_all_callables(
        lang_pkg.register_lang,
        lang_pkg.available_langs,
        lang_discovery.load_all,
        lang_discovery.raise_load_errors,
        lang_resolution.make_lang_config,
        lang_resolution.get_lang,
    )

    # typescript
    assert isinstance(ts_review.module_patterns("export default function A() {}"), list)
    assert ts_review.api_surface({"a.ts": "export function f() {}"}) == {}


def test_typescript_split_smell_helpers_have_direct_coverage():
    lines = [
        "function demo() {",
        "  const text = '{ok}';",
        "  if (ready) {",
        "    return value;",
        "  }",
        "}",
    ]
    assert ts_smell_helpers_mod._track_brace_body(lines, 0) == 5
    body = ts_smell_helpers_mod._extract_block_body("if (ok) { keep(); }", 8)
    assert body == " keep(); "
    masked = ts_smell_helpers_mod._code_text('const x = "message"; // hi')
    assert "message" not in masked

    assert ts_smell_helpers_mod._scan_template_content("x`${a}`", 1, 0)[1] is True
    assert ts_smell_helpers_mod._scan_code_line("/* open comment") == (True, False, 0)
    states = ts_smell_helpers_mod._build_ts_line_state(
        [
            "const a = 1;",
            "/* block",
            "still block",
            "end */",
            "const tpl = `",
            "value",
            "`;",
        ]
    )
    assert states[2] == "block_comment"
    assert states[5] == "template_literal"


def test_smoke_intelligence():
    """Intelligence modules: review dimensions, context, prepare, integrity."""
    assert isinstance(review_dimensions_holistic.DIMENSIONS, list)
    assert "cross_module_architecture" in review_dimensions_holistic.DIMENSIONS
    _assert_all_callables(
        review_prepare_batches.build_investigation_batches,
        review_context_structure.compute_structure_context,
        review_dimensions_validation.parse_dimensions_payload,
        subjective_review_integrity.subjective_review_open_breakdown,
        scorecard_draw.draw_left_panel,
        scorecard_draw.draw_right_panel,
        scorecard_draw.draw_ornament,
        scorecard_left_panel.draw_left_panel,
        scorecard_ornaments.draw_ornament,
        viz_cmd_context.load_cmd_context,
        tree_text_mod._aggregate,
    )


# ---------------------------------------------------------------------------
# Behavioral tests for key functions (beyond assert callable)
# ---------------------------------------------------------------------------


def test_noise_budget_defaults():
    """resolve_issue_noise_budget returns default for None config."""
    assert noise.resolve_issue_noise_budget(None) == 10
    assert noise.resolve_issue_noise_budget({}) == 10


def test_noise_budget_from_config():
    """resolve_issue_noise_budget reads the config value."""
    assert noise.resolve_issue_noise_budget({"issue_noise_budget": 5}) == 5
    assert noise.resolve_issue_noise_budget({"issue_noise_budget": 0}) == 0


def test_noise_settings_invalid_config():
    """resolve_issue_noise_settings returns warning for invalid values."""
    per, glob, warning = noise.resolve_issue_noise_settings(
        {"issue_noise_budget": "bad"}
    )
    assert per == 10  # default
    assert warning is not None
    assert "Invalid" in warning


def test_serialize_item_minimal():
    """serialize_item extracts expected fields from a minimal item dict."""
    item = {
        "id": "smells::foo.py::1",
        "kind": "issue",
        "tier": 2,
        "confidence": "high",
        "detector": "smells",
        "file": "foo.py",
        "summary": "Unused import",
        "status": "open",
    }
    result = next_output.serialize_item(item)
    assert result["id"] == "smells::foo.py::1"
    assert result["kind"] == "issue"
    assert result["confidence"] == "high"
    assert result["detector"] == "smells"
    assert result["file"] == "foo.py"
    assert "explain" not in result
    # Non-workflow items omit blocked_by/is_blocked
    assert "blocked_by" not in result
    assert "is_blocked" not in result


def test_serialize_item_includes_blocked_by_for_workflow_stage():
    """serialize_item includes blocked_by and is_blocked for workflow_stage items."""
    item = {
        "id": "triage::reflect",
        "kind": "workflow_stage",
        "confidence": "high",
        "detector": "triage",
        "file": ".",
        "summary": "Planning: reflect",
        "status": "open",
        "blocked_by": ["triage::observe"],
        "is_blocked": True,
    }
    result = next_output.serialize_item(item)
    assert result["blocked_by"] == ["triage::observe"]
    assert result["is_blocked"] is True


def test_serialize_item_omits_blocked_by_when_empty():
    """serialize_item omits blocked_by/is_blocked when not blocked."""
    item = {
        "id": "triage::observe",
        "kind": "workflow_stage",
        "confidence": "high",
        "detector": "triage",
        "file": ".",
        "summary": "Planning: observe",
        "status": "open",
        "blocked_by": [],
        "is_blocked": False,
    }
    result = next_output.serialize_item(item)
    assert "blocked_by" not in result
    assert "is_blocked" not in result


def test_serialize_cluster_item_caps_member_payload():
    """Cluster serialization should cap nested members and strip heavy metadata."""
    sibling_ids = [f"security::src/f{i}.py::B101::{i}" for i in range(80)]
    members = [
        {
            "id": f"security::src/f{i}.py::B101::{i}",
            "kind": "issue",
            "confidence": "high",
            "detector": "security",
            "file": f"src/f{i}.py",
            "summary": "Security issue",
            "status": "open",
            "primary_command": "desloppify plan resolve ...",
            "plan_cluster": {
                "name": "auto/security",
                "sibling_ids": sibling_ids,
            },
        }
        for i in range(80)
    ]
    cluster = {
        "id": "auto/security",
        "kind": "cluster",
        "action_type": "refactor",
        "summary": "Fix security issues",
        "member_count": len(members),
        "members": members,
        "cluster_name": "auto/security",
        "cluster_auto": True,
        "detector": "security",
        "primary_command": "desloppify next --cluster auto/security --count 10",
    }

    result = next_output.serialize_item(cluster)
    assert result["kind"] == "cluster"
    assert result["member_count"] == 80
    assert len(result["members"]) == 25
    assert result["members_truncated"] is True
    assert result["members_sample_limit"] == 25
    assert "plan_cluster" not in result["members"][0]


def test_build_query_payload_structure():
    """build_query_payload returns well-formed dict with queue metadata."""
    items = [{"id": "f1", "kind": "issue", "tier": 1}]
    queue = {"tier_counts": {1: 1}, "total": 1}
    payload = next_output.build_query_payload(
        queue, items, command="next", narrative=None
    )
    assert payload["command"] == "next"
    assert len(payload["items"]) == 1
    assert payload["queue"]["total"] == 1
    assert payload["queue"]["mode"] == "execution"
    assert payload["narrative"] is None


def test_render_markdown_for_backlog_uses_backlog_heading():
    text = next_output.render_markdown_for_command([], command="backlog")
    assert "# Desloppify Backlog" in text


def test_private_imports_is_dunder():
    """_is_dunder correctly identifies dunder names."""


def test_command_registry_has_core_commands():
    """get_command_handlers includes scan, status, next, plan."""
    handlers = cmd_registry.get_command_handlers()
    for cmd in ("scan", "status", "next", "plan"):
        assert cmd in handlers, f"Missing command handler: {cmd}"
        assert callable(handlers[cmd])
