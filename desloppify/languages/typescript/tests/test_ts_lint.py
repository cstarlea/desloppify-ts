"""Tests for the project-linter (lint) detector and its phase."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import desloppify.languages.typescript.detectors.lint.eslint as eslint_mod
import desloppify.languages.typescript.detectors.lint.oxlint as oxlint_mod
import desloppify.languages.typescript.detectors.lint.runner as runner_mod
import desloppify.languages.typescript.phases_basic as phases_basic_mod
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.engine.policy.zones import Zone
from desloppify.languages.typescript.detectors.lint import detect_lint_result
from desloppify.languages.typescript.detectors.lint.configs import (
    find_lint_configs,
    nested_config_dirs,
)
from desloppify.languages.typescript.detectors.lint.biome import parse_biome_output
from desloppify.languages.typescript.detectors.lint.eslint import parse_eslint_output
from desloppify.languages.typescript.detectors.lint.oxlint import parse_oxlint_output, rule_name
from desloppify.languages.typescript.detectors.lint.runner import LinterRun
from desloppify.languages.typescript.detectors.lint.rules import classify


def _write(root: Path, name: str, text: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
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

    def fake(stdout="", *, returncode=1, stderr="", config=None, raises=None, by_linter=None, rules=()):
        def run(cmd, linter_config, timeout=300):
            calls.append(cmd)
            if any(arg.startswith("--print-config") for arg in cmd):
                return SimpleNamespace(stdout=json.dumps(config or {}), stderr="", returncode=0)
            if "--rules" in cmd:
                return SimpleNamespace(stdout=json.dumps(list(rules)), stderr="", returncode=0)
            if raises is not None:
                raise raises
            out = (by_linter or {}).get(linter_config.linter, stdout)
            return SimpleNamespace(stdout=out, stderr=stderr, returncode=returncode)

        for module in (runner_mod, eslint_mod, oxlint_mod):
            monkeypatch.setattr(module, "run_process", run)
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

    assert parse_eslint_output(stdout, run)
    assert [(m.rule, m.line, m.fixable, m.severity) for m in run.messages] == [
        ("eqeqeq", 3, True, "error"),
        ("unused-disable-directive", 7, False, "warning"),
    ]
    assert run.messages[0].meta == {"type": "suggestion"}
    assert run.files == {a, b} and run.unparsed == {b}
    assert not parse_eslint_output("Oops", LinterRun(config=config))


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


# XO, Biome, oxlint


def test_configs_found_per_linter(tmp_path):
    _write(tmp_path, "package.json", '{"devDependencies": {"xo": "1"}}')
    _write(tmp_path, "biome.jsonc", '{ // comment\n "linter": {"enabled": true} }')
    _write(tmp_path, ".oxlintrc.json", "{}")
    _write(tmp_path, "fmt/biome.json", '{"linter": {"enabled": false}}')
    _write(tmp_path, "fmt/package.json", "{}")

    assert [c.linter for c in find_lint_configs(tmp_path / "src")] == ["xo", "biome", "oxlint"]
    # a Biome config with its linter off is a formatter config, not a linter
    assert [c.linter for c in find_lint_configs(tmp_path / "fmt")] == ["xo", "biome", "oxlint"]
    assert nested_config_dirs(tmp_path, tmp_path) == []


def test_parse_biome_output(tmp_path):
    a = _write(tmp_path, "src/a.ts", 'const é = "é";\nexport const b = 1 == 2;\n')
    config = find_lint_configs(_write(tmp_path, "biome.json", "{}").parent)[0]
    run = LinterRun(config=config)
    diagnostics = [
        {
            "category": "lint/suspicious/noDoubleEquals",
            "severity": "error",
            "description": "Use === instead of ==",
            "location": {"path": {"file": "./src/a.ts"}, "span": [36, 38]},
            "tags": ["fixable"],
        },
        {
            "category": "lint/style/useConst",
            "severity": "warning",
            "description": "Use const",
            "location": {"path": "src/a.ts", "start": {"line": 1, "column": 1}},
        },
        {"category": "parse", "severity": "error", "location": {"path": {"file": "src/b.ts"}, "span": [0, 0]}},
    ]
    stdout = json.dumps({"summary": {"changed": 0, "unchanged": 2}, "diagnostics": diagnostics})
    files = {a, tmp_path / "src/b.ts"}

    assert parse_biome_output("unstable warning\n" + stdout, run, files)
    # the span is in UTF-8 bytes: line 1 has two two-byte characters
    assert [(m.rule, m.line, m.col, m.severity, m.fixable) for m in run.messages] == [
        ("suspicious/noDoubleEquals", 2, 20, "error", True),
        ("style/useConst", 1, 1, "warning", False),
    ]
    assert classify("suspicious/noDoubleEquals", "error", run.messages[0].meta) == "high"
    assert classify("style/useConst", "error", run.messages[1].meta) == "low"
    assert classify("suspicious/noExplicitAny", "error", None) is None
    assert run.unparsed == {tmp_path / "src/b.ts"} and run.files == files

    broken = LinterRun(config=config)
    bad = json.dumps({"diagnostics": [{"category": "configuration", "description": "bad key"}]})
    assert not parse_biome_output(bad, broken, files)
    assert broken.error == "bad key"


def _ox(code, line, severity="error", filename="src/a.ts"):
    return {
        "code": code,
        "severity": severity,
        "message": "m",
        "filename": filename,
        "labels": [{"span": {"offset": 0, "length": 1, "line": line, "column": 5}}],
    }


def test_parse_oxlint_output(tmp_path):
    config = find_lint_configs(_write(tmp_path, ".oxlintrc.json", "{}").parent)[0]
    run = LinterRun(config=config)
    diagnostics = [
        _ox("eslint(eqeqeq)", 1, filename="src/b.ts"),
        _ox("typescript(no-floating-promises)", 3, "warning", filename="src/b.ts"),
        {"message": "Expected `)`", "severity": "error", "filename": "src/a.ts", "labels": []},
    ]
    stdout = json.dumps({"diagnostics": diagnostics, "number_of_files": 2})
    files = {tmp_path / "src/a.ts", tmp_path / "src/b.ts"}

    assert parse_oxlint_output(stdout, run, files, {"eqeqeq": {"type": "suggestion"}})
    assert [(m.rule, m.line, m.severity, m.meta) for m in run.messages] == [
        ("eqeqeq", 1, "error", {"type": "suggestion"}),
        ("@typescript-eslint/no-floating-promises", 3, "warning", None),
    ]
    assert run.unparsed == {tmp_path / "src/a.ts"}
    assert rule_name("jsx_a11y", "alt-text") == "jsx-a11y/alt-text"
    assert rule_name("eslint-plugin-react", "jsx-key") == "react/jsx-key"

    empty = LinterRun(config=config)
    assert parse_oxlint_output(json.dumps({"diagnostics": [], "number_of_files": 0}), empty, files, {})
    assert empty.files == set()


def test_every_configured_linter_runs_and_shared_rules_merge(project):
    root, fake = project
    a = _write(root, "src/a.ts")
    _write(root, ".oxlintrc.json", "{}")
    _write(root, "node_modules/.bin/oxlint", "#!/bin/sh\n")
    oxlint_out = json.dumps(
        {"diagnostics": [_ox("eslint(eqeqeq)", 3), _ox("eslint(no-debugger)", 4, "warning")], "number_of_files": 1}
    )
    fake(
        by_linter={
            "eslint": _output({a: [_message("eqeqeq", 3, 5)]}, {"eqeqeq": {"type": "suggestion"}}),
            "oxlint": oxlint_out,
        },
        rules=[{"scope": "eslint", "value": "no-debugger", "category": "correctness"}],
    )

    result = detect_lint_result(root / "src")
    assert result.linters == ("eslint", "oxlint")
    assert [(e["rule"], e["line"], e["count"], e["confidence"]) for e in result.entries] == [
        ("eqeqeq", 3, 2, "medium"),
        ("no-debugger", 4, 1, "medium"),
    ]


def test_xo_runs_with_its_own_flags(project):
    root, fake = project
    (root / "eslint.config.js").unlink()
    _write(root, "package.json", '{"devDependencies": {"xo": "1"}, "xo": {"rules": {}}}')
    _write(root, "node_modules/.bin/xo", "#!/bin/sh\n")
    a = _write(root, "src/a.ts")
    calls = fake(json.dumps([{"filePath": str(a), "messages": [_message("max-depth", 2, severity=1)]}]))

    result = detect_lint_result(root / "src")
    assert calls[0][1] == f"--print-config={a}"
    assert calls[-1][1] == "--reporter=json"
    assert [(e["rule"], e["confidence"]) for e in result.entries] == [("max-depth", "low")]
