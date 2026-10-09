"""Tests for the shared tsc run (detectors/tsc.py)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import desloppify.languages.typescript.detectors.tsc as tsc_mod
import desloppify.languages.typescript.detectors.unused as unused_mod
from desloppify.languages.typescript.detectors.tsc import (
    TscDiagnostic,
    parse_tsc_output,
)


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
        TscDiagnostic("src/a.ts", 3, 7, "TS2322", "Type 'string' is not assignable to type 'number'."),
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
        ["app/(shop)/page.tsx(2,5): error TS2339: Property 'x' does not exist on type 'Y'."]
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
