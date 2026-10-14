"""Tests for desloppify_ts.languages.typescript.commands — command registry."""

from types import SimpleNamespace

import desloppify_ts.languages.typescript.commands as commands_mod
from desloppify_ts.languages.typescript.commands import get_detect_commands
from desloppify_ts.languages.typescript.detectors.lint import LintResult

# ── get_detect_commands() ─────────────────────────────────────


def test_returns_dict():
    """get_detect_commands() returns a dict."""
    cmds = get_detect_commands()
    assert isinstance(cmds, dict)


def test_keys_are_strings():
    """All keys in the command registry are strings."""
    cmds = get_detect_commands()
    for key in cmds:
        assert isinstance(key, str)


def test_expected_commands_present():
    """Core detect commands are present in the registry."""
    cmds = get_detect_commands()
    expected = [
        "logs",
        "unused",
        "exports",
        "deprecated",
        "large",
        "complexity",
        "gods",
        "single_use",
        "props",
        "passthrough",
        "concerns",
        "dupes",
        "smells",
        "coupling",
        "patterns",
        "naming",
        "cycles",
        "orphaned",
        "react",
        "facade",
        "lint",
    ]
    for name in expected:
        assert name in cmds, f"Expected command '{name}' not found"


def test_non_empty():
    """Command registry has at least 10 entries."""
    cmds = get_detect_commands()
    assert len(cmds) >= 10


# ── detect lint ───────────────────────────────────────────────


def _lint_args(**extra):
    return SimpleNamespace(path=".", json=False, top=20, **extra)


def test_cmd_lint_lists_findings(monkeypatch, capsys):
    entry = {
        "file": "src/a.ts",
        "line": 3,
        "rule": "no-console",
        "message": "Unexpected console statement.\nmore",
    }
    monkeypatch.setattr(
        commands_mod,
        "detect_lint_result",
        lambda _path: LintResult([entry], ["src/a.ts"], None, ("eslint",)),
    )
    commands_mod.cmd_lint(_lint_args())
    out = capsys.readouterr().out
    assert "Lint findings (eslint): 1" in out
    assert "no-console" in out and "Unexpected console statement." in out
    assert "more" not in out


def test_cmd_lint_without_a_linter(monkeypatch, capsys):
    monkeypatch.setattr(
        commands_mod, "detect_lint_result", lambda _path: LintResult([], None, None)
    )
    commands_mod.cmd_lint(_lint_args())
    assert "No linter config found" in capsys.readouterr().out
