"""Tests for external tool adapters: Knip and jscpd.

Each adapter must:
  1. Return None (not crash) when the tool is not installed.
  2. Correctly parse the tool's JSON output format.
  3. Produce entries/issues in the structure the phase runners expect.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Knip adapter ────────────────────────────────────────────────────────────
from desloppify_ts.languages.typescript.detectors.knip_adapter import (  # noqa: E402
    detect_with_knip,
    detect_with_knip_result,
)

_KNIP_RUN = "desloppify_ts.languages.typescript.detectors.knip_adapter.subprocess.run"


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
        assert argv == [
            str(root / "node_modules" / ".bin" / "knip"),
            "--reporter",
            "json",
        ]
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
                    "exports": [
                        {"name": "deadHelper", "line": 2, "col": 14, "pos": 42}
                    ],
                    "types": [{"name": "DeadType", "line": 3, "col": 13, "pos": 70}],
                    "enumMembers": [
                        {"namespace": "E", "name": "B", "line": 4, "pos": 108}
                    ],
                },
                {
                    "file": "package.json",
                    "dependencies": [{"name": "left-pad", "line": 10}],
                },
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
                {
                    "file": "/other/path/file.ts",
                    "exports": [{"name": "far", "line": 1}],
                },
            ]
        }
        with patch(_KNIP_RUN, return_value=_knip_output(json.dumps(payload))):
            assert detect_with_knip(root / "src") == []


# ── jscpd adapter ────────────────────────────────────────────────────────────


from desloppify_ts.engine.detectors.jscpd_adapter import (  # noqa: E402
    JSCPD_VERSION,
    _jscpd_ignore_arg,
    _logic_token_count,
    _parse_jscpd_report,
    _resolve_jscpd_command,
    _run_jscpd_command,
    _statement_count,
    detect_with_jscpd,
    detect_with_jscpd_result,
)
from desloppify_ts.languages._framework.base.shared_phases_helpers import (  # noqa: E402
    _filter_boilerplate_entries_by_zone,
)


def _fake_bin(directory: Path, name: str) -> Path:
    """An executable ``name`` (``name.cmd`` on Windows, as npm installs it)."""
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / (f"{name}.cmd" if os.name == "nt" else name)
    binary.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


def _same_command(actual, expected: list[str]) -> bool:
    """Compare argv lists; ``shutil.which`` may change a Windows extension's case."""
    return isinstance(actual, list) and [os.path.normcase(a) for a in actual] == [
        os.path.normcase(e) for e in expected
    ]


class TestResolveJscpdCommand:
    @pytest.fixture(autouse=True)
    def _isolated(self, monkeypatch):
        monkeypatch.delenv("DESLOPPIFY_TS_ALLOW_TOOL_DOWNLOAD", raising=False)
        if os.name == "nt":
            monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        from desloppify_ts.base.runtime_state import current_runtime_context

        monkeypatch.setattr(current_runtime_context(), "allow_tool_download", False)

    def test_prefers_project_install(self, tmp_path, monkeypatch):
        local = _fake_bin(tmp_path / "node_modules" / ".bin", "jscpd")
        (tmp_path / "packages" / "app").mkdir(parents=True)
        _fake_bin(tmp_path / "global", "jscpd")
        monkeypatch.setenv("PATH", str(tmp_path / "global"))
        assert _same_command(
            _resolve_jscpd_command(tmp_path / "packages" / "app"), [str(local)]
        )

    def test_uses_path_install(self, tmp_path, monkeypatch):
        on_path = _fake_bin(tmp_path / "global", "jscpd")
        monkeypatch.setenv("PATH", str(tmp_path / "global"))
        assert _same_command(_resolve_jscpd_command(tmp_path), [str(on_path)])

    def test_never_downloads_without_opt_in(self, tmp_path, monkeypatch):
        _fake_bin(tmp_path / "global", "npx")
        monkeypatch.setenv("PATH", str(tmp_path / "global"))
        assert _resolve_jscpd_command(tmp_path) == "jscpd_not_installed"

    def test_env_opt_in_downloads_pinned_version(self, tmp_path, monkeypatch):
        npx = _fake_bin(tmp_path / "global", "npx")
        monkeypatch.setenv("PATH", str(tmp_path / "global"))
        monkeypatch.setenv("DESLOPPIFY_TS_ALLOW_TOOL_DOWNLOAD", "1")
        assert _same_command(
            _resolve_jscpd_command(tmp_path),
            [str(npx), "--yes", f"jscpd@{JSCPD_VERSION}"],
        )

    def test_config_opt_in_downloads(self, tmp_path, monkeypatch):
        from desloppify_ts.base.runtime_state import current_runtime_context

        _fake_bin(tmp_path / "global", "npx")
        monkeypatch.setenv("PATH", str(tmp_path / "global"))
        monkeypatch.setattr(current_runtime_context(), "allow_tool_download", True)
        command = _resolve_jscpd_command(tmp_path)
        assert isinstance(command, list)
        assert command[-1] == f"jscpd@{JSCPD_VERSION}"

    def test_opt_in_without_npx(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        monkeypatch.setenv("DESLOPPIFY_TS_ALLOW_TOOL_DOWNLOAD", "yes")
        assert _resolve_jscpd_command(tmp_path) == "npx_not_found"

    def test_version_is_exact(self):
        assert all(part.isdigit() for part in JSCPD_VERSION.split("."))


class TestJscpdRunsFromScanRoot:
    @pytest.fixture(autouse=True)
    def _project_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DESLOPPIFY_TS_ROOT", str(tmp_path))

    def test_runs_with_absolute_target_under_dot_claude(self, tmp_path, monkeypatch):
        root = tmp_path / ".claude" / "worktrees" / "copy"
        _write_sources(root, "src/a.ts", "src/b.ts")
        monkeypatch.setenv("DESLOPPIFY_TS_ROOT", str(root))
        seen = {}

        def fake_run(cmd, *, timeout, cwd=None):
            seen["cmd"], seen["cwd"] = cmd, cwd
            out = Path(cmd[cmd.index("--output") + 1])
            out.joinpath("jscpd-report.json").write_text(
                json.dumps(
                    {
                        "duplicates": [
                            {
                                "fragment": _REAL_FRAGMENT,
                                "lines": 5,
                                "firstFile": {
                                    "name": str(root / "src/a.ts"),
                                    "start": 1,
                                },
                                "secondFile": {
                                    "name": str(root / "src/b.ts"),
                                    "start": 3,
                                },
                            }
                        ]
                    }
                )
            )

        with (
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._resolve_jscpd_command",
                return_value=["jscpd"],
            ),
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._run_jscpd_command",
                side_effect=fake_run,
            ),
        ):
            entries = detect_with_jscpd(root)
        assert seen["cwd"] == root.resolve()
        assert seen["cmd"][1] == str(root.resolve())
        assert "--absolute" in seen["cmd"]
        ignores = seen["cmd"][seen["cmd"].index("--ignore") + 1].split(",")
        # Anchored at the scan root, so the .claude/ above it doesn't match.
        assert f"{root.resolve().as_posix()}/**/.claude/**" in ignores
        assert all(glob.startswith(root.resolve().as_posix()) for glob in ignores)
        assert entries is not None and len(entries) == 1
        assert [loc["file"] for loc in entries[0]["locations"]] == [
            "src/a.ts",
            "src/b.ts",
        ]

    def test_ignore_anchor_escapes_glob_characters(self, tmp_path):
        root = tmp_path / "app" / "[slug]"
        root.mkdir(parents=True)
        ignores = _jscpd_ignore_arg(root, root).split(",")
        assert f"{(tmp_path / 'app').as_posix()}/[[]slug[]]/**/.git/**" in ignores

    def test_relative_report_names_resolve_against_scan_root(self, tmp_path):
        _write_sources(tmp_path, "a.ts", "b.ts")
        report = {
            "duplicates": [
                {
                    "fragment": _REAL_FRAGMENT,
                    "lines": 4,
                    "firstFile": {"name": "a.ts", "start": 1},
                    "secondFile": {"name": str(tmp_path / "b.ts"), "start": 2},
                }
            ]
        }
        entries = _parse_jscpd_report(report, tmp_path)
        assert [loc["file"] for loc in entries[0]["locations"]] == ["a.ts", "b.ts"]


def _jscpd_side(name: str, start: int, end: int) -> dict:
    """One side of a duplicate, shaped like jscpd 5's JSON reporter output."""
    return {
        "name": name,
        "start": start,
        "end": end,
        "startLoc": {"line": start, "column": 0, "position": 0},
        "endLoc": {"line": end, "column": 1, "position": 228},
    }


_REAL_FRAGMENT = (
    "export function handler(input: number) {\n"
    "  const doubled = input * 2;\n"
    "  const tripled = input * 3;\n"
    "  const total = doubled + tripled + input;\n"
    "  if (total > 100) {\n"
    "    return total - 100;\n"
    "  }\n"
    "  return total + doubled * tripled;\n"
    "}"
)


def _jscpd_report(first: str, second: str) -> dict:
    return {
        "duplicates": [
            {
                "format": "typescript",
                "lines": 9,
                "fragment": _REAL_FRAGMENT,
                "tokens": 53,
                "kind": "exact",
                "isNew": False,
                "firstFile": _jscpd_side(first, 1, 9),
                "secondFile": _jscpd_side(second, 12, 20),
            }
        ],
        "statistics": {"total": {"clones": 1, "duplicatedLines": 9}},
    }


def _write_sources(root: Path, *rel_paths: str) -> None:
    for rel_path in rel_paths:
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_REAL_FRAGMENT)


class TestJscpdReportPathMapping:
    """jscpd reports absolute names (relative ones are taken against the scan
    root); issues need project-relative paths whatever ``--path`` is."""

    @pytest.fixture
    def project(self, tmp_path, monkeypatch):
        root = tmp_path / "proj"
        root.mkdir()
        monkeypatch.setenv("DESLOPPIFY_TS_ROOT", str(root))
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        return root

    @staticmethod
    def _files(entries: list[dict]) -> list[str]:
        assert len(entries) == 1
        return [loc["file"] for loc in entries[0]["locations"]]

    def test_scan_root_is_project_root(self, project):
        _write_sources(project, "src/app/a.ts", "src/app/b.ts")
        report = _jscpd_report("src/app/a.ts", "src/app/b.ts")
        entries = _parse_jscpd_report(report, project)
        assert self._files(entries) == ["src/app/a.ts", "src/app/b.ts"]
        assert entries[0]["locations"][1]["line"] == 12

    def test_src_subdirectory(self, project):
        _write_sources(project, "src/app/(app)/a.tsx", "src/lib/b.ts")
        report = _jscpd_report("app/(app)/a.tsx", "lib/b.ts")
        entries = _parse_jscpd_report(report, project / "src")
        assert self._files(entries) == ["src/app/(app)/a.tsx", "src/lib/b.ts"]

    def test_nested_package_path(self, project):
        pkg = project / "packages" / "web" / "src"
        _write_sources(pkg, "routes/a.ts", "routes/b.ts")
        report = _jscpd_report("./routes/a.ts", "routes/b.ts")
        entries = _parse_jscpd_report(report, pkg)
        assert self._files(entries) == [
            "packages/web/src/routes/a.ts",
            "packages/web/src/routes/b.ts",
        ]

    def test_absolute_names(self, project):
        _write_sources(project, "src/a.ts", "src/b.ts")
        report = _jscpd_report(str(project / "src/a.ts"), str(project / "src/b.ts"))
        entries = _parse_jscpd_report(report, project / "src")
        assert self._files(entries) == ["src/a.ts", "src/b.ts"]

    def test_windows_separators(self, project):
        _write_sources(project, "src/app/a.ts", "src/app/nested/b.ts")
        report = _jscpd_report("app\\a.ts", "app\\nested\\b.ts")
        entries = _parse_jscpd_report(report, project / "src")
        assert self._files(entries) == ["src/app/a.ts", "src/app/nested/b.ts"]

    def test_artifact_checks_use_scan_relative_paths(self, project):
        _write_sources(project, "src/dist/a.ts", "src/b.ts")
        report = _jscpd_report("dist/a.ts", "b.ts")
        assert _parse_jscpd_report(report, project / "src") == []

    def test_names_outside_scan_root_are_dropped_with_warning(self, project, capsys):
        _write_sources(project, "src/a.ts", "other/b.ts")
        report = _jscpd_report("../other/b.ts", str(project / "other/a.ts"))
        assert _parse_jscpd_report(report, project / "src") == []
        assert "none mapped to files under" in capsys.readouterr().err

    def test_no_warning_when_some_pairs_map(self, project, capsys):
        _write_sources(project, "src/a.ts", "src/b.ts")
        report = _jscpd_report("a.ts", "b.ts")
        report["duplicates"].append(
            {**report["duplicates"][0], "firstFile": _jscpd_side("../x.ts", 1, 9)}
        )
        assert len(_parse_jscpd_report(report, project / "src")) == 1
        assert "WARNING" not in capsys.readouterr().err

    def test_npm_workspace_relative_names_are_rejected(self, project, capsys):
        """npx runs jscpd from the npm workspace package, so a relative
        target came back relative to it: ``src/v4/...`` for a scan of
        ``packages/zod/src``. Such names must not map to made-up files."""
        scan = project / "packages" / "zod" / "src"
        _write_sources(scan, "v4/classic/schemas.ts", "v4/mini/schemas.ts")
        report = _jscpd_report("src/v4/classic/schemas.ts", "src/v4/mini/schemas.ts")
        assert _parse_jscpd_report(report, scan) == []
        assert "none mapped to files under" in capsys.readouterr().err

    def test_npm_workspace_absolute_names(self, project):
        scan = project / "packages" / "zod" / "src"
        _write_sources(scan, "v4/classic/schemas.ts", "v4/mini/schemas.ts")
        report = _jscpd_report(
            str(scan / "v4/classic/schemas.ts"), str(scan / "v4/mini/schemas.ts")
        )
        assert self._files(_parse_jscpd_report(report, scan)) == [
            "packages/zod/src/v4/classic/schemas.ts",
            "packages/zod/src/v4/mini/schemas.ts",
        ]

    def test_detect_maps_src_scan_end_to_end(self, project):
        _write_sources(project, "src/app/a.ts", "src/app/b.ts")
        seen = {}

        def fake_run(cmd, *, timeout, cwd=None):
            seen["cwd"] = cwd
            out = Path(cmd[cmd.index("--output") + 1])
            src = project.resolve() / "src"
            report = _jscpd_report(str(src / "app/a.ts"), str(src / "app/b.ts"))
            out.joinpath("jscpd-report.json").write_text(json.dumps(report))

        with (
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._resolve_jscpd_command",
                return_value=["jscpd"],
            ),
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._run_jscpd_command",
                side_effect=fake_run,
            ),
        ):
            entries = detect_with_jscpd(project / "src")
        assert seen["cwd"] == (project / "src").resolve()
        assert entries is not None
        assert self._files(entries) == ["src/app/a.ts", "src/app/b.ts"]


class TestBoilerplateZoneFilterWarning:
    @staticmethod
    def _zone_map(files: list[str]) -> MagicMock:
        zone_map = MagicMock()
        zone_map.all_files.return_value = files
        return zone_map

    @staticmethod
    def _entry(*files: str) -> dict:
        return {
            "id": "abc",
            "distinct_files": len(files),
            "window_size": 9,
            "locations": [{"file": f, "line": 1} for f in files],
            "sample": [],
        }

    def test_warns_when_no_cluster_matches_scanned_files(self, capsys):
        zone_map = self._zone_map(["src/app/a.ts", "src/app/b.ts"])
        result = _filter_boilerplate_entries_by_zone(
            [self._entry("app/a.ts", "app/b.ts")], zone_map
        )
        assert result == []
        assert "point at a scanned file" in capsys.readouterr().err

    def test_no_warning_when_clusters_match(self, capsys):
        zone_map = self._zone_map(["src/app/a.ts", "src/app/b.ts"])
        with patch(
            "desloppify_ts.languages._framework.base.shared_phases_helpers.should_skip_issue",
            return_value=False,
        ):
            result = _filter_boilerplate_entries_by_zone(
                [self._entry("src/app/a.ts", "src/app/b.ts")], zone_map
            )
        assert len(result) == 1
        assert "WARNING" not in capsys.readouterr().err


class TestJscpdAdapter:
    @pytest.fixture(autouse=True)
    def _sources(self, tmp_path):
        _write_sources(tmp_path, "a.py", "b.py", "c.py", "a.ts", "b.ts")

    def test_returns_none_when_jscpd_not_installed(self, tmp_path):
        with patch(
            "desloppify_ts.engine.detectors.jscpd_adapter._resolve_jscpd_command",
            return_value="jscpd_not_installed",
        ):
            assert detect_with_jscpd(tmp_path) is None
            assert detect_with_jscpd_result(tmp_path) == (None, "jscpd_not_installed")

    def test_returns_none_on_timeout(self, tmp_path):
        with (
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._resolve_jscpd_command",
                return_value=["/usr/bin/npx", "--yes", "jscpd"],
            ),
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._run_jscpd_command",
                side_effect=subprocess.TimeoutExpired("npx", 120),
            ),
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
        with (
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter.subprocess.Popen",
                return_value=fake_proc,
            ) as popen,
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter.os.getpgid",
                return_value=9876,
            ),
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter.os.killpg",
            ) as killpg,
        ):
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
        with (
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._resolve_jscpd_command",
                return_value=["/usr/bin/npx", "--yes", "jscpd"],
            ),
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._run_jscpd_command",
            ),
            patch("tempfile.TemporaryDirectory") as mock_td,
        ):
            mock_td.return_value.__enter__.return_value = str(tmp_path)
            mock_td.return_value.__exit__.return_value = None
            result = detect_with_jscpd(tmp_path)
        assert result is None

    def test_clusters_pairs_with_same_fragment_hash(self, tmp_path):
        f1 = str(tmp_path / "a.py")
        f2 = str(tmp_path / "b.py")
        f3 = str(tmp_path / "c.py")
        fragment = _REAL_FRAGMENT
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
        fragment = _REAL_FRAGMENT
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

    @staticmethod
    def _pair_report(tmp_path, fragment: str) -> dict:
        return {
            "duplicates": [
                {
                    "fragment": fragment,
                    "lines": len(fragment.splitlines()),
                    "firstFile": {"name": str(tmp_path / "a.ts"), "start": 1},
                    "secondFile": {"name": str(tmp_path / "b.ts"), "start": 1},
                }
            ]
        }

    @pytest.mark.parametrize(
        "fragment",
        [
            # Whole imports, a directive, comments and blank lines
            "'use client'\n\nimport * as React from 'react'\n// ui\n"
            "import { Button } from '@/components/ui/button'\n"
            "import type { X } from './x';\nimport './styles.css'\n",
            # Starts and ends inside a multi-line import
            "  b,\n  type C,\n} from './shared'\n/* note */\n"
            "import Default, { d as e } from 'lib'\nimport {\n  f,\n  g",
            # Re-exports
            "export * from './a'\nexport * as ns from './b'\n"
            "export { x, type Y } from './c'\nexport type { Z } from './d'\n",
        ],
    )
    def test_drops_import_only_fragments(self, tmp_path, fragment):
        assert (
            _parse_jscpd_report(self._pair_report(tmp_path, fragment), tmp_path) == []
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            "import { z } from 'zod'\nimport { db } from './db'\n\n"
            "const limit = input.limit ?? 20\n"
            "if (limit > 100) throw new Error('too many')\n",
            "export const A = 1\nimport B from 'c'\nimport D from 'e'\n"
            "export const MODE = A > 0 ? 'on' : 'off'\n",
        ],
    )
    def test_keeps_fragments_with_code_beside_imports(self, tmp_path, fragment):
        result = _parse_jscpd_report(self._pair_report(tmp_path, fragment), tmp_path)
        assert len(result) == 1

    @pytest.mark.parametrize(
        "fragment",
        [
            # tRPC procedure preamble
            "  listMembers: protectedProcedure\n"
            "    .input(z.object({ projectId: z.uuid() }))\n"
            "    .query(async ({ ctx, input }) => {\n"
            "      await assertAccess(ctx.db, ctx.userId, 'project_admin', "
            "{ projectId: input.projectId })\n"
            "\n"
            "      return paginate(\n",
            # The same with a comment block and a multi-line guard call
            "    }),\n\n"
            "  // Join a workspace with an invite code. The code must not have\n"
            "  // expired, and each code works once.\n"
            "  joinWorkspace: protectedProcedure\n"
            "    .input(z.object({ code: z.string().min(1).max(64) }))\n"
            "    .mutation(async ({ ctx, input }) => {\n"
            "      const workspace = await loadWorkspaceFor(\n"
            "        ctx.db,\n"
            "        ctx.userId,\n"
            "        'workspace_owner',\n"
            "        input.workspaceId,\n"
            "      )\n",
            # Next.js page prelude
            "export function ReportsPage({\n"
            "  params,\n"
            "}: {\n"
            "  params: Promise<{ slug: string }>\n"
            "}) {\n"
            "  const { slug: paramSlug } = use(params)\n"
            "  const { projectId, project, owner } = useProjectContext({\n"
            "    projectSlug: paramSlug,\n"
            "    activeTab: 'reports',\n",
            "export default async function Page({ params }: Props) {\n"
            "  const { id } = await params;\n"
            "  const item = await getItem(id);\n",
            # Query-builder chain
            "    .from(projectMembers)\n"
            "    .innerJoin(users, eq(users.id, projectMembers.userId))\n"
            "    .leftJoin(memberSettings, settingsJoin(project.ownerId))\n"
            "    .where(\n"
            "      and(\n"
            "        eq(projectMembers.projectId, input.projectId),\n"
            "        eq(projectMembers.status, 'pending'),\n",
        ],
    )
    def test_drops_framework_scaffolding(self, tmp_path, fragment):
        assert (
            _parse_jscpd_report(self._pair_report(tmp_path, fragment), tmp_path) == []
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            # A duplicated auth guard: few lines, plenty of logic
            "  const authHeader = req.headers.get('authorization')\n"
            "  const token =\n"
            "    authHeader !== null && authHeader.startsWith('Bearer ')\n"
            "      ? authHeader.slice(7)\n"
            "      : ''\n"
            "  if (!isValid) {\n"
            "    return NextResponse.json({ error: 'Unauthorized' }, "
            "{ status: 401 })\n"
            "  }\n",
            # Imperative code with few literals: counted by its statements
            "  const queryCache = queryClient.getQueryCache();\n"
            "  const query = queryCache.build(queryClient, { queryKey });\n"
            "  const aggregate: unknown[] = [];\n"
            "  for await (const value of asyncIterable) {\n"
            "    aggregate.push(value);\n"
            "    query.setState({ data: [...aggregate] });\n"
            "  }\n",
        ],
    )
    def test_keeps_duplicated_logic(self, tmp_path, fragment):
        result = _parse_jscpd_report(self._pair_report(tmp_path, fragment), tmp_path)
        assert len(result) == 1

    def test_logic_tokens_ignore_identifiers_punctuation_and_comments(self):
        assert _logic_token_count("foo.bar({ a, b: [c] }).baz(async () => x)") == 0
        assert _logic_token_count("// if (a === 'b') return 1\n/* 'x' */") == 0
        assert _logic_token_count("Array<string> <div>") == 0
        # if, ===, 'b', return, 1, a < b's <
        assert _logic_token_count("if (a === 'b') return 1; a < b") == 6

    def test_statements_skip_chain_and_argument_lines(self):
        assert _statement_count("const a = f(\n  g(x),\n  h(y),\n)\n.then(z)") == 1
        assert _statement_count("a.b = 1\nc()\nreturn d\nx += 2") == 4

    def test_skips_files_outside_scan_path(self, tmp_path):
        f_in = str(tmp_path / "a.py")
        f_out = "/other/path/b.py"
        fragment = _REAL_FRAGMENT
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
        fragment = _REAL_FRAGMENT
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
        assert result[0]["sample"] == _REAL_FRAGMENT.splitlines()[:4]

    def test_skips_same_file_pairs(self, tmp_path):
        f1 = str(tmp_path / "a.py")
        fragment = _REAL_FRAGMENT
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
        fragment = _REAL_FRAGMENT
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
        f_artifact = str(tmp_path / ".desloppify-ts" / "cache.py")
        f_src = str(tmp_path / "pkg" / "module.py")
        fragment = _REAL_FRAGMENT
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
            assert "**/.desloppify-ts/**" in ignore_value
            assert "**/.claude/**" in ignore_value
            assert "**/build/**" in ignore_value
            assert "**/node_modules/**" in ignore_value
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._resolve_jscpd_command",
                return_value=["/usr/bin/npx", "--yes", "jscpd"],
            ),
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter._run_jscpd_command",
                side_effect=_fake_run,
            ),
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter.collect_exclude_dirs",
                return_value=fake_dirs,
            ),
            patch(
                "desloppify_ts.engine.detectors.jscpd_adapter.get_exclusions",
                return_value=(),
            ),
            patch("tempfile.TemporaryDirectory") as mock_td,
        ):
            mock_td.return_value.__enter__.return_value = str(tmp_path)
            mock_td.return_value.__exit__.return_value = None
            result = detect_with_jscpd(tmp_path)
        assert result == []


# ── collect_exclude_dirs ─────────────────────────────────────────────────────


from desloppify_ts.base.discovery.source import collect_exclude_dirs  # noqa: E402


class TestCollectExcludeDirs:
    def test_returns_absolute_paths(self, tmp_path):
        with patch(
            "desloppify_ts.base.discovery.source.get_exclusions", return_value=()
        ):
            result = collect_exclude_dirs(tmp_path)
        assert all(p.startswith(str(tmp_path)) for p in result)

    def test_includes_default_non_glob_entries(self, tmp_path):
        with patch(
            "desloppify_ts.base.discovery.source.get_exclusions", return_value=()
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
            "desloppify_ts.base.discovery.source.get_exclusions", return_value=()
        ):
            result = collect_exclude_dirs(tmp_path)
        # *.egg-info and .venv* are glob patterns and should be excluded
        assert not any("*" in p for p in result)

    def test_includes_runtime_exclusions(self, tmp_path):
        with patch(
            "desloppify_ts.base.discovery.source.get_exclusions",
            return_value=("vendor", "third_party"),
        ):
            result = collect_exclude_dirs(tmp_path)
        basenames = {Path(p).name for p in result}
        assert "vendor" in basenames
        assert "third_party" in basenames

    def test_skips_runtime_glob_exclusions(self, tmp_path):
        with patch(
            "desloppify_ts.base.discovery.source.get_exclusions",
            return_value=("vendor/**",),
        ):
            result = collect_exclude_dirs(tmp_path)
        # glob pattern should not appear
        assert not any("vendor" in p for p in result)

    def test_deduplicates(self, tmp_path):
        """Runtime exclusion that overlaps with DEFAULT_EXCLUSIONS doesn't produce dupes."""
        with patch(
            "desloppify_ts.base.discovery.source.get_exclusions",
            return_value=("node_modules",),
        ):
            result = collect_exclude_dirs(tmp_path)
        node_entries = [p for p in result if Path(p).name == "node_modules"]
        assert len(node_entries) == 1
