"""Monorepo mode of the lint detector: each package with its own config and file limit."""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import desloppify.languages.typescript.detectors.lint.eslint as eslint_mod
import desloppify.languages.typescript.detectors.lint.runner as runner_mod
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.languages.typescript.detectors.bounded import Budget
from desloppify.languages.typescript.detectors.lint import detect_lint_result

_TYPE_AWARE = {"languageOptions": {"parserOptions": {"projectService": True}}}


def _write(root: Path, name: str, text: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A workspace with ESLint installed; the fake ESLint lints what it is given.

    Each linted file gets one ``eqeqeq`` message. ``type_aware`` holds the config
    directories whose ``--print-config`` says type-aware. ``runs`` records each
    lint run as ``(config dir, targets)``.
    """
    _write(tmp_path, "eslint.config.js")
    _write(tmp_path, "package.json", '{"workspaces": ["packages/*"], "devDependencies": {"eslint": "9"}}\n')
    _write(tmp_path, "node_modules/.bin/eslint", "#!/bin/sh\n")
    type_aware: set[Path] = set()
    runs: list[tuple[str, list[str]]] = []

    def run(cmd, config, timeout=300):
        directory = config.directory
        if any(arg.startswith("--print-config") for arg in cmd):
            return SimpleNamespace(
                stdout=json.dumps(_TYPE_AWARE if directory in type_aware else {}), stderr="", returncode=0
            )
        ignores = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--ignore-pattern"]
        targets = [arg for arg in cmd[4:] if arg not in ignores and arg != "--ignore-pattern"]
        runs.append((directory.relative_to(tmp_path).as_posix() or ".", targets))
        results = []
        for target in targets:
            for dirpath, _dirs, files in os.walk(directory / target):
                for name in files:
                    file = Path(dirpath) / name
                    relative = os.path.relpath(file, directory).replace(os.sep, "/")
                    if file.suffix != ".ts" or any(fnmatch.fnmatch(relative, p) for p in ignores):
                        continue
                    message = {"ruleId": "eqeqeq", "line": 1, "column": 1, "severity": 2, "message": "m"}
                    results.append({"filePath": str(file), "messages": [message]})
        return SimpleNamespace(stdout=json.dumps({"results": results}), stderr="", returncode=1)

    for module in (runner_mod, eslint_mod):
        monkeypatch.setattr(module, "run_process", run)
    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        yield tmp_path, type_aware, runs


def _files(result, root: Path) -> list[str]:
    return sorted(Path(f).relative_to(root).as_posix() for f in result.checked_files or [])


def test_nested_config_is_linted_from_its_own_directory(workspace):
    root, _type_aware, runs = workspace
    _write(root, "src/a.ts")
    _write(root, "packages/p/eslint.config.js")
    _write(root, "packages/p/src/x.ts")
    _write(root, "packages/p/package.json", '{"name": "p"}')

    default = detect_lint_result(root)
    runs.clear()
    result = detect_lint_result(root, monorepo=Budget(60, 1024))

    assert _files(default, root) == ["src/a.ts"]
    assert "under other lint configs" in default.coverage.summary
    assert _files(result, root) == ["packages/p/src/x.ts", "src/a.ts"]
    assert runs == [(".", ["."]), ("packages/p", ["."])]
    assert result.coverage is None
    assert [(p.directory.relative_to(root).as_posix(), p.skipped) for p in result.packages] == [
        ("packages/p", None)
    ]
    assert len({(e["file"], e["rule"], e["line"]) for e in result.entries}) == len(result.entries) == 2


def test_type_aware_root_config_runs_once_per_package_under_the_limit(workspace):
    root, type_aware, runs = workspace
    type_aware.add(root)
    _write(root, "scripts/build.ts")
    for name, count in (("a", 2), ("b", 3)):
        _write(root, f"packages/{name}/package.json", f'{{"name": "{name}"}}')
        for i in range(count):
            _write(root, f"packages/{name}/src/f{i}.ts")

    default = detect_lint_result(root, type_aware_max_files=2)
    runs.clear()
    result = detect_lint_result(root, type_aware_max_files=2, monorepo=Budget(60, 1024))

    assert default.checked_files is None and default.coverage.reason == "type_aware_too_large"
    assert runs == [(".", ["packages/a"]), (".", ["."])]
    assert _files(result, root) == ["packages/a/src/f0.ts", "packages/a/src/f1.ts", "scripts/build.ts"]
    assert [(p.directory.relative_to(root).as_posix() or ".", p.skipped) for p in result.packages] == [
        ("packages/a", None),
        ("packages/b", "type_aware_too_large"),
        (".", None),
    ]
    summary = result.coverage.summary
    assert "1 of 3 packages were not linted (3 files; type-aware and over the file limit: packages/b)" in summary
    assert "uses type information and the scan has 6 files" not in summary


def test_spent_budget_skips_the_remaining_packages(workspace):
    root, _type_aware, runs = workspace
    _write(root, "src/a.ts")
    for name in ("p", "q"):
        _write(root, f"packages/{name}/eslint.config.js")
        _write(root, f"packages/{name}/x.ts")

    result = detect_lint_result(root, monorepo=Budget(1e-9, 1024))

    assert [(p.directory.name, p.skipped) for p in result.packages] == [("p", None), ("q", "time_budget")]
    assert "time budget spent: packages/q" in result.coverage.summary
    assert ("packages/q", ["."]) not in runs
