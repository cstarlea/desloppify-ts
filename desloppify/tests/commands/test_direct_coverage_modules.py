"""Direct coverage smoke tests for modules often covered only transitively."""

from __future__ import annotations

from types import SimpleNamespace

import desloppify.app.commands.next.output as next_output
import desloppify.app.commands.registry as cmd_registry
import desloppify.engine._state.noise as noise
import desloppify.engine._work_queue.finalize as work_queue_finalize_mod
import desloppify.engine._work_queue.inputs as work_queue_inputs_mod
import desloppify.engine._work_queue.selection as work_queue_selection_mod
import desloppify.languages.typescript.detectors.smells.helpers as ts_smell_helpers_mod
from desloppify.engine._work_queue.models import QueueBuildOptions, QueueVisibility
from desloppify.languages.typescript.syntax.scanner import SourceText


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

    source = SourceText(
        "\n".join(
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
    )
    assert source.kind_at(source.line_starts[2]) == "comment"
    assert source.kind_at(source.line_starts[5]) == "template"
    assert source.kind_at(source.line_starts[0]) is None


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
