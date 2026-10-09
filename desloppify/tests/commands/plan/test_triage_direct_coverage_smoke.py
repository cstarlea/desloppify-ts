"""Direct-coverage smoke tests for triage helper modules."""

from __future__ import annotations

import desloppify.app.commands.plan.triage.helpers as triage_helpers_mod
from desloppify.app.commands.plan.triage.plan_state_access import ensure_execution_log


def test_count_log_activity_since_ignores_malformed_entries() -> None:
    plan = {
        "execution_log": [
            {"timestamp": "2026-01-01T00:00:00Z", "action": "resolve"},
            {"timestamp": "2026-01-01T00:00:00Z", "action": 123},
            {"timestamp": 123, "action": "skip"},
            {"action": "skip"},
            {"timestamp": "2026-01-01T00:00:00Z"},
            "bad-entry",
        ]
    }
    counts = triage_helpers_mod.count_log_activity_since(plan, "2025-12-31T00:00:00Z")
    assert counts == {"resolve": 1}


def test_count_log_activity_since_includes_all_entries_when_since_is_none() -> None:
    plan = {
        "execution_log": [
            {"timestamp": "2026-01-01T00:00:00Z", "action": "resolve"},
            {"timestamp": "2026-01-02T00:00:00Z", "action": "skip"},
            {"timestamp": "2026-01-03T00:00:00Z", "action": "done"},
        ]
    }

    counts = triage_helpers_mod.count_log_activity_since(plan, None)

    assert counts == {"resolve": 1, "skip": 1, "done": 1}


def test_ensure_execution_log_replaces_malformed_entries_in_plan() -> None:
    plan = {
        "execution_log": [
            {"timestamp": "2026-01-01T00:00:00Z", "action": "resolve"},
            "bad-entry",
            123,
        ]
    }

    normalized = ensure_execution_log(plan)

    assert normalized == [{"timestamp": "2026-01-01T00:00:00Z", "action": "resolve"}]
    assert plan["execution_log"] == normalized
