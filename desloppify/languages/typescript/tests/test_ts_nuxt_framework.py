"""Tests for the Nuxt framework scanners (TypeScript)."""

from __future__ import annotations

from pathlib import Path

import pytest

from desloppify.languages._framework.frameworks.specs.nuxt import NUXT_SPEC
from desloppify.languages._framework.node.frameworks.nuxt import (
    private_runtime_config_keys,
    scan_data_composables_outside_setup,
    scan_legacy_process_flags,
    scan_private_runtime_config_in_client,
)


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _package(tmp_path: Path, version: str = "^3.12.0") -> None:
    _write(tmp_path, "package.json", f'{{"devDependencies": {{"nuxt": "{version}"}}}}')


def _lines(entries: list[dict]) -> list[tuple[str, int]]:
    return sorted((Path(e["file"]).name, e["line"]) for e in entries)


# ── process.client / process.server ──────────────────────────


def test_legacy_process_flags(tmp_path: Path):
    _package(tmp_path)
    _write(
        tmp_path,
        "components/Chart.vue",
        "<template>\n  <div v-if=\"process.client\" />\n</template>\n"
        "<script setup lang=\"ts\">\n"
        "// process.server in a comment\n"
        "const label = 'process.client'\n"
        "if (process.client) {\n  init()\n}\n"
        "const dev = process.dev\n"
        "</script>\n",
    )
    _write(tmp_path, "plugins/analytics.ts", "export default defineNuxtPlugin(() => {\n  if (!process.server) track()\n})\n")
    _write(tmp_path, "utils/ok.ts", "export const isClient = import.meta.client\n")
    # Node-side code.
    _write(tmp_path, "server/api/x.ts", "export default defineEventHandler(() => process.server)\n")
    _write(tmp_path, "nuxt.config.ts", "export default defineNuxtConfig({ ssr: !process.client })\n")
    _write(tmp_path, "modules/m.ts", "export default () => process.server\n")
    entries, _ = scan_legacy_process_flags(tmp_path)
    assert sorted((Path(e["file"]).name, e["line"], e["flag"], e["count"]) for e in entries) == [
        ("Chart.vue", 7, "process.client", 2),
        ("analytics.ts", 2, "process.server", 1),
    ]


def test_legacy_process_flags_fine_in_nuxt2(tmp_path: Path):
    _package(tmp_path, "^2.17.0")
    _write(tmp_path, "plugins/a.js", "if (process.client) init()\n")
    assert scan_legacy_process_flags(tmp_path) == ([], 0)


# ── useFetch / useAsyncData after setup ──────────────────────


def test_data_composables_in_handlers_and_hooks(tmp_path: Path):
    _package(tmp_path)
    _write(
        tmp_path,
        "pages/index.vue",
        "<script setup lang=\"ts\">\n"
        "const { data } = await useFetch('/api/items')\n"
        "async function save(item: Item) {\n"
        "  await useFetch('/api/items', { method: 'POST', body: item })\n"
        "}\n"
        "const remove = async (id: string) => {\n"
        "  await useAsyncData(`del-${id}`, () => $fetch(`/api/items/${id}`, { method: 'DELETE' }))\n"
        "}\n"
        "onMounted(async () => {\n"
        "  const { data: more } = await useLazyFetch('/api/more')\n"
        "})\n"
        "watch(() => route.query.page, () => refresh())\n"
        "function refresh() {\n"
        "  useFetch('/api/items')\n"
        "}\n"
        "</script>\n"
        "<template>\n  <button @click=\"save(item)\">Save</button>\n  <Item @remove=\"remove\" />\n</template>\n",
    )
    entries, scanned = scan_data_composables_outside_setup(tmp_path)
    assert scanned == 1
    assert [(e["line"], e["composable"]) for e in sorted(entries, key=lambda e: e["line"])] == [
        (4, "useFetch"),
        (7, "useAsyncData"),
        (10, "useLazyFetch"),
        (14, "useFetch"),
    ]


def test_data_composables_in_setup_and_composables_pass(tmp_path: Path):
    _package(tmp_path)
    _write(
        tmp_path,
        "pages/a.vue",
        "<script setup lang=\"ts\">\n"
        "function useItems() {\n  return useFetch('/api/items')\n}\n"
        "async function load() {\n  return await useAsyncData('a', () => $fetch('/api/a'))\n}\n"
        "const { data } = await load()\n"
        "const [x, y] = await Promise.all([\n  useFetch('/api/x'),\n  useAsyncData('y', () => $fetch('/y')),\n])\n"
        "async function onSubmit() {\n  await $fetch('/api/save', { method: 'POST' })\n}\n"
        "</script>\n"
        "<template><form @submit=\"onSubmit\" /></template>\n",
    )
    _write(
        tmp_path,
        "components/B.vue",
        "<script lang=\"ts\">\nexport default defineNuxtComponent({\n"
        "  async setup() {\n    const { data } = await useFetch('/api/b')\n    return { data }\n  },\n})\n</script>\n",
    )
    entries, _ = scan_data_composables_outside_setup(tmp_path)
    assert entries == []


# ── private runtimeConfig on the client ──────────────────────


_CONFIG = """export default defineNuxtConfig({
  runtimeConfig: {
    // server only
    apiSecret: '',
    'stripe-key': '',
    github: { clientSecret: '' },
    public: { apiBase: '/api' },
    app: {},
  },
})
"""


def test_private_runtime_config_keys(tmp_path: Path):
    _write(tmp_path, "nuxt.config.ts", _CONFIG)
    assert private_runtime_config_keys(tmp_path) == {"apiSecret", "stripe-key", "github"}


def test_private_runtime_config_read_in_component(tmp_path: Path):
    _package(tmp_path)
    _write(tmp_path, "nuxt.config.ts", _CONFIG)
    _write(
        tmp_path,
        "components/Pay.vue",
        "<script setup lang=\"ts\">\n"
        "const config = useRuntimeConfig()\n"
        "const base = config.public.apiBase\n"
        "const secret = config.apiSecret\n"
        "</script>\n",
    )
    _write(
        tmp_path,
        "components/Login.vue",
        "<script setup>\nconst { github, public: pub } = useRuntimeConfig()\n</script>\n",
    )
    _write(tmp_path, "plugins/track.client.ts", "export default defineNuxtPlugin(() => {\n  init(useRuntimeConfig().apiSecret)\n})\n")
    # Server-side readers.
    _write(tmp_path, "components/Report.server.vue", "<script setup>\nconst k = useRuntimeConfig().apiSecret\n</script>\n")
    _write(tmp_path, "server/api/pay.ts", "export default defineEventHandler((e) => useRuntimeConfig(e).apiSecret)\n")
    _write(tmp_path, "plugins/a.server.ts", "export default defineNuxtPlugin(() => useRuntimeConfig().apiSecret)\n")
    entries, _ = scan_private_runtime_config_in_client(tmp_path)
    assert sorted((Path(e["file"]).name, e["line"], e["key"]) for e in entries) == [
        ("Login.vue", 2, "github"),
        ("Pay.vue", 4, "apiSecret"),
        ("track.client.ts", 2, "apiSecret"),
    ]


def test_private_runtime_config_needs_declared_keys(tmp_path: Path):
    _package(tmp_path)
    _write(tmp_path, "nuxt.config.ts", "export default defineNuxtConfig({})\n")
    _write(tmp_path, "components/A.vue", "<script setup>\nconst k = useRuntimeConfig().apiSecret\n</script>\n")
    assert scan_private_runtime_config_in_client(tmp_path) == ([], 0)


def test_spec_wires_scanners(tmp_path: Path):
    assert [rule.id for rule in NUXT_SPEC.scanners] == [
        "data_composable_outside_setup",
        "private_runtime_config_in_client",
        "legacy_process_flag",
    ]
    _package(tmp_path)
    _write(tmp_path, "app/app.vue", "<script setup>\nif (process.server) x()\n</script>\n")
    rule = NUXT_SPEC.scanners[2]
    entries, _ = rule.scan(tmp_path, None)
    issue = rule.issue_factory(entries[0])
    assert issue["detector"] == "nuxt"
    assert issue["id"].endswith("app.vue::legacy_process_flag")
    assert "import.meta.server" in issue["summary"]
