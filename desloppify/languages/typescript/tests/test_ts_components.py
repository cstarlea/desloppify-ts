"""Single-file components (.vue, .svelte, .astro): script extraction and the detectors on it."""

from __future__ import annotations

from pathlib import Path

import pytest

import desloppify.languages.typescript.detectors.deps as deps_detector_mod
import desloppify.languages.typescript.detectors.deps.resolve as deps_resolve_mod
from desloppify.base.discovery.sfc import (
    apply_view_change,
    code_text,
    is_sfc,
    script_blocks,
    sfc_code,
)
from desloppify.engine.detectors.orphaned import (
    OrphanedDetectionOptions,
    detect_orphaned_files,
)
from desloppify.languages._framework.frameworks.registry import (
    framework_entry_conventions,
)
from desloppify.languages.typescript._fixers import get_ts_fixers
from desloppify.languages.typescript.detectors.deps.auto_imports import (
    auto_import_entries,
)
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache
from desloppify.languages.typescript.detectors.io import iter_typescript_sources
from desloppify.languages.typescript.detectors.smells import detect_smells
from desloppify.languages.typescript.detectors.tsc import TscDiagnostic
from desloppify.languages.typescript.detectors.type_errors import _imports_component
from desloppify.languages.typescript.syntax.tree import get_parser, grammar_for
from desloppify.languages.typescript.syntax.validation import count_syntax_errors

needs_treesitter = pytest.mark.skipif(
    get_parser("tsx") is None, reason="needs tree-sitter"
)


def _codes(text: str, suffix: str) -> list[str]:
    return [text[b.start : b.end].strip() for b in script_blocks(text, suffix)]


# ── Extraction ──────────────────────────────────────────────


def test_vue_script_and_script_setup_with_attributes_in_any_order():
    text = (
        '<template>\n  <div :a="x > 1">{{ y }}</div>\n</template>\n'
        "<script lang=\"ts\">\nexport default { name: 'A' }\n</script>\n"
        "<script setup\n  lang='ts' generic=\"T extends string\">\nconst a = 1\n</script>\n"
        "<style scoped>\n.a { color: red }\n</style>\n"
    )
    blocks = script_blocks(text, ".vue")
    assert [(b.kind, b.lang) for b in blocks] == [("script", "ts"), ("setup", "ts")]
    assert _codes(text, ".vue") == ["export default { name: 'A' }", "const a = 1"]


def test_vue_skips_scripts_in_template_comments_and_custom_blocks():
    text = (
        "<!-- <script>bad()</script> -->\n"
        '<template>\n  <template v-if="ok"><script>bad()</script></template>\n'
        "  <p>a</p>\n</template>\n"
        "<docs>\n```html\n<script>bad()</script>\n```\n</docs>\n"
        "<script setup>\ngood()\n</script>\n"
    )
    assert _codes(text, ".vue") == ["good()"]


def test_src_scripts_have_no_code_and_keep_their_src():
    blocks = script_blocks(
        '<template><p/></template>\n<script src="./logic.ts" lang="ts"></script>\n',
        ".vue",
    )
    assert [(b.src, b.start == b.end) for b in blocks] == [("./logic.ts", True)]


def test_svelte_module_and_instance_scripts():
    text = (
        '<script context="module" lang="ts">\nexport const prerender = true\n</script>\n'
        '<script lang="ts" {...rest}>\nlet { a } = $props()\n</script>\n'
        "<svelte:head><script>window.analytics = 1</script></svelte:head>\n"
        "{#if a<b}<p>{a}</p>{/if}\n"
    )
    blocks = script_blocks(text, ".svelte")
    assert [(b.kind, b.lang) for b in blocks] == [("module", "ts"), ("script", "ts")]
    assert (
        script_blocks("<script module>\nlet x\n</script>", ".svelte")[0].kind
        == "module"
    )


def test_astro_frontmatter_and_scripts():
    text = (
        "---\nimport Layout from '../layouts/Layout.astro'\nconst { title } = Astro.props\n---\n"
        "<Layout title={title}>\n  <p>It's {title}</p>\n</Layout>\n"
        "<script>\nconst el = document.querySelector('p')!\n</script>\n"
        "<script is:inline>\nwindow.x = 1\n</script>\n"
        '<script type="application/ld+json">{"a": 1}</script>\n'
    )
    blocks = script_blocks(text, ".astro")
    assert [(b.kind, b.lang) for b in blocks] == [
        ("frontmatter", "ts"),
        ("client", "ts"),
        ("client", "js"),
    ]
    assert _codes(text, ".astro")[0].startswith("import Layout")
    crlf = "﻿" + text.replace("\n", "\r\n")
    assert [code.replace("\r\n", "\n") for code in _codes(crlf, ".astro")] == _codes(
        text, ".astro"
    )


def test_other_languages_and_unterminated_scripts_are_not_code():
    assert script_blocks('<script lang="coffee">\nx = 1\n</script>', ".vue") == ()
    assert script_blocks("<script>\nlet a = 1\n", ".svelte") == ()
    assert _codes("<SCRIPT>\nlet a = 1\n</Script >", ".svelte") == ["let a = 1"]


def test_code_view_keeps_every_line_and_column():
    text = '<template>\n  <p>é {{ a }}</p>\r\n</template>\n<script setup lang="ts">const a = 1\nconst b = a as any\n</script>\n'
    view = code_text(text, "Comp.vue")
    assert len(view) == len(text)
    assert view.splitlines() == [
        " " * len(line)
        if "const" not in line
        else line.replace('<script setup lang="ts">', " " * 24)
        for line in text.splitlines()
    ]
    assert view.splitlines()[4] == text.splitlines()[4] == "const b = a as any"
    assert code_text(text, "a.ts") == text


def test_grammar_follows_the_script_language(tmp_path):
    ts = tmp_path / "A.vue"
    ts.write_text('<script setup lang="ts">\nconst a = <number>b\n</script>\n')
    js = tmp_path / "B.svelte"
    js.write_text("<script>\nlet a = 1\n</script>\n")
    tsx = tmp_path / "C.vue"
    tsx.write_text('<script lang="tsx">\nexport default () => <div/>\n</script>\n')
    assert [grammar_for(p) for p in (ts, js, tsx)] == ["typescript", "tsx", "tsx"]
    assert is_sfc("x/Y.astro") and not is_sfc("x/y.ts")


def test_view_edits_carry_over_only_inside_script_blocks():
    text = "<template>\n  <p>{{ a }}</p>\n</template>\n<script setup>console.log('[A]')\nconst a = 1\nconsole.log('[B]')\n</script>\n"
    component = sfc_code(text, "A.vue")
    lines = component.view.splitlines(keepends=True)
    inside = "".join(line for line in lines if "[B]" not in line)
    assert apply_view_change(component, inside, "A.vue") == text.replace(
        "console.log('[B]')\n", ""
    )
    # Removing the whole first line would take the <script setup> tag with it.
    reaching = "".join(line for line in lines if "[A]" not in line)
    assert apply_view_change(component, reaching, "A.vue") is None


@needs_treesitter
def test_syntax_errors_are_counted_in_the_script_code():
    assert (
        count_syntax_errors(
            "<template><p>{{ a b }}</p></template>\n<script>\nlet a = 1\n</script>\n",
            "A.vue",
        )
        == 0
    )
    assert count_syntax_errors("<script>\nlet = = 1\n</script>\n", "A.vue")


def test_tsc_cannot_find_module_for_a_component_is_not_a_type_error():
    def diagnostic(specifier: str) -> TscDiagnostic:
        return TscDiagnostic(
            "src/main.ts",
            1,
            1,
            "TS2307",
            f"Cannot find module '{specifier}' or its type declarations.",
        )

    assert _imports_component(diagnostic("./App.vue"))
    assert not _imports_component(diagnostic("./missing"))


# ── Detectors ───────────────────────────────────────────────


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


_VUE = """\
<template>
  <div>
    <!-- const x = y as any -->
    <p>{{ (value as any).name }}</p>
  </div>
</template>

<script setup lang="ts">
import { helper } from './helper'

const value = helper() as any
</script>
"""


@needs_treesitter
def test_smells_report_the_line_in_the_component(tmp_path, set_project_root):
    _write(
        tmp_path,
        {"src/Comp.vue": _VUE, "src/helper.ts": "export const helper = () => 1\n"},
    )
    assert "src/Comp.vue" in iter_typescript_sources(tmp_path)
    entries, _ = detect_smells(tmp_path)
    matches = [m for e in entries if e["id"] == "as_any_cast" for m in e["matches"]]
    assert [(m["file"], m["line"]) for m in matches] == [("src/Comp.vue", 11)]


def _graph(root: Path) -> dict:
    deps_resolve_mod.load_tsconfig_paths_cached.cache_clear()
    clear_resolver_cache()
    return deps_detector_mod.build_dep_graph(root)


def _orphans(root: Path, graph: dict) -> list[str]:
    entries, _ = detect_orphaned_files(
        root,
        graph,
        extensions=[".ts", ".vue", ".svelte", ".astro"],
        options=OrphanedDetectionOptions(
            entry_files=auto_import_entries(root),
            entry_conventions=framework_entry_conventions(),
        ),
    )
    return sorted(
        Path(e["file"]).relative_to(root.resolve()).as_posix() for e in entries
    )


_FILLER = "\n".join(f"export const v{i} = {i}" for i in range(12)) + "\n"


def _component(script: str) -> str:
    return f'<template><p>x</p></template>\n<script setup lang="ts">\n{script}\n{_FILLER}</script>\n'


def test_components_are_graph_nodes_and_importers(tmp_path, set_project_root):
    _write(
        tmp_path,
        {
            "src/main.ts": "import App from './App.vue'\nexport default App\n",
            "src/App.vue": _component(
                "import Child from './Child.vue'\nimport { u } from './util'"
            ),
            "src/Child.vue": _component("const c = 1"),
            "src/Logic.vue": '<template><p/></template>\n<script src="./logic.ts"></script>\n'
            + "\n" * 10,
            "src/logic.ts": _FILLER,
            "src/util.ts": "export const u = 1\n" + _FILLER,
            "src/Dead.vue": _component("const dead = 1"),
        },
    )
    graph = _graph(tmp_path)
    key = lambda name: str((tmp_path / name).resolve())  # noqa: E731
    assert key("src/Child.vue") in graph[key("src/App.vue")]["imports"]
    assert key("src/util.ts") in graph[key("src/App.vue")]["imports"]
    assert key("src/logic.ts") in graph[key("src/Logic.vue")]["imports"]
    # Child.vue is imported only by another component; Dead.vue by nothing.
    assert _orphans(tmp_path, graph) == ["src/Dead.vue", "src/Logic.vue"]


def test_components_follow_the_framework_conventions(tmp_path, set_project_root):
    """Routes, pages and auto-imported components are entry points (see
    test_ts_framework_conventions); others are orphan candidates like any module."""
    _write(
        tmp_path,
        {
            "nuxt/package.json": '{"devDependencies": {"nuxt": "^4.0.0"}}',
            "nuxt/nuxt.config.ts": "export default {}\n",
            "nuxt/app/app.vue": _component("const a = 1"),
            "nuxt/app/components/Card.vue": _component("const c = 1"),
            "nuxt/app/pages/index.vue": _component(
                "import { LIST } from '~/constants/lists'"
            ),
            "nuxt/app/constants/lists.ts": _FILLER,
            "nuxt/app/legacy/Old.vue": _component("const o = 1"),
            "kit/package.json": '{"devDependencies": {"@sveltejs/kit": "^2.0.0"}}',
            "kit/svelte.config.js": "export default {}\n",
            "kit/src/routes/+page.svelte": _component(
                "import { api } from '$lib/api'\nimport { page } from '$app/state'"
            ),
            "kit/src/routes/(app)/+layout@.svelte": _component("const l = 1"),
            "kit/src/lib/api.ts": _FILLER,
            "kit/src/lib/Unused.svelte": _component("const u = 1"),
            "astro/astro.config.mjs": "export default {}\n",
            "astro/src/pages/index.astro": "---\nimport Card from '../components/Card.astro'\n---\n<Card />\n",
            "astro/src/components/Card.astro": "---\n" + _FILLER + "---\n<p/>\n",
            "astro/src/components/Old.astro": "---\n" + _FILLER + "---\n<p/>\n",
            "vue/package.json": '{"dependencies": {"vue": "^3"}, "devDependencies": {"unplugin-vue-components": "^1"}}',
            "vue/vite.config.ts": "export default {}\n",
            "vue/src/components.d.ts": (
                "// Generated by unplugin-vue-components\nexport {}\ndeclare module 'vue' {\n"
                "  export interface GlobalComponents {\n"
                "    TheFooter: typeof import('./widgets/TheFooter.vue')['default']\n  }\n}\n"
            ),
            "vue/src/widgets/TheFooter.vue": _component("const f = 1"),
            "vue/src/components/Badge.vue": _component("const b = 1"),
        },
    )
    graph = _graph(tmp_path)
    page = graph[str((tmp_path / "kit/src/routes/+page.svelte").resolve())]
    assert "unresolved_imports" not in page  # $app/state is SvelteKit's own
    packages = [tmp_path / name for name in ("nuxt", "kit", "astro", "vue")]
    entries, _ = detect_orphaned_files(
        tmp_path,
        graph,
        extensions=[".ts", ".vue", ".svelte", ".astro"],
        options=OrphanedDetectionOptions(
            entry_files=auto_import_entries(tmp_path),
            package_roots=packages,
            entry_conventions=framework_entry_conventions(),
        ),
    )
    orphans = sorted(
        Path(e["file"]).relative_to(tmp_path.resolve()).as_posix() for e in entries
    )
    assert orphans == [
        "astro/src/components/Old.astro",
        "kit/src/lib/Unused.svelte",
        "nuxt/app/legacy/Old.vue",
    ]


@needs_treesitter
def test_debug_logs_fixer_edits_only_the_script(tmp_path, set_project_root):
    vue = (
        "<template>\n  <p>{{ console.log('[Tpl] kept') }}</p>\n</template>\n"
        "<script setup lang=\"ts\">\nconst a = 1\nconsole.log('[Debug] gone', a)\n</script>\n"
    )
    inline = "<script>console.log('[Inline] kept')\nlet b = 2\n</script>\n<p>{b}</p>\n"
    _write(tmp_path, {"src/A.vue": vue, "src/B.svelte": inline})
    fixer = get_ts_fixers()["debug-logs"]
    entries = fixer.detect(tmp_path)
    assert sorted((e["file"], e["line"]) for e in entries) == [
        ("src/A.vue", 6),
        ("src/B.svelte", 1),
    ]
    fixer.fix(entries, dry_run=False)
    assert (tmp_path / "src/A.vue").read_text() == vue.replace(
        "console.log('[Debug] gone', a)\n", ""
    )
    # The log shares its line with the <script> tag: the edit would remove the tag.
    assert (tmp_path / "src/B.svelte").read_text() == inline
