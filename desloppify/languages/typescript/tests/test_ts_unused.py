"""Tests for desloppify.languages.typescript.detectors.unused — unused declaration detection.

Note: detect_unused depends on tsc (TypeScript compiler) and a real project setup,
so we test what is feasible: the helper function _categorize_unused and module imports.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import desloppify.languages.typescript.detectors.tsc as tsc_mod
import desloppify.languages.typescript.detectors.unused as ts_unused_mod
from desloppify.languages.typescript.detectors.unused import (
    TS6133_RE,
    TS6192_RE,
    _categorize_unused,
    detect_unused,
)


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# ── Module import smoke test ─────────────────────────────────


# ── TS error regex patterns ──────────────────────────────────


class TestErrorRegex:
    def test_ts6133_matches(self):
        """TS6133_RE matches the tsc unused variable error format."""

        line = "src/utils.ts(15,7): error TS6133: 'unusedVar' is declared but its value is never read."
        m = TS6133_RE.match(line)
        assert m is not None
        assert m.group(1) == "src/utils.ts"
        assert m.group(2) == "15"
        assert m.group(3) == "7"
        assert m.group(4) == "unusedVar"

    def test_ts6133_no_match_on_other_errors(self):
        """TS6133_RE does not match other tsc errors."""

        line = "src/utils.ts(15,7): error TS2304: Cannot find name 'foo'."
        m = TS6133_RE.match(line)
        assert m is None

    def test_ts6192_matches(self):
        """TS6192_RE matches the tsc all-imports-unused error format."""

        line = "src/app.ts(1,1): error TS6192: All imports in import declaration are unused."
        m = TS6192_RE.match(line)
        assert m is not None
        assert m.group(1) == "src/app.ts"
        assert m.group(2) == "1"

    def test_ts6192_no_match_on_other(self):
        """TS6192_RE does not match non-6192 lines."""

        line = "src/app.ts(1,1): error TS6133: 'x' is declared but its value is never read."
        m = TS6192_RE.match(line)
        assert m is None


# ── _categorize_unused ───────────────────────────────────────


class TestCategorizeUnused:
    def test_import_line(self, tmp_path):
        """Lines starting with 'import' are categorized as imports."""

        _write(tmp_path, "app.ts", "import { foo } from './utils';\nconst x = foo();\n")
        result = _categorize_unused(str(tmp_path / "app.ts"), 1)
        assert result == "imports"

    def test_const_line(self, tmp_path):
        """Lines starting with 'const' are categorized as vars."""

        _write(
            tmp_path, "app.ts", "import { foo } from './utils';\nconst unused = 42;\n"
        )
        result = _categorize_unused(str(tmp_path / "app.ts"), 2)
        assert result == "vars"

    def test_let_line(self, tmp_path):
        """Lines starting with 'let' are categorized as vars."""

        _write(tmp_path, "app.ts", "let unused = 42;\n")
        result = _categorize_unused(str(tmp_path / "app.ts"), 1)
        assert result == "vars"

    def test_function_line(self, tmp_path):
        """Lines starting with 'function' are categorized as vars."""

        _write(tmp_path, "app.ts", "function unused() {}\n")
        result = _categorize_unused(str(tmp_path / "app.ts"), 1)
        assert result == "vars"

    def test_multiline_import(self, tmp_path):
        """Names within multi-line import blocks are categorized as imports."""

        _write(tmp_path, "app.ts", ("import {\n  foo,\n  bar,\n} from './utils';\n"))
        # Line 3 is 'bar,' which is inside a multi-line import
        result = _categorize_unused(str(tmp_path / "app.ts"), 3)
        assert result == "imports"

    def test_nonexistent_file_defaults_vars(self, tmp_path):
        """Unknown context must not be routed to the import fixer."""

        result = _categorize_unused(str(tmp_path / "nonexistent.ts"), 1)
        assert result == "vars"

    def test_parameter_is_not_an_import(self, tmp_path):
        _write(tmp_path, "fn.ts", "function handler(req, res) {\n  return req;\n}\n")
        assert _categorize_unused(str(tmp_path / "fn.ts"), 1) == "vars"

    def test_multiline_import_member_is_import(self, tmp_path):
        _write(tmp_path, "m.ts", "import {\n  used,\n  unused,\n} from './x';\n")
        assert _categorize_unused(str(tmp_path / "m.ts"), 3) == "imports"

    def test_export_const_is_vars(self, tmp_path):
        """Lines starting with 'export const' are categorized as vars."""

        _write(tmp_path, "app.ts", "export const unused = 42;\n")
        result = _categorize_unused(str(tmp_path / "app.ts"), 1)
        assert result == "vars"


class TestDenoFallback:
    def test_run_tsc_unused_check_prefers_local_compiler(self, tmp_path, monkeypatch):
        class _Result:
            stdout = ""
            stderr = ""
            returncode = 0

        recorded: dict[str, object] = {}

        def _fake_run(*args, **kwargs):
            recorded["args"] = args[0]
            recorded["cwd"] = kwargs["cwd"]
            recorded["timeout"] = kwargs["timeout"]
            recorded["stdin"] = kwargs.get("stdin")
            return _Result()

        local_tsc = _write(tmp_path, "node_modules/.bin/tsc", "#!/bin/sh\n")
        _write(tmp_path, "packages/app/tsconfig.json", "{}\n")
        monkeypatch.setattr(tsc_mod.os, "name", "posix")
        monkeypatch.setattr(tsc_mod.shutil, "which", lambda _name: "/usr/bin/tsc")
        monkeypatch.setattr(tsc_mod._proc_runtime, "run", _fake_run)
        tsconfig = tmp_path / "packages/app/tsconfig.json"
        result = tsc_mod.run_tsc_check(tmp_path, tsconfig)

        assert result.stdout == ""
        # Hoisted monorepo install is found by walking up; no npx, no temp config.
        assert recorded["args"] == [
            str(local_tsc),
            "--project",
            str(tsconfig),
            "--noEmit",
            "--noUnusedLocals",
            "--noUnusedParameters",
            "--listFiles",
            "--pretty",
            "false",
        ]
        assert recorded["cwd"] == tmp_path
        assert recorded["timeout"] == 120
        assert recorded["stdin"] == tsc_mod.subprocess.DEVNULL

    def test_run_tsc_unused_check_never_uses_npx(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            tsc_mod.shutil,
            "which",
            lambda name: "/usr/bin/npx" if name == "npx" else None,
        )

        with pytest.raises(OSError, match="TypeScript compiler not found"):
            tsc_mod.run_tsc_check(tmp_path, tmp_path / "tsconfig.json")

    def test_detect_unused_uses_deno_fallback_for_url_imports(self, tmp_path, monkeypatch):
        """Deno-style URL imports should bypass tsc and use source-based fallback."""
        _write(
            tmp_path,
            "supabase/functions/edge.ts",
            (
                'import { serve } from "https://deno.land/std@0.177.0/http/server.ts";\n'
                "import { local } from './local.ts';\n"
                "const unusedVar = 1;\n"
                "local();\n"
            ),
        )
        _write(tmp_path, "supabase/functions/local.ts", "export function local() {}\n")

        def _should_not_run(*args, **kwargs):
            raise AssertionError("tsc subprocess should not run in Deno fallback mode")

        monkeypatch.setattr(tsc_mod._proc_runtime, "run", _should_not_run)
        entries, total = detect_unused(tmp_path / "supabase/functions")
        names = {entry["name"] for entry in entries}
        assert "serve" in names
        assert "unusedVar" in names
        assert total == 2

    def test_detect_unused_fallback_category_filter(self, tmp_path, monkeypatch):
        """Deno fallback should honor --category filtering."""
        _write(
            tmp_path,
            "supabase/functions/main.ts",
            (
                "import { x } from './dep.ts';\n"
                "const unusedLocal = 1;\n"
                "console.log('hello')\n"
            ),
        )
        _write(tmp_path, "supabase/functions/dep.ts", "export const x = 1;\n")
        monkeypatch.setattr(
            tsc_mod._proc_runtime,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("tsc subprocess should not run in Deno fallback mode")
            ),
        )

        imports_only, _ = detect_unused(tmp_path / "supabase/functions", "imports")
        vars_only, _ = detect_unused(tmp_path / "supabase/functions", "vars")
        assert all(entry["category"] == "imports" for entry in imports_only)
        assert all(entry["category"] == "vars" for entry in vars_only)
        assert any(entry["name"] == "x" for entry in imports_only)
        assert any(entry["name"] == "unusedLocal" for entry in vars_only)

    def test_detect_unused_non_deno_keeps_tsc_path(self, tmp_path, monkeypatch):
        """Regular TypeScript projects should still parse TS6133/TS6192 from tsc."""
        _write(tmp_path, "tsconfig.json", "{}\n")
        _write(tmp_path, "src/app.ts", "const x = 1;\n")

        class _Result:
            stdout = (
                "src/app.ts(1,7): error TS6133: 'x' is declared but its value is never read.\n"
            )
            stderr = ""
            returncode = 2

        calls = {"count": 0}

        def _fake_run(*args, **kwargs):
            calls["count"] += 1
            return _Result()

        _write(tmp_path, "node_modules/.bin/tsc", "#!/bin/sh\n")
        monkeypatch.setattr(tsc_mod.shutil, "which", lambda _name: None)
        monkeypatch.setattr(tsc_mod._proc_runtime, "run", _fake_run)
        entries, total = detect_unused(tmp_path / "src")
        assert calls["count"] == 1
        assert total == 1
        assert entries and entries[0]["name"] == "x"

    def test_detect_unused_root_deno_lock_does_not_force_fallback(
        self, tmp_path, monkeypatch
    ):
        """A repo-level deno.lock alone should not disable tsc-based unused detection."""
        _write(tmp_path, "deno.lock", "{}\n")
        _write(tmp_path, "tsconfig.json", "{}\n")
        _write(tmp_path, "src/app.ts", "const x = 1;\n")

        class _Result:
            stdout = (
                "src/app.ts(1,7): error TS6133: 'x' is declared but its value is never read.\n"
            )
            stderr = ""
            returncode = 2

        calls = {"count": 0}

        def _fake_run(*args, **kwargs):
            calls["count"] += 1
            return _Result()

        _write(tmp_path, "node_modules/.bin/tsc", "#!/bin/sh\n")
        monkeypatch.setattr(tsc_mod.shutil, "which", lambda _name: None)
        monkeypatch.setattr(tsc_mod._proc_runtime, "run", _fake_run)
        entries, total = detect_unused(tmp_path / "src")
        assert calls["count"] == 1
        assert total == 1
        assert entries and entries[0]["name"] == "x"

    def test_detect_unused_uses_nearest_monorepo_tsconfig(self, tmp_path, monkeypatch):
        """A nested project must not inherit an unrelated root app config."""
        _write(tmp_path, "tsconfig.app.json", "{}\n")
        _write(tmp_path, "libs/contracts/tsconfig.json", "{}\n")
        _write(tmp_path, "libs/contracts/src/index.ts", "const x = 1;\n")

        recorded: dict[str, object] = {}

        class _Result:
            stdout = ""
            stderr = ""
            returncode = 0

        def _fake_run(project_root, tsconfig_path):
            recorded["tsconfig_path"] = tsconfig_path
            return _Result()

        monkeypatch.setattr(tsc_mod, "run_tsc_check", _fake_run)

        entries, total, coverage = ts_unused_mod.detect_unused_result(
            tmp_path / "libs/contracts"
        )

        assert entries == []
        assert total == 1
        assert coverage is None
        assert recorded["tsconfig_path"] == tmp_path / "libs/contracts/tsconfig.json"
        # Nothing is written into the user's project.
        assert not (tmp_path / "libs/contracts/tsconfig.desloppify.json").exists()


class TestTscFailureModes:
    """tsc failures must surface as reduced coverage, never as a clean result."""

    def _project(self, tmp_path):
        _write(tmp_path, "tsconfig.json", "{}\n")
        _write(tmp_path, "src/app.ts", "const unusedLocal = 1;\n")

    def _fake_result(self, monkeypatch, *, stdout="", stderr="", returncode=0):
        result = SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
        monkeypatch.setattr(tsc_mod, "run_tsc_check", lambda *_a: result)

    def test_missing_compiler_falls_back_with_reduced_coverage(self, tmp_path, monkeypatch):
        self._project(tmp_path)

        def _missing(*_a):
            raise OSError("TypeScript compiler not found")

        monkeypatch.setattr(tsc_mod, "run_tsc_check", _missing)
        entries, _total, coverage = ts_unused_mod.detect_unused_result(tmp_path / "src")

        assert coverage is not None and coverage.status == "reduced"
        assert coverage.reason == "tsc_missing"
        assert any(entry["name"] == "unusedLocal" for entry in entries)

    def test_bogus_npm_tsc_package_is_not_treated_as_clean(self, tmp_path, monkeypatch):
        self._project(tmp_path)
        self._fake_result(
            monkeypatch,
            stdout="This is not the tsc command you are looking for\n",
            returncode=1,
        )
        _entries, _total, coverage = ts_unused_mod.detect_unused_result(tmp_path / "src")
        assert coverage is not None and coverage.reason == "wrong_tsc_package"

    def test_nonzero_exit_without_diagnostics_is_a_failure(self, tmp_path, monkeypatch):
        self._project(tmp_path)
        self._fake_result(monkeypatch, stderr="node: command crashed\n", returncode=1)
        _entries, _total, coverage = ts_unused_mod.detect_unused_result(tmp_path / "src")
        assert coverage is not None and coverage.reason == "tsc_failed"

    def test_config_errors_keep_results_but_reduce_coverage(self, tmp_path, monkeypatch):
        self._project(tmp_path)
        self._fake_result(
            monkeypatch,
            stdout=(
                "tsconfig.json(3,5): error TS5083: Cannot read file 'tsconfig.base.json'.\n"
                "src/app.ts(1,7): error TS6133: 'unusedLocal' is declared but its value is never read.\n"
            ),
            returncode=2,
        )
        entries, _total, coverage = ts_unused_mod.detect_unused_result(tmp_path / "src")
        assert [entry["name"] for entry in entries] == ["unusedLocal"]
        assert coverage is not None and coverage.reason == "tsconfig_error"

    def test_all_unused_diagnostic_codes_are_parsed(self, tmp_path, monkeypatch):
        _write(tmp_path, "tsconfig.json", "{}\n")
        _write(
            tmp_path,
            "src/app.ts",
            "type Unused = string;\nclass A { private p = 1; }\n"
            "const { a, b } = obj;\nlet c, d;\nfunction f<T>() {}\n",
        )
        self._fake_result(
            monkeypatch,
            stdout=(
                "src/app.ts(1,6): error TS6196: 'Unused' is declared but never used.\n"
                "src/app.ts(2,19): error TS6138: Property 'p' is declared but its value is never read.\n"
                "src/app.ts(3,7): error TS6198: All destructured elements are unused.\n"
                "src/app.ts(4,1): error TS6199: All variables are unused.\n"
                "src/app.ts(5,12): error TS6205: All type parameters are unused.\n"
            ),
            returncode=2,
        )
        entries, _total, coverage = ts_unused_mod.detect_unused_result(tmp_path / "src")
        assert coverage is None
        assert [entry["name"] for entry in entries] == [
            "Unused",
            "p",
            "(all destructured elements)",
            "(all variables)",
            "(all type parameters)",
        ]

    def test_phase_records_reduced_coverage_warning(self, tmp_path, monkeypatch):
        import desloppify.languages.typescript.phases_basic as phases_basic_mod
        from desloppify.languages._framework.base.types import DetectorCoverageStatus

        coverage = DetectorCoverageStatus(
            detector="unused", status="reduced", confidence=0.5, reason="tsc_missing"
        )
        monkeypatch.setattr(
            phases_basic_mod.unused_detector_mod,
            "detect_unused_result",
            lambda _path, **_kwargs: ([], 0, coverage),
        )
        lang = SimpleNamespace(
            zone_map=None, detector_coverage={}, coverage_warnings=[], runtime_cache={}
        )
        phases_basic_mod.phase_unused(tmp_path, lang)

        assert lang.detector_coverage["unused"]["status"] == "reduced"
        assert [w["reason"] for w in lang.coverage_warnings] == ["tsc_missing"]
