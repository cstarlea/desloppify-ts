"""`plan resolve` / `plan skip` refusals exit 2 and print a command that works."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

import desloppify.cli as cli_mod
from desloppify.app.commands.next.render_nudges import (
    render_single_item_resolution_hint,
    resolve_command_for,
)
from desloppify.app.commands.plan.override.resolve_cmd import confirm_command
from desloppify.app.commands.plan.override.skip import _validate_skip_requirements
from desloppify.engine._plan.schema import empty_plan
from desloppify.engine._work_queue.core import ATTEST_EXAMPLE, SKIP_ATTEST_EXAMPLE

ISSUE_ID = "unused::src/widget.ts::helper"
LONG_NOTE = "removed the unused helper import from the widget module entirely"
SHORT_NOTE = "removed the unused import."


def _issue(issue_id: str) -> dict:
    return {
        "id": issue_id,
        "detector": "unused",
        "file": "src/widget.ts",
        "tier": 1,
        "confidence": "high",
        "summary": "Unused import: helper",
        "detail": {"category": "imports"},
        "status": "open",
        "work_item_kind": "mechanical_defect",
        "issue_kind": "mechanical_defect",
        "origin": "scan",
        "zone": "production",
    }


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "widget.ts").write_text("export const widget = 1;\n")
    monkeypatch.setenv("DESLOPPIFY_ROOT", str(root))
    state_dir = root / ".desloppify"
    state_dir.mkdir()
    ids = [ISSUE_ID] + [f"unused::src/widget.ts::extra{n}" for n in range(6)]
    work_items = {issue_id: _issue(issue_id) for issue_id in ids}
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "last_scan": "2026-10-12T00:00:00+00:00",
                "scan_count": 1,
                "scan_path": ".",
                "work_items": work_items,
                "issues": work_items,
                "codebase_metrics": {"typescript": {"total_files": 1, "total_loc": 1}},
            }
        )
    )
    monkeypatch.chdir(root)
    return root


def _run(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["desloppify", *argv])
    try:
        cli_mod.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_resolve_short_note_exits_2_and_shows_the_confirm_form(
    project, monkeypatch, capsys
) -> None:
    code = _run(monkeypatch, "plan", "resolve", ISSUE_ID, "--note", SHORT_NOTE)

    assert code == 2
    err = capsys.readouterr().err
    assert f"Note must be at least 50 characters (got {len(SHORT_NOTE)})" in err
    assert confirm_command([ISSUE_ID]) in err


def test_resolve_without_attest_exits_2_and_offers_confirm(
    project, monkeypatch, capsys
) -> None:
    code = _run(monkeypatch, "plan", "resolve", ISSUE_ID, "--note", LONG_NOTE)

    assert code == 2
    err = capsys.readouterr().err
    assert "Plan resolve requires --confirm or --attest." in err
    assert confirm_command([ISSUE_ID], LONG_NOTE) in err
    assert ATTEST_EXAMPLE in err


def test_resolve_with_weak_attest_exits_2(project, monkeypatch, capsys) -> None:
    code = _run(
        monkeypatch,
        "plan",
        "resolve",
        ISSUE_ID,
        "--note",
        LONG_NOTE,
        "--attest",
        "trust me",
    )

    assert code == 2
    assert "missing required keyword(s)" in capsys.readouterr().err


def test_resolve_confirm_without_note_exits_2(project, monkeypatch, capsys) -> None:
    code = _run(monkeypatch, "plan", "resolve", ISSUE_ID, "--confirm")

    assert code == 2
    assert "--confirm requires --note" in capsys.readouterr().err


def test_the_suggested_confirm_command_works(project, monkeypatch, capsys) -> None:
    _run(monkeypatch, "plan", "resolve", ISSUE_ID, "--note", LONG_NOTE)
    suggested = confirm_command([ISSUE_ID], LONG_NOTE)
    assert suggested in capsys.readouterr().err

    argv = shlex.split(suggested)[1:]
    code = _run(monkeypatch, *argv)

    assert code == 0
    state = json.loads((project / ".desloppify" / "state.json").read_text())
    assert state["work_items"][ISSUE_ID]["status"] == "fixed"


def test_permanent_skip_without_attest_exits_2_with_a_skip_example(
    project, monkeypatch, capsys
) -> None:
    code = _run(
        monkeypatch, "plan", "skip", "--permanent", ISSUE_ID, "--note", "intentional"
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "Permanent skip requires --attest." in err
    assert SKIP_ATTEST_EXAMPLE in err
    assert "CONCRETE CHANGE YOU MADE" not in err


def test_permanent_skip_without_note_exits_2(project, monkeypatch, capsys) -> None:
    code = _run(
        monkeypatch,
        "plan",
        "skip",
        "--permanent",
        ISSUE_ID,
        "--attest",
        SKIP_ATTEST_EXAMPLE,
    )

    assert code == 2
    assert "--permanent requires --note" in capsys.readouterr().err


def test_permanent_skip_with_the_suggested_attest_works(
    project, monkeypatch, capsys
) -> None:
    code = _run(
        monkeypatch,
        "plan",
        "skip",
        "--permanent",
        ISSUE_ID,
        "--note",
        "intentional",
        "--attest",
        SKIP_ATTEST_EXAMPLE,
    )

    assert code == 0
    assert "1 item(s)" in capsys.readouterr().out


def test_unconfirmed_deferral_exits_2(project, monkeypatch, capsys) -> None:
    code = _run(monkeypatch, "plan", "skip", ISSUE_ID, "--confirm")

    assert code == 2
    assert "--confirm requires --reason" in capsys.readouterr().out


def test_bulk_skip_without_confirm_exits_2(project, monkeypatch, capsys) -> None:
    code = _run(monkeypatch, "plan", "skip", "unused::*")

    assert code == 2
    assert "requires --confirm" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ("plan", "reorder", ISSUE_ID, "before"),
        ("plan", "scan-gate", "--skip", "--note", "too short"),
        ("plan", "focus", "no-such-cluster"),
    ],
)
def test_sibling_plan_usage_errors_exit_2(project, monkeypatch, argv) -> None:
    plan = empty_plan()
    plan["scan_count_at_plan_start"] = 1
    (project / ".desloppify" / "plan.json").write_text(json.dumps(plan))
    assert _run(monkeypatch, *argv) == 2


def test_skip_attest_example_passes_skip_validation() -> None:
    assert _validate_skip_requirements(
        kind="permanent", attestation=SKIP_ATTEST_EXAMPLE, note="intentional"
    )
    assert _validate_skip_requirements(
        kind="false_positive", attestation=SKIP_ATTEST_EXAMPLE, note=None
    )


def test_resolve_command_for_queue_items() -> None:
    assert resolve_command_for({"id": ISSUE_ID, "kind": "issue"}) == (
        f'desloppify plan resolve "{ISSUE_ID}" --note "<what you did>" --confirm'
    )
    assert resolve_command_for({"id": "auto/unused", "kind": "cluster"}) == (
        'desloppify plan resolve "auto/unused" --note "<what you did>" --confirm'
    )
    assert resolve_command_for({"id": "workflow::x", "kind": "workflow_action"}) is None


def test_single_item_hint_uses_a_skip_attestation(capsys) -> None:
    render_single_item_resolution_hint(
        [{"id": ISSUE_ID, "kind": "issue", "detector": "unused"}]
    )

    out = capsys.readouterr().out
    assert f'plan resolve "{ISSUE_ID}" --note "<what you did>" --confirm' in out
    assert f'--attest "{SKIP_ATTEST_EXAMPLE}"' in out
    assert ATTEST_EXAMPLE not in out
