"""The dead-export fallback used when Knip isn't installed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from desloppify.base.discovery.source import clear_source_file_cache_for_tests
from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.languages.typescript.detectors import exports as exports_mod
from desloppify.languages.typescript.detectors.deps.resolver import (
    clear_resolver_cache,
)
from desloppify.languages.typescript.detectors.exports_fallback import (
    detect_dead_exports_fallback,
)

needs_treesitter = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_language_pack") is None,
    reason="needs tree-sitter",
)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"name": "app", "private": True}))
    clear_source_file_cache_for_tests()
    clear_resolver_cache()
    with runtime_scope(RuntimeContext(project_root=root)):
        yield root
    clear_source_file_cache_for_tests()
    clear_resolver_cache()


def _write(root: Path, files: dict[str, str]) -> None:
    for rel_path, text in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _dead(root: Path) -> set[tuple[str, str]]:
    found = detect_dead_exports_fallback(root)
    assert found is not None
    return {(e["file"], e["name"]) for e in found}


@needs_treesitter
def test_reports_only_names_nobody_imports(project):
    _write(
        project,
        {
            "src/main.ts": "import { used } from './lib/constants';\nused;\n",
            "src/lib/constants.ts": (
                "export const used = 1;\n"
                "export const MAX_PIXELS = 2;\n"
                "export function helper() { return MAX_PIXELS; }\n"
            ),
        },
    )
    assert _dead(project) == {
        ("src/lib/constants.ts", "MAX_PIXELS"),
        ("src/lib/constants.ts", "helper"),
    }


@needs_treesitter
def test_follows_reexport_chains(project):
    _write(
        project,
        {
            "src/main.ts": "import { a, b } from './barrel';\na; b;\n",
            "src/barrel.ts": "export * from './inner';\nexport { b } from './other';\n",
            "src/inner.ts": "export const a = 1;\nexport const unusedInner = 2;\n",
            "src/other.ts": "export const b = 1;\nexport const c = 2;\n",
        },
    )
    assert _dead(project) == {
        ("src/inner.ts", "unusedInner"),
        ("src/other.ts", "c"),
    }


@needs_treesitter
def test_namespace_dynamic_and_require_use_every_export(project):
    _write(
        project,
        {
            "src/main.ts": (
                "import * as ns from './ns';\n"
                "const lazy = await import('./lazy');\n"
                "const { picked } = await import('./picked');\n"
                "const cjs = require('./cjs');\n"
                "ns; lazy; picked; cjs;\n"
            ),
            "src/ns.ts": "export const x = 1;\nexport const y = 2;\n",
            "src/lazy.ts": "export const x = 1;\n",
            "src/picked.ts": "export const picked = 1;\nexport const left = 2;\n",
            "src/cjs.ts": "export const x = 1;\n",
        },
    )
    assert _dead(project) == {("src/picked.ts", "left")}


@needs_treesitter
def test_entry_files_and_manifest_targets_are_used(project):
    (project / "package.json").write_text(
        json.dumps({"name": "app", "main": "src/index.ts", "bin": "src/cli.ts"})
    )
    _write(
        project,
        {
            "src/index.ts": "export const api = 1;\nexport { helper } from './util';\n",
            "src/util.ts": "export const helper = 1;\n",
            "src/cli.ts": "import { helper } from './util';\nexport const flag = helper;\n",
            "src/main.ts": "export const boot = 1;\n",
            "src/feature.test.ts": "import { x } from './feature';\nexport const t = x;\n",
            "src/feature.ts": "export const x = 1;\n",
        },
    )
    assert _dead(project) == set()


@needs_treesitter
def test_types_follow_knips_rules(project):
    _write(
        project,
        {
            "src/main.tsx": "import { Row, makeRow } from './row';\nRow; makeRow;\n",
            "src/row.tsx": (
                "export interface RowProps { label: string }\n"
                "export function Row(props: RowProps) { return props.label; }\n"
                "export interface Cell { v: number }\n"
                "export interface Grid { cells: Cell[] }\n"
                "export type Unused = string;\n"
                "export const makeRow = () => 1;\n"
            ),
        },
    )
    assert _dead(project) == {
        ("src/row.tsx", "Cell"),
        ("src/row.tsx", "Grid"),
        ("src/row.tsx", "Unused"),
    }


@needs_treesitter
def test_unresolved_alias_and_orphans_are_left_alone(project):
    _write(
        project,
        {
            "src/main.ts": "import { a } from '~/lib/aliased';\nimport './used';\na;\n",
            "src/lib/aliased.ts": "export const a = 1;\nexport const b = 2;\n",
            "src/orphan.ts": "export const nobody = 1;\n",
            "src/used.ts": "export const sideEffectOnly = 1;\n",
        },
    )
    assert _dead(project) == {("src/used.ts", "sideEffectOnly")}


@needs_treesitter
def test_config_file_paths_count_as_used(project):
    _write(
        project,
        {
            "drizzle.config.ts": "export default { schema: './src/db/schema.ts' };\n",
            "src/main.ts": "import { users } from './db/schema';\nusers;\n",
            "src/db/schema.ts": "export const users = 1;\nexport const posts = 2;\n",
        },
    )
    from desloppify.base.discovery.file_paths import rel
    from desloppify.engine.policy.zones import FileZoneMap
    from desloppify.languages.framework import get_lang

    lang = get_lang("typescript")
    files = [str(p) for p in project.rglob("*.ts")]
    zone_map = FileZoneMap(files, lang.zone_rules, rel_fn=rel)
    found = detect_dead_exports_fallback(project, zone_map)
    assert found == []


@needs_treesitter
def test_exports_detector_falls_back_without_knip(project, monkeypatch):
    _write(
        project,
        {
            "src/main.ts": "import { used } from './constants';\nused;\n",
            "src/constants.ts": "export const used = 1;\nexport const DEAD = 2;\n",
        },
    )
    monkeypatch.setattr(
        exports_mod,
        "detect_with_knip_result",
        lambda *_a, **_k: (None, "knip_not_installed"),
    )
    entries, _total, coverage = exports_mod.detect_dead_exports_result(project)
    assert [(e["name"], e["source"]) for e in entries] == [("DEAD", "fallback")]
    assert coverage is not None
    assert coverage.status == "reduced"
    assert coverage.reason == "knip_not_installed"
    assert "npm i -D knip" in coverage.remediation
    assert "conservative" in coverage.summary
