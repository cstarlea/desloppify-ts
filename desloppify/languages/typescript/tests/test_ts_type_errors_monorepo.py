"""Monorepo mode of the type_error detector: one tsc run per package tsconfig."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import desloppify.languages.typescript.detectors.tsc as tsc_mod
import desloppify.languages.typescript.phases_basic as phases_basic_mod
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.languages.typescript.detectors.bounded import (
    Budget,
    MemoryLimitExceeded,
    RunLimits,
    run_bounded,
)
from desloppify.languages.typescript.detectors.type_errors import detect_type_errors_result
from desloppify.languages.typescript.monorepo import monorepo_budget, monorepo_mode


def _write(root: Path, name: str, text: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A root config owning ``src/``, plus packages ``a`` and ``b`` with their own configs.

    ``outputs[tsconfig relpath] = (stdout, files)`` sets what each tsc run prints;
    a value that is an exception is raised instead.
    """
    _write(tmp_path, "tsconfig.json", "{}\n")
    _write(tmp_path, "package.json", '{"workspaces": ["packages/*"], "dependencies": {"x": "1"}}\n')
    (tmp_path / "node_modules").mkdir()
    _write(tmp_path, "src/root.ts")
    for name in ("a", "b"):
        _write(tmp_path, f"packages/{name}/package.json", f'{{"name": "@w/{name}"}}\n')
        _write(tmp_path, f"packages/{name}/tsconfig.json", "{}\n")
        _write(tmp_path, f"packages/{name}/src/index.ts")
    outputs: dict[str, object] = {
        "tsconfig.json": (
            "src/root.ts(1,1): error TS2304: Cannot find name 'r'.\n",
            ("src/root.ts", "packages/a/src/index.ts"),
        ),
    }
    calls: list[str] = []

    def run(project_root, tsconfig, limits=None):
        key = tsconfig.relative_to(tmp_path).as_posix()
        calls.append(key)
        output = outputs.get(key, ("", ()))
        if isinstance(output, BaseException):
            raise output
        stdout, files = output
        listed = "".join(f"{tmp_path / name}\n" for name in files)
        return SimpleNamespace(stdout=listed + stdout, stderr="", returncode=2 if stdout else 0)

    monkeypatch.setattr(tsc_mod, "run_tsc_check", run)
    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        yield tmp_path, outputs, calls


def _ids(result, root: Path) -> list[tuple[str, str, int]]:
    return [(Path(e["file"]).relative_to(root).as_posix(), e["code"], e["line"]) for e in result.entries]


def test_each_package_is_checked_with_its_own_tsconfig(workspace):
    root, outputs, calls = workspace
    outputs["packages/a/tsconfig.json"] = (
        "packages/a/src/index.ts(2,1): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        # b's file, seen through a's program: b's own run decides about it.
        "packages/b/src/index.ts(1,1): error TS2304: Cannot find name 'q'.\n",
        ("packages/a/src/index.ts", "packages/b/src/index.ts"),
    )
    outputs["packages/b/tsconfig.json"] = ("", ("packages/b/src/index.ts",))

    default = detect_type_errors_result(root, cache={})
    result = detect_type_errors_result(root, cache={}, monorepo=Budget(60, 1024))

    assert _ids(default, root) == [("src/root.ts", "TS2304", 1)]
    assert _ids(result, root) == [
        ("packages/a/src/index.ts", "TS2322", 2),
        ("src/root.ts", "TS2304", 1),
    ]
    assert sorted(Path(f).relative_to(root).as_posix() for f in result.checked_files) == [
        "packages/a/src/index.ts",
        "packages/b/src/index.ts",
        "src/root.ts",
    ]
    assert [(p.tsconfig.relative_to(root).as_posix(), p.skipped) for p in result.packages] == [
        ("packages/a/tsconfig.json", None),
        ("packages/b/tsconfig.json", None),
    ]
    assert result.coverage is None
    assert calls.count("tsconfig.json") == 2  # once per detect call: the base run is unchanged


def test_solution_config_runs_its_references_and_dedupes(workspace):
    # (tsconfig.app.json would own b's files by name, so the references use other names.)
    root, outputs, calls = workspace
    _write(
        root,
        "packages/b/tsconfig.json",
        '{"files": [], "references": [{"path": "./tsconfig.web.json"}, {"path": "./tsconfig.node.json"}]}\n',
    )
    error = "packages/b/src/index.ts(3,1): error TS2304: Cannot find name 'q'.\n"
    for name in ("web", "node"):
        _write(root, f"packages/b/tsconfig.{name}.json", "{}\n")
        outputs[f"packages/b/tsconfig.{name}.json"] = (error, ("packages/b/src/index.ts",))

    result = detect_type_errors_result(root, cache={}, monorepo=Budget(60, 1024))

    assert [i for i in _ids(result, root) if i[0].startswith("packages/b")] == [
        ("packages/b/src/index.ts", "TS2304", 3)
    ]
    assert "packages/b/tsconfig.web.json" in calls and "packages/b/tsconfig.node.json" in calls
    assert "packages/b/tsconfig.json" not in calls


def test_spent_time_budget_skips_the_remaining_packages(workspace):
    root, outputs, calls = workspace
    outputs["packages/b/tsconfig.json"] = (
        "packages/b/src/index.ts(1,1): error TS2304: Cannot find name 'q'.\n",
        ("packages/b/src/index.ts",),
    )
    result = detect_type_errors_result(root, cache={}, monorepo=Budget(1e-9, 1024))

    assert [p.skipped for p in result.packages] == [None, "time_budget"]
    assert "packages/b/tsconfig.json" not in calls
    assert not any(i[0].startswith("packages/b") for i in _ids(result, root))
    assert result.coverage is not None
    assert "1 of 2 package tsconfigs were not type-checked" in result.coverage.summary
    assert "time budget spent: packages/b/tsconfig.json" in result.coverage.summary


def test_timeout_and_memory_failures_skip_that_package(workspace):
    root, outputs, _calls = workspace
    outputs["packages/a/tsconfig.json"] = subprocess.TimeoutExpired("tsc", 1)
    outputs["packages/b/tsconfig.json"] = MemoryLimitExceeded("over 1024 MB")
    result = detect_type_errors_result(root, cache={}, monorepo=Budget(60, 1024))

    assert [p.skipped for p in result.packages] == ["timeout", "memory"]
    assert "over the memory limit: packages/b/tsconfig.json" in result.coverage.summary
    assert "tsc timed out: packages/a/tsconfig.json" in result.coverage.summary
    assert _ids(result, root) == [("src/root.ts", "TS2304", 1)]


def test_package_importing_an_unbuilt_workspace_package_is_skipped(workspace):
    root, outputs, _calls = workspace
    outputs["packages/b/tsconfig.json"] = (
        "packages/b/src/index.ts(1,19): error TS2307: Cannot find module '@w/a/sub' or its"
        " corresponding type declarations.\n"
        "packages/b/src/other.ts(4,9): error TS7006: Parameter 'x' implicitly has an 'any' type.\n",
        ("packages/b/src/index.ts", "packages/b/src/other.ts"),
    )
    _write(root, "packages/b/src/other.ts")
    result = detect_type_errors_result(root, cache={}, monorepo=Budget(60, 1024))

    assert [(p.skipped, p.unbuilt) for p in result.packages] == [
        (None, ()),
        ("workspace_unbuilt", ("@w/a",)),
    ]
    assert not any(i[0].startswith("packages/b") for i in _ids(result, root))
    assert "imports workspace packages that aren't built (@w/a)" in result.coverage.summary


def _lang(option: str = "", **settings) -> SimpleNamespace:
    return SimpleNamespace(
        zone_map=None,
        runtime_cache={},
        detector_coverage={},
        coverage_warnings=[],
        runtime_setting=lambda key, default=None: settings.get(key, default),
        runtime_option=lambda key, default=None: option if key == "monorepo_mode" else default,
    )


def test_mode_comes_from_the_option_then_the_setting():
    assert monorepo_mode(_lang()) == "off"
    assert monorepo_mode(_lang(monorepo_mode="packages")) == "packages"
    assert monorepo_mode(_lang("off", monorepo_mode="packages")) == "off"
    assert monorepo_mode(_lang("packages")) == "packages"
    assert monorepo_mode(_lang("everything")) == "off"
    assert monorepo_budget(_lang()) is None
    budget = monorepo_budget(_lang("packages", monorepo_budget_seconds=30, monorepo_max_memory_mb=512))
    assert budget is not None and (budget.seconds, budget.max_memory_mb) == (30, 512)


def test_phase_logs_packages_and_keeps_base_ids(workspace, capsys):
    root, outputs, _calls = workspace
    outputs["packages/a/tsconfig.json"] = (
        "packages/a/src/index.ts(2,1): error TS2322: Type 'string' is not assignable to type 'number'.\n",
        ("packages/a/src/index.ts",),
    )
    issues, potentials = phases_basic_mod.phase_type_errors(root, _lang("packages"))

    assert sorted(issue["id"] for issue in issues) == [
        "type_error::packages/a/src/index.ts::TS2322::2",
        "type_error::src/root.ts::TS2304::1",
    ]
    assert potentials == {"type_error": 2}
    assert "monorepo mode: 2 of 2 package tsconfigs type-checked" in capsys.readouterr().err


def test_budget_limits_each_run_to_what_is_left():
    budget = Budget(100, 512)
    first = budget.limits(300)
    assert first is not None and first.timeout <= 100 and first.max_memory_mb == 512
    assert Budget(0, 512).limits(300) == RunLimits(300, 512)


@pytest.mark.skipif(not Path("/proc/self/statm").exists(), reason="needs /proc")
def test_run_bounded_kills_a_process_over_the_memory_limit(tmp_path):
    hog = "import time; b = bytearray(300 * 2**20); b[::4096] = b'x' * len(b[::4096]); time.sleep(10)"
    with pytest.raises(MemoryLimitExceeded):
        run_bounded([sys.executable, "-c", hog], cwd=tmp_path, limits=RunLimits(20, 100))


def test_run_bounded_times_out_and_passes_output(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path, limits=RunLimits(0.5, 1024))
    done = run_bounded(
        [sys.executable, "-c", "import os; print(os.environ['NODE_OPTIONS'])"],
        cwd=tmp_path,
        limits=RunLimits(20, 1024),
    )
    assert done.returncode == 0 and "--max-old-space-size=1024" in done.stdout
