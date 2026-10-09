"""Direct coverage smoke tests for high-priority untested modules."""

from __future__ import annotations

from pathlib import Path

import desloppify.app.commands.helpers.display as display_mod
import desloppify.app.commands.review.importing.parse as review_import_parse_mod
import desloppify.languages.typescript.fixers.syntax_scan as ts_syntax_scan_mod


def test_direct_coverage_priority_modules_behavior():
    assert display_mod.short_issue_id("foo::bar::baz").startswith("foo")
    assert ts_syntax_scan_mod.collapse_blank_lines(["a", "", "", "b"]) == ["a", "", "b"]


def test_review_import_parse_normalizes_legacy_findings_alias():
    payload, errors = review_import_parse_mod._normalize_import_root_payload(
        {"findings": []}
    )
    assert errors == []
    assert payload == {"issues": []}


def test_app_plan_modules_avoid_old_plan_queue_facade():
    """After plan_queue removal, app modules import _plan directly.

    This test verifies the *old* facade is not referenced.
    """
    package_root = Path(__file__).resolve().parents[2]
    app_root = package_root / "app"
    for module_path in app_root.rglob("*.py"):
        text = module_path.read_text(encoding="utf-8")
        assert "desloppify.engine.plan_queue" not in text, str(module_path.relative_to(package_root))


def test_selected_command_modules_use_focused_plan_facades() -> None:
    package_root = Path(__file__).resolve().parents[2]
    rel_paths = (
        "app/commands/helpers/guardrails.py",
        "app/commands/plan/triage/command.py",
        "app/commands/review/importing/plan_sync.py",
    )
    for rel_path in rel_paths:
        text = (package_root / rel_path).read_text(encoding="utf-8")
        assert "from desloppify.engine.plan import" not in text, rel_path
        assert "from desloppify.engine import plan as" not in text, rel_path


def test_next_and_status_init_modules_are_stub_only():
    package_root = Path(__file__).resolve().parents[2]
    for rel_path in (
        "app/commands/next/__init__.py",
        "app/commands/status/__init__.py",
    ):
        text = (package_root / rel_path).read_text(encoding="utf-8")
        assert "__getattr__" not in text
