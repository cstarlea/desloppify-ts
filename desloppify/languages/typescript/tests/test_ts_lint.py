"""Tests for the project-linter (lint) detector and its phase."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import desloppify.languages.typescript.detectors.lint.eslint as eslint_mod
import desloppify.languages.typescript.phases_basic as phases_basic_mod
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.engine.policy.zones import Zone
from desloppify.languages.typescript.detectors.lint import detect_lint_result
from desloppify.languages.typescript.detectors.lint.configs import (
    find_lint_configs,
    nested_config_dirs,
)
from desloppify.languages.typescript.detectors.lint.eslint import LinterRun, parse_eslint_output
from desloppify.languages.typescript.detectors.lint.rules import classify


def _write(root: Path, name: str, text: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _message(rule, line, col=1, severity=2, message="msg", **extra):
    return {"ruleId": rule, "line": line, "column": col, "severity": severity, "message": message, **extra}


def _output(results: dict[Path, list[dict]], meta: dict | None = None) -> str:
    return json.dumps(
        {
            "results": [{"filePath": str(f), "messages": m} for f, m in results.items()],
            "metadata": {"rulesMeta": meta or {}},
        }
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project with ESLint installed; ``fake(...)`` sets what ESLint prints."""
    _write(tmp_path, "eslint.config.js", "export default [];\n")
    _write(tmp_path, "package.json", '{"devDependencies": {"eslint": "9"}}\n')
    _write(tmp_path, "node_modules/.bin/eslint", "#!/bin/sh\n")
    calls: list[list[str]] = []

    def fake(stdout="", *, returncode=1, stderr="", config=None, raises=None):
        def run(cmd, linter_config, timeout):
            calls.append(cmd)
            if "--print-config" in cmd:
                return SimpleNamespace(stdout=json.dumps(config or {}), stderr="", returncode=0)
            if raises is not None:
                raise raises
            return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

        monkeypatch.setattr(eslint_mod, "_run", run)
        return calls

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        yield tmp_path, fake


# rules


@pytest.mark.parametrize(
    "rule,severity,meta,expected",
    [
        ("eqeqeq", "error", {"type": "suggestion"}, "medium"),
        ("no-dupe-keys", "error", {"type": "problem"}, "high"),
        ("no-dupe-keys", "warning", {"type": "problem"}, "medium"),
        ("indent", "error", {"type": "layout"}, None),
        ("@stylistic/semi", "error", None, None),
        ("@typescript-eslint/no-unused-vars", "error", {"type": "problem"}, None),
        ("@typescript-eslint/no-explicit-any", "error", None, None),
        ("@typescript-eslint/no-floating-promises", "error", {"type": "problem"}, "high"),
        ("@typescript-eslint/no-unsafe-call", "error", {"type": "problem"}, "medium"),
        ("@typescript-eslint/no-unnecessary-type-assertion", "error", {"type": "suggestion"}, "low"),
        ("@typescript-eslint/array-type", "error", {"docs": {"recommended": "stylistic"}}, "low"),
        ("unicorn/filename-case", "error", {"type": "suggestion"}, "low"),
        ("some-plugin/unknown", "warning", None, "low"),
    ],
)
def test_classify(rule, severity, meta, expected):
    assert classify(rule, severity, meta) == expected


# configs


def test_nearest_flat_config_wins_and_legacy_configs_under_it_are_ignored(tmp_path):
    root_config = _write(tmp_path, "eslint.config.mjs")
    _write(tmp_path, "packages/a/package.json", '{"eslintConfig": {"rules": {}}}')
    _write(tmp_path, "packages/a/.eslintrc.json", "{}")
    own = _write(tmp_path, "packages/b/eslint.config.js")

    assert [c.path for c in find_lint_configs(tmp_path / "packages/a")] == [root_config]
    assert [c.path for c in find_lint_configs(tmp_path / "packages/b/src")] == [own]
    assert nested_config_dirs(tmp_path, tmp_path) == [tmp_path / "packages/b"]


def test_legacy_config_is_used_without_a_flat_config(tmp_path):
    _write(tmp_path, ".eslintrc.cjs")
    _write(tmp_path, "pkg/package.json", '{"eslintConfig": {}}')

    (config,) = find_lint_configs(tmp_path / "pkg")
    assert config.legacy and config.path == tmp_path / "pkg/package.json"
    assert find_lint_configs(tmp_path / "pkg") and not nested_config_dirs(tmp_path, tmp_path)


def test_no_config(tmp_path):
    _write(tmp_path, "package.json", "{}")
    assert find_lint_configs(tmp_path) == []


# output parsing


def test_parse_output_reads_rules_fixes_and_parse_errors(tmp_path):
    a, b = tmp_path / "a.ts", tmp_path / "b.ts"
    stdout = _output(
        {
            a: [
                _message("eqeqeq", 3, fix={"range": [0, 1], "text": "="}),
                _message(None, 7, severity=1, message="Unused eslint-disable directive (no problems were reported)."),
                _message(None, 0, severity=1, message="File ignored because of a matching ignore pattern."),
            ],
            b: [_message(None, 2, fatal=True, message="Parsing error: ';' expected.")],
        },
        {"eqeqeq": {"type": "suggestion"}},
    )
    config = find_lint_configs(_write(tmp_path, "eslint.config.js").parent)[0]
    run = LinterRun(config=config)

    assert parse_eslint_output(stdout, config, run)
    assert [(m.rule, m.line, m.fixable, m.severity) for m in run.messages] == [
        ("eqeqeq", 3, True, "error"),
        ("unused-disable-directive", 7, False, "warning"),
    ]
    assert run.messages[0].meta == {"type": "suggestion"}
    assert run.files == {a, b} and run.unparsed == {b}
    assert not parse_eslint_output("Oops", config, LinterRun(config=config))


# detector


def test_reports_findings_one_issue_per_rule_and_line(project):
    root, fake = project
    a = _write(root, "src/a.ts", "x\n")
    _write(root, "src/b.ts", "x\n")
    outside = _write(root, "other/c.ts", "x\n")
    calls = fake(
        _output(
            {
                a: [
                    _message("eqeqeq", 3, 5),
                    _message("eqeqeq", 3, 12),
                    _message("@typescript-eslint/no-floating-promises", 4),
                    _message("@typescript-eslint/no-unused-vars", 6),
                    _message("prettier/prettier", 8),
                ],
                root / "src/b.ts": [],
                outside: [_message("eqeqeq", 1)],
            },
            {"eqeqeq": {"type": "suggestion"}},
        )
    )
    result = detect_lint_result(root / "src")

    assert [(e["rule"], e["line"], e["cols"], e["count"], e["confidence"]) for e in result.entries] == [
        ("@typescript-eslint/no-floating-promises", 4, [1], 1, "high"),
        ("eqeqeq", 3, [5, 12], 2, "medium"),
    ]
    assert result.checked_files == [str(a), str(root / "src/b.ts")]
    assert result.linters == ("eslint",) and result.coverage is None
    lint_call = calls[-1]
    assert lint_call[1:3] == ["--format", "json-with-metadata"] and lint_call[-1] == "src"


def test_nested_config_is_left_out_and_named(project):
    root, fake = project
    _write(root, "src/a.ts")
    _write(root, "packages/p/eslint.config.js")
    _write(root, "packages/p/x.ts")
    calls = fake(_output({root / "src/a.ts": []}), returncode=0)
    result = detect_lint_result(root)

    assert result.checked_files == [str(root / "src/a.ts")]
    assert "2 files are under other lint configs (packages/p)" in result.coverage.summary
    assert "packages/p/**" in calls[-1]


def test_missing_eslint_with_dependencies_not_installed(project):
    root, _fake = project
    _write(root, "src/a.ts")
    for path in sorted((root / "node_modules").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (root / "node_modules").rmdir()

    result = detect_lint_result(root / "src")
    assert result.checked_files is None
    assert result.coverage.reason == "deps_not_installed"
    assert result.coverage.confidence == 0.0


def test_missing_eslint_with_dependencies_installed(project):
    root, _fake = project
    _write(root, "src/a.ts")
    (root / "node_modules/.bin/eslint").unlink()

    result = detect_lint_result(root / "src")
    assert result.checked_files is None and result.coverage.reason == "linter_missing"


def test_no_config_is_not_reduced_coverage(project):
    root, _fake = project
    (root / "eslint.config.js").unlink()
    _write(root, "src/a.ts")

    result = detect_lint_result(root / "src")
    assert result.checked_files is None and result.coverage is None


@pytest.mark.parametrize(
    "kwargs,reason,summary",
    [
        (
            {"returncode": 2, "stderr": "Oops! Something went wrong! :(\n\nESLint: 9.0.0\n\nCannot find package 'x'\n"},
            "linter_failed",
            "Cannot find package 'x'",
        ),
        ({"returncode": 134, "stderr": "FATAL ERROR: JavaScript heap out of memory"}, "linter_oom", "out of memory"),
        ({"raises": subprocess.TimeoutExpired("eslint", 300)}, "linter_timeout", "over 300s"),
    ],
)
def test_linter_failures_skip_cleanly(project, kwargs, reason, summary):
    root, fake = project
    _write(root, "src/a.ts")
    fake(**kwargs)

    result = detect_lint_result(root / "src")
    assert result.checked_files is None and result.entries == []
    assert result.coverage.reason == reason and summary in result.coverage.summary


def test_type_aware_config_over_the_size_limit_is_skipped(project):
    root, fake = project
    for i in range(3):
        _write(root, f"src/f{i}.ts")
    calls = fake(config={"languageOptions": {"parserOptions": {"projectService": True}}})

    result = detect_lint_result(root / "src", type_aware_max_files=2)
    assert result.checked_files is None and result.coverage.reason == "type_aware_too_large"
    assert len(calls) == 1  # only --print-config

    fake(_output({root / "src/f0.ts": []}), returncode=0, config={"parserOptions": {"project": True}})
    assert detect_lint_result(root / "src", type_aware_max_files=0).checked_files is not None


def test_unparsable_files_are_not_counted(project):
    root, fake = project
    a, b = _write(root, "src/a.ts"), _write(root, "src/b.ts")
    fake(_output({a: [_message(None, 1, fatal=True, message="Parsing error")], b: []}))

    result = detect_lint_result(root / "src")
    assert result.checked_files == [str(b)]
    assert result.coverage.reason == "partial" and "could not be parsed" in result.coverage.summary


# phase


def test_phase_issue_ids_are_rule_and_line(project):
    root, fake = project
    a = _write(root, "src/a.ts")
    vendored = _write(root, "src/vendor/v.ts")
    fake(_output({a: [_message("eqeqeq", 3, message="Expected '==='\nmore")], vendored: [_message("eqeqeq", 1)]}))
    zone_map = SimpleNamespace(get=lambda path: Zone.VENDOR if "vendor" in path else Zone.PRODUCTION)
    lang = SimpleNamespace(
        zone_map=zone_map,
        detector_coverage={},
        coverage_warnings=[],
        runtime_setting=lambda key, default=None: default,
    )
    issues, potentials = phases_basic_mod.phase_lint(root / "src", lang)

    assert [issue["id"] for issue in issues] == ["lint::src/a.ts::eqeqeq::3"]
    assert issues[0]["summary"] == "eqeqeq: Expected '==='"
    assert issues[0]["tier"] == 3 and issues[0]["confidence"] == "medium"
    assert potentials == {"lint": 1, "next_lint": 0}


def test_phase_reports_no_potential_when_skipped(project):
    root, fake = project
    _write(root, "src/a.ts")
    fake(returncode=2, stderr="boom")
    lang = SimpleNamespace(
        zone_map=None, detector_coverage={}, coverage_warnings=[], runtime_setting=lambda k, d=None: d
    )
    issues, potentials = phases_basic_mod.phase_lint(root / "src", lang)

    assert issues == [] and potentials == {}
    assert lang.detector_coverage["lint"]["reason"] == "linter_failed"
