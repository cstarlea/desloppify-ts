"""Focused tests for plan schema migration helpers."""

from __future__ import annotations

import desloppify.engine._plan.schema.migrations as migrations
from desloppify.engine._plan.operations.lifecycle import purge_ids
from desloppify.engine._plan.schema import ensure_plan_defaults
from desloppify.engine._plan.schema.version_upgrades import V7_SCHEMA_VERSION


def test_ensure_container_types_sets_defaults_and_renames_keys() -> None:
    plan = {
        "queue_order": "bad",
        "deferred": None,
        "skipped": [],
        "clusters": [],
        "epic_triage_meta": {"finding_snapshot_hash": "abc"},
        "uncommitted_findings": ["x"],
    }

    migrations.ensure_container_types(plan)

    assert isinstance(plan["queue_order"], list)
    assert isinstance(plan["deferred"], list)
    assert isinstance(plan["skipped"], dict)
    assert isinstance(plan["clusters"], dict)
    assert plan["epic_triage_meta"]["issue_snapshot_hash"] == "abc"
    assert "finding_snapshot_hash" not in plan["epic_triage_meta"]
    assert plan["uncommitted_issues"] == ["x"]
    assert plan["commit_tracking_branch"] is None


def test_ensure_container_types_rename_overwrites_existing_target_key() -> None:
    plan = {
        "epic_triage_meta": {"finding_snapshot_hash": "abc", "issue_snapshot_hash": ""},
        "uncommitted_findings": ["x"],
        "uncommitted_issues": [],
    }

    migrations.ensure_container_types(plan)

    assert plan["epic_triage_meta"]["issue_snapshot_hash"] == "abc"
    assert "finding_snapshot_hash" not in plan["epic_triage_meta"]
    assert plan["uncommitted_issues"] == ["x"]
    assert "uncommitted_findings" not in plan


def test_migrate_synthesis_to_triage_renames_ids_meta_and_cluster_fields() -> None:
    plan = {
        "queue_order": ["synthesis::a", "other"],
        "skipped": {
            "synthesis::b": {"issue_id": "synthesis::b", "kind": "synthesized_out"}
        },
        "epic_synthesis_meta": {
            "synthesis_stages": {"observe": {}},
            "synthesized_ids": ["id1"],
        },
        "clusters": {
            "c": {"synthesis_version": 4},
        },
    }

    migrations.migrate_synthesis_to_triage(plan)

    assert plan["queue_order"][0] == "triage::a"
    assert "triage::b" in plan["skipped"]
    assert plan["skipped"]["triage::b"]["kind"] == "triaged_out"
    assert "epic_synthesis_meta" not in plan
    assert plan["epic_triage_meta"]["triage_stages"] == {"observe": {}}
    assert plan["epic_triage_meta"]["triaged_ids"] == ["id1"]
    assert plan["clusters"]["c"]["triage_version"] == 4


def test_upgrade_plan_to_v7_runs_legacy_cleanup() -> None:
    plan = {
        "version": 5,
        "queue_order": ["synthesis::legacy"],
        "deferred": ["issue-1"],
        "skipped": {},
        "clusters": {"c": {"synthesis_version": 1}},
        "epics": {},
        "epic_synthesis_meta": {"synthesized_ids": ["x"]},
        "pending_plan_gate": True,
        "uncommitted_findings": ["x"],
    }
    changed = migrations.upgrade_plan_to_v7(plan)

    assert changed is True
    assert plan["version"] == V7_SCHEMA_VERSION
    assert "epics" not in plan
    assert "epic_synthesis_meta" not in plan
    assert "pending_plan_gate" not in plan
    assert "uncommitted_findings" not in plan
    assert "deferred" in plan and plan["deferred"] == []


def test_normalize_cluster_defaults_restores_issue_ids_from_execution_log() -> None:
    plan = {
        "clusters": {
            "manual": {
                "name": "manual",
                "issue_ids": [],
                "action_steps": [
                    {"title": "fix", "issue_refs": ["07c3759c"]},
                ],
            }
        },
        "execution_log": [
            {
                "action": "cluster_add",
                "cluster_name": "manual",
                "issue_ids": [
                    "review::.::holistic::authorization_consistency::decrypted_api_key_rpc_not_restricted::07c3759c",
                    "review::.::holistic::test_strategy::untested_shared_request_guards::1d016b7e",
                ],
            },
            {
                "action": "cluster_remove",
                "cluster_name": "manual",
                "issue_ids": [
                    "review::.::holistic::test_strategy::untested_shared_request_guards::1d016b7e",
                ],
            },
        ],
    }

    migrations.normalize_cluster_defaults(plan, recover_from_log=True)

    assert plan["clusters"]["manual"]["issue_ids"] == [
        "review::.::holistic::authorization_consistency::decrypted_api_key_rpc_not_restricted",
    ]


def test_normalize_cluster_defaults_preserves_non_review_ids_and_recovers_overrides() -> None:
    plan = {
        "clusters": {
            "auto/initial-review": {
                "name": "auto/initial-review",
                "issue_ids": [],
                "auto": True,
                "cluster_key": "subjective::unscored",
            },
            "auto/security": {
                "name": "auto/security",
                "issue_ids": [
                    "security::pkg/mod.py::security::B101::pkg/mod.py::12",
                ],
                "auto": True,
                "cluster_key": "detector::security",
            },
        },
        "overrides": {
            "subjective::abstraction_fitness": {"cluster": "auto/initial-review"},
            "subjective::type_safety": {"cluster": "auto/initial-review"},
        },
        "execution_log": [],
    }

    migrations.normalize_cluster_defaults(plan)

    assert plan["clusters"]["auto/initial-review"]["issue_ids"] == [
        "subjective::abstraction_fitness",
        "subjective::type_safety",
    ]
    assert plan["clusters"]["auto/security"]["issue_ids"] == [
        "security::pkg/mod.py::security::B101::pkg/mod.py::12",
    ]


def _plan_with_logged_cluster(version: int) -> dict:
    return {
        "version": version,
        "clusters": {"anys": {"name": "anys", "issue_ids": ["b"]}},
        "execution_log": [
            {"action": "cluster_add", "cluster_name": "anys", "issue_ids": ["a", "b"]},
            {"action": "resolve", "issue_ids": ["a"]},
        ],
    }


def test_removed_cluster_member_is_not_recovered_from_log() -> None:
    """A member removed by resolve or supersede stays removed (2.43)."""
    plan = _plan_with_logged_cluster(V7_SCHEMA_VERSION)
    ensure_plan_defaults(plan)
    assert plan["clusters"]["anys"]["issue_ids"] == ["b"]

    purge_ids(plan, ["b"])
    ensure_plan_defaults(plan)
    assert plan["clusters"]["anys"]["issue_ids"] == []


def test_legacy_plan_still_recovers_cluster_members_from_log() -> None:
    plan = _plan_with_logged_cluster(V7_SCHEMA_VERSION - 1)
    ensure_plan_defaults(plan)
    assert plan["clusters"]["anys"]["issue_ids"] == ["b", "a"]
