"""Entry conventions and aliases of Nuxt, Vue + Vite plugins, SvelteKit and Astro."""

from __future__ import annotations

from pathlib import Path

import desloppify.languages.typescript.detectors.deps as deps_detector_mod
import desloppify.languages.typescript.detectors.deps.resolve as deps_resolve_mod
from desloppify.engine.detectors.orphaned import (
    EntryConventions,
    OrphanedDetectionOptions,
    detect_orphaned_files,
)
from desloppify.languages._framework.frameworks.registry import (
    framework_entry_conventions,
    get_framework_spec,
)
from desloppify.languages.typescript.detectors.deps.auto_imports import (
    auto_import_entries,
)
from desloppify.languages.typescript.detectors.deps.resolver import (
    ModuleResolver,
    clear_resolver_cache,
)

_FILLER = "\n".join(f"export const v{i} = {i}" for i in range(12)) + "\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _orphans(root: Path) -> list[str]:
    deps_resolve_mod.load_tsconfig_paths_cached.cache_clear()
    clear_resolver_cache()
    graph = deps_detector_mod.build_dep_graph(root)
    entries, _ = detect_orphaned_files(
        root,
        graph,
        extensions=[".ts", ".js"],
        options=OrphanedDetectionOptions(
            entry_files=auto_import_entries(root), entry_conventions=framework_entry_conventions()
        ),
    )
    return sorted(Path(e["file"]).relative_to(root.resolve()).as_posix() for e in entries)


def test_specs_are_registered_with_their_conventions():
    # Vue's plugins each bring their own conventions.
    assert len(framework_entry_conventions()) >= 7
    for framework_id in ("nextjs", "nuxt", "vue", "sveltekit", "astro"):
        spec = get_framework_spec(framework_id)
        assert spec is not None and spec.entry_conventions


def test_entry_convention_matching(tmp_path):
    conventions = EntryConventions(
        config_files=("x.config.ts",),
        extensions=frozenset({".ts"}),
        route_dir="routes",
        route_stem_prefix="+",
        entry_dirs=("src/params",),
        entry_paths=frozenset({"src/content/config"}),
        dependencies=("x",),
    )
    assert conventions.is_entry("src/routes/blog/+page.server.ts")
    assert conventions.is_entry("src/routes/(app)/+layout@.ts")
    assert not conventions.is_entry("src/routes/blog/helpers.ts")
    assert not conventions.is_entry("src/lib/+page.ts")
    assert conventions.is_entry("src/params/integer.ts")
    assert conventions.is_entry("src/content/config.ts")
    assert not conventions.is_entry("src/content/config.css")
    (tmp_path / "x.config.ts").write_text("")
    assert not conventions.applies_to(tmp_path)
    (tmp_path / "package.json").write_text('{"devDependencies": {"x": "1"}}')
    assert conventions.applies_to(tmp_path)


def test_nuxt_aliases_and_virtual_modules(tmp_path, set_project_root):
    _write(
        tmp_path,
        {
            "nuxt.config.ts": "export default {}\n",
            "app/app.vue": "<template><p/></template>\n",
            "app/utils/format.ts": _FILLER,
            "shared/types.ts": _FILLER,
            "server/utils/db.ts": _FILLER,
        },
    )
    clear_resolver_cache()
    resolver = ModuleResolver(tmp_path, tmp_path)
    importer = str(tmp_path / "app/pages/index.ts")
    assert resolver.resolve("~/utils/format", importer) == str((tmp_path / "app/utils/format.ts").resolve())
    assert resolver.resolve("@/utils/format", importer) == str((tmp_path / "app/utils/format.ts").resolve())
    assert resolver.resolve("~~/server/utils/db", importer) == str((tmp_path / "server/utils/db.ts").resolve())
    assert resolver.resolve("#shared/types", importer) == str((tmp_path / "shared/types.ts").resolve())
    assert resolver.is_external("#imports", importer)
    assert not resolver.is_external("~/utils/missing", importer)


def test_sveltekit_aliases_and_virtual_modules(tmp_path, set_project_root):
    _write(tmp_path, {"svelte.config.js": "export default {}\n", "src/lib/api.ts": _FILLER, "src/lib/index.ts": _FILLER})
    clear_resolver_cache()
    resolver = ModuleResolver(tmp_path, tmp_path)
    importer = str(tmp_path / "src/routes/+page.ts")
    assert resolver.resolve("$lib/api", importer) == str((tmp_path / "src/lib/api.ts").resolve())
    assert resolver.resolve("$lib", importer) == str((tmp_path / "src/lib/index.ts").resolve())
    assert resolver.is_external("$app/navigation", importer)
    assert resolver.is_external("$env/static/private", importer)
    assert not resolver.is_external("$app/navigation", str(tmp_path.parent / "elsewhere.ts"))


def test_nuxt_conventions(tmp_path, set_project_root):
    _write(
        tmp_path,
        {
            "package.json": '{"devDependencies": {"nuxt": "^4.0.0"}}',
            "nuxt.config.ts": "export default {}\n",
            "app/app.vue": "<template><p/></template>\n",
            "app/composables/useThing.ts": _FILLER,
            "app/plugins/scroll.client.ts": _FILLER,
            "app/middleware/auth.ts": _FILLER,
            "app/pages/index.ts": "import { LIST } from '~/constants/lists'\nexport default LIST\n" + _FILLER,
            "app/constants/lists.ts": _FILLER,
            "server/api/hello.ts": _FILLER,
            "shared/utils/slug.ts": _FILLER,
            "app/lib/dead.ts": _FILLER,
        },
    )
    assert _orphans(tmp_path) == ["app/lib/dead.ts"]


def test_sveltekit_conventions(tmp_path, set_project_root):
    _write(
        tmp_path,
        {
            "package.json": '{"devDependencies": {"@sveltejs/kit": "^2.0.0"}}',
            "svelte.config.js": "export default {}\n",
            "src/routes/+page.server.ts": "import { api } from '$lib/api'\nexport const load = api\n" + _FILLER,
            "src/routes/blog/[slug]/+page.ts": _FILLER,
            "src/routes/api/health/+server.ts": _FILLER,
            "src/hooks.server.ts": _FILLER,
            "src/hooks.client.ts": _FILLER,
            "src/params/integer.ts": _FILLER,
            "src/lib/api.ts": _FILLER,
            "src/lib/dead.ts": _FILLER,
        },
    )
    assert _orphans(tmp_path) == ["src/lib/dead.ts"]


def test_sveltekit_conventions_need_the_kit_dependency(tmp_path, set_project_root):
    _write(
        tmp_path,
        {
            "package.json": '{"devDependencies": {"svelte": "^5.0.0"}}',
            "svelte.config.js": "export default {}\n",
            "src/routes/+page.ts": _FILLER,
        },
    )
    assert _orphans(tmp_path) == ["src/routes/+page.ts"]


def test_astro_conventions(tmp_path, set_project_root):
    _write(
        tmp_path,
        {
            "astro.config.mjs": "export default {}\n",
            "src/pages/rss.xml.js": _FILLER,
            "src/pages/api/data.ts": _FILLER,
            "src/content.config.ts": _FILLER,
            "src/middleware.ts": _FILLER,
            "src/actions/index.ts": _FILLER,
            "src/utils/dead.ts": _FILLER,
        },
    )
    assert _orphans(tmp_path) == ["src/utils/dead.ts"]


def test_vue_plugin_registries_and_conventions(tmp_path, set_project_root):
    _write(
        tmp_path,
        {
            "package.json": '{"dependencies": {"vue": "^3"}, "devDependencies": {"unplugin-auto-import": "^1"}}',
            "vite.config.ts": "export default {}\n",
            "src/auto-imports.d.ts": (
                "// Generated by unplugin-auto-import\nexport {}\ndeclare global {\n"
                "  const useUserStore: typeof import('./stores/user').useUserStore\n"
                "  const ref: typeof import('vue').ref\n}\n"
            ),
            "src/stores/user.ts": _FILLER,
            # Not generated by a plugin: its imports are no registry.
            "src/shims.d.ts": "declare const x: typeof import('./legacy/old').x\n",
            "src/legacy/old.ts": _FILLER,
            # No vue-router or page plugin: src/pages/ isn't routed by convention.
            "src/pages/home.ts": _FILLER,
        },
    )
    assert _orphans(tmp_path) == ["src/legacy/old.ts", "src/pages/home.ts"]
