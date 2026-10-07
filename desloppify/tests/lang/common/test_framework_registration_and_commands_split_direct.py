"""Direct tests for framework command/registration/runtime helper split modules."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import desloppify.app.commands.detect as detect_cmd_mod
import desloppify.app.commands.review.runtime.setup as review_runtime_setup_mod
import desloppify.app.commands.scan.contracts as scan_contracts_mod
import desloppify.app.commands.scan.coverage as scan_coverage_mod
import desloppify.app.commands.scan.workflow as scan_workflow_mod
import desloppify.app.commands.langs.cmd as langs_cmd_mod
import desloppify.engine._scoring.state_coverage as state_coverage_mod
import desloppify.engine._state.schema_types as state_schema_types_mod
import desloppify.engine.planning.scan as planning_scan_mod
import desloppify.languages as languages_mod
import desloppify.languages.framework as public_framework_mod
import desloppify.languages._framework as framework_root_mod
import desloppify.languages._framework.commands.registry as registry_cmd_mod
import desloppify.languages._framework.commands.scaffold as scaffold_mod
import desloppify.languages._framework.registry.registration as framework_registration_mod
import desloppify.languages._framework.runtime_support.accessors as accessors_mod
import desloppify.languages.typescript.commands as ts_commands_mod
import desloppify.languages.typescript.commands as ts_detector_cli_mod


def test_scaffold_defaults_and_registry_builder() -> None:
    registry = registry_cmd_mod.build_standard_detect_registry(
        cmd_deps=lambda _args: None,
        cmd_cycles=lambda _args: None,
        cmd_orphaned=lambda _args: None,
        cmd_dupes=lambda _args: None,
        cmd_large=lambda _args: None,
        cmd_complexity=lambda _args: None,
    )
    assert set(registry.keys()) == {
        "deps",
        "cycles",
        "orphaned",
        "dupes",
        "large",
        "complexity",
    }

    assert scaffold_mod.scaffold_find_replacements("a", "b", {}) == {}
    assert scaffold_mod.scaffold_find_self_replacements("a", "b", {}) == []
    assert scaffold_mod.scaffold_verify_hint() == "desloppify detect deps"


def test_framework_root_contract_is_types_only_and_explicit() -> None:
    src = inspect.getsource(framework_root_mod)
    assert "Top-level role" in src
    assert "Non-role" in src
    assert "types only" in src.lower()
    assert "catch-all entrypoint" in src
    assert framework_root_mod.__all__ == [
        "BoundaryRule",
        "DetectorPhase",
        "FixerConfig",
        "FixResult",
        "LangConfig",
        "LangValueSpec",
    ]


def test_public_framework_facade_exposes_operational_accessors() -> None:
    source = inspect.getsource(public_framework_mod)
    assert "Public framework facade" in source
    assert callable(public_framework_mod.load_all)
    assert callable(public_framework_mod.make_lang_config)
    assert callable(public_framework_mod.enable_parse_cache)
    assert hasattr(public_framework_mod, "registry_state")


def test_languages_registry_surface_keeps_legacy_exports_compat_only() -> None:
    source = inspect.getsource(languages_mod)
    assert "language registration api focused on canonical framework boundaries" in source.lower()
    assert "desloppify.languages.framework" in source
    assert "__getattr__" not in source
    assert "registry_state" not in languages_mod.__all__
    assert not hasattr(languages_mod, "registry_state")


def test_langs_command_uses_public_framework_facade() -> None:
    source = inspect.getsource(langs_cmd_mod)
    assert "from desloppify.languages.framework import" in source
    assert "desloppify.languages._framework" not in source


def test_app_and_engine_runtime_paths_use_public_framework_facade() -> None:
    for module in (
        detect_cmd_mod,
        review_runtime_setup_mod,
        scan_contracts_mod,
        scan_coverage_mod,
        scan_workflow_mod,
        planning_scan_mod,
        state_coverage_mod,
        state_schema_types_mod,
    ):
        source = inspect.getsource(module)
        assert "from desloppify.languages.framework import" in source
        assert "desloppify.languages._framework" not in source


def test_languages_readme_documents_current_runtime_boundary() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    readme_path = repo_root / "languages/README.md"
    source = readme_path.read_text(encoding="utf-8")
    assert "register_full_plugin(...)" in source
    assert "`desloppify.languages.framework`" in source


def test_typescript_command_registry_uses_base_composition_pattern() -> None:
    ts_source = inspect.getsource(ts_detector_cli_mod)
    assert "build_standard_detect_registry(" in ts_source
    assert "compose_detect_registry(" in ts_source

    ts_commands_source = inspect.getsource(ts_commands_mod)
    assert "build_standard_detect_registry(" in ts_commands_source
    assert "compose_detect_registry(" in ts_commands_source


def test_framework_registration_owns_lang_class_registration_flow() -> None:
    source = inspect.getsource(framework_registration_mod)
    assert "def register_lang_class(" in source
    assert "from desloppify.languages import register_lang" not in source


def test_make_cmd_deps_json_and_text_paths(monkeypatch, tmp_path) -> None:
    file_a = str((tmp_path / "a.py").resolve())
    file_b = str((tmp_path / "b.py").resolve())
    graph = {
        file_a: {
            "import_count": 1,
            "importer_count": 0,
            "imports": {file_b},
        }
    }

    cmd = registry_cmd_mod.make_cmd_deps(
        build_dep_graph_fn=lambda _path: graph,
        empty_message="No dependencies",
        import_count_label="Imports",
        top_imports_label="Top imports",
    )

    printed: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(a) for a in args)))
    monkeypatch.setattr(registry_cmd_mod, "colorize", lambda text, _style: text)
    monkeypatch.setattr(registry_cmd_mod, "print_table", lambda *args, **kwargs: printed.append("TABLE"))

    cmd(SimpleNamespace(path=str(tmp_path), json=True, top=5))
    payload = json.loads(printed[-1])
    assert payload["count"] == 1
    assert payload["entries"][0]["import_count"] == 1

    printed.clear()
    cmd(SimpleNamespace(path=str(tmp_path), json=False, top=5))
    assert any("Dependency graph:" in line for line in printed)
    assert "TABLE" in printed

    cmd_empty = registry_cmd_mod.make_cmd_deps(
        build_dep_graph_fn=lambda _path: {},
        empty_message="No dependencies",
        import_count_label="Imports",
        top_imports_label="Top imports",
    )
    printed.clear()
    cmd_empty(SimpleNamespace(path=str(tmp_path), json=False, top=5))
    assert any("No dependencies" in line for line in printed)


def test_make_cmd_cycles_orphaned_and_dupes(monkeypatch, tmp_path) -> None:
    file_a = str((tmp_path / "a.py").resolve())
    file_b = str((tmp_path / "b.py").resolve())
    graph = {
        file_a: {"imports": {file_b}, "importers": set(), "import_count": 1, "importer_count": 0},
        file_b: {"imports": set(), "importers": {file_a}, "import_count": 0, "importer_count": 1},
    }

    printed: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(a) for a in args)))
    monkeypatch.setattr(registry_cmd_mod, "colorize", lambda text, _style: text)
    monkeypatch.setattr(registry_cmd_mod, "print_table", lambda *args, **kwargs: printed.append("TABLE"))

    cycle_entries = [{"length": 2, "files": [file_a, file_b]}]
    monkeypatch.setattr(registry_cmd_mod, "detect_cycles", lambda _graph: (cycle_entries, 2))
    cmd_cycles = registry_cmd_mod.make_cmd_cycles(build_dep_graph_fn=lambda _path: graph)

    cmd_cycles(SimpleNamespace(path=str(tmp_path), json=True, top=5))
    payload = json.loads(printed[-1])
    assert payload["count"] == 1

    printed.clear()
    monkeypatch.setattr(registry_cmd_mod, "detect_cycles", lambda _graph: ([], 2))
    cmd_cycles(SimpleNamespace(path=str(tmp_path), json=False, top=5))
    assert any("No dependency cycles" in line for line in printed)

    orphan_entries = [{"file": file_a, "loc": 12}]
    monkeypatch.setattr(registry_cmd_mod, "detect_orphaned_files", lambda *_args, **_kwargs: (orphan_entries, 1))
    cmd_orphaned = registry_cmd_mod.make_cmd_orphaned(
        build_dep_graph_fn=lambda _path: graph,
        extensions=[".py"],
        extra_entry_patterns=["main.py"],
        extra_barrel_names={"__init__.py"},
    )

    printed.clear()
    cmd_orphaned(SimpleNamespace(path=str(tmp_path), json=True, top=5))
    payload = json.loads(printed[-1])
    assert payload["count"] == 1
    assert payload["entries"][0]["loc"] == 12

    monkeypatch.setattr(registry_cmd_mod, "detect_duplicates", lambda *_args, **_kwargs: ([
        {
            "fn_a": {"name": "a", "file": file_a, "line": 1},
            "fn_b": {"name": "b", "file": file_b, "line": 2},
            "similarity": 0.91,
            "kind": "exact",
        }
    ], 1))
    cmd_dupes = registry_cmd_mod.make_cmd_dupes(
        extract_functions_fn=lambda _path: [SimpleNamespace(name="a"), SimpleNamespace(name="b")]
    )

    printed.clear()
    cmd_dupes(SimpleNamespace(path=str(tmp_path), json=False, top=5, threshold=0.8))
    assert any("Duplicate functions:" in line for line in printed)
    assert "TABLE" in printed


class _AccessorHarness(accessors_mod.LangRunStateAccessors):
    def __init__(self):
        self.state = SimpleNamespace(
            zone_map=None,
            dep_graph=None,
            complexity_map={},
            runtime_cache={},
            review_cache={},
            review_max_age_days=30,
            runtime_settings={},
            runtime_options={},
            large_threshold_override=0,
            props_threshold_override=0,
            detector_coverage={},
            coverage_warnings=[],
        )
        self.config = SimpleNamespace(
            large_threshold=500,
            props_threshold=14,
            setting_specs={"flag": SimpleNamespace(default={"x": 1})},
            runtime_option_specs={"limit": SimpleNamespace(default=[1, 2, 3])},
        )


def test_runtime_accessors_cover_state_and_default_resolution() -> None:
    run = _AccessorHarness()

    run.zone_map = {"src/a.py": "production"}
    run.dep_graph = {"src/a.py": {"imports": set(), "importers": set()}}
    run.complexity_map = {"src/a.py": 4.2}
    run.runtime_cache = {"framework": {"present": True}}
    run.review_cache = {"src/a.py": {"score": 92}}
    run.review_max_age_days = "60"
    run.runtime_settings = {"flag": {"x": 9}}
    run.runtime_options = {"limit": [9]}
    run.large_threshold_override = "800"
    run.props_threshold_override = "25"
    run.detector_coverage = {"security": {"status": "full"}}
    run.coverage_warnings = [{"detector": "security"}]

    assert run.zone_map["src/a.py"] == "production"
    assert run.dep_graph["src/a.py"]["imports"] == set()
    assert run.complexity_map["src/a.py"] == 4.2
    assert run.runtime_cache["framework"]["present"] is True
    assert run.review_cache["src/a.py"]["score"] == 92
    assert run.review_max_age_days == 60
    assert run.runtime_settings["flag"] == {"x": 9}
    assert run.runtime_options["limit"] == [9]
    assert run.large_threshold == 800
    assert run.props_threshold == 25
    assert run.detector_coverage["security"]["status"] == "full"
    assert run.coverage_warnings[0]["detector"] == "security"

    run.large_threshold_override = 0
    run.props_threshold_override = 0
    assert run.large_threshold == 500
    assert run.props_threshold == 14

    default_setting = run.runtime_setting("flag")
    default_option = run.runtime_option("limit")
    assert default_setting == {"x": 9}
    assert default_option == [9]

    # Unknown keys should use provided defaults.
    assert run.runtime_setting("missing", default="fallback") == "fallback"
    assert run.runtime_option("missing", default=123) == 123
