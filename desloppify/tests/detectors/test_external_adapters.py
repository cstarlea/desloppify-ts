"""Tests for external tool adapters: Knip and jscpd.

Each adapter must:
  1. Return None (not crash) when the tool is not installed.
  2. Correctly parse the tool's JSON output format.
  3. Produce entries/issues in the structure the phase runners expect.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Knip adapter ────────────────────────────────────────────────────────────
from desloppify.languages.typescript.detectors.knip_adapter import (  # noqa: E402
    detect_with_knip,
    detect_with_knip_result,
)

_KNIP_RUN = "desloppify.languages.typescript.detectors.knip_adapter.subprocess.run"


def _knip_project(root: Path, *, package: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps(package or {"name": "app"}))
    (root / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
    (root / "node_modules" / ".bin" / "knip").touch()
    return root


def _knip_output(stdout: str) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.stderr = ""
    result.returncode = 1
    return result


class TestKnipAdapter:
    def test_skips_with_reason_when_knip_not_installed(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        with patch(_KNIP_RUN) as run:
            entries, reason = detect_with_knip_result(tmp_path / "src")
        run.assert_not_called()
        assert entries is None
        assert reason == "knip_not_installed"

    def test_skips_with_reason_without_package_json(self, tmp_path):
        entries, reason = detect_with_knip_result(tmp_path)
        assert entries is None
        assert reason == "no_package_json"

    def test_runs_local_binary_from_package_root_for_subdirectory_scan(self, tmp_path):
        """The default ``--path src`` must still find and run the package's knip."""
        root = _knip_project(tmp_path)
        (root / "src").mkdir()
        with patch(_KNIP_RUN, return_value=_knip_output('{"issues": []}')) as run:
            assert detect_with_knip(root / "src") == []
        argv = run.call_args.args[0]
        assert argv == [str(root / "node_modules" / ".bin" / "knip"), "--reporter", "json"]
        assert run.call_args.kwargs["cwd"] == str(root.resolve())

    def test_monorepo_package_runs_from_workspace_root(self, tmp_path):
        _knip_project(tmp_path, package={"name": "repo", "workspaces": ["packages/*"]})
        pkg = tmp_path / "packages" / "web"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text('{"name": "web"}')
        with patch(_KNIP_RUN, return_value=_knip_output('{"issues": []}')) as run:
            detect_with_knip(pkg)
        argv = run.call_args.args[0]
        assert argv[-2:] == ["--workspace", "packages/web"]
        assert run.call_args.kwargs["cwd"] == str(tmp_path.resolve())

    def test_timeout_and_bad_output_have_reasons(self, tmp_path):
        root = _knip_project(tmp_path)
        with patch(_KNIP_RUN, side_effect=subprocess.TimeoutExpired("knip", 120)):
            assert detect_with_knip_result(root) == (None, "knip_timeout")
        with patch(_KNIP_RUN, return_value=_knip_output("")):
            assert detect_with_knip_result(root) == (None, "knip_failed")
        with patch(_KNIP_RUN, return_value=_knip_output("not-json")):
            assert detect_with_knip_result(root) == (None, "knip_bad_output")

    def test_parses_knip_v6_output(self, tmp_path, set_project_root):
        """Real knip 6 JSON: ``line`` is the line, ``pos`` is a character offset."""
        root = _knip_project(tmp_path)
        payload = {
            "issues": [
                {
                    "file": "src/lib.ts",
                    "exports": [{"name": "deadHelper", "line": 2, "col": 14, "pos": 42}],
                    "types": [{"name": "DeadType", "line": 3, "col": 13, "pos": 70}],
                    "enumMembers": [{"namespace": "E", "name": "B", "line": 4, "pos": 108}],
                },
                {"file": "package.json", "dependencies": [{"name": "left-pad", "line": 10}]},
            ]
        }
        with patch(_KNIP_RUN, return_value=_knip_output(json.dumps(payload))):
            result = detect_with_knip(root / "src")
        assert result == [
            {"file": "src/lib.ts", "name": "deadHelper", "line": 2, "kind": "export"},
            {"file": "src/lib.ts", "name": "DeadType", "line": 3, "kind": "type"},
            {"file": "src/lib.ts", "name": "E.B", "line": 4, "kind": "enum_member"},
        ]

    def test_skips_files_outside_scan_path(self, tmp_path, set_project_root):
        root = _knip_project(tmp_path)
        (root / "src").mkdir()
        payload = {
            "issues": [
                {"file": "scripts/tool.ts", "exports": [{"name": "gone", "line": 1}]},
                {"file": "/other/path/file.ts", "exports": [{"name": "far", "line": 1}]},
            ]
        }
        with patch(_KNIP_RUN, return_value=_knip_output(json.dumps(payload))):
            assert detect_with_knip(root / "src") == []


# ── jscpd adapter ────────────────────────────────────────────────────────────


from desloppify.engine.detectors.jscpd_adapter import (  # noqa: E402
    _parse_jscpd_report,
    _run_jscpd_command,
    detect_with_jscpd,
)


class TestJscpdAdapter:
    def test_returns_none_when_jscpd_not_installed(self, tmp_path):
        with patch(
            "desloppify.engine.detectors.jscpd_adapter._resolve_jscpd_command",
            return_value=None,
        ):
            assert detect_with_jscpd(tmp_path) is None

    def test_returns_none_on_timeout(self, tmp_path):
        with patch(
            "desloppify.engine.detectors.jscpd_adapter._resolve_jscpd_command",
            return_value=["/usr/bin/npx", "--yes", "jscpd"],
        ), patch(
            "desloppify.engine.detectors.jscpd_adapter._run_jscpd_command",
            side_effect=subprocess.TimeoutExpired("npx", 120),
        ):
            assert detect_with_jscpd(tmp_path) is None

    @pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
    def test_timeout_kills_jscpd_process_group(self):
        class FakeProc:
            pid = 4321
            returncode = None
            calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                if timeout is not None:
                    raise subprocess.TimeoutExpired(["jscpd"], timeout)
                self.returncode = -9
                return "", ""

        fake_proc = FakeProc()
        with patch(
            "desloppify.engine.detectors.jscpd_adapter.subprocess.Popen",
            return_value=fake_proc,
        ) as popen, patch(
            "desloppify.engine.detectors.jscpd_adapter.os.getpgid",
            return_value=9876,
        ), patch(
            "desloppify.engine.detectors.jscpd_adapter.os.killpg",
        ) as killpg:
            with pytest.raises(subprocess.TimeoutExpired):
                _run_jscpd_command(["jscpd"], timeout=1)

        popen.assert_called_once()
        assert popen.call_args.kwargs["start_new_session"] is True
        killpg.assert_called_once()
        assert killpg.call_args.args[0] == 9876

    def test_returns_empty_on_no_duplicates(self, tmp_path):
        result = _parse_jscpd_report({"duplicates": []}, tmp_path)
        assert result == []

    def test_returns_none_on_invalid_json_file(self, tmp_path):
        bad_report = tmp_path / "jscpd-report.json"
        bad_report.write_text("not-json")
        with patch(
            "desloppify.engine.detectors.jscpd_adapter._resolve_jscpd_command",
            return_value=["/usr/bin/npx", "--yes", "jscpd"],
        ), patch(
            "desloppify.engine.detectors.jscpd_adapter._run_jscpd_command",
        ), patch("tempfile.TemporaryDirectory") as mock_td:
            mock_td.return_value.__enter__.return_value = str(tmp_path)
            mock_td.return_value.__exit__.return_value = None
            result = detect_with_jscpd(tmp_path)
        assert result is None

    def test_clusters_pairs_with_same_fragment_hash(self, tmp_path):
        f1 = str(tmp_path / "a.py")
        f2 = str(tmp_path / "b.py")
        f3 = str(tmp_path / "c.py")
        fragment = "def foo():\n    pass\n    return None\n    # end"
        report = {
            "duplicates": [
                {
                    "fragment": fragment,
                    "lines": 4,
                    "firstFile": {"name": f1, "start": 1},
                    "secondFile": {"name": f2, "start": 5},
                },
                {
                    "fragment": fragment,
                    "lines": 4,
                    "firstFile": {"name": f2, "start": 5},
                    "secondFile": {"name": f3, "start": 10},
                },
            ]
        }
        result = _parse_jscpd_report(report, tmp_path)
        assert len(result) == 1  # Clustered into one entry
        assert result[0]["distinct_files"] == 3

    def test_distinct_files_counted_correctly(self, tmp_path):
        f1 = str(tmp_path / "a.py")
        f2 = str(tmp_path / "b.py")
        fragment = "x = 1\ny = 2\nz = 3\nw = 4"
        report = {
            "duplicates": [
                {
                    "fragment": fragment,
                    "lines": 4,
                    "firstFile": {"name": f1, "start": 1},
                    "secondFile": {"name": f2, "start": 10},
                }
            ]
        }
        result = _parse_jscpd_report(report, tmp_path)
        assert len(result) == 1
        assert result[0]["distinct_files"] == 2

    def test_skips_files_outside_scan_path(self, tmp_path):
        f_in = str(tmp_path / "a.py")
        f_out = "/other/path/b.py"
        fragment = "x = 1\ny = 2\nz = 3\nw = 4"
        report = {
            "duplicates": [
                {
                    "fragment": fragment,
                    "lines": 4,
                    "firstFile": {"name": f_in, "start": 1},
                    "secondFile": {"name": f_out, "start": 5},
                }
            ]
        }
        result = _parse_jscpd_report(report, tmp_path)
        assert result == []

    def test_sample_extracted_from_fragment(self, tmp_path):
        f1 = str(tmp_path / "a.py")
        f2 = str(tmp_path / "b.py")
        fragment = "line1\nline2\nline3\nline4\nline5\nline6"
        report = {
            "duplicates": [
                {
                    "fragment": fragment,
                    "lines": 6,
                    "firstFile": {"name": f1, "start": 1},
                    "secondFile": {"name": f2, "start": 10},
                }
            ]
        }
        result = _parse_jscpd_report(report, tmp_path)
        assert result[0]["sample"] == ["line1", "line2", "line3", "line4"]

    def test_skips_same_file_pairs(self, tmp_path):
        f1 = str(tmp_path / "a.py")
        fragment = "x = 1\ny = 2\nz = 3\nw = 4"
        report = {
            "duplicates": [
                {
                    "fragment": fragment,
                    "lines": 4,
                    "firstFile": {"name": f1, "start": 3},
                    "secondFile": {"name": f1, "start": 30},
                }
            ]
        }
        result = _parse_jscpd_report(report, tmp_path)
        assert result == []

    def test_skips_build_lib_source_mirror_pairs(self, tmp_path):
        f_build = str(tmp_path / "build" / "lib" / "pkg" / "module.py")
        f_src = str(tmp_path / "pkg" / "module.py")
        fragment = "x = 1\ny = 2\nz = 3\nw = 4"
        report = {
            "duplicates": [
                {
                    "fragment": fragment,
                    "lines": 4,
                    "firstFile": {"name": f_build, "start": 8},
                    "secondFile": {"name": f_src, "start": 9},
                }
            ]
        }
        result = _parse_jscpd_report(report, tmp_path)
        assert result == []

    def test_skips_artifact_paths(self, tmp_path):
        f_artifact = str(tmp_path / ".desloppify" / "cache.py")
        f_src = str(tmp_path / "pkg" / "module.py")
        fragment = "x = 1\ny = 2\nz = 3\nw = 4"
        report = {
            "duplicates": [
                {
                    "fragment": fragment,
                    "lines": 4,
                    "firstFile": {"name": f_artifact, "start": 2},
                    "secondFile": {"name": f_src, "start": 11},
                }
            ]
        }
        result = _parse_jscpd_report(report, tmp_path)
        assert result == []

    def test_detect_command_includes_artifact_ignores(self, tmp_path):
        report_file = tmp_path / "jscpd-report.json"
        report_file.write_text(json.dumps({"duplicates": []}))

        fake_dirs = [
            str(tmp_path / "build"),
            str(tmp_path / "node_modules"),
        ]

        def _fake_run(cmd, **kwargs):
            assert "--ignore" in cmd
            ignore_value = cmd[cmd.index("--ignore") + 1]
            assert "**/.desloppify/**" in ignore_value
            assert "**/.claude/**" in ignore_value
            assert "**/build/**" in ignore_value
            assert "**/node_modules/**" in ignore_value
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "desloppify.engine.detectors.jscpd_adapter._resolve_jscpd_command",
            return_value=["/usr/bin/npx", "--yes", "jscpd"],
        ), patch(
            "desloppify.engine.detectors.jscpd_adapter._run_jscpd_command",
            side_effect=_fake_run,
        ), patch(
            "desloppify.engine.detectors.jscpd_adapter.collect_exclude_dirs",
            return_value=fake_dirs,
        ), patch(
            "desloppify.engine.detectors.jscpd_adapter.get_exclusions",
            return_value=(),
        ), patch(
            "tempfile.TemporaryDirectory"
        ) as mock_td:
            mock_td.return_value.__enter__.return_value = str(tmp_path)
            mock_td.return_value.__exit__.return_value = None
            result = detect_with_jscpd(tmp_path)
        assert result == []


# ── collect_exclude_dirs ─────────────────────────────────────────────────────


from desloppify.base.discovery.source import collect_exclude_dirs  # noqa: E402


class TestCollectExcludeDirs:
    def test_returns_absolute_paths(self, tmp_path):
        with patch(
            "desloppify.base.discovery.source.get_exclusions", return_value=()
        ):
            result = collect_exclude_dirs(tmp_path)
        assert all(p.startswith(str(tmp_path)) for p in result)

    def test_includes_default_non_glob_entries(self, tmp_path):
        with patch(
            "desloppify.base.discovery.source.get_exclusions", return_value=()
        ):
            result = collect_exclude_dirs(tmp_path)
        basenames = {Path(p).name for p in result}
        assert "node_modules" in basenames
        assert "__pycache__" in basenames
        assert ".git" in basenames
        assert ".venv" in basenames
        assert "venv" in basenames

    def test_excludes_glob_patterns(self, tmp_path):
        with patch(
            "desloppify.base.discovery.source.get_exclusions", return_value=()
        ):
            result = collect_exclude_dirs(tmp_path)
        # *.egg-info and .venv* are glob patterns and should be excluded
        assert not any("*" in p for p in result)

    def test_includes_runtime_exclusions(self, tmp_path):
        with patch(
            "desloppify.base.discovery.source.get_exclusions",
            return_value=("vendor", "third_party"),
        ):
            result = collect_exclude_dirs(tmp_path)
        basenames = {Path(p).name for p in result}
        assert "vendor" in basenames
        assert "third_party" in basenames

    def test_skips_runtime_glob_exclusions(self, tmp_path):
        with patch(
            "desloppify.base.discovery.source.get_exclusions",
            return_value=("vendor/**",),
        ):
            result = collect_exclude_dirs(tmp_path)
        # glob pattern should not appear
        assert not any("vendor" in p for p in result)

    def test_deduplicates(self, tmp_path):
        """Runtime exclusion that overlaps with DEFAULT_EXCLUSIONS doesn't produce dupes."""
        with patch(
            "desloppify.base.discovery.source.get_exclusions",
            return_value=("node_modules",),
        ):
            result = collect_exclude_dirs(tmp_path)
        node_entries = [p for p in result if Path(p).name == "node_modules"]
        assert len(node_entries) == 1
