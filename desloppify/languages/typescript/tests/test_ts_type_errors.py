"""Tests for the tsc type_error detector and its phase."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import desloppify.languages.typescript.detectors.tsc as tsc_mod
import desloppify.languages.typescript.phases_basic as phases_basic_mod
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.languages.typescript.detectors.type_errors import detect_type_errors_result


def _write(root: Path, name: str, text: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project with installed deps; ``fake(stdout)`` sets what tsc prints."""
    _write(tmp_path, "tsconfig.json", "{}\n")
    _write(tmp_path, "package.json", '{"dependencies": {"react": "1"}}\n')
    (tmp_path / "node_modules").mkdir()
    calls: list[Path] = []

    def fake(stdout: str, *, files: tuple[str, ...] = ()) -> list[Path]:
        listed = "".join(f"{tmp_path / name}\n" for name in files)

        def run(project_root, tsconfig):
            calls.append(tsconfig)
            return SimpleNamespace(stdout=listed + stdout, stderr="", returncode=2 if stdout else 0)

        monkeypatch.setattr(tsc_mod, "run_tsc_check", run)
        return calls

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        yield tmp_path, fake


def test_reports_type_errors_and_leaves_unused_to_its_detector(project):
    root, fake = project
    _write(root, "src/a.ts", "x\n")
    fake(
        "src/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        "src/a.ts(3,20): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        "src/a.ts(5,1): error TS6133: 'y' is declared but its value is never read.\n"
        "src/a.ts(9,3): error TS7006: Parameter 'p' implicitly has an 'any' type.\n"
        "tsconfig.json(2,5): error TS5023: Unknown compiler option 'foo'.\n",
        files=("node_modules/typescript/lib/lib.d.ts", "src/a.ts"),
    )
    result = detect_type_errors_result(root / "src")

    assert [(e["code"], e["line"], e["cols"], e["count"], e["confidence"]) for e in result.entries] == [
        ("TS2322", 3, [7, 20], 2, "high"),
        ("TS7006", 9, [3], 1, "medium"),
    ]
    assert result.checked_files == [str(root / "src/a.ts")]
    assert result.coverage is None


def test_one_tsc_run_serves_unused_and_type_errors(project):
    import desloppify.languages.typescript.detectors.unused as unused_mod

    root, fake = project
    _write(root, "src/a.ts", "const y = 1;\n")
    calls = fake(
        "src/a.ts(1,7): error TS6133: 'y' is declared but its value is never read.\n"
        "src/a.ts(2,1): error TS2304: Cannot find name 'z'.\n",
        files=("src/a.ts",),
    )
    cache: dict = {}
    unused, _total, _coverage = unused_mod.detect_unused_result(root / "src", cache=cache)
    result = detect_type_errors_result(root / "src", cache=cache)

    assert [e["name"] for e in unused] == ["y"]
    assert [e["code"] for e in result.entries] == ["TS2304"]
    assert len(calls) == 1


def test_files_owned_by_another_tsconfig_are_not_reported(project):
    root, fake = project
    _write(root, "src/a.ts")
    _write(root, "packages/app/tsconfig.json", "{}\n")
    _write(root, "packages/app/src/b.tsx")
    fake(
        "src/a.ts(1,1): error TS2304: Cannot find name 'z'.\n"
        "packages/app/src/b.tsx(1,1): error TS2686: 'React' refers to a UMD global.\n",
        files=("src/a.ts", "packages/app/src/b.tsx"),
    )
    result = detect_type_errors_result(root)

    assert [e["file"] for e in result.entries] == [str(root / "src/a.ts")]
    assert result.checked_files == [str(root / "src/a.ts")]
    assert result.coverage is not None and result.coverage.reason == "partial"
    assert "packages/app/tsconfig.json" in result.coverage.summary
    assert "its own tsconfig" in result.coverage.remediation


def test_errors_outside_the_scan_path_are_dropped(project):
    root, fake = project
    _write(root, "src/a.ts")
    _write(root, "scripts/b.ts")
    fake(
        "scripts/b.ts(1,1): error TS2304: Cannot find name 'z'.\n",
        files=("src/a.ts", "scripts/b.ts"),
    )
    result = detect_type_errors_result(root / "src")

    assert result.entries == []
    assert result.checked_files == [str(root / "src/a.ts")]


def test_missing_packages_are_environment_noise(project):
    root, fake = project
    _write(root, "src/a.ts")
    _write(root, "src/b.ts")
    fake(
        "error TS2688: Cannot find type definition file for 'node'.\n"
        "src/a.ts(1,19): error TS2307: Cannot find module 'react' or its corresponding type declarations.\n"
        "src/a.ts(4,9): error TS2339: Property 'x' does not exist on type 'Y'.\n"
        "src/b.ts(1,19): error TS2307: Cannot find module './missing' or its corresponding type declarations.\n",
        files=("src/a.ts", "src/b.ts"),
    )
    result = detect_type_errors_result(root / "src")

    # The bare-specifier miss is noise, and a.ts's other error may be its cascade;
    # a relative import that doesn't resolve is a real error.
    assert [(e["file"], e["code"], e["confidence"]) for e in result.entries] == [
        (str(root / "src/b.ts"), "TS2307", "high"),
    ]
    assert result.checked_files == [str(root / "src/b.ts")]
    assert result.coverage is not None
    assert "1 files can't resolve a package" in result.coverage.summary
    assert "(1 besides the missing modules)" in result.coverage.summary


def test_uninstalled_dependencies_skip_the_detector(project):
    root, fake = project
    (root / "node_modules").rmdir()
    _write(root, "src/a.ts")
    fake(
        "src/a.ts(1,19): error TS2307: Cannot find module 'react' or its corresponding type declarations.\n",
        files=("src/a.ts",),
    )
    result = detect_type_errors_result(root / "src")

    assert result.entries == [] and result.checked_files is None
    assert result.coverage is not None and result.coverage.reason == "deps_not_installed"


def test_missing_tsc_skips_cleanly(project, monkeypatch):
    root, _fake = project
    _write(root, "src/a.ts")

    def missing(*_args):
        raise OSError("TypeScript compiler not found")

    monkeypatch.setattr(tsc_mod, "run_tsc_check", missing)
    result = detect_type_errors_result(root / "src")

    assert result.entries == [] and result.checked_files is None
    assert result.coverage is not None and result.coverage.reason == "tsc_missing"


def test_phase_issue_ids_are_code_and_line(project):
    root, fake = project
    _write(root, "src/a.ts")
    fake(
        "src/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        "  Types of property 'a' are incompatible.\n",
        files=("src/a.ts",),
    )
    lang = SimpleNamespace(zone_map=None, runtime_cache={}, detector_coverage={}, coverage_warnings=[])
    issues, potentials = phases_basic_mod.phase_type_errors(root / "src", lang)

    assert [issue["id"] for issue in issues] == ["type_error::src/a.ts::TS2322::3"]
    assert issues[0]["summary"] == "TS2322: Type 'string' is not assignable to type 'number'."
    assert issues[0]["detail"]["message"].endswith("Types of property 'a' are incompatible.")
    assert potentials == {"type_error": 1}


def test_phase_reports_no_potential_when_skipped(project, monkeypatch):
    root, _fake = project
    _write(root, "src/a.ts")
    monkeypatch.setattr(tsc_mod, "run_tsc_check", lambda *_a: (_ for _ in ()).throw(OSError("no tsc")))
    lang = SimpleNamespace(zone_map=None, runtime_cache={}, detector_coverage={}, coverage_warnings=[])
    issues, potentials = phases_basic_mod.phase_type_errors(root / "src", lang)

    # No potential: the dimension is carried forward and old issues aren't auto-resolved.
    assert issues == [] and potentials == {}
    assert lang.detector_coverage["type_error"]["reason"] == "tsc_missing"
