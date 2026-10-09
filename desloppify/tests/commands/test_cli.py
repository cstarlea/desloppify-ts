"""Tests for desloppify.cli — argument parsing, state path resolution, helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import desloppify.cli as cli_mod
from desloppify.app.commands.helpers.lang import resolve_lang, resolve_lang_settings
from desloppify.app.commands.helpers.runtime_options import (
    LangRuntimeOptionsError,
    resolve_lang_runtime_options,
)
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.cli import (
    _get_detector_names,
    _resolve_default_path,
    _running_installed_package_from_checkout,
    _warn_if_running_installed_package_from_checkout,
    create_parser,
    state_path,
)
from desloppify.languages._framework.base.types_shared import LangValueSpec
from desloppify.languages.typescript import TypeScriptConfig

# ===========================================================================
# Module import
# ===========================================================================


def _lang_with_specs() -> TypeScriptConfig:
    lang = TypeScriptConfig()
    lang.runtime_option_specs = {"tsc_cmd": LangValueSpec(str, "")}
    lang.setting_specs = {
        "corroboration_min_signals": LangValueSpec(int, 2),
        "high_fanout_threshold": LangValueSpec(int, 5),
    }
    return lang


class TestInstalledPackageCheckoutWarning:
    @pytest.mark.parametrize("dist_name", ["desloppify", "desloppify-ts"])
    def test_detects_installed_package_running_from_checkout(
        self, tmp_path: Path, dist_name: str
    ) -> None:
        (tmp_path / "desloppify").mkdir()
        (tmp_path / "desloppify" / "__init__.py").write_text("__all__ = []\n")
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "{dist_name}"\nversion = "0.0.0"\n'
        )

        assert _running_installed_package_from_checkout(
            cwd_root=tmp_path,
            module_file="/tmp/site-packages/desloppify/cli.py",
        )

    def test_does_not_flag_local_checkout_execution(self, tmp_path: Path) -> None:
        (tmp_path / "desloppify").mkdir()
        (tmp_path / "desloppify" / "__init__.py").write_text("__all__ = []\n")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "desloppify"\nversion = "0.0.0"\n'
        )

        assert not _running_installed_package_from_checkout(
            cwd_root=tmp_path,
            module_file=tmp_path / "desloppify" / "cli.py",
        )

    def test_does_not_flag_non_checkout_project(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "other-project"\nversion = "0.0.0"\n'
        )

        assert not _running_installed_package_from_checkout(
            cwd_root=tmp_path,
            module_file="/tmp/site-packages/desloppify/cli.py",
        )

    def test_warns_when_running_installed_package_from_checkout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "desloppify").mkdir()
        (tmp_path / "desloppify" / "__init__.py").write_text("__all__ = []\n")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "desloppify"\nversion = "0.0.0"\n'
        )

        monkeypatch.setattr(cli_mod, "get_project_root", lambda: tmp_path)
        monkeypatch.setattr(cli_mod, "__file__", "/tmp/site-packages/desloppify/cli.py")

        _warn_if_running_installed_package_from_checkout()

        err = capsys.readouterr().err
        assert "running installed desloppify package" in err
        assert "python -m desloppify" in err


# ===========================================================================
# create_parser — argument parsing
# ===========================================================================


class TestCreateParser:
    @pytest.fixture()
    def parser(self):
        return create_parser()

    def test_scan_command_parses(self, parser):
        args = parser.parse_args(["scan"])
        assert args.command == "scan"
        assert args.path is None
        assert args.reset_subjective is False
        assert args.skip_slow is False
        assert args.profile is None

    def test_scan_with_path_and_skip_slow(self, parser):
        args = parser.parse_args(["scan", "--path", "/tmp/mycode", "--skip-slow"])
        assert args.path == "/tmp/mycode"
        assert args.skip_slow is True

    def test_scan_with_reset_subjective_flag(self, parser):
        args = parser.parse_args(["scan", "--reset-subjective"])
        assert args.reset_subjective is True

    def test_scan_rejects_legacy_deep_flag(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["scan", "--deep"])

    def test_scan_with_profile(self, parser):
        args = parser.parse_args(["scan", "--profile", "ci"])
        assert args.profile == "ci"

    def test_scan_with_lang_opt(self, parser):
        args = parser.parse_args(["scan", "--lang-opt", "foo=bar", "--lang-opt", "x=1"])
        assert args.lang_opt == ["foo=bar", "x=1"]

    def test_scan_rejects_language_specific_legacy_flag(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["scan", "--roslyn-cmd", "legacy"])

    def test_scan_rejects_lang_flag(self, parser, capsys):
        with pytest.raises(SystemExit):
            parser.parse_args(["scan", "--lang", "python"])
        err = capsys.readouterr().err
        assert "unrecognized arguments" in err
        assert "--lang" in err

    def test_scan_with_exclude(self, parser):
        args = parser.parse_args(
            ["--exclude", "node_modules", "--exclude", "dist", "scan"]
        )
        assert args.exclude == ["node_modules", "dist"]

    def test_top_level_version_flag(self, parser, capsys):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out.startswith("desloppify")
        assert "version unknown" not in out
        assert "\nPython " in out
        assert " at " in out

    def test_top_level_short_version_flag(self, parser, capsys):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["-V"])
        assert exc.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out.startswith("desloppify")
        assert "\nPython " in out
        assert " at " in out

    def test_status_command(self, parser):
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_status_with_json_flag(self, parser):
        args = parser.parse_args(["status", "--json"])
        assert args.json is True

    def test_show_command_with_pattern(self, parser):
        args = parser.parse_args(["show", "src/foo.py"])
        assert args.command == "show"
        assert args.pattern == "src/foo.py"

    def test_show_command_default_status(self, parser):
        args = parser.parse_args(["show"])
        assert args.status == "open"

    def test_show_command_with_status_filter(self, parser):
        args = parser.parse_args(["show", "--status", "all"])
        assert args.status == "all"

    def test_show_chronic_flag(self, parser):
        args = parser.parse_args(["show", "--chronic"])
        assert args.chronic is True

    def test_next_command(self, parser):
        args = parser.parse_args(["next"])
        assert args.command == "next"
        assert args.count == 1

    def test_backlog_command(self, parser):
        args = parser.parse_args(["backlog"])
        assert args.command == "backlog"
        assert args.count == 1

    def test_next_with_scope_status_group_and_format(self, parser):
        args = parser.parse_args(
            [
                "next",
                "--scope",
                "src/core",
                "--status",
                "all",
                "--group",
                "file",
                "--format",
                "md",
            ]
        )
        assert args.scope == "src/core"
        assert args.status == "all"
        assert args.group == "file"
        assert args.format == "md"

    def test_plan_resolve_command(self, parser):
        args = parser.parse_args(["plan", "resolve", "id1", "id2"])
        assert args.command == "plan"
        assert args.plan_action == "resolve"
        assert args.patterns == ["id1", "id2"]

    def test_plan_resolve_with_note(self, parser):
        args = parser.parse_args(["plan", "resolve", "id1", "--note", "removed import"])
        assert args.note == "removed import"

    def test_plan_resolve_with_attest(self, parser):
        args = parser.parse_args(
            [
                "plan",
                "resolve",
                "id1",
                "--attest",
                "I have actually fixed this and I am not gaming",
            ]
        )
        assert args.attest is not None

    def test_resolve_not_top_level(self, parser):
        """resolve is no longer a top-level command."""
        import pytest

        with pytest.raises(SystemExit):
            parser.parse_args(["resolve", "fixed", "id1"])

    def test_suppress_command(self, parser):
        args = parser.parse_args(["suppress", "smells::*::async_no_await"])
        assert args.command == "suppress"
        assert args.pattern == "smells::*::async_no_await"

    def test_suppress_with_attest(self, parser):
        args = parser.parse_args(
            [
                "suppress",
                "smells::*::async_no_await",
                "--attest",
                "I have actually reviewed this and I am not gaming",
            ]
        )
        assert args.attest is not None

    def test_autofix_command(self, parser):
        args = parser.parse_args(["autofix", "unused_imports", "--dry-run"])
        assert args.command == "autofix"
        assert args.fixer == "unused_imports"
        assert args.dry_run is True

    def test_plan_command(self, parser):
        args = parser.parse_args(["plan"])
        assert args.command == "plan"

    def test_plan_triage_parses_flags(self, parser):
        args = parser.parse_args(
            ["plan", "triage", "--stage", "observe", "--report", "analysis"]
        )
        assert args.command == "plan"
        assert args.plan_action == "triage"
        assert args.stage == "observe"
        assert args.report == "analysis"

    def test_plan_with_output(self, parser):
        args = parser.parse_args(["plan", "--output", "plan.md"])
        assert args.output == "plan.md"

    def test_tree_command_defaults(self, parser):
        args = parser.parse_args(["tree"])
        assert args.command == "tree"
        assert args.depth == 2
        assert args.focus is None
        assert args.min_loc == 0
        assert args.sort == "loc"
        assert args.detail is False

    def test_tree_with_all_options(self, parser):
        args = parser.parse_args(
            [
                "tree",
                "--depth",
                "4",
                "--focus",
                "shared/components",
                "--min-loc",
                "100",
                "--sort",
                "issues",
                "--detail",
            ]
        )
        assert args.depth == 4
        assert args.focus == "shared/components"
        assert args.min_loc == 100
        assert args.sort == "issues"
        assert args.detail is True

    def test_detect_command(self, parser):
        args = parser.parse_args(["detect", "smells", "--top", "5"])
        assert args.command == "detect"
        assert args.detector == "smells"
        assert args.top == 5

    def test_detect_with_threshold(self, parser):
        args = parser.parse_args(["detect", "dupes", "--threshold", "0.85"])
        assert args.threshold == pytest.approx(0.85)

    def test_detect_with_lang_opt(self, parser):
        args = parser.parse_args(["detect", "deps", "--lang-opt", "foo=bar"])
        assert args.lang_opt == ["foo=bar"]

    def test_detect_rejects_language_specific_legacy_flag(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["detect", "deps", "--roslyn-cmd", "legacy"])

    def test_lang_opt_parsed_against_runtime_option_specs(self):
        args = SimpleNamespace(lang_opt=["tsc_cmd=fake-tsc --json"])
        options = resolve_lang_runtime_options(args, _lang_with_specs())
        assert options["tsc_cmd"] == "fake-tsc --json"

    def test_lang_opt_rejects_invalid_key_value_pair(self):
        args = SimpleNamespace(lang_opt=["not_a_pair"])
        with pytest.raises(LangRuntimeOptionsError) as exc:
            resolve_lang_runtime_options(args, _lang_with_specs())
        assert "Invalid --lang-opt" in str(exc.value)
        assert "Expected KEY=VALUE" in str(exc.value)

    def test_language_settings_loaded_from_config_namespace(self):
        lang = _lang_with_specs()
        config = {
            "languages": {
                "typescript": {
                    "corroboration_min_signals": 3,
                    "high_fanout_threshold": 8,
                }
            }
        }
        settings = resolve_lang_settings(config, lang)
        assert settings["corroboration_min_signals"] == 3
        assert settings["high_fanout_threshold"] == 8

    def test_move_command(self, parser):
        args = parser.parse_args(["move", "src/foo.py", "src/bar/foo.py", "--dry-run"])
        assert args.command == "move"
        assert args.source == "src/foo.py"
        assert args.dest == "src/bar/foo.py"
        assert args.dry_run is True

    def test_viz_command(self, parser):
        args = parser.parse_args(["viz"])
        assert args.command == "viz"

    def test_review_command_defaults(self, parser):
        args = parser.parse_args(["review"])
        assert args.command == "review"
        assert args.prepare is False
        assert args.import_file is None
        assert args.validate_import_file is None
        assert args.external_start is False
        assert args.external_submit is False
        assert args.session_id is None
        assert args.external_runner == "claude"
        assert args.session_ttl_hours == 24
        assert args.allow_partial is False
        assert args.manual_override is False
        assert args.attested_external is False
        assert args.attest is None
        assert args.retrospective is True
        assert args.retrospective_max_issues == 30
        assert args.retrospective_max_batch_items == 20

    def test_review_prepare_flag(self, parser):
        args = parser.parse_args(["review", "--prepare"])
        assert args.prepare is True

    def test_review_allow_partial_flag(self, parser):
        args = parser.parse_args(
            ["review", "--import", "issues.json", "--allow-partial"]
        )
        assert args.import_file == "issues.json"
        assert args.allow_partial is True

    def test_review_validate_import_flag(self, parser):
        args = parser.parse_args(["review", "--validate-import", "issues.json"])
        assert args.validate_import_file == "issues.json"

    def test_review_external_start_flag(self, parser):
        args = parser.parse_args(
            [
                "review",
                "--external-start",
                "--external-runner",
                "claude",
                "--session-ttl-hours",
                "12",
            ]
        )
        assert args.external_start is True
        assert args.external_runner == "claude"
        assert args.session_ttl_hours == 12

    def test_review_external_submit_flag(self, parser):
        args = parser.parse_args(
            [
                "review",
                "--external-submit",
                "--session-id",
                "ext_20260223_000000_deadbeef",
                "--import",
                "issues.json",
            ]
        )
        assert args.external_submit is True
        assert args.session_id == "ext_20260223_000000_deadbeef"
        assert args.import_file == "issues.json"

    def test_review_manual_override_flag(self, parser):
        args = parser.parse_args(
            [
                "review",
                "--import",
                "issues.json",
                "--manual-override",
                "--attest",
                "manual calibration justified by independent reviewer output",
            ]
        )
        assert args.manual_override is True
        assert isinstance(args.attest, str)

    def test_review_attested_external_flag(self, parser):
        args = parser.parse_args(
            [
                "review",
                "--import",
                "issues.json",
                "--attested-external",
                "--attest",
                "I validated this review was completed without awareness of overall score and is unbiased.",
            ]
        )
        assert args.attested_external is True
        assert isinstance(args.attest, str)

    def test_config_command_defaults(self, parser):
        args = parser.parse_args(["config"])
        assert args.command == "config"
        assert args.config_action is None

    def test_config_set_subcommand(self, parser):
        args = parser.parse_args(["config", "set", "review_max_age_days", "14"])
        assert args.command == "config"
        assert args.config_action == "set"
        assert args.config_key == "review_max_age_days"
        assert args.config_value == "14"

    def test_config_unset_subcommand(self, parser):
        args = parser.parse_args(["config", "unset", "review_max_age_days"])
        assert args.command == "config"
        assert args.config_action == "unset"
        assert args.config_key == "review_max_age_days"

    def test_zone_show(self, parser):
        args = parser.parse_args(["zone", "show"])
        assert args.command == "zone"
        assert args.zone_action == "show"

    def test_zone_set(self, parser):
        args = parser.parse_args(["zone", "set", "src/foo.py", "test"])
        assert args.zone_action == "set"
        assert args.zone_path == "src/foo.py"
        assert args.zone_value == "test"

    def test_zone_clear(self, parser):
        args = parser.parse_args(["zone", "clear", "src/foo.py"])
        assert args.zone_action == "clear"
        assert args.zone_path == "src/foo.py"

    def test_scan_badge_options(self, parser):
        args = parser.parse_args(["scan", "--no-badge", "--badge-path", "custom.png"])
        assert args.no_badge is True
        assert args.badge_path == "custom.png"

    def test_missing_command_defaults_to_none(self, parser):
        args = parser.parse_args([])
        assert args.command is None

    def test_invalid_resolve_status_raises(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["resolve", "invalid_status", "id1"])


# ===========================================================================
# _get_detector_names (lazy)
# ===========================================================================


class TestDetectorNames:
    def test_is_non_empty_list(self):
        names = _get_detector_names()
        assert isinstance(names, list)
        assert len(names) > 0

    def test_contains_known_detectors(self):
        names = _get_detector_names()
        for name in ["logs", "unused", "smells", "cycles", "dupes"]:
            assert name in names


# ===========================================================================
# state_path
# ===========================================================================


class TestStatePath:
    def test_returns_explicit_state_path(self):
        args = SimpleNamespace(state="/tmp/custom.json")
        assert state_path(args) == Path("/tmp/custom.json")

    def test_defaults_to_state_json(self, tmp_path):
        with runtime_scope(RuntimeContext(project_root=tmp_path)):
            result = state_path(SimpleNamespace(state=None))
        assert result == tmp_path / ".desloppify" / "state.json"

    def test_adopts_legacy_typescript_state(self, tmp_path):
        state_dir = tmp_path / ".desloppify"
        state_dir.mkdir()
        (state_dir / "state-typescript.json").write_text('{"scan_count": 3}')
        with runtime_scope(RuntimeContext(project_root=tmp_path)):
            result = state_path(SimpleNamespace(state=None))
        assert result == state_dir / "state.json"
        assert json.loads(result.read_text()) == {"scan_count": 3}
        assert not (state_dir / "state-typescript.json").exists()

    def test_existing_state_json_is_not_replaced(self, tmp_path):
        state_dir = tmp_path / ".desloppify"
        state_dir.mkdir()
        (state_dir / "state.json").write_text('{"scan_count": 1}')
        (state_dir / "state-typescript.json").write_text('{"scan_count": 3}')
        with runtime_scope(RuntimeContext(project_root=tmp_path)):
            result = state_path(SimpleNamespace(state=None))
        assert json.loads(result.read_text()) == {"scan_count": 1}
        assert (state_dir / "state-typescript.json").exists()


class TestResolveDefaultPath:
    """Tests for _resolve_default_path — defaulting to the last scan's scan_path."""

    def test_does_nothing_when_path_already_set(self):
        args = SimpleNamespace(command="review", path="/explicit/path")
        _resolve_default_path(args)
        assert args.path == "/explicit/path"

    def test_review_uses_scan_path_from_state(self, monkeypatch, tmp_path):
        """Regression test for issue #127: review --prepare should use last scan path."""
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        # Simulate a project with files at the root (no src/ subdir)
        (project_root / "server.ts").write_text("export {}")
        saved_state = {"scan_path": "."}  # scan was run with --path .

        monkeypatch.setattr(cli_mod, "get_project_root", lambda: project_root)

        with (
            patch("desloppify.cli.state_path", return_value=tmp_path / "state.json"),
            patch("desloppify.cli.load_state", return_value=saved_state),
        ):
            args = SimpleNamespace(command="review", path=None)
            _resolve_default_path(args)

        assert args.path == str(project_root.resolve())

    def test_review_falls_back_to_lang_default_when_no_scan_path(self, monkeypatch):
        """When state has no scan_path, review falls back to lang.default_src."""
        with (
            patch("desloppify.cli.state_path", return_value=None),
            patch("desloppify.cli.load_state", return_value={}),
            patch("desloppify.cli.resolve_lang") as mock_lang,
        ):
            mock_lang.return_value = SimpleNamespace(default_src="src")
            args = SimpleNamespace(command="review", path=None)
            _resolve_default_path(args)

        assert args.path.endswith("src")

    def test_review_falls_back_when_state_load_raises(self, monkeypatch):
        """If state cannot be loaded, path resolution continues without crashing."""
        with (
            patch("desloppify.cli.state_path", return_value=None),
            patch("desloppify.cli.load_state", side_effect=OSError("no file")),
            patch("desloppify.cli.resolve_lang") as mock_lang,
        ):
            mock_lang.return_value = SimpleNamespace(default_src="src")
            args = SimpleNamespace(command="review", path=None)
            _resolve_default_path(args)  # must not raise

        assert args.path.endswith("src")

    @pytest.mark.parametrize(
        "command", ["scan", "autofix", "detect", "tree", "viz", "zone"]
    )
    def test_other_path_commands_use_scan_path_from_state(
        self, monkeypatch, tmp_path, command
    ):
        """Every --path command defaults to the last scan's scope, not src/."""
        project_root = tmp_path / "myproject"
        (project_root / "app").mkdir(parents=True)
        monkeypatch.setattr(cli_mod, "get_project_root", lambda: project_root)

        with (
            patch("desloppify.cli.state_path", return_value=tmp_path / "state.json"),
            patch("desloppify.cli.load_state", return_value={"scan_path": "app"}),
        ):
            args = SimpleNamespace(command=command, path=None)
            _resolve_default_path(args)

        assert args.path == str((project_root / "app").resolve())

    def test_missing_saved_scan_path_falls_back_to_lang_default(
        self, monkeypatch, tmp_path
    ):
        project_root = tmp_path / "proj"
        project_root.mkdir()
        monkeypatch.setattr(cli_mod, "get_project_root", lambda: project_root)
        with (
            patch("desloppify.cli.state_path", return_value=tmp_path / "state.json"),
            patch("desloppify.cli.load_state", return_value={"scan_path": "gone"}),
            patch("desloppify.cli.resolve_lang") as mock_lang,
        ):
            mock_lang.return_value = SimpleNamespace(default_src="src")
            args = SimpleNamespace(command="autofix", path=None)
            _resolve_default_path(args)

        assert args.path == str(project_root / "src")

    def test_command_without_scan_path_uses_lang_default(self):
        with (
            patch("desloppify.cli.state_path", return_value=None),
            patch("desloppify.cli.resolve_lang") as mock_lang,
        ):
            mock_lang.return_value = SimpleNamespace(default_src="src")
            args = SimpleNamespace(command="scan", path=None)
            _resolve_default_path(args)

        assert args.path.endswith("src")

    def test_command_honors_language_default_src_exactly(self, monkeypatch, tmp_path):
        project_root = tmp_path / "proj"
        project_root.mkdir()
        monkeypatch.setattr(cli_mod, "get_project_root", lambda: project_root)
        with (
            patch("desloppify.cli.state_path", return_value=None),
            patch("desloppify.cli.resolve_lang") as mock_lang,
        ):
            mock_lang.return_value = SimpleNamespace(default_src=".")
            args = SimpleNamespace(command="scan", path=None)
            _resolve_default_path(args)

        assert args.path == str(project_root.resolve())

    def test_command_without_path_argument_is_untouched(self):
        args = SimpleNamespace(command="next")
        _resolve_default_path(args)
        assert not hasattr(args, "path")


class TestResolveLang:
    def test_always_typescript(self, tmp_path):
        lang = resolve_lang(SimpleNamespace(path=str(tmp_path)))
        assert lang.name == "typescript"


# ===========================================================================
# _project_root_from_state_path
# ===========================================================================


class TestProjectRootFromStatePath:
    """Infer project root from an explicit --state path."""

    def test_language_state_file(self, tmp_path: Path):
        dot = tmp_path / ".desloppify"
        dot.mkdir()
        sf = dot / "state-python.json"
        sf.write_text("{}")
        from desloppify.cli import _project_root_from_state_path

        assert _project_root_from_state_path(str(sf)) == tmp_path

    def test_plain_state_file(self, tmp_path: Path):
        dot = tmp_path / ".desloppify"
        dot.mkdir()
        sf = dot / "state.json"
        sf.write_text("{}")
        from desloppify.cli import _project_root_from_state_path

        assert _project_root_from_state_path(str(sf)) == tmp_path

    def test_none_input(self):
        from desloppify.cli import _project_root_from_state_path

        assert _project_root_from_state_path(None) is None

    def test_empty_string(self):
        from desloppify.cli import _project_root_from_state_path

        assert _project_root_from_state_path("") is None

    def test_non_desloppify_dir(self, tmp_path: Path):
        sf = tmp_path / "other" / "state-python.json"
        sf.parent.mkdir()
        sf.write_text("{}")
        from desloppify.cli import _project_root_from_state_path

        assert _project_root_from_state_path(str(sf)) is None

    def test_unexpected_filename(self, tmp_path: Path):
        dot = tmp_path / ".desloppify"
        dot.mkdir()
        sf = dot / "config.json"
        sf.write_text("{}")
        from desloppify.cli import _project_root_from_state_path

        assert _project_root_from_state_path(str(sf)) is None


# ---------------------------------------------------------------------------
# _project_root_from_scan_path
# ---------------------------------------------------------------------------


class TestProjectRootFromScanPath:
    """``--path`` decides the project root (roadmap 1.9)."""

    def test_none_or_empty(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        assert _project_root_from_scan_path(None, tmp_path) is None
        assert _project_root_from_scan_path("", tmp_path) is None

    def test_existing_state_dir_wins(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / "repo" / ".git").mkdir(parents=True)
        (tmp_path / "repo" / "app" / ".desloppify").mkdir(parents=True)
        (tmp_path / "repo" / "app" / "src").mkdir()
        cwd = tmp_path / "elsewhere"
        cwd.mkdir()
        assert _project_root_from_scan_path(tmp_path / "repo" / "app" / "src", cwd) == (
            tmp_path / "repo" / "app"
        )

    def test_nested_git_repo_beats_outer_state(self, tmp_path):
        """A repo copied inside a project with state keeps its own state."""
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / ".git").mkdir()
        (tmp_path / ".desloppify").mkdir()
        (tmp_path / "copies" / "ky" / ".git").mkdir(parents=True)
        (tmp_path / "copies" / "ky" / "src").mkdir()
        assert _project_root_from_scan_path(
            tmp_path / "copies" / "ky" / "src", tmp_path
        ) == (tmp_path / "copies" / "ky")

    def test_monorepo_package_with_own_git(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / ".git").mkdir()
        (tmp_path / ".desloppify").mkdir()
        (tmp_path / "packages" / "a" / ".git").mkdir(parents=True)
        assert _project_root_from_scan_path(tmp_path / "packages" / "a", tmp_path) == (
            tmp_path / "packages" / "a"
        )

    def test_state_at_git_root(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / "repo" / ".git").mkdir(parents=True)
        (tmp_path / "repo" / ".desloppify").mkdir()
        (tmp_path / "repo" / "packages" / "a").mkdir(parents=True)
        assert _project_root_from_scan_path(
            tmp_path / "repo" / "packages" / "a", tmp_path / "other"
        ) == (tmp_path / "repo")

    def test_state_in_subdirectory_beats_git_root(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / "repo" / ".git").mkdir(parents=True)
        (tmp_path / "repo" / ".desloppify").mkdir()
        (tmp_path / "repo" / "packages" / "a" / ".desloppify").mkdir(parents=True)
        assert _project_root_from_scan_path(
            tmp_path / "repo" / "packages" / "a", tmp_path / "repo"
        ) == (tmp_path / "repo" / "packages" / "a")

    def test_state_without_git(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / "app" / ".desloppify").mkdir(parents=True)
        (tmp_path / "app" / "src").mkdir()
        assert _project_root_from_scan_path(tmp_path / "app" / "src", tmp_path) == (
            tmp_path / "app"
        )

    def test_desloppify_file_is_not_state(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / "repo" / ".git").mkdir(parents=True)
        (tmp_path / "repo" / "app").mkdir()
        (tmp_path / "repo" / "app" / ".desloppify").write_text("")
        assert _project_root_from_scan_path(tmp_path / "repo" / "app", tmp_path) == (
            tmp_path / "repo"
        )

    def test_worktree_gitfile_is_a_root(self, tmp_path):
        """An agent worktree under the main checkout is its own project."""
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / ".git" / "worktrees" / "wt").mkdir(parents=True)
        (tmp_path / ".desloppify").mkdir()
        wt = tmp_path / ".claude" / "worktrees" / "wt"
        (wt / "src").mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {tmp_path / '.git' / 'worktrees' / 'wt'}\n")
        assert _project_root_from_scan_path(wt / "src", tmp_path) == wt

    def test_submodule_stays_with_superproject_state(self, tmp_path):
        """A submodule's .git must not move an existing project's state."""
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / ".git").mkdir()
        (tmp_path / ".desloppify").mkdir()
        (tmp_path / "vendor" / "lib").mkdir(parents=True)
        (tmp_path / "vendor" / "lib" / ".git").write_text(
            "gitdir: ../../.git/modules/vendor/lib\n"
        )
        assert (
            _project_root_from_scan_path(tmp_path / "vendor" / "lib", tmp_path)
            == tmp_path
        )

    def test_submodule_without_superproject_state_is_its_own_root(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / ".git").mkdir()
        (tmp_path / "vendor" / "lib" / "src").mkdir(parents=True)
        (tmp_path / "vendor" / "lib" / ".git").write_text(
            "gitdir: ../../.git/modules/vendor/lib\n"
        )
        assert _project_root_from_scan_path(
            tmp_path / "vendor" / "lib" / "src", tmp_path
        ) == (tmp_path / "vendor" / "lib")

    def test_submodule_with_own_state(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / ".git").mkdir()
        (tmp_path / ".desloppify").mkdir()
        lib = tmp_path / "vendor" / "lib"
        (lib / ".desloppify").mkdir(parents=True)
        (lib / ".git").write_text("gitdir: ../../.git/modules/vendor/lib\n")
        assert _project_root_from_scan_path(lib, tmp_path) == lib

    def test_git_work_tree(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / "repo" / ".git").mkdir(parents=True)
        (tmp_path / "repo" / "packages" / "a").mkdir(parents=True)
        assert _project_root_from_scan_path(
            tmp_path / "repo" / "packages" / "a", tmp_path / "other"
        ) == (tmp_path / "repo")

    def test_path_inside_cwd_without_markers_keeps_cwd(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / "src").mkdir()
        assert _project_root_from_scan_path(tmp_path / "src", tmp_path) is None

    def test_path_outside_cwd_without_markers_is_its_own_root(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / "app").mkdir()
        (tmp_path / "cwd").mkdir()
        assert _project_root_from_scan_path(tmp_path / "app", tmp_path / "cwd") == (
            tmp_path / "app"
        )

    def test_file_path_uses_its_directory(self, tmp_path):
        from desloppify.cli import _project_root_from_scan_path

        (tmp_path / "app").mkdir()
        target = tmp_path / "app" / "main.ts"
        target.write_text("export {};\n")
        assert (
            _project_root_from_scan_path(target, tmp_path / "cwd") == tmp_path / "app"
        )


class TestMainProjectRoot:
    """``main`` applies the ``--path`` inference unless DESLOPPIFY_ROOT is set."""

    @staticmethod
    def _root_seen_by_handler(monkeypatch, argv, cwd):
        from desloppify.base.discovery.paths import get_project_root

        seen = []
        monkeypatch.chdir(cwd)
        monkeypatch.setattr("sys.argv", ["desloppify", *argv])
        monkeypatch.setattr(
            cli_mod,
            "_resolve_handler",
            lambda _cmd: lambda _args: seen.append(get_project_root()),
        )
        cli_mod.main()
        return seen[0]

    def _layout(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".desloppify").mkdir()
        nested = tmp_path / "copies" / "ky"
        (nested / ".git").mkdir(parents=True)
        (nested / "src").mkdir()
        return nested

    def test_nested_repo_is_root(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DESLOPPIFY_ROOT", raising=False)
        nested = self._layout(tmp_path)
        root = self._root_seen_by_handler(
            monkeypatch, ["scan", "--path", "copies/ky/src"], tmp_path
        )
        assert root == nested

    def test_env_root_overrides_path_inference(self, tmp_path, monkeypatch):
        self._layout(tmp_path)
        override = tmp_path / "override"
        override.mkdir()
        monkeypatch.setenv("DESLOPPIFY_ROOT", str(override))
        root = self._root_seen_by_handler(
            monkeypatch, ["scan", "--path", "copies/ky/src"], tmp_path
        )
        assert root == override


class TestRemovedLangFlag:
    @pytest.mark.parametrize(
        "argv", [["--lang", "typescript", "scan"], ["scan", "--lang=python"]]
    )
    def test_explains_removal(self, argv, capsys):
        with pytest.raises(SystemExit) as exc:
            cli_mod._reject_removed_lang_flag(argv)
        assert exc.value.code == 2
        assert "--lang was removed" in capsys.readouterr().err

    def test_ignores_lang_opt_and_args_after_separator(self):
        cli_mod._reject_removed_lang_flag(
            ["scan", "--lang-opt", "tsc_cmd=x", "--", "--lang"]
        )
