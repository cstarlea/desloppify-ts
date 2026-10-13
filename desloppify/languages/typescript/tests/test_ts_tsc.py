"""Tests for the shared tsc run (detectors/tsc.py)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import desloppify.languages.typescript.detectors.tsc as tsc_mod
import desloppify.languages.typescript.detectors.unused as unused_mod
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.languages.typescript.detectors.tsc import (
    TscDiagnostic,
    parse_tsc_output,
)
from desloppify.languages.typescript.tests.golden import harness


def test_parse_joins_message_chains_and_collects_listed_files():
    lines = [
        "/proj/node_modules/typescript/lib/lib.es5.d.ts",
        "src/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.",
        "src/b.ts(1,1): error TS2345: Argument of type '{ a: string; }' is not assignable.",
        "  Types of property 'a' are incompatible.",
        "    Type 'string' is not assignable to type 'number'.",
        "error TS2688: Cannot find type definition file for 'node'.",
        "  The file is in the program because:",
        "/proj/src/a.ts",
        "",
    ]
    diagnostics, files = parse_tsc_output(lines)

    assert diagnostics == [
        TscDiagnostic(
            "src/a.ts",
            3,
            7,
            "TS2322",
            "Type 'string' is not assignable to type 'number'.",
        ),
        TscDiagnostic(
            "src/b.ts",
            1,
            1,
            "TS2345",
            "Argument of type '{ a: string; }' is not assignable.\n"
            "Types of property 'a' are incompatible.\n"
            "Type 'string' is not assignable to type 'number'.",
        ),
        TscDiagnostic(
            None,
            0,
            0,
            "TS2688",
            "Cannot find type definition file for 'node'.\nThe file is in the program because:",
        ),
    ]
    assert files == ["/proj/node_modules/typescript/lib/lib.es5.d.ts", "/proj/src/a.ts"]


def test_path_with_parentheses_parses():
    diagnostics, _ = parse_tsc_output(
        [
            "app/(shop)/page.tsx(2,5): error TS2339: Property 'x' does not exist on type 'Y'."
        ]
    )
    assert diagnostics[0].file == "app/(shop)/page.tsx"
    assert (diagnostics[0].line, diagnostics[0].col) == (2, 5)


def test_one_run_per_tsconfig_with_a_shared_cache(tmp_path, monkeypatch):
    calls = []

    def fake_check(project_root, tsconfig):
        calls.append(tsconfig)
        return SimpleNamespace(
            stdout="src/a.ts(1,7): error TS6133: 'x' is declared but its value is never read.\n",
            stderr="",
            returncode=2,
        )

    monkeypatch.setattr(tsc_mod, "run_tsc_check", fake_check)
    cache: dict = {}
    first = tsc_mod.run_tsc(tmp_path, tmp_path / "tsconfig.json", cache=cache)
    second = tsc_mod.run_tsc(tmp_path, tmp_path / "tsconfig.json", cache=cache)
    other = tsc_mod.run_tsc(tmp_path, tmp_path / "pkg" / "tsconfig.json", cache=cache)

    assert first is second
    assert other is not first
    assert calls == [tmp_path / "tsconfig.json", tmp_path / "pkg" / "tsconfig.json"]
    assert [d.code for d in first.diagnostics] == ["TS6133"]


def test_failures_are_cached_too(tmp_path, monkeypatch):
    calls = []

    def missing(*_args):
        calls.append(1)
        raise OSError("TypeScript compiler not found")

    monkeypatch.setattr(tsc_mod, "run_tsc_check", missing)
    cache: dict = {}
    run = tsc_mod.run_tsc(tmp_path, tmp_path / "tsconfig.json", cache=cache)
    again = tsc_mod.run_tsc(tmp_path, tmp_path / "tsconfig.json", cache=cache)

    assert run.failure == "tsc_missing" and "not found" in run.error
    assert again is run and calls == [1]


def test_unused_reuses_the_cached_run(tmp_path, monkeypatch):
    (tmp_path / "tsconfig.json").write_text("{}\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("const x = 1;\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DESLOPPIFY_ROOT", str(tmp_path))
    calls = []

    def fake_check(project_root, tsconfig):
        calls.append(tsconfig)
        return SimpleNamespace(
            stdout="src/a.ts(1,7): error TS6133: 'x' is declared but its value is never read.\n",
            stderr="",
            returncode=2,
        )

    monkeypatch.setattr(tsc_mod, "run_tsc_check", fake_check)
    cache: dict = {}
    first, _, _ = unused_mod.detect_unused_result(tmp_path / "src", cache=cache)
    second, _, _ = unused_mod.detect_unused_result(tmp_path / "src", cache=cache)

    assert [e["name"] for e in first] == ["x"] == [e["name"] for e in second]
    assert calls == [Path(tmp_path / "tsconfig.json")]


def _project_files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "node_modules" not in p.parts
    }


def test_build_info_goes_to_the_state_dir(tmp_path, monkeypatch):
    project, state = tmp_path / "proj", tmp_path / "state"
    project.mkdir()
    tsconfig = project / "tsconfig.json"
    tsconfig.write_text('{"compilerOptions": {"incremental": true}}\n')
    recorded: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        recorded.append(cmd)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(tsc_mod, "resolve_tsc_command", lambda *_a: ["tsc"])
    monkeypatch.setattr(tsc_mod._proc_runtime, "run", fake_run)
    with runtime_scope(RuntimeContext(project_root=project, state_dir=state)):
        tsc_mod.run_tsc_check(project, tsconfig)
        other = project / "packages" / "tsconfig.json"
        assert tsc_mod.build_info_path(other) != tsc_mod.build_info_path(tsconfig)

    (cmd,) = recorded
    assert cmd[-3:-1] == ["--incremental", "--tsBuildInfoFile"]
    target = Path(cmd[-1])
    assert target.parent == state / "tsc" and target.parent.is_dir()
    assert target.name.startswith("tsconfig-") and target.suffix == ".tsbuildinfo"


@pytest.mark.skipif(
    not harness.node_tools_installed(), reason="pinned tsc not installed"
)
@pytest.mark.parametrize(
    "options",
    [
        '{"incremental": true, "noEmit": true}',
        '{"composite": true, "outDir": "out"}',
        "{}",
    ],
    ids=["incremental", "composite", "plain"],
)
def test_real_tsc_writes_nothing_into_the_project(tmp_path, monkeypatch, options):
    project, state = tmp_path / "proj", tmp_path / "state"
    project.mkdir()
    (project / "a.ts").write_text("export const x = 1;\nconst unused = 2;\n")
    tsconfig = project / "tsconfig.json"
    tsconfig.write_text(f'{{"compilerOptions": {options}, "include": ["*.ts"]}}\n')
    tsc_cmd = tsc_mod.resolve_tsc_command(harness.NODE_MODULES.parent)
    monkeypatch.setattr(tsc_mod, "resolve_tsc_command", lambda *_a: tsc_cmd)
    before = _project_files(project)

    with runtime_scope(RuntimeContext(project_root=project, state_dir=state)):
        first = tsc_mod.run_tsc(project, tsconfig)
        second = tsc_mod.run_tsc(project, tsconfig)
        build_info = tsc_mod.build_info_path(tsconfig)

    assert _project_files(project) == before
    assert build_info.is_file()
    for run in (first, second):
        assert run.usable
        assert [d.code for d in run.diagnostics] == ["TS6133"]
    assert second.files == first.files
