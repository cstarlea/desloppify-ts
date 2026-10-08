"""State loading survives malformed issues and corrupt files without losing data."""

from __future__ import annotations

import json

import pytest

from desloppify.engine._state.legacy_lang_state import migrate_legacy_lang_state
from desloppify.engine._state.persistence import load_state, save_state, state_lock
from desloppify.engine._state.schema import (
    CURRENT_VERSION,
    coerce_state_version,
    empty_state,
    ensure_state_defaults,
)


def _issue(issue_id: str, **overrides) -> dict:
    issue = {
        "id": issue_id,
        "detector": "unused",
        "file": "src/a.ts",
        "tier": 2,
        "confidence": "high",
        "summary": "unused import",
        "detail": {},
        "status": "open",
    }
    issue.update(overrides)
    return issue


def _state_with(work_items: object, **extra) -> dict:
    return {"version": CURRENT_VERSION, "work_items": work_items, **extra}


def _write(path, payload) -> None:
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))


def _on_disk(path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Quarantine of malformed issues
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bad_issue", "reason_fragment"),
    [
        ("not an issue", "is a str"),
        (["a", "list"], "is a list"),
        (_issue("bad", tier="high"), "invalid tier"),
        (_issue("bad", tier=9), "invalid tier"),
        (_issue("someone-else"), "id mismatch"),
        (_issue("bad", file=None), "non-string file"),
        (_issue("bad", detail="oops"), "non-object detail"),
    ],
)
def test_one_bad_issue_is_quarantined_and_the_rest_load(
    tmp_path, capsys, bad_issue, reason_fragment
):
    path = tmp_path / "state.json"
    _write(
        path,
        _state_with(
            {"a": _issue("a"), "bad": bad_issue, "c": _issue("c")},
            last_scan="2026-01-01T00:00:00+00:00",
            scan_count=4,
        ),
    )

    state = load_state(path)

    assert sorted(state["work_items"]) == ["a", "c"]
    assert state["scan_count"] == 4
    [entry] = state["quarantined_work_items"]
    assert entry["id"] == "bad"
    assert reason_fragment in entry["reason"]
    assert entry["item"] == bad_issue  # as on disk, not half-normalized
    err = capsys.readouterr().err
    assert err.count("malformed work item") == 1
    assert "1 malformed work item(s)" in err


def test_bad_status_is_coerced_not_quarantined(tmp_path):
    path = tmp_path / "state.json"
    _write(path, _state_with({"a": _issue("a", status="bogus")}))

    state = load_state(path)

    assert state["work_items"]["a"]["status"] == "open"
    assert "quarantined_work_items" not in state


def test_missing_optional_fields_are_defaulted_not_quarantined(tmp_path):
    path = tmp_path / "state.json"
    _write(path, _state_with({"a": {"id": "a"}}))

    state = load_state(path)

    assert state["work_items"]["a"]["tier"] == 3
    assert "quarantined_work_items" not in state


def test_work_items_not_an_object_is_quarantined_whole(tmp_path, capsys):
    path = tmp_path / "state.json"
    _write(path, _state_with([_issue("a")], scan_count=2))

    state = load_state(path)

    assert state["work_items"] == {}
    assert state["scan_count"] == 2
    [entry] = state["quarantined_work_items"]
    assert entry["id"] is None
    assert entry["item"] == [_issue("a")]
    assert "1 malformed work item(s)" in capsys.readouterr().err


def test_quarantine_persists_and_does_not_warn_again(tmp_path, capsys):
    path = tmp_path / "state.json"
    _write(path, _state_with({"a": _issue("a"), "bad": 7, "worse": "x"}))

    save_state(load_state(path), path)
    assert "2 malformed work item(s)" in capsys.readouterr().err

    on_disk = _on_disk(path)
    assert sorted(on_disk["work_items"]) == ["a"]
    assert sorted(e["id"] for e in on_disk["quarantined_work_items"]) == ["bad", "worse"]

    reloaded = load_state(path)
    assert len(reloaded["quarantined_work_items"]) == 2
    assert "malformed" not in capsys.readouterr().err


def test_requarantined_id_replaces_older_entry(tmp_path):
    path = tmp_path / "state.json"
    older = {"id": "bad", "reason": "old", "quarantined_at": "then", "item": 1}
    _write(path, _state_with({"bad": 2}, quarantined_work_items=[older]))

    state = load_state(path)

    [entry] = state["quarantined_work_items"]
    assert entry["item"] == 2


def test_saving_a_bad_issue_still_raises(tmp_path):
    state = empty_state()
    state["work_items"]["x"] = _issue("x", tier="high")
    with pytest.raises(ValueError, match="invalid tier"):
        save_state(state, tmp_path / "state.json")


def test_ensure_state_defaults_without_quarantine_keeps_old_behaviour():
    state = _state_with({"a": _issue("a"), "junk": "x"})
    ensure_state_defaults(state)
    assert sorted(state["work_items"]) == ["a"]
    assert "quarantined_work_items" not in state


# ---------------------------------------------------------------------------
# Unusable files: .corrupted, .bak fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '"text"', "\xff\xfe"])
def test_unusable_file_is_renamed_and_state_starts_fresh(tmp_path, capsys, content):
    path = tmp_path / "state.json"
    if content == "\xff\xfe":
        path.write_bytes(b"\xff\xfe\x00")
    else:
        _write(path, content)

    state = load_state(path)

    assert state["work_items"] == {}
    assert not path.exists()
    assert (tmp_path / "state.json.corrupted").exists()
    err = capsys.readouterr().err
    assert "moved to state.json.corrupted" in err
    assert "Starting fresh" in err


def test_existing_corrupted_file_is_not_clobbered(tmp_path):
    path = tmp_path / "state.json"
    (tmp_path / "state.json.corrupted").write_text("first")
    (tmp_path / "state.json.corrupted.1").write_text("second")
    _write(path, "{third")

    load_state(path)

    assert (tmp_path / "state.json.corrupted").read_text() == "first"
    assert (tmp_path / "state.json.corrupted.1").read_text() == "second"
    assert (tmp_path / "state.json.corrupted.2").read_text() == "{third"


def test_unusable_file_falls_back_to_good_backup(tmp_path, capsys):
    path = tmp_path / "state.json"
    backup = tmp_path / "state.json.bak"
    _write(path, "{not json")
    _write(backup, _state_with({"a": _issue("a")}, scan_count=3))

    state = load_state(path)

    assert sorted(state["work_items"]) == ["a"]
    assert state["scan_count"] == 3
    assert (tmp_path / "state.json.corrupted").read_text() == "{not json"
    assert path.read_text() == backup.read_text()  # restored in place
    err = capsys.readouterr().err
    assert "moved to state.json.corrupted" in err
    assert "Loaded from state.json.bak" in err

    # A second load in the same run sees the restored state, not a gap.
    again = load_state(path)
    assert sorted(again["work_items"]) == ["a"]
    assert "State file" not in capsys.readouterr().err


def test_quarantine_warning_is_shown_once_per_file_version(tmp_path, capsys):
    path = tmp_path / "state.json"
    _write(path, _state_with({"a": _issue("a"), "bad": 0}))

    load_state(path)
    load_state(path)

    assert capsys.readouterr().err.count("malformed work item") == 1


def test_backup_with_a_bad_issue_is_quarantined_too(tmp_path, capsys):
    path = tmp_path / "state.json"
    _write(path, "[]")
    _write(tmp_path / "state.json.bak", _state_with({"a": _issue("a"), "bad": 0}))

    state = load_state(path)

    assert sorted(state["work_items"]) == ["a"]
    assert [e["id"] for e in state["quarantined_work_items"]] == ["bad"]
    assert "in state.json.bak were set aside" in capsys.readouterr().err


def test_both_primary_and_backup_broken_starts_fresh(tmp_path, capsys):
    path = tmp_path / "state.json"
    backup = tmp_path / "state.json.bak"
    _write(path, "{bad")
    _write(backup, "{also bad")

    state = load_state(path)

    assert state["work_items"] == {}
    assert (tmp_path / "state.json.corrupted").exists()
    assert backup.read_text() == "{also bad"  # left in place for inspection
    err = capsys.readouterr().err
    assert "state.json.bak is unusable too" in err
    assert "Starting fresh" in err


def test_valid_json_that_cannot_be_normalized_is_set_aside(tmp_path):
    path = tmp_path / "state.json"
    _write(path, _state_with({}, dimension_scores=["not", "a", "dict"]))
    _write(tmp_path / "state.json.bak", _state_with({"a": _issue("a")}))

    state = load_state(path)

    assert sorted(state["work_items"]) == ["a"]
    assert (tmp_path / "state.json.corrupted").exists()


# ---------------------------------------------------------------------------
# No .bak rotation after a degraded load
# ---------------------------------------------------------------------------


def test_save_after_backup_fallback_keeps_the_good_backup(tmp_path):
    path = tmp_path / "state.json"
    backup = tmp_path / "state.json.bak"
    _write(path, "{not json")
    _write(backup, _state_with({"a": _issue("a")}))
    good_backup = backup.read_text()

    save_state(load_state(path), path)

    assert backup.read_text() == good_backup
    assert sorted(_on_disk(path)["work_items"]) == ["a"]


def test_save_after_quarantine_does_not_rotate_over_backup(tmp_path):
    path = tmp_path / "state.json"
    backup = tmp_path / "state.json.bak"
    _write(path, _state_with({"a": _issue("a"), "bad": 0}))
    _write(backup, _state_with({"a": _issue("a"), "old": _issue("old")}))
    good_backup = backup.read_text()

    save_state(load_state(path), path)
    assert backup.read_text() == good_backup

    # Once a clean state has been written, rotation resumes.
    save_state(load_state(path), path)
    assert "quarantined_work_items" in _on_disk(backup)


def test_unrenamable_corrupt_file_is_not_rotated_over_backup(tmp_path, monkeypatch):
    import desloppify.engine._state.persistence as persistence_mod

    path = tmp_path / "state.json"
    backup = tmp_path / "state.json.bak"
    _write(path, "{not json")
    _write(backup, _state_with({"a": _issue("a")}))
    good_backup = backup.read_text()
    monkeypatch.setattr(persistence_mod, "_set_aside_corrupted", lambda _path: None)

    state = load_state(path)
    assert path.read_text() == "{not json"
    save_state(state, path)

    assert backup.read_text() == good_backup


def test_save_after_clean_load_still_rotates(tmp_path):
    path = tmp_path / "state.json"
    backup = tmp_path / "state.json.bak"
    _write(path, _state_with({"a": _issue("a")}))

    save_state(load_state(path), path)

    assert backup.exists()
    assert sorted(_on_disk(backup)["work_items"]) == ["a"]


def test_state_lock_after_fallback_keeps_the_good_backup(tmp_path):
    path = tmp_path / "state.json"
    backup = tmp_path / "state.json.bak"
    _write(path, "{not json")
    _write(backup, _state_with({"a": _issue("a")}))
    good_backup = backup.read_text()

    with state_lock(path) as state:
        state["scan_count"] = 9

    assert backup.read_text() == good_backup
    assert _on_disk(path)["scan_count"] == 9


# ---------------------------------------------------------------------------
# Version coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3, 3),
        ("3", 3),
        (" 2 ", 2),
        (3.0, 3),
        ("4.0", 4),
        (3.5, None),
        ("three", None),
        (None, None),
        (True, None),
        ([3], None),
    ],
)
def test_coerce_state_version(raw, expected):
    assert coerce_state_version(raw) == expected


@pytest.mark.parametrize("raw", ["3", 3.0, "2", 1, None, "garbage", [1]])
def test_odd_version_loads_as_current(tmp_path, raw):
    path = tmp_path / "state.json"
    _write(path, {"version": raw, "work_items": {"a": _issue("a")}})

    state = load_state(path)

    assert state["version"] == CURRENT_VERSION
    assert sorted(state["work_items"]) == ["a"]


def test_missing_version_loads_as_current(tmp_path):
    path = tmp_path / "state.json"
    _write(path, {"work_items": {"a": _issue("a")}})

    assert load_state(path)["version"] == CURRENT_VERSION


@pytest.mark.parametrize("raw", [CURRENT_VERSION + 1, str(CURRENT_VERSION + 1)])
def test_newer_version_warns_and_is_kept(tmp_path, capsys, raw):
    path = tmp_path / "state.json"
    _write(path, {"version": raw, "work_items": {"a": _issue("a")}})

    state = load_state(path)

    assert state["version"] == CURRENT_VERSION + 1
    assert sorted(state["work_items"]) == ["a"]
    assert "newer than supported" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Legacy per-language state files
# ---------------------------------------------------------------------------


def test_legacy_state_file_migration_still_loads(tmp_path):
    legacy = tmp_path / "state-javascript.json"
    _write(
        legacy,
        {
            "version": "2",
            "lang": "javascript",
            "work_items": {"a": _issue("a", lang="javascript"), "bad": None},
        },
    )

    adopted = migrate_legacy_lang_state(tmp_path)
    state = load_state(tmp_path / "state.json")

    assert adopted == legacy
    assert not legacy.exists()
    assert state["lang"] == "typescript"
    assert state["work_items"]["a"]["lang"] == "typescript"
    assert [e["id"] for e in state["quarantined_work_items"]] == ["bad"]
