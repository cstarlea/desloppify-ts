"""Tests for workspace package discovery, package entry points and workspace imports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import desloppify.base.discovery.paths as paths_api_mod
import desloppify.languages.typescript.detectors.deps as deps_detector_mod
import desloppify.languages.typescript.detectors.deps.resolve as deps_resolve_mod
from desloppify.engine.detectors.orphaned import (
    OrphanedDetectionOptions,
    detect_orphaned_files,
)
from desloppify.languages._framework.frameworks.registry import framework_entry_conventions
from desloppify.languages.typescript.detectors.deps.packages import (
    WorkspaceResolver,
    discover_packages,
    package_entries,
)
from desloppify.languages.typescript.detectors.deps.resolver import ModuleResolver

_BODY = "".join(f"export const v{i} = {i};\n" for i in range(12))


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root, monkeypatch):
    monkeypatch.setattr(paths_api_mod, "SRC_PATH", tmp_path / "src")
    deps_resolve_mod.load_tsconfig_paths_cached.cache_clear()


def _write(root: Path, name: str, content: str = "export const x = 1;\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _manifest(root: Path, directory: str, **fields) -> None:
    _write(root, f"{directory}/package.json".lstrip("/"), json.dumps(fields))


def _key(root: Path, name: str) -> str:
    return str((root / name).resolve())


def _names(packages) -> set[str | None]:
    return {p.name for p in packages}


# ── discovery ────────────────────────────────────────────────


def test_pnpm_workspace_globs_and_negation(tmp_path):
    _write(
        tmp_path,
        "pnpm-workspace.yaml",
        "packages:\n"
        "  - 'packages/*'  # libraries\n"
        '  - "apps/**"\n'
        "  - '!packages/internal'\n"
        "catalog:\n"
        "  react: ^19\n",
    )
    _manifest(tmp_path, "", name="root")
    _manifest(tmp_path, "packages/ui", name="@acme/ui")
    _manifest(tmp_path, "packages/internal", name="@acme/internal")
    _manifest(tmp_path, "apps/web", name="web")
    _manifest(tmp_path, "apps/group/admin", name="admin")
    _manifest(tmp_path, "packages/ui/node_modules/dep", name="dep")
    assert _names(discover_packages(tmp_path, tmp_path)) == {"root", "@acme/ui", "web", "admin"}


def test_pnpm_inline_list(tmp_path):
    _write(tmp_path, "pnpm-workspace.yaml", "packages: [libs/*]\n")
    _manifest(tmp_path, "libs/a", name="a")
    assert _names(discover_packages(tmp_path, tmp_path)) == {"a"}


def test_yarn_workspaces_object_form(tmp_path):
    _manifest(tmp_path, "", name="root", workspaces={"packages": ["packages/*"]})
    _manifest(tmp_path, "packages/core", name="core")
    assert _names(discover_packages(tmp_path, tmp_path)) == {"root", "core"}


def test_scanning_one_package_still_sees_the_workspace(tmp_path):
    _manifest(tmp_path, "", name="root", workspaces=["packages/*"])
    _manifest(tmp_path, "packages/a", name="a")
    _manifest(tmp_path, "packages/b", name="b")
    packages = discover_packages(tmp_path / "packages" / "a", tmp_path)
    assert _names(packages) == {"root", "a", "b"}


def test_without_workspaces_only_the_enclosing_package(tmp_path):
    _manifest(tmp_path, "", name="app")
    _manifest(tmp_path, "vendor/lib", name="lib")
    assert _names(discover_packages(tmp_path, tmp_path)) == {"app"}


# ── workspace resolution ─────────────────────────────────────


def _resolver(root: Path) -> WorkspaceResolver:
    return WorkspaceResolver(discover_packages(root, root))


def _workspace(root: Path) -> None:
    _manifest(root, "", name="root", workspaces=["packages/*"])


def test_exports_conditions_subpaths_and_patterns(tmp_path):
    _workspace(tmp_path)
    _manifest(
        tmp_path,
        "packages/ui",
        name="@acme/ui",
        exports={
            ".": {"types": "./dist/index.d.ts", "import": "./dist/index.js"},
            "./button": "./src/button.tsx",
            "./icons/*": "./src/icons/*.tsx",
            "./internal/*": None,
        },
    )
    _write(tmp_path, "packages/ui/src/index.ts")
    _write(tmp_path, "packages/ui/src/button.tsx")
    _write(tmp_path, "packages/ui/src/icons/arrow.tsx")
    resolver = _resolver(tmp_path)
    assert resolver.resolve("@acme/ui") == _key(tmp_path, "packages/ui/src/index.ts")
    assert resolver.resolve("@acme/ui/button") == _key(tmp_path, "packages/ui/src/button.tsx")
    assert resolver.resolve("@acme/ui/icons/arrow") == _key(
        tmp_path, "packages/ui/src/icons/arrow.tsx"
    )
    # Not exported: Node refuses it, so it is not an edge.
    assert resolver.resolve("@acme/ui/src/button") is None


def test_main_is_mapped_from_out_dir_to_root_dir(tmp_path):
    _workspace(tmp_path)
    _manifest(tmp_path, "packages/core", name="core", main="./build/lib/index.js")
    _write(
        tmp_path,
        "packages/core/tsconfig.json",
        '{ "compilerOptions": { "rootDir": "./sources", "outDir": "./build/lib" } }',
    )
    _write(tmp_path, "packages/core/sources/index.ts")
    assert _resolver(tmp_path).resolve("core") == _key(tmp_path, "packages/core/sources/index.ts")


def test_out_dir_inherited_through_extends(tmp_path):
    _workspace(tmp_path)
    _write(tmp_path, "tsconfig.lib.json", '{ "compilerOptions": { "outDir": "packages/core/out" } }')
    _write(tmp_path, "packages/core/tsconfig.json", '{ "extends": "../../tsconfig.lib.json" }')
    _manifest(tmp_path, "packages/core", name="core", types="./out/index.d.ts")
    _write(tmp_path, "packages/core/src/index.ts")
    assert _resolver(tmp_path).resolve("core") == _key(tmp_path, "packages/core/src/index.ts")


def test_common_output_dir_fallback_without_tsconfig(tmp_path):
    _workspace(tmp_path)
    _manifest(tmp_path, "packages/core", name="core", main="dist/esm/index.mjs")
    _write(tmp_path, "packages/core/src/index.mts")
    assert _resolver(tmp_path).resolve("core") == _key(tmp_path, "packages/core/src/index.mts")


def test_subpath_without_exports_is_a_package_relative_path(tmp_path):
    _workspace(tmp_path)
    _manifest(tmp_path, "packages/core", name="core")
    _write(tmp_path, "packages/core/utils/strings.ts")
    assert _resolver(tmp_path).resolve("core/utils/strings") == _key(
        tmp_path, "packages/core/utils/strings.ts"
    )


def test_longest_package_name_wins(tmp_path):
    _workspace(tmp_path)
    _manifest(tmp_path, "packages/ui", name="@acme/ui", main="./index.ts")
    _manifest(tmp_path, "packages/ui-kit", name="@acme/ui-kit", main="./index.ts")
    _write(tmp_path, "packages/ui/index.ts")
    _write(tmp_path, "packages/ui-kit/index.ts")
    assert _resolver(tmp_path).resolve("@acme/ui-kit") == _key(tmp_path, "packages/ui-kit/index.ts")


# ── package.json imports (#subpath) ──────────────────────────


def _module_resolver(root: Path) -> ModuleResolver:
    return ModuleResolver(root, root)


def test_imports_exact_keys(tmp_path):
    _manifest(tmp_path, "", name="app", imports={"#config": "./src/config/index.ts"})
    _write(tmp_path, "src/config/index.ts")
    resolver = _module_resolver(tmp_path)
    main = _key(tmp_path, "src/main.ts")
    assert resolver.resolve("#config", main) == _key(tmp_path, "src/config/index.ts")
    # An exact key does not match its subpaths, and unknown keys stay unresolved.
    assert resolver.resolve("#config/extra", main) is None
    assert resolver.resolve("#missing", main) is None


def test_imports_wildcard_keys(tmp_path):
    _manifest(
        tmp_path,
        "",
        name="app",
        imports={"#lib/*": "./src/lib/*.ts", "#lib/internal/*": "./src/private/*.ts"},
    )
    _write(tmp_path, "src/lib/format.ts")
    _write(tmp_path, "src/lib/nested/deep.ts")
    _write(tmp_path, "src/private/secret.ts")
    resolver = _module_resolver(tmp_path)
    main = _key(tmp_path, "src/main.ts")
    assert resolver.resolve("#lib/format", main) == _key(tmp_path, "src/lib/format.ts")
    # ``*`` spans directories, as in Node.
    assert resolver.resolve("#lib/nested/deep", main) == _key(tmp_path, "src/lib/nested/deep.ts")
    # The longest matching prefix wins.
    assert resolver.resolve("#lib/internal/secret", main) == _key(tmp_path, "src/private/secret.ts")


def test_imports_wildcard_target_without_extension(tmp_path):
    _manifest(tmp_path, "", name="app", imports={"#components/*": "./src/components/*"})
    _write(tmp_path, "src/components/Button.tsx")
    resolver = _module_resolver(tmp_path)
    assert resolver.resolve("#components/Button", _key(tmp_path, "main.ts")) == _key(
        tmp_path, "src/components/Button.tsx"
    )


def test_imports_condition_objects(tmp_path):
    _manifest(
        tmp_path,
        "",
        name="app",
        imports={
            "#types": {"types": "./dist/types.d.ts", "default": "./dist/types.js"},
            "#db": {"node": {"import": "./src/db/node.ts"}, "default": "./src/db/browser.ts"},
            "#fallback": {"import": "./src/missing.ts", "default": "./src/fallback.ts"},
            "#dep": {"default": "some-npm-package"},
        },
    )
    _write(tmp_path, "src/types.ts")
    _write(tmp_path, "src/db/node.ts")
    _write(tmp_path, "src/db/browser.ts")
    _write(tmp_path, "src/fallback.ts")
    resolver = _module_resolver(tmp_path)
    main = _key(tmp_path, "src/main.ts")
    # ``types`` points at build output; it maps back to the source file.
    assert resolver.resolve("#types", main) == _key(tmp_path, "src/types.ts")
    # Nested conditions resolve in order.
    assert resolver.resolve("#db", main) == _key(tmp_path, "src/db/node.ts")
    # A target that does not exist falls through to the next condition.
    assert resolver.resolve("#fallback", main) == _key(tmp_path, "src/fallback.ts")
    # A bare package target is not a file in the project.
    assert resolver.resolve("#dep", main) is None


def test_imports_bare_target_naming_a_workspace_package(tmp_path):
    _workspace(tmp_path)
    _manifest(tmp_path, "packages/ui", name="@acme/ui", exports="./src/index.ts")
    _write(tmp_path, "packages/ui/src/index.ts")
    _manifest(tmp_path, "packages/app", name="app", imports={"#ui": "@acme/ui"})
    resolver = _module_resolver(tmp_path)
    assert resolver.resolve("#ui", _key(tmp_path, "packages/app/src/main.ts")) == _key(
        tmp_path, "packages/ui/src/index.ts"
    )


def test_imports_come_from_the_nearest_package_json(tmp_path):
    _manifest(tmp_path, "", name="root", imports={"#util": "./shared/util.ts"})
    # A nested package.json is its own scope, even outside any workspace...
    _manifest(tmp_path, "tools/cli", name="cli", imports={"#util": "./lib/util.ts"})
    # ...and hides the parent's imports even when it has none, as in Node.
    _manifest(tmp_path, "vendor/lib", name="lib")
    _write(tmp_path, "shared/util.ts")
    _write(tmp_path, "tools/cli/lib/util.ts")
    resolver = _module_resolver(tmp_path)
    assert resolver.resolve("#util", _key(tmp_path, "src/main.ts")) == _key(tmp_path, "shared/util.ts")
    assert resolver.resolve("#util", _key(tmp_path, "tools/cli/src/run.ts")) == _key(
        tmp_path, "tools/cli/lib/util.ts"
    )
    assert resolver.resolve("#util", _key(tmp_path, "vendor/lib/index.ts")) is None


def test_imports_are_graph_edges(tmp_path):
    _manifest(tmp_path, "", name="app", imports={"#lib/*": "./src/lib/*.ts"})
    _write(tmp_path, "src/lib/analytics.ts")
    _write(tmp_path, "src/main.ts", "import { x } from '#lib/analytics';\n")
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    main = graph[_key(tmp_path, "src/main.ts")]
    assert main["imports"] == {_key(tmp_path, "src/lib/analytics.ts")}
    assert not main.get("unresolved_imports")


def test_imports_mapped_to_an_npm_package_are_external(tmp_path):
    _manifest(
        tmp_path,
        "",
        name="app",
        dependencies={"node-fetch": "^3", "undici": "^6"},
        imports={
            "#fetch": {"node": "node-fetch", "default": "./src/fetch-browser.ts"},
            "#http/*": "undici/*",
            "#fs": "node:fs",
            "#crypto": "crypto",
            "#undeclared": "left-pad",
            "#lib/*": "./src/lib/*.ts",
        },
    )
    _write(tmp_path, "src/lib/polyfill.ts")
    _write(
        tmp_path,
        "src/main.ts",
        "import '#fetch';\nimport '#http/agent';\nimport '#fs';\nimport '#crypto';\n"
        "import '#undeclared';\nimport '#lib/missing';\nimport '#nokey/polyfill';\n",
    )
    resolver = _module_resolver(tmp_path)
    main = _key(tmp_path, "src/main.ts")
    assert resolver.is_external("#fetch", main)
    assert resolver.is_external("#http/agent", main)
    assert resolver.is_external("#fs", main) and resolver.is_external("#crypto", main)
    # Like a bare import, a package nothing declares is not external.
    assert not resolver.is_external("#undeclared", main)
    assert not resolver.is_external("#lib/missing", main)
    assert not resolver.is_external("#fetch")

    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[main]["unresolved_imports"] == {"#undeclared", "#lib/missing", "#nokey/polyfill"}


# ── graph edges ──────────────────────────────────────────────


def test_workspace_import_is_a_graph_edge(tmp_path):
    _workspace(tmp_path)
    _manifest(tmp_path, "packages/ui", name="@acme/ui", exports="./src/index.ts")
    _write(tmp_path, "packages/ui/src/index.ts")
    _write(tmp_path, "packages/app/src/main.ts", "import { x } from '@acme/ui';\n")
    _manifest(tmp_path, "packages/app", name="app")
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "packages/app/src/main.ts")]["imports"] == {
        _key(tmp_path, "packages/ui/src/index.ts")
    }


def test_tsconfig_paths_take_precedence_over_workspace(tmp_path):
    _workspace(tmp_path)
    _write(
        tmp_path,
        "tsconfig.json",
        '{ "compilerOptions": { "paths": { "@acme/ui": ["./packages/ui/src/dev.ts"] } } }',
    )
    _manifest(tmp_path, "packages/ui", name="@acme/ui", exports="./src/index.ts")
    _write(tmp_path, "packages/ui/src/index.ts")
    _write(tmp_path, "packages/ui/src/dev.ts")
    _write(tmp_path, "main.ts", "import { x } from '@acme/ui';\n")
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "main.ts")]["imports"] == {_key(tmp_path, "packages/ui/src/dev.ts")}


def test_aliases_come_from_the_nearest_tsconfig(tmp_path):
    _write(tmp_path, "tsconfig.json", '{ "compilerOptions": { "paths": { "~/*": ["./shared/*"] } } }')
    _write(tmp_path, "apps/web/tsconfig.json", '{ "compilerOptions": { "paths": { "~/*": ["./src/*"] } } }')
    _write(tmp_path, "shared/util.ts")
    _write(tmp_path, "apps/web/src/util.ts")
    _write(tmp_path, "apps/web/src/main.ts", "import { x } from '~/util';\n")
    _write(tmp_path, "tools/run.ts", "import { x } from '~/util';\n")
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "apps/web/src/main.ts")]["imports"] == {
        _key(tmp_path, "apps/web/src/util.ts")
    }
    assert graph[_key(tmp_path, "tools/run.ts")]["imports"] == {_key(tmp_path, "shared/util.ts")}


# ── entry points ─────────────────────────────────────────────


def _only_package(root: Path):
    (package,) = discover_packages(root, root)
    return package


def test_entries_from_exports_bin_and_scripts(tmp_path):
    _manifest(
        tmp_path,
        "",
        name="tool",
        exports={".": {"types": "./dist/index.d.ts", "default": "./dist/index.js"}},
        bin={"tool": "./dist/cli.js"},
        scripts={
            "dev": "tsx watch src/server",
            "seed": "node --import tsx ./src/seed.ts --force",
            "lint": "eslint --cache src",
            "test": "vitest run test/",
            "migrate": "typeorm --dataSource=src/data-source.ts --env-file=.env migration:run",
        },
    )
    _write(tmp_path, "tsconfig.json", '{ "compilerOptions": { "rootDir": "src", "outDir": "dist" } }')
    for name in ("index", "cli", "server", "seed", "data-source"):
        _write(tmp_path, f"src/{name}.ts")
    _write(tmp_path, "test/index.ts")
    entries = package_entries(_only_package(tmp_path), [])
    assert entries.public == {_key(tmp_path, "src/index.ts")}
    assert entries.run == {
        _key(tmp_path, "src/cli.ts"),
        _key(tmp_path, "src/server.ts"),
        _key(tmp_path, "src/seed.ts"),
        _key(tmp_path, "src/data-source.ts"),
    }


def test_wildcard_exports_expose_every_match(tmp_path):
    _manifest(tmp_path, "", name="lib", exports={"./locales/*": "./dist/locales/*.js"})
    _write(tmp_path, "tsconfig.json", '{ "compilerOptions": { "rootDir": "src", "outDir": "dist" } }')
    en = _write(tmp_path, "src/locales/en.ts")
    fr = _write(tmp_path, "src/locales/nested/fr.ts")
    other = _write(tmp_path, "src/other.ts")
    candidates = [str(p.resolve()) for p in (en, fr, other)]
    entries = package_entries(_only_package(tmp_path), candidates)
    assert entries.public == {str(en.resolve()), str(fr.resolve())}


def test_orphan_detection_skips_entry_files(tmp_path):
    cli = _write(tmp_path, "src/cli.ts", _BODY)
    dead = _write(tmp_path, "src/dead.ts", _BODY)
    graph = {
        str(p.resolve()): {"imports": set(), "importers": set(), "importer_count": 0}
        for p in (cli, dead)
    }
    entries, _ = detect_orphaned_files(
        tmp_path,
        graph,
        [".ts"],
        OrphanedDetectionOptions(entry_files={str(cli.resolve())}),
    )
    assert [e["file"] for e in entries] == [str(dead.resolve())]


def test_framework_conventions_detected_per_package(tmp_path):
    _write(tmp_path, "apps/site/next.config.ts", "export default {};\n")
    page = _write(tmp_path, "apps/site/app/page.tsx", _BODY)
    proxy = _write(tmp_path, "apps/site/proxy.ts", _BODY)
    other_page = _write(tmp_path, "apps/api/app/page.tsx", _BODY)
    graph = {
        str(p.resolve()): {"imports": set(), "importers": set(), "importer_count": 0}
        for p in (page, proxy, other_page)
    }
    entries, _ = detect_orphaned_files(
        tmp_path,
        graph,
        [".ts", ".tsx"],
        OrphanedDetectionOptions(
            package_roots=[tmp_path / "apps" / "site", tmp_path / "apps" / "api"],
            entry_conventions=framework_entry_conventions(),
        ),
    )
    assert [e["file"] for e in entries] == [str(other_page.resolve())]


def test_bundled_output_maps_to_any_source_extension(tmp_path):
    _workspace(tmp_path)
    _manifest(
        tmp_path,
        "packages/next",
        name="@trpc/next",
        exports={"./client": {"import": {"types": "./dist/client.d.mts", "default": "./dist/client.mjs"}}},
    )
    _write(tmp_path, "packages/next/src/client.ts")
    assert _resolver(tmp_path).resolve("@trpc/next/client") == _key(
        tmp_path, "packages/next/src/client.ts"
    )


# ── unresolved imports (orphan confidence) ───────────────────


def test_unresolved_bare_imports_exclude_dependencies_and_builtins(tmp_path):
    _manifest(tmp_path, "", name="app", dependencies={"react": "^19", "@tanstack/query": "^5"})
    _write(
        tmp_path,
        "main.ts",
        "import React from 'react';\n"
        "import { useQuery } from '@tanstack/query/react';\n"
        "import fs from 'fs';\n"
        "import path from 'node:path';\n"
        "import 'virtual:pwa-register';\n"
        "import { track } from '#lib/analytics';\n"
        "import { x } from '~/missing/thing';\n",
    )
    graph = deps_detector_mod.build_dep_graph(tmp_path)
    assert graph[_key(tmp_path, "main.ts")]["unresolved_imports"] == {
        "#lib/analytics",
        "~/missing/thing",
    }


def test_orphans_reachable_by_unresolved_imports_get_low_confidence(tmp_path):
    from desloppify.languages.typescript.phases_coupling import flag_unresolved_orphans

    main = str(tmp_path / "src" / "main.ts")
    graph = {main: {"unresolved_imports": {"#lib/analytics", "~/widgets"}}}
    entries = [
        {"file": str(tmp_path / "src" / "lib" / "analytics.ts"), "loc": 20},
        {"file": str(tmp_path / "src" / "widgets" / "index.ts"), "loc": 20},
        {"file": str(tmp_path / "src" / "lib" / "unrelated.ts"), "loc": 20},
    ]
    flag_unresolved_orphans(entries, graph)
    analytics, widgets, unrelated = entries
    assert analytics["confidence"] == widgets["confidence"] == "low"
    assert analytics["possible_importers"] == ["src/main.ts"]
    assert "confidence" not in unrelated
