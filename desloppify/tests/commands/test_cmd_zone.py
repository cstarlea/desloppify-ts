"""Tests for desloppify.app.commands.zone — zone command helpers."""

import pytest

import desloppify.base.config as config_mod
from desloppify.app.commands.helpers.command_runtime import CommandRuntime
from desloppify.app.commands.zone import (
    _zone_clear,
    _zone_set,
    cmd_zone,
)
from desloppify.base.exception_sets import CommandError
from desloppify.state_io import load_state, save_state

# ---------------------------------------------------------------------------
# Module-level sanity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# cmd_zone dispatch
# ---------------------------------------------------------------------------


class TestCmdZoneDispatch:
    """cmd_zone dispatches to sub-actions based on zone_action attr."""

    def test_missing_action_defaults_to_show(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "desloppify.app.commands.zone._zone_show",
            lambda args: calls.append("show"),
        )

        class FakeArgs:
            zone_action = None

        cmd_zone(FakeArgs())
        assert calls == ["show"]

    def test_show_action_dispatches(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "desloppify.app.commands.zone._zone_show",
            lambda args: calls.append("show"),
        )

        class FakeArgs:
            zone_action = "show"

        cmd_zone(FakeArgs())
        assert calls == ["show"]

    def test_set_action_dispatches(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "desloppify.app.commands.zone._zone_set",
            lambda args: calls.append("set"),
        )

        class FakeArgs:
            zone_action = "set"

        cmd_zone(FakeArgs())
        assert calls == ["set"]

    def test_clear_action_dispatches(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "desloppify.app.commands.zone._zone_clear",
            lambda args: calls.append("clear"),
        )

        class FakeArgs:
            zone_action = "clear"

        cmd_zone(FakeArgs())
        assert calls == ["clear"]

    def test_unknown_action_prints_usage(self):
        import pytest

        class FakeArgs:
            zone_action = "bogus"

        with pytest.raises(CommandError, match="Usage:"):
            cmd_zone(FakeArgs())


# ---------------------------------------------------------------------------
# _zone_set
# ---------------------------------------------------------------------------


class TestZoneSet:
    """_zone_set validates zone values and persists overrides."""

    def test_invalid_zone_value(self, monkeypatch):
        """Setting an invalid zone value should exit with error."""
        import pytest

        fake_config = {"zone_overrides": {}}

        class FakeArgs:
            zone_path = "src/foo.ts"
            zone_value = "invalid_zone"
            lang = None
            path = "."
            runtime = CommandRuntime(
                config=fake_config,
                state={},
                state_path=None,
            )

        with pytest.raises(CommandError, match="Invalid zone"):
            _zone_set(FakeArgs())

    def test_valid_zone_value_saves(self, monkeypatch, capsys):
        """Setting a valid zone value should save config."""

        saved = []
        fake_config = {"zone_overrides": {}}
        monkeypatch.setattr(
            config_mod, "save_config", lambda cfg, path=None: saved.append(dict(cfg))
        )
        monkeypatch.setattr(
            "desloppify.app.commands.zone.rel",
            lambda p: p,
        )

        class FakeArgs:
            zone_path = "src/foo.ts"
            zone_value = "test"
            lang = None
            path = "."
            runtime = CommandRuntime(
                config=fake_config,
                state={},
                state_path=None,
            )

        _zone_set(FakeArgs())
        out = capsys.readouterr().out
        assert "src/foo.ts" in out
        assert "test" in out
        assert len(saved) == 1
        assert saved[0]["zone_overrides"]["src/foo.ts"] == "test"


# ---------------------------------------------------------------------------
# _zone_clear
# ---------------------------------------------------------------------------


class TestZoneClear:
    """_zone_clear removes zone overrides."""

    def test_clear_existing_override(self, monkeypatch, capsys):
        saved = []
        fake_config = {"zone_overrides": {"src/foo.ts": "test"}}
        monkeypatch.setattr(
            config_mod, "save_config", lambda cfg, path=None: saved.append(dict(cfg))
        )
        monkeypatch.setattr(
            "desloppify.app.commands.zone.rel",
            lambda p: p,
        )

        class FakeArgs:
            zone_path = "src/foo.ts"
            lang = None
            path = "."
            runtime = CommandRuntime(
                config=fake_config,
                state={},
                state_path=None,
            )

        _zone_clear(FakeArgs())
        out = capsys.readouterr().out
        assert "Cleared" in out
        assert len(saved) == 1
        assert "src/foo.ts" not in fake_config["zone_overrides"]

    def test_clear_nonexistent_override(self, monkeypatch, capsys):
        fake_config = {"zone_overrides": {}}
        monkeypatch.setattr(
            "desloppify.app.commands.zone.rel",
            lambda p: p,
        )

        class FakeArgs:
            zone_path = "src/bar.ts"
            lang = None
            path = "."
            runtime = CommandRuntime(
                config=fake_config,
                state={},
                state_path=None,
            )

        _zone_clear(FakeArgs())
        out = capsys.readouterr().out
        assert "No override found" in out


# ---------------------------------------------------------------------------
# Zone path normalization (#159)
# ---------------------------------------------------------------------------


class TestZonePathNormalization:
    """_zone_set and _zone_clear normalize paths with rel() before storing."""

    def test_zone_set_stores_normalized_key(self, monkeypatch, capsys):
        """_zone_set uses rel() to normalize the path before storing."""
        saved = []
        fake_config = {"zone_overrides": {}}
        monkeypatch.setattr(
            config_mod, "save_config", lambda cfg, path=None: saved.append(dict(cfg))
        )
        # rel() normalizes the absolute path to relative form
        monkeypatch.setattr(
            "desloppify.app.commands.zone.rel",
            lambda p: "src/file.py",
        )

        class FakeArgs:
            zone_path = "/absolute/project/src/file.py"
            zone_value = "production"
            lang = None
            path = "."
            runtime = CommandRuntime(
                config=fake_config,
                state={},
                state_path=None,
            )

        _zone_set(FakeArgs())
        assert len(saved) == 1
        # Key should be the normalized form, not the raw input
        assert "src/file.py" in saved[0]["zone_overrides"]
        assert "/absolute/project/src/file.py" not in saved[0]["zone_overrides"]

    def test_zone_clear_uses_normalized_key(self, monkeypatch, capsys):
        """_zone_clear uses rel() to normalize path for lookup."""
        saved = []
        fake_config = {"zone_overrides": {"src/file.py": "test"}}
        monkeypatch.setattr(
            config_mod, "save_config", lambda cfg, path=None: saved.append(dict(cfg))
        )
        monkeypatch.setattr(
            "desloppify.app.commands.zone.rel",
            lambda p: "src/file.py",
        )

        class FakeArgs:
            zone_path = "/absolute/project/src/file.py"
            lang = None
            path = "."
            runtime = CommandRuntime(
                config=fake_config,
                state={},
                state_path=None,
            )

        _zone_clear(FakeArgs())
        out = capsys.readouterr().out
        assert "Cleared" in out
        assert "src/file.py" not in fake_config["zone_overrides"]


# ---------------------------------------------------------------------------
# Directory and glob overrides
# ---------------------------------------------------------------------------


def _args(state_file, config, zone_path, zone_value=None):
    class FakeArgs:
        lang = None
        path = "."
        state = str(state_file)
        runtime = CommandRuntime(config=config, state={}, state_path=state_file)

    args = FakeArgs()
    args.zone_path = zone_path
    args.zone_value = zone_value
    return args


def _write_state(path, files):
    issues = {
        f"smells::{file}::x": {"file": file, "zone": "production", "status": "open"}
        for file in files
    }
    save_state({"work_items": issues}, path)


def _zones_in_state(path):
    return {
        issue["file"]: issue["zone"]
        for issue in load_state(path)["work_items"].values()
    }


class TestZonePatternOverrides:
    @pytest.fixture()
    def project(self, set_project_root, monkeypatch):
        root = set_project_root
        (root / "www" / "docs").mkdir(parents=True)
        (root / "src").mkdir()
        monkeypatch.setattr(config_mod, "save_config", lambda cfg, path=None: None)
        state_file = root / ".desloppify" / "state.json"
        state_file.parent.mkdir()
        _write_state(state_file, ["www/a.ts", "www/docs/b.ts", "src/app.ts", "www"])
        return root, state_file

    @pytest.mark.parametrize(
        ("given", "stored"),
        [
            ("www", "www/**"),
            ("www/", "www/**"),
            ("./www/docs", "www/docs/**"),
            ("www/**", "www/**"),
            ("**/*.gen.ts", "**/*.gen.ts"),
            ("src/app.ts", "src/app.ts"),
        ],
    )
    def test_set_normalizes_key(self, project, given, stored):
        _root, state_file = project
        config = {"zone_overrides": {}}
        _zone_set(_args(state_file, config, given, "vendor"))
        assert config["zone_overrides"] == {stored: "vendor"}

    def test_set_absolute_glob_is_made_relative(self, project):
        root, state_file = project
        config = {"zone_overrides": {}}
        _zone_set(_args(state_file, config, f"{root}/www/**/*.ts", "vendor"))
        assert config["zone_overrides"] == {"www/**/*.ts": "vendor"}

    def test_directory_with_brackets_is_escaped(self, project):
        root, state_file = project
        (root / "app" / "[slug]").mkdir(parents=True)
        config = {"zone_overrides": {}}
        _zone_set(_args(state_file, config, "app/[slug]", "test"))
        assert config["zone_overrides"] == {"app/[[]slug]/**": "test"}

    def test_set_pattern_restamps_matching_issues(self, project, capsys):
        _root, state_file = project
        config = {"zone_overrides": {"www/docs/b.ts": "test"}}
        _zone_set(_args(state_file, config, "www", "vendor"))
        assert "Applied to 2 issue(s)" in capsys.readouterr().out
        assert _zones_in_state(state_file) == {
            "www/a.ts": "vendor",
            "www/docs/b.ts": "production",  # the exact override still decides it
            "src/app.ts": "production",
            "www": "vendor",
        }

    def test_clear_pattern_falls_back_to_broader_pattern(self, project, capsys):
        _root, state_file = project
        config = {"zone_overrides": {}}
        _zone_set(_args(state_file, config, "www/docs", "test"))
        _zone_set(_args(state_file, config, "www", "vendor"))
        assert _zones_in_state(state_file)["www/docs/b.ts"] == "test"
        _zone_clear(_args(state_file, config, "www/docs"))
        assert config["zone_overrides"] == {"www/**": "vendor"}
        assert _zones_in_state(state_file)["www/docs/b.ts"] == "vendor"
        _zone_clear(_args(state_file, config, "www/**"))
        assert config["zone_overrides"] == {}
        assert set(_zones_in_state(state_file).values()) == {"production"}
        assert "Cleared override for www/**" in capsys.readouterr().out

    def test_clear_file_covered_by_pattern_names_the_pattern(self, project, capsys):
        _root, state_file = project
        config = {"zone_overrides": {"www/**": "vendor"}}
        _zone_clear(_args(state_file, config, "www/a.ts"))
        out = capsys.readouterr().out
        assert "No override found for www/a.ts" in out
        assert "covered by www/**" in out
        assert config["zone_overrides"] == {"www/**": "vendor"}
