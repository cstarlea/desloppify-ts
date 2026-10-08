"""Tests for adopting multi-language-era state files as ``state.json``."""

from __future__ import annotations

import json

from desloppify.engine._state.legacy_lang_state import migrate_legacy_lang_state


def _write(path, payload) -> None:
    path.write_text(json.dumps(payload))


def test_no_legacy_file_is_a_noop(tmp_path):
    assert migrate_legacy_lang_state(tmp_path) is None
    assert not (tmp_path / "state.json").exists()


def test_typescript_state_is_moved(tmp_path):
    _write(tmp_path / "state-typescript.json", {"lang": "typescript", "scan_count": 4})

    adopted = migrate_legacy_lang_state(tmp_path)

    assert adopted == tmp_path / "state-typescript.json"
    assert json.loads((tmp_path / "state.json").read_text()) == {
        "lang": "typescript",
        "scan_count": 4,
    }
    assert not adopted.exists()


def test_javascript_state_is_relabelled(tmp_path):
    _write(
        tmp_path / "state-javascript.json",
        {
            "lang": "javascript",
            "work_items": {
                "smells::a.js::x": {"lang": "javascript", "status": "open"},
                "review::.::holistic::y": {"status": "open"},
            },
            "scan_coverage": {"javascript": {"status": "full"}},
            "lang_capabilities": {"javascript": {"fixers": []}},
        },
    )

    migrate_legacy_lang_state(tmp_path)

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["lang"] == "typescript"
    assert state["work_items"]["smells::a.js::x"]["lang"] == "typescript"
    assert "lang" not in state["work_items"]["review::.::holistic::y"]
    assert state["scan_coverage"] == {"typescript": {"status": "full"}}
    assert state["lang_capabilities"] == {"typescript": {"fixers": []}}
    assert not (tmp_path / "state-javascript.json").exists()


def test_typescript_state_wins_over_javascript(tmp_path):
    _write(tmp_path / "state-typescript.json", {"scan_count": 1})
    _write(tmp_path / "state-javascript.json", {"scan_count": 2})

    migrate_legacy_lang_state(tmp_path)

    assert json.loads((tmp_path / "state.json").read_text()) == {"scan_count": 1}
    assert (tmp_path / "state-javascript.json").exists()


def test_other_language_states_are_ignored(tmp_path):
    _write(tmp_path / "state-python.json", {"scan_count": 9})

    assert migrate_legacy_lang_state(tmp_path) is None
    assert not (tmp_path / "state.json").exists()


def test_unreadable_legacy_state_is_left_in_place(tmp_path):
    (tmp_path / "state-typescript.json").write_text("{not json")

    assert migrate_legacy_lang_state(tmp_path) is None
    assert (tmp_path / "state-typescript.json").exists()
    assert not (tmp_path / "state.json").exists()
