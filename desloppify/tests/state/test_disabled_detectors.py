"""Detectors and dimensions disabled in config are removed from scoring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import desloppify.app.commands.config as config_cmd
from desloppify.base.config import set_config_value, unset_config_value
from desloppify.engine._state import _recompute_stats
from desloppify.engine._state.disabled import (
    apply_disabled,
    canonical_disabled_entry,
    disabled_detectors,
)
from desloppify.state import MergeScanOptions, empty_state, make_issue, merge_scan


def _smell(name: str) -> dict:
    return make_issue("smells", "src/a.ts", name, tier=3, confidence="high", summary=name)


def _structural() -> dict:
    return make_issue("structural", "src/b.ts", "", tier=3, confidence="high", summary="big")


def _scan(state: dict, issues: list[dict], disabled: list[str]) -> dict:
    return merge_scan(
        state,
        [dict(i) for i in issues],
        MergeScanOptions(
            lang="typescript",
            scan_path=".",
            potentials={"smells": 10, "structural": 10, "unused": 10},
            disabled=disabled,
        ),
    )


def test_entries_resolve_to_detectors():
    assert canonical_disabled_entry("smells") == "smells"
    assert canonical_disabled_entry("file_health") == "File health"
    assert canonical_disabled_entry(" test-health ") == "Test health"
    assert disabled_detectors(["File health", "smells", "nonsense"]) == {"structural", "smells"}
    for bad in ("nonsense", "review", "subjective_review"):
        with pytest.raises(ValueError):
            canonical_disabled_entry(bad)


def test_disabled_detector_has_no_potential_issues_or_score():
    state = empty_state()
    issues = [_smell("a"), _smell("b"), _structural()]
    _scan(state, issues, [])
    assert {"Code quality", "File health"} <= set(state["dimension_scores"])
    smells_wontfix = issues[1]["id"]
    state["work_items"][smells_wontfix]["status"] = "wontfix"

    diff = _scan(state, issues, ["smells", "File health"])

    assert "smells" not in state["potentials"]["typescript"]
    assert "structural" not in state["potentials"]["typescript"]
    assert "File health" not in state["dimension_scores"]
    assert "smells" not in state["dimension_scores"]["Code quality"]["detectors"]
    assert state["disabled_detectors"] == ["smells", "structural"]
    assert not diff["suspect_detectors"]
    smell, wontfix, structural = (state["work_items"][i["id"]] for i in issues)
    # Hidden, status unchanged; wontfix is left alone entirely.
    assert smell["status"] == "open" and smell["suppressed"]
    assert smell["suppression_pattern"] == "disabled:smells"
    assert structural["status"] == "open" and structural["suppressed"]
    assert wontfix["status"] == "wontfix" and not wontfix.get("suppressed")
    assert state["objective_score"] == 100.0


def test_disabled_dimension_is_not_carried_forward():
    state = empty_state()
    _scan(state, [_structural()], [])
    state["potentials"]["typescript"].pop("structural")
    state["disabled_detectors"] = ["structural"]

    _recompute_stats(state, scan_path=".")
    assert "File health" not in state["dimension_scores"]


def test_reenabling_shows_issues_again_and_rescans_normally():
    state = empty_state()
    issues = [_smell("a"), _smell("b")]
    _scan(state, issues, [])
    _scan(state, issues, ["smells"])
    assert all(state["work_items"][i["id"]]["suppressed"] for i in issues)

    # Only "a" is still detected: "b" was fixed while smells was disabled.
    diff = _scan(state, issues[:1], [])
    a, b = (state["work_items"][i["id"]] for i in issues)
    assert a["status"] == "open" and not a["suppressed"] and a["reopen_count"] == 0
    assert b["status"] == "auto_resolved" and not b["suppressed"]
    assert diff["new"] == 0
    assert "smells" in state["dimension_scores"]["Code quality"]["detectors"]


def test_apply_disabled_rescores_without_a_scan():
    state = empty_state()
    _scan(state, [_smell("a")], [])
    assert state["dimension_scores"]["Code quality"]["score"] < 100

    hidden, restored = apply_disabled(state, ["smells"], "now")

    _recompute_stats(state, scan_path=".")
    assert (hidden, restored) == (1, 0)
    assert state["dimension_scores"]["Code quality"]["score"] == 100.0
    assert apply_disabled(state, [], "now") == (0, 1)


def test_config_set_validates_and_unset_removes_one_value(monkeypatch, capsys):
    config: dict = {"disabled": []}
    runtime = SimpleNamespace(config=config, state={}, state_path=None)
    monkeypatch.setattr(config_cmd, "command_runtime", lambda _args: runtime)
    monkeypatch.setattr(config_cmd, "save_config", lambda _config: None)

    config_cmd._config_set(SimpleNamespace(config_key="disabled", config_value="test_health"))
    config_cmd._config_set(SimpleNamespace(config_key="disabled", config_value="smells"))
    assert config["disabled"] == ["Test health", "smells"]
    with pytest.raises(Exception, match="Unknown detector or dimension"):
        config_cmd._config_set(SimpleNamespace(config_key="disabled", config_value="bogus"))

    config_cmd._config_unset(SimpleNamespace(config_key="disabled", config_value="smells"))
    assert config["disabled"] == ["Test health"]
    with pytest.raises(ValueError):
        unset_config_value(config, "disabled", "smells")
    with pytest.raises(ValueError):
        unset_config_value({"target_strict_score": 85}, "target_strict_score", "1")
    set_config_value(config, "disabled", "smells")
    unset_config_value(config, "disabled")
    assert config["disabled"] == []
