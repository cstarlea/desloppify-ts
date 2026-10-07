"""Tests for running external tools as detector phases."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from desloppify.languages._framework.tools.parsers import PARSERS, ToolParserError
from desloppify.languages._framework.tools.phase import make_tool_phase
from desloppify.languages._framework.tools.runner import (
    resolve_command_argv,
    run_tool_result,
)

_LINE_RE = re.compile(r"^(.+?):(\d+): (.+)$")


def _parse_lines(output: str, _scan_path: Path) -> list[dict]:
    """`file:line: message` per line."""
    entries = []
    for line in output.splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            entries.append({"file": m.group(1), "line": int(m.group(2)), "message": m.group(3)})
    return entries


def _parse_json_list(output: str, _scan_path: Path) -> list[dict]:
    """A JSON array of {file, line, message} objects."""
    try:
        data = json.loads(output)
    except ValueError as exc:
        raise ToolParserError("could not decode JSON") from exc
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


@pytest.fixture(autouse=True)
def _lines_parser():
    with patch.dict(PARSERS, {"lines": _parse_lines}):
        yield


class TestMakeToolPhase:
    def test_missing_tool_returns_no_issues(self):
        phase = make_tool_phase("test", "nonexistent_tool_xyz_123", "lines", "test_id", 2)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            issues, signals = phase.run(Path("."), None)
        assert issues == []
        assert signals == {}

    def test_timeout_returns_no_issues(self):
        phase = make_tool_phase("test", "sleep 999", "lines", "test_id", 2)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)):
            issues, signals = phase.run(Path("."), None)
        assert issues == []
        assert signals == {}

    def test_missing_tool_records_coverage_degradation(self):
        phase = make_tool_phase("test", "nonexistent_tool_xyz_123", "lines", "test_id", 2)
        lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])
        with patch("subprocess.run", side_effect=FileNotFoundError):
            issues, signals = phase.run(Path("."), lang)
        assert issues == []
        assert signals == {}
        assert lang.detector_coverage["test_id"]["status"] == "reduced"
        assert lang.detector_coverage["test_id"]["reason"] == "tool_not_found"

    def test_line_output_produces_issues(self):
        phase = make_tool_phase("test", "fake", "lines", "test_lint", 2)
        mock_result = subprocess.CompletedProcess(
            args="fake",
            returncode=1,
            stdout="src/foo.go:10: something wrong\nsrc/bar.go:20: another issue\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            issues, signals = phase.run(Path("."), None)
        assert len(issues) == 2
        assert signals == {"test_lint": 2}
        assert issues[0]["detector"] == "test_lint"
        assert issues[0]["summary"] == "something wrong"

    def test_empty_output_returns_empty(self):
        phase = make_tool_phase("test", "fake", "lines", "test_id", 2)
        mock_result = subprocess.CompletedProcess(
            args="fake", returncode=0, stdout="", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result):
            issues, signals = phase.run(Path("."), None)
        assert issues == []
        assert signals == {}

    def test_run_tool_parser_exception_returns_error(self, tmp_path):
        mock_result = subprocess.CompletedProcess(
            args="fake",
            returncode=1,
            stdout="bad payload\n",
            stderr="",
        )

        def _raising_parser(_output: str, _scan_path: Path) -> list[dict]:
            raise ValueError("bad parser row")

        result = run_tool_result(
            "fake",
            tmp_path,
            _raising_parser,
            run_subprocess=lambda *_a, **_k: mock_result,
        )
        assert result.status == "error"
        assert result.error_kind == "parser_exception"
        assert result.entries == []

    def test_run_tool_result_distinguishes_empty_vs_error(self, tmp_path):
        clean = subprocess.CompletedProcess(args="fake", returncode=0, stdout="", stderr="")
        empty_result = run_tool_result(
            "fake",
            tmp_path,
            _parse_lines,
            run_subprocess=lambda *_a, **_k: clean,
        )
        assert empty_result.status == "empty"
        assert empty_result.error_kind is None

        failed = subprocess.CompletedProcess(args="fake", returncode=2, stdout="", stderr="")
        failed_result = run_tool_result(
            "fake",
            tmp_path,
            _parse_lines,
            run_subprocess=lambda *_a, **_k: failed,
        )
        assert failed_result.status == "error"
        assert failed_result.error_kind == "tool_failed_no_output"

    def test_run_tool_result_nonzero_unparsed_output_is_error(self, tmp_path):
        failed = subprocess.CompletedProcess(
            args="fake",
            returncode=2,
            stdout='{"unexpected":"shape"}',
            stderr="",
        )
        failed_result = run_tool_result(
            "fake",
            tmp_path,
            _parse_json_list,
            run_subprocess=lambda *_a, **_k: failed,
        )
        assert failed_result.status == "error"
        assert failed_result.error_kind == "tool_failed_unparsed_output"

    def test_run_tool_result_parser_decode_error_is_error(self, tmp_path):
        failed = subprocess.CompletedProcess(
            args="fake",
            returncode=0,
            stdout="{bad-json",
            stderr="",
        )
        failed_result = run_tool_result(
            "fake",
            tmp_path,
            _parse_json_list,
            run_subprocess=lambda *_a, **_k: failed,
        )
        assert failed_result.status == "error"
        assert failed_result.error_kind == "parser_error"

    def test_run_tool_result_parses_stdout_ignoring_stderr_preamble(self, tmp_path):
        """stdout JSON should parse successfully even when stderr has non-JSON diagnostics."""
        valid_json = json.dumps([{"file": "a.php", "line": 1, "message": "err"}])
        result_with_stderr_noise = subprocess.CompletedProcess(
            args="fake",
            returncode=1,
            stdout=valid_json,
            stderr="Note: Using configuration file /app/phpstan.neon.dist.\n",
        )
        result = run_tool_result(
            "fake",
            tmp_path,
            _parse_json_list,
            run_subprocess=lambda *_a, **_k: result_with_stderr_noise,
        )
        assert result.status == "ok"
        assert len(result.entries) == 1

    def test_run_tool_result_error_preview_uses_combined_output(self, tmp_path):
        """Error preview in tool_failed_unparsed_output message includes both stdout and stderr."""
        result_bad = subprocess.CompletedProcess(
            args="fake",
            returncode=2,
            stdout='{"not": "an array"}',
            stderr="Note: some diagnostic from stderr\n",
        )
        result = run_tool_result(
            "fake",
            tmp_path,
            _parse_json_list,
            run_subprocess=lambda *_a, **_k: result_bad,
        )
        assert result.status == "error"
        assert result.error_kind == "tool_failed_unparsed_output"
        assert result.message is not None
        assert "not" in result.message  # from stdout
        assert "diagnostic from stderr" in result.message  # from stderr

    def test_resolve_command_argv_plain_command_does_not_shell_fallback(self):
        argv = resolve_command_argv("nonexistent_tool_xyz_123 --version")
        assert argv == ["nonexistent_tool_xyz_123", "--version"]

    def test_resolve_command_argv_shell_meta_uses_platform_shell(self):
        argv = resolve_command_argv("echo ok | cat")
        if os.name == "nt":
            assert argv == ["cmd.exe", "/d", "/s", "/c", "echo ok | cat"]
        else:
            assert argv == ["/bin/sh", "-lc", "echo ok | cat"]

    def test_resolve_command_argv_windows_backslash_path_preserved(self):
        with patch("desloppify.languages._framework.tools.runner.os.name", "nt"):
            argv = resolve_command_argv(r"C:\Tools\tool.exe --flag")
        assert argv == [r"C:\Tools\tool.exe", "--flag"]

    def test_resolve_command_argv_windows_quoted_path_unquotes_executable(self):
        with patch("desloppify.languages._framework.tools.runner.os.name", "nt"):
            argv = resolve_command_argv('"C:\\Program Files\\Tool\\tool.exe" --flag')
        assert argv == [r"C:\Program Files\Tool\tool.exe", "--flag"]
