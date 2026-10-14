"""The dead-export fallback used when Knip isn't installed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from desloppify_ts.base.discovery.source import clear_source_file_cache_for_tests
from desloppify_ts.base.runtime_state import RuntimeContext, runtime_scope
from desloppify_ts.languages.typescript.detectors import exports as exports_mod
from desloppify_ts.languages.typescript.detectors.deps.resolver import (
    clear_resolver_cache,
)
from desloppify_ts.languages.typescript.detectors.exports_fallback import (
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
    from desloppify_ts.base.discovery.file_paths import rel
    from desloppify_ts.engine.policy.zones import FileZoneMap
    from desloppify_ts.languages.framework import get_lang

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


def _zoned_dead(root: Path, scan_path: Path) -> set[tuple[str, str]]:
    """Dead exports for a scan of *scan_path*, with the zone map scan builds."""
    from desloppify_ts.base.discovery.file_paths import rel
    from desloppify_ts.engine.policy.zones import FileZoneMap
    from desloppify_ts.languages.framework import get_lang, make_lang_run

    lang = make_lang_run(get_lang("typescript"))
    zone_map = FileZoneMap(lang.file_finder(scan_path), lang.zone_rules, rel_fn=rel)
    found = detect_dead_exports_fallback(scan_path, zone_map)
    assert found is not None
    return {(e["file"], e["name"]) for e in found}


_SETTINGS = (
    "export const APP_NAME = 'Example';\n"
    "export const SITE_URL = 'https://example.test';\n"
    "export const UNUSED_SETTING = 'unused';\n"
)


@needs_treesitter
@pytest.mark.parametrize(
    ("importer", "text"),
    [
        (
            "app.config.ts",
            "import { SITE_URL } from './src/lib/settings';\n"
            "export default { server: { url: SITE_URL } };\n",
        ),
        (
            "test/settings.test.ts",
            "import { SITE_URL } from '../src/lib/settings';\nSITE_URL;\n",
        ),
        (
            "scripts/seed.ts",
            "import { SITE_URL } from '@/lib/settings';\nSITE_URL;\n",
        ),
        (
            "e2e/support.ts",
            "export { SITE_URL as siteUrl } from '../src/lib/settings';\n",
        ),
    ],
    ids=["root-config", "test-dir", "alias-from-scripts", "reexport-from-e2e"],
)
def test_importers_outside_the_scan_path_count(project, importer, text):
    _write(
        project,
        {
            "tsconfig.json": json.dumps(
                {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}}
            ),
            "src/main.ts": "import { APP_NAME } from './lib/settings';\nAPP_NAME;\n",
            "src/lib/settings.ts": _SETTINGS,
            "scripts/unused.ts": "export const notScanned = 1;\n",
            importer: text,
        },
    )
    assert _zoned_dead(project, project / "src") == {
        ("src/lib/settings.ts", "UNUSED_SETTING")
    }


@needs_treesitter
def test_files_outside_the_scan_path_stay_out_of_the_parse_cache(project):
    from desloppify_ts.languages._framework.treesitter.cache import (
        current_parse_tree_cache,
        disable_parse_cache,
        enable_parse_cache,
    )

    _write(
        project,
        {
            "src/main.ts": "import { APP_NAME } from './lib/settings';\nAPP_NAME;\n",
            "src/lib/settings.ts": _SETTINGS,
            "test/settings.test.ts": (
                "import { SITE_URL } from '../src/lib/settings';\nSITE_URL;\n"
            ),
        },
    )
    enable_parse_cache()
    try:
        assert _zoned_dead(project, project / "src") == {
            ("src/lib/settings.ts", "UNUSED_SETTING")
        }
        cached = {
            Path(p).relative_to(project).as_posix()
            for p, _grammar in current_parse_tree_cache()._trees
        }
    finally:
        disable_parse_cache()
    assert cached == {"src/lib/settings.ts", "src/main.ts"}


@needs_treesitter
def test_config_globs_name_loaded_modules_only(project):
    _write(
        project,
        {
            "vitest.config.ts": (
                "export default { test: { projects: ['./packages/*'],\n"
                "  coverage: { include: ['./packages/**/*.ts'] } } };\n"
            ),
            "tailwind.config.ts": (
                "export default { content: ['./packages/**/*.{ts,tsx}'] };\n"
            ),
            "drizzle.config.ts": (
                "export default { schema: './packages/a/src/db/*.ts' };\n"
            ),
            "packages/a/src/main.ts": (
                "import { used } from './lib';\nimport { users } from './db/users';\n"
                "used; users;\n"
            ),
            "packages/a/src/lib.ts": "export const used = 1;\nexport const DEAD = 2;\n",
            "packages/a/src/db/users.ts": (
                "export const users = 1;\nexport const userIndex = 2;\n"
            ),
        },
    )
    assert _zoned_dead(project, project / "packages") == {
        ("packages/a/src/lib.ts", "DEAD")
    }


@needs_treesitter
def test_shadcn_ui_kit_exports_are_library_api(project, monkeypatch):
    _write(
        project,
        {
            "components.json": json.dumps({"aliases": {"ui": "@/components/ui"}}),
            "src/main.ts": (
                "import { Button } from './components/ui/button';\n"
                "import { used } from './lib';\nButton; used;\n"
            ),
            "src/components/ui/button.tsx": (
                "export const Button = 1;\nexport const buttonVariants = 2;\n"
            ),
            "src/lib.ts": "export const used = 1;\nexport const DEAD = 2;\n",
        },
    )
    monkeypatch.setattr(
        exports_mod,
        "detect_with_knip_result",
        lambda *_a, **_k: (None, "knip_not_installed"),
    )
    entries, _total, _coverage = exports_mod.detect_dead_exports_result(project)
    assert [(e["file"], e["name"]) for e in entries] == [("src/lib.ts", "DEAD")]
