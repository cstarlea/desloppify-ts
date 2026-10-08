"""Plan loading survives malformed entries and corrupt files without losing data."""

from __future__ import annotations

import json

import pytest

import desloppify.engine._plan.persistence as persistence_mod
from desloppify.engine._plan.persistence import (
    load_plan,
    resolve_plan_load_status,
    save_plan,
)
from desloppify.engine._plan.schema import (
    PLAN_VERSION,
    empty_plan,
    ensure_plan_defaults,
)

SKIP_ID = "smells::src/a.ts::any_type"


def _cluster(cluster_name: str, **overrides) -> dict:
    cluster = {
        "name": cluster_name,
        "description": "mine",
        "issue_ids": ["unused::src/a.ts::join"],
        "auto": False,
    }
    cluster.update(overrides)
    return cluster


def _skip(issue_id: str, **overrides) -> dict:
    entry = {"issue_id": issue_id, "kind": "temporary", "reason": "later"}
    entry.update(overrides)
    return entry


def _plan_with(**fields) -> dict:
    plan = {
        "version": PLAN_VERSION,
        "created": "2026-01-01T00:00:00+00:00",
        "updated": "2026-01-01T00:00:00+00:00",
        "queue_order": ["unused::src/a.ts::join", "smells::src/b.ts::todo"],
        "skipped": {SKIP_ID: _skip(SKIP_ID)},
        "clusters": {"mine": _cluster("mine")},
        "overrides": {},
    }
    plan.update(fields)
    return plan


def _write(path, payload) -> None:
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))


def _on_disk(path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Quarantine of malformed entries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "section", "key", "reason_fragment"),
    [
        (
            {"queue_order": ["a", 42, "b"]},
            "queue_order",
            1,
            "is a int, not an ID string",
        ),
        (
            {"queue_order": ["a", {"id": "x"}, "b"]},
            "queue_order",
            1,
            "is a dict, not an ID string",
        ),
        (
            {"clusters": {"mine": _cluster("mine"), "bad": "oops"}},
            "clusters",
            "bad",
            "is a str, not an object",
        ),
        (
            {"clusters": {"mine": _cluster("mine"), "bad": _cluster("bad", issue_ids="x")}},
            "clusters",
            "bad",
            "non-list issue_ids",
        ),
        (
            {"clusters": {"mine": _cluster("mine"), "bad": _cluster("bad", name=5)}},
            "clusters",
            "bad",
            "non-string name",
        ),
        (
            {"clusters": {"mine": _cluster("mine"), "bad": _cluster("bad", action_steps=[5])}},
            "clusters",
            "bad",
            "malformed action step",
        ),
        (
            {"skipped": {SKIP_ID: _skip(SKIP_ID), "bad": "oops"}},
            "skipped",
            "bad",
            "is a str, not an object",
        ),
        (
            {"skipped": {SKIP_ID: _skip(SKIP_ID), "bad": _skip("bad", kind="bogus")}},
            "skipped",
            "bad",
            "invalid kind 'bogus'",
        ),
        (
            {"skipped": {SKIP_ID: _skip(SKIP_ID), "bad": {"issue_id": "bad"}}},
            "skipped",
            "bad",
            "invalid kind None",
        ),
        (
            {"overrides": {"good": {"issue_id": "good"}, "bad": 7}},
            "overrides",
            "bad",
            "is a int, not an object",
        ),
    ],
)
def test_one_bad_entry_is_quarantined_and_the_rest_load(
    tmp_path, capsys, fields, section, key, reason_fragment
):
    path = tmp_path / "plan.json"
    raw = _plan_with(**fields)
    _write(path, raw)
    bad_item = raw[section][key]

    status = resolve_plan_load_status(path)

    assert status.degraded is False
    assert status.quarantined == 1
    plan = status.plan
    assert plan is not None
    assert SKIP_ID in plan["skipped"]
    assert "mine" in plan["clusters"]
    assert all(isinstance(issue_id, str) for issue_id in plan["queue_order"])
    [entry] = plan["quarantined_entries"]
    assert entry["section"] == section
    assert entry["key"] == key
    assert reason_fragment in entry["reason"]
    assert entry["item"] == bad_item  # as on disk, not half-normalized
    if isinstance(key, str):
        assert key not in plan[section]
    err = capsys.readouterr().err
    assert err.count("malformed plan entry") == 1
    assert "1 malformed plan entry(s)" in err


def test_queued_and_skipped_id_keeps_the_skip(tmp_path):
    path = tmp_path / "plan.json"
    _write(path, _plan_with(queue_order=["a", SKIP_ID, "b"]))

    plan = load_plan(path)

    assert plan["queue_order"] == ["a", "b"]
    assert SKIP_ID in plan["skipped"]
    [entry] = plan["quarantined_entries"]
    assert (entry["section"], entry["key"]) == ("queue_order", SKIP_ID)
    assert "also skipped" in entry["reason"]


@pytest.mark.parametrize(
    ("section", "value"),
    [("skipped", [1, 2]), ("clusters", "oops"), ("queue_order", {"a": 1})],
)
def test_container_of_the_wrong_type_is_quarantined_whole(tmp_path, section, value):
    path = tmp_path / "plan.json"
    _write(path, _plan_with(**{section: value}))

    plan = load_plan(path)

    assert plan[section] == type(plan[section])()
    [entry] = plan["quarantined_entries"]
    assert (entry["section"], entry["key"]) == (section, None)
    assert entry["item"] == value


def test_missing_keys_are_defaulted_not_quarantined(tmp_path):
    path = tmp_path / "plan.json"
    _write(path, {"version": PLAN_VERSION, "clusters": {"mine": {"issue_ids": []}}})

    status = resolve_plan_load_status(path)

    assert status.degraded is False
    assert status.quarantined == 0
    plan = status.plan
    assert plan is not None
    assert plan["queue_order"] == []
    assert plan["clusters"]["mine"]["name"] == "mine"
    assert "quarantined_entries" not in plan


def test_legacy_skip_kind_is_migrated_not_quarantined(tmp_path):
    path = tmp_path / "plan.json"
    entry = _skip("synthesis::observe", kind="synthesized_out")
    _write(path, _plan_with(version=6, skipped={"synthesis::observe": entry}))

    plan = load_plan(path)

    assert plan["skipped"]["triage::observe"]["kind"] == "triaged_out"
    assert "quarantined_entries" not in plan


def test_quarantine_persists_and_does_not_warn_again(tmp_path, capsys):
    path = tmp_path / "plan.json"
    _write(path, _plan_with(queue_order=["a", 1, 2]))

    save_plan(load_plan(path), path)
    assert "2 malformed plan entry(s)" in capsys.readouterr().err

    on_disk = _on_disk(path)
    assert on_disk["queue_order"] == ["a"]
    assert [e["item"] for e in on_disk["quarantined_entries"]] == [1, 2]

    reloaded = load_plan(path)
    assert len(reloaded["quarantined_entries"]) == 2
    assert "malformed" not in capsys.readouterr().err


def test_requarantined_key_replaces_older_entry(tmp_path):
    path = tmp_path / "plan.json"
    older = {
        "section": "clusters",
        "key": "bad",
        "reason": "old",
        "quarantined_at": "then",
        "item": 1,
    }
    _write(path, _plan_with(clusters={"bad": 2}, quarantined_entries=[older]))

    plan = load_plan(path)

    [entry] = plan["quarantined_entries"]
    assert entry["item"] == 2


def test_quarantine_warning_is_shown_once_per_file_version(tmp_path, capsys):
    path = tmp_path / "plan.json"
    _write(path, _plan_with(clusters={"bad": 0}))

    load_plan(path)
    load_plan(path)

    assert capsys.readouterr().err.count("malformed plan entry") == 1


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda plan: plan["queue_order"].append(3), "queue_order"),
        (lambda plan: plan["clusters"].update(bad="x"), "clusters"),
        (lambda plan: plan["skipped"].update(bad={"kind": "bogus"}), "skip kind"),
    ],
)
def test_saving_a_bad_entry_still_raises(tmp_path, mutate, match):
    plan = empty_plan()
    mutate(plan)
    with pytest.raises(ValueError, match=match):
        save_plan(plan, tmp_path / "plan.json")


def test_ensure_plan_defaults_without_quarantine_keeps_old_behaviour():
    plan = _plan_with(skipped=["not", "a", "dict"])
    ensure_plan_defaults(plan)
    assert plan["skipped"] == {}
    assert "quarantined_entries" not in plan


# ---------------------------------------------------------------------------
# Unusable files: .corrupted, .bak fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content", ["{not json", '{"queue_order": [', "[1, 2]", '"text"', "\xff\xfe"])
def test_unusable_file_is_renamed_and_plan_starts_fresh(tmp_path, capsys, content):
    path = tmp_path / "plan.json"
    if content == "\xff\xfe":
        path.write_bytes(b"\xff\xfe\x00")
    else:
        _write(path, content)

    status = resolve_plan_load_status(path)

    assert status.degraded is True
    assert status.recovery == "fresh_start"
    assert status.plan is not None
    assert status.plan["queue_order"] == []
    assert not path.exists()
    assert (tmp_path / "plan.json.corrupted").exists()
    err = capsys.readouterr().err
    assert "moved to plan.json.corrupted" in err
    assert "starting fresh" in err


def test_existing_corrupted_file_is_not_clobbered(tmp_path):
    path = tmp_path / "plan.json"
    (tmp_path / "plan.json.corrupted").write_text("first")
    (tmp_path / "plan.json.corrupted.1").write_text("second")
    _write(path, "{third")

    load_plan(path)

    assert (tmp_path / "plan.json.corrupted").read_text() == "first"
    assert (tmp_path / "plan.json.corrupted.1").read_text() == "second"
    assert (tmp_path / "plan.json.corrupted.2").read_text() == "{third"


def test_unusable_file_falls_back_to_good_backup(tmp_path, capsys):
    path = tmp_path / "plan.json"
    backup = tmp_path / "plan.json.bak"
    _write(path, "{not json")
    _write(backup, _plan_with())

    status = resolve_plan_load_status(path)

    assert status.degraded is True
    assert status.recovery == "backup"
    assert status.error_kind == "JSONDecodeError"
    assert status.plan is not None
    assert "mine" in status.plan["clusters"]
    assert (tmp_path / "plan.json.corrupted").read_text() == "{not json"
    assert path.read_text() == backup.read_text()  # restored in place
    err = capsys.readouterr().err
    assert "moved to plan.json.corrupted" in err
    assert "recovered from backup plan.json.bak" in err

    # A second load in the same run sees the restored plan, not a gap.
    again = resolve_plan_load_status(path)
    assert again.degraded is False
    assert again.plan is not None
    assert "mine" in again.plan["clusters"]
    assert "Plan file" not in capsys.readouterr().err


def test_backup_with_a_bad_entry_is_quarantined_too(tmp_path, capsys):
    path = tmp_path / "plan.json"
    _write(path, "[]")
    _write(tmp_path / "plan.json.bak", _plan_with(clusters={"mine": _cluster("mine"), "bad": 0}))

    status = resolve_plan_load_status(path)

    assert status.recovery == "backup"
    assert status.quarantined == 1
    assert status.plan is not None
    assert sorted(status.plan["clusters"]) == ["mine"]
    assert [e["key"] for e in status.plan["quarantined_entries"]] == ["bad"]
    assert "in plan.json.bak were set aside" in capsys.readouterr().err


def test_both_primary_and_backup_broken_starts_fresh(tmp_path, capsys):
    path = tmp_path / "plan.json"
    backup = tmp_path / "plan.json.bak"
    _write(path, "{bad")
    _write(backup, "{also bad")

    status = resolve_plan_load_status(path)

    assert status.recovery == "fresh_start"
    assert status.plan is not None
    assert status.plan["clusters"] == {}
    assert (tmp_path / "plan.json.corrupted").exists()
    assert backup.read_text() == "{also bad"  # left in place for inspection
    err = capsys.readouterr().err
    assert "plan.json.bak is unusable too" in err
    assert "starting fresh" in err


def test_valid_json_that_cannot_be_normalized_is_set_aside(tmp_path):
    path = tmp_path / "plan.json"
    # The legacy-deferred migration cannot key skipped entries by a list.
    _write(path, _plan_with(version=3, deferred=[["unhashable"]]))
    _write(tmp_path / "plan.json.bak", _plan_with())

    status = resolve_plan_load_status(path)

    assert status.recovery == "backup"
    assert status.plan is not None
    assert "mine" in status.plan["clusters"]
    assert (tmp_path / "plan.json.corrupted").exists()


# ---------------------------------------------------------------------------
# No .bak rotation after a degraded load
# ---------------------------------------------------------------------------


def test_save_after_backup_fallback_keeps_the_good_backup(tmp_path):
    path = tmp_path / "plan.json"
    backup = tmp_path / "plan.json.bak"
    _write(path, "{not json")
    _write(backup, _plan_with())
    good_backup = backup.read_text()

    save_plan(load_plan(path), path)

    assert backup.read_text() == good_backup
    assert "mine" in _on_disk(path)["clusters"]


def test_save_after_quarantine_does_not_rotate_over_backup(tmp_path):
    path = tmp_path / "plan.json"
    backup = tmp_path / "plan.json.bak"
    _write(path, _plan_with(clusters={"mine": _cluster("mine"), "bad": 0}))
    _write(backup, _plan_with(clusters={"mine": _cluster("mine"), "old": _cluster("old")}))
    good_backup = backup.read_text()

    save_plan(load_plan(path), path)
    assert backup.read_text() == good_backup

    # Once a clean plan has been written, rotation resumes.
    save_plan(load_plan(path), path)
    assert "quarantined_entries" in _on_disk(backup)


def test_unrenamable_corrupt_file_is_not_rotated_over_backup(tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    backup = tmp_path / "plan.json.bak"
    _write(path, "{not json")
    _write(backup, _plan_with())
    good_backup = backup.read_text()
    monkeypatch.setattr(persistence_mod, "set_aside_corrupted", lambda _path: None)

    plan = load_plan(path)
    assert path.read_text() == "{not json"
    save_plan(plan, path)

    assert backup.read_text() == good_backup


def test_save_after_fresh_start_keeps_the_corrupt_original(tmp_path):
    path = tmp_path / "plan.json"
    _write(path, '{"clusters": {"mine": ')

    save_plan(load_plan(path), path)
    save_plan(load_plan(path), path)

    assert (tmp_path / "plan.json.corrupted").read_text() == '{"clusters": {"mine": '


def test_save_after_clean_load_still_rotates(tmp_path):
    path = tmp_path / "plan.json"
    backup = tmp_path / "plan.json.bak"
    _write(path, _plan_with())

    save_plan(load_plan(path), path)

    assert backup.exists()
    assert "mine" in _on_disk(backup)["clusters"]


# ---------------------------------------------------------------------------
# Version coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["8", 8.0, " 8 ", "7", 1, None, "garbage", [8], True])
def test_odd_version_loads_as_current(tmp_path, raw):
    path = tmp_path / "plan.json"
    _write(path, _plan_with(version=raw))

    status = resolve_plan_load_status(path)

    assert status.degraded is False
    assert status.plan is not None
    assert status.plan["version"] == PLAN_VERSION
    assert "mine" in status.plan["clusters"]
    assert SKIP_ID in status.plan["skipped"]


def test_missing_version_loads_as_current(tmp_path):
    path = tmp_path / "plan.json"
    raw = _plan_with()
    del raw["version"]
    _write(path, raw)

    assert load_plan(path)["version"] == PLAN_VERSION


@pytest.mark.parametrize("raw", [PLAN_VERSION + 1, str(PLAN_VERSION + 1)])
def test_newer_version_warns_and_is_kept(tmp_path, capsys, raw):
    path = tmp_path / "plan.json"
    _write(path, _plan_with(version=raw))

    plan = load_plan(path)

    assert plan["version"] == PLAN_VERSION + 1
    assert "mine" in plan["clusters"]
    assert "newer than supported" in capsys.readouterr().err

    save_plan(plan, path)
    assert _on_disk(path)["version"] == PLAN_VERSION + 1
