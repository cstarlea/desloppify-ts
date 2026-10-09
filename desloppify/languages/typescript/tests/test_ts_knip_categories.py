"""Knip's categories beyond unused exports: the shared run, enum members,
duplicate exports, orphan corroboration and the dependencies detector."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import desloppify.languages.typescript.detectors.knip_adapter as knip_mod
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.base.discovery.source import clear_source_file_cache_for_tests
from desloppify.engine.policy.zones import Zone
from desloppify.languages.typescript.detectors.dependencies import (
    detect_dependencies,
    package_name,
)
from desloppify.languages.typescript.phases_coupling import corroborate_orphans_with_knip

_RUN = "desloppify.languages.typescript.detectors.knip_adapter.subprocess.run"


def _write(root: Path, name: str, text: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _row(file: str, **categories) -> dict:
    return {"file": file, **categories}


@pytest.fixture
def project(tmp_path):
    """An installed package with a local knip; ``report(*rows)`` sets Knip's JSON."""
    _write(tmp_path, "package.json", json.dumps({"name": "app"}))
    _write(tmp_path, "node_modules/.bin/knip")

    def report(*rows: dict, prefix: str = ""):
        stdout = prefix + json.dumps({"issues": list(rows)}) + "\n"
        return patch(_RUN, return_value=SimpleNamespace(stdout=stdout, stderr="", returncode=1))

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        clear_source_file_cache_for_tests()
        yield tmp_path, report
        clear_source_file_cache_for_tests()


def _install(root: Path, *names: str) -> None:
    for name in names:
        _write(root, f"node_modules/{name}/package.json", "{}")


# ── Shared run and report parsing ───────────────────────────────────────────


def test_one_knip_run_serves_every_reader(project):
    root, report = project
    cache: dict = {}
    with report() as run:
        knip_mod.run_knip(root, cache=cache)
        knip_mod.detect_with_knip_result(root, cache=cache)
        detect_dependencies(root, cache=cache)
    assert run.call_count == 1


def test_report_after_plugin_log_lines_is_parsed(project):
    """A plugin loading the project's config can print to stdout before the report."""
    root, report = project
    with report(_row("src/a.ts", exports=[{"name": "x", "line": 1}]), prefix="Using base URL\n") as _:
        entries, reason = knip_mod.detect_with_knip_result(root)
    assert reason is None
    assert [e["name"] for e in entries] == ["x"]


def test_crash_without_report_is_a_failure(project):
    root, _report = project
    output = SimpleNamespace(stdout="Using base URL\n", stderr="TypeError: boom", returncode=2)
    with patch(_RUN, return_value=output):
        assert knip_mod.run_knip(root).failure == "knip_bad_output"


# ── exports: enum members and duplicates ────────────────────────────────────


def test_enum_members_and_duplicates(project):
    root, report = project
    _write(
        root,
        "src/url.ts",
        "export function build() {}\n"
        "export const join = build;\n"
        "/** @deprecated Use build. */\n"
        "export const make = build;\n"
        "export const unusedAlias = build;\n",
    )
    rows = _row(
        "src/url.ts",
        exports=[{"name": "unusedAlias", "line": 5}],
        enumMembers=[{"namespace": "Method", "name": "Purge", "line": 9}],
        duplicates=[
            [
                {"name": "build", "line": 1},
                {"name": "join", "line": 2},
                {"name": "make", "line": 4},
                {"name": "unusedAlias", "line": 5},
            ]
        ],
    )
    with report(rows):
        entries, _ = knip_mod.detect_with_knip_result(root)
    assert [(e["name"], e["kind"]) for e in entries] == [
        ("unusedAlias", "export"),
        ("Method.Purge", "enum_member"),
        ("build=join", "duplicate"),
    ]


def test_deprecated_alias_is_not_a_duplicate(project):
    root, report = project
    _write(
        root,
        "src/link.ts",
        "export const link = () => 1;\n/**\n * @deprecated use {@link link}\n */\nexport const oldLink = link;\n",
    )
    rows = _row("src/link.ts", duplicates=[[{"name": "link", "line": 1}, {"name": "oldLink", "line": 5}]])
    with report(rows):
        entries, _ = knip_mod.detect_with_knip_result(root)
    assert entries == []


# ── orphaned corroboration ──────────────────────────────────────────────────


def _orphans(root: Path, *names: str, confidence: str = "medium") -> list[dict]:
    return [{"file": str(root / name), "loc": 20, "confidence": confidence} for name in names]


def test_knip_agreeing_raises_and_disagreeing_lowers_orphans(project):
    root, report = project
    lang = SimpleNamespace(runtime_cache={})
    entries = _orphans(root, "src/dead.ts", "src/story.ts")
    with report(_row("src/dead.ts", files=[{"name": "src/dead.ts"}])):
        corroborate_orphans_with_knip(entries, root, lang)
    assert [(e["confidence"], e["knip"]) for e in entries] == [("high", "unused"), ("low", "reachable")]


def test_orphan_stays_low_while_knip_cannot_resolve_its_importer(project):
    root, report = project
    lang = SimpleNamespace(runtime_cache={})
    entries = _orphans(root, "src/lib/format.ts", confidence="low")
    entries[0]["possible_importers"] = ["src/app.ts"]
    rows = (
        _row("src/lib/format.ts", files=[{"name": "src/lib/format.ts"}]),
        _row("src/app.ts", unresolved=[{"name": "~/lib/format", "line": 1}]),
    )
    with report(*rows):
        corroborate_orphans_with_knip(entries, root, lang)
    assert (entries[0]["confidence"], entries[0]["knip"]) == ("low", "unused")


def test_orphans_unchanged_when_knip_does_not_run(tmp_path):
    _write(tmp_path, "package.json", "{}")
    entries = _orphans(tmp_path, "src/dead.ts")
    corroborate_orphans_with_knip(entries, tmp_path, SimpleNamespace(runtime_cache={}))
    assert entries[0]["confidence"] == "medium" and "knip" not in entries[0]


# ── dependencies ────────────────────────────────────────────────────────────


def test_package_name():
    assert package_name("@scope/pkg/sub") == "@scope/pkg"
    assert package_name("lodash/fp") == "lodash"
    assert [package_name(s) for s in ("./a", "node:fs", "~/x", "#internal")] == [None] * 4


def test_unused_unlisted_and_binaries(project):
    root, report = project
    _write(
        root,
        "package.json",
        json.dumps(
            {
                "name": "app",
                "dependencies": {"left-pad": "1", "react": "1"},
                "devDependencies": {"prettier-plugin-x": "1", "@types/bench": "1"},
            }
        ),
    )
    _install(root, "left-pad", "react", "prettier-plugin-x", "@types/bench")
    _write(root, "src/app.ts", "import React from 'react';\nimport { x } from 'zod';\n")
    _write(root, "src/bench.ts", "import Benchmark from 'bench';\n")
    _write(root, "src/app.test.ts", "import { y } from 'vitest';\n")
    rows = (
        _row(
            "package.json",
            dependencies=[{"name": "left-pad", "line": 3}],
            devDependencies=[{"name": "prettier-plugin-x", "line": 6}, {"name": "@types/bench", "line": 7}],
            binaries=[{"name": "tsx"}],
        ),
        _row("src/app.ts", unlisted=[{"name": "zod", "line": 2}]),
        _row("src/app.test.ts", unlisted=[{"name": "vitest", "line": 1}]),
        _row("src/run.ts", binaries=[{"name": "openssl"}]),
    )
    zones = {"src/app.test.ts": Zone.TEST}
    zone_map = SimpleNamespace(get=lambda path: zones.get(path, Zone.PRODUCTION))
    with report(*rows):
        result = detect_dependencies(root, zone_map)

    assert result.coverage is None
    # @types/bench: its package is still imported (by a file Knip calls unused).
    assert [(e["kind"], e["name"], e["confidence"]) for e in result.entries] == [
        ("unused", "left-pad", "high"),
        ("unused_dev", "prettier-plugin-x", "medium"),
        ("unlisted_binary", "tsx", "medium"),
        ("unlisted", "vitest", "medium"),
        ("unlisted", "zod", "high"),
    ]
    assert result.entries[-1]["importers"] == ["src/app.ts:2"]
    assert result.population_size == 4 + 2


def test_dependency_a_script_or_manifest_key_uses_is_not_unused(project):
    """Knip doesn't parse task runners (``nub exec --node husky``) or see a bin
    named unlike its package (``attw``)."""
    root, report = project
    _write(
        root,
        "package.json",
        json.dumps(
            {
                "name": "app",
                "scripts": {"prepare": "nub exec --node husky", "check": "runner attw --pack ."},
                "lint-staged": {"*.ts": "biome check"},
                "devDependencies": {"husky": "9", "@arethetypeswrong/cli": "1", "lint-staged": "16", "globby": "16"},
            }
        ),
    )
    _install(root, "husky", "lint-staged", "globby")
    _write(root, "node_modules/@arethetypeswrong/cli/package.json", json.dumps({"bin": {"attw": "x.js"}}))
    _write(root, "src/app.ts", "export const a = 1;\n")
    unused = [{"name": n, "line": 1} for n in ("husky", "@arethetypeswrong/cli", "lint-staged", "globby")]
    with report(_row("package.json", devDependencies=unused)):
        result = detect_dependencies(root)
    assert [e["name"] for e in result.entries] == ["globby"]


def test_uninstalled_package_skips_unused_and_binaries(project):
    root, report = project
    _write(root, "package.json", json.dumps({"name": "app", "dependencies": {"next": "16"}}))
    _write(root, "src/app.ts", "import { x } from 'zod';\n")
    rows = (
        _row("package.json", dependencies=[{"name": "next", "line": 3}], binaries=[{"name": "next"}]),
        _row("src/app.ts", unlisted=[{"name": "zod", "line": 1}]),
    )
    with report(*rows):
        result = detect_dependencies(root)
    assert [(e["kind"], e["name"]) for e in result.entries] == [("unlisted", "zod")]
    assert result.coverage is not None and result.coverage.reason == "dependencies_not_installed"
    assert result.population_size == 1


def test_no_potential_when_knip_does_not_run(tmp_path):
    _write(tmp_path, "package.json", "{}")
    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        result = detect_dependencies(tmp_path)
    assert result.population_size is None
    assert result.coverage is not None and result.coverage.reason == "knip_not_installed"
