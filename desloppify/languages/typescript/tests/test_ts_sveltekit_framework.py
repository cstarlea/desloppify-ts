"""Tests for the SvelteKit framework scanners (TypeScript)."""

from __future__ import annotations

from pathlib import Path

import pytest

from desloppify.languages._framework.frameworks.specs.sveltekit import SVELTEKIT_SPEC
from desloppify.languages._framework.node.frameworks.sveltekit import (
    scan_load_global_fetch,
    scan_redirects_in_try,
    scan_server_imports_in_client,
)


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _lines(entries: list[dict]) -> list[tuple[str, int]]:
    return sorted((Path(e["file"]).name, e["line"]) for e in entries)


# ── server-only imports in client modules ────────────────────


def test_private_env_in_component_and_universal_load(tmp_path: Path):
    _write(
        tmp_path,
        "src/routes/+page.svelte",
        "<script lang=\"ts\">\n"
        "  import { onMount } from 'svelte';\n"
        "  import { API_KEY } from '$env/static/private';\n"
        "</script>\n<p>{API_KEY}</p>\n",
    )
    _write(tmp_path, "src/routes/+page.ts", "import { env } from '$env/dynamic/private';\nexport const load = () => ({});\n")
    _write(tmp_path, "src/hooks.client.ts", "import { db } from '$lib/server/db';\n")
    _write(tmp_path, "src/lib/Widget.svelte", "<script>\n  import { q } from './server/queries';\n</script>\n")
    _write(tmp_path, "src/lib/Other.svelte", "<script>\n  import { s } from '../secrets.server';\n</script>\n")
    entries, scanned = scan_server_imports_in_client(tmp_path)
    assert scanned == 5
    assert sorted((Path(e["file"]).name, e["line"], e["module"]) for e in entries) == [
        ("+page.svelte", 3, "$env/static/private"),
        ("+page.ts", 1, "$env/dynamic/private"),
        ("Other.svelte", 2, "../secrets.server"),
        ("Widget.svelte", 2, "./server/queries"),
        ("hooks.client.ts", 1, "$lib/server/db"),
    ]


def test_server_modules_and_type_imports_may_import_server_code(tmp_path: Path):
    _write(tmp_path, "src/routes/+page.server.ts", "import { SECRET } from '$env/static/private';\n")
    _write(tmp_path, "src/routes/+layout.server.ts", "import { db } from '$lib/server/db';\n")
    _write(tmp_path, "src/hooks.server.ts", "import { env } from '$env/dynamic/private';\n")
    _write(tmp_path, "src/lib/db.ts", "import { env } from '$env/dynamic/private';\n")
    _write(
        tmp_path,
        "src/routes/+page.svelte",
        "<script lang=\"ts\">\n"
        "  import type { User } from '$lib/server/db';\n"
        "  import { type Row } from '$lib/server/db';\n"
        "  import { PUBLIC_URL } from '$env/static/public';\n"
        "  // import { KEY } from '$env/static/private';\n"
        "</script>\n"
        "<code>import {'{'} KEY {'}'} from '$env/static/private'</code>\n",
    )
    entries, _ = scan_server_imports_in_client(tmp_path)
    assert entries == []


# ── load using the global fetch ──────────────────────────────


def test_universal_load_global_fetch(tmp_path: Path):
    _write(
        tmp_path,
        "src/routes/+page.ts",
        "import type { PageLoad } from './$types';\n"
        "export const load: PageLoad = async ({ params }) => {\n"
        "  const res = await fetch(`https://api.example.com/${params.id}`);\n"
        "  return { item: await res.json() };\n"
        "};\n",
    )
    _write(
        tmp_path,
        "src/routes/a/+layout.js",
        "export async function load(event) {\n  return { x: await fetch('/api/x').then((r) => r.json()) };\n}\n",
    )
    entries, scanned = scan_load_global_fetch(tmp_path)
    assert scanned == 2
    assert _lines(entries) == [("+layout.js", 2), ("+page.ts", 3)]
    assert all(e["universal"] for e in entries)


def test_load_with_provided_fetch_passes(tmp_path: Path):
    _write(
        tmp_path,
        "src/routes/+page.ts",
        "export const load = async ({ fetch, params }) => {\n  return { a: await fetch(`/api/${params.id}`) };\n};\n",
    )
    _write(
        tmp_path,
        "src/routes/b/+page.ts",
        "export async function load(event) {\n  const { fetch } = event;\n  return { a: await fetch('/x') };\n}\n",
    )
    _write(
        tmp_path,
        "src/routes/c/+page.ts",
        "export async function load(event) {\n  return { a: await event.fetch('/x') };\n}\n",
    )
    # A server load may call external APIs with the global fetch.
    _write(
        tmp_path,
        "src/routes/d/+page.server.ts",
        "export async function load() {\n  return { a: await fetch('https://api.example.com/x') };\n}\n",
    )
    # fetch outside load (an action) isn't load's business.
    _write(
        tmp_path,
        "src/routes/e/+page.server.ts",
        "export const load = async ({ fetch }) => ({ a: await fetch('/x') });\n"
        "export const actions = { default: async () => { await fetch('/api'); } };\n",
    )
    entries, _ = scan_load_global_fetch(tmp_path)
    assert entries == []


def test_server_load_global_fetch_with_relative_url(tmp_path: Path):
    _write(
        tmp_path,
        "src/routes/+page.server.ts",
        "export const load = async ({ params }) => {\n  const r = await fetch(`/api/${params.id}`);\n  return {};\n};\n",
    )
    entries, _ = scan_load_global_fetch(tmp_path)
    assert _lines(entries) == [("+page.server.ts", 2)]
    assert entries[0]["universal"] is False


# ── redirect inside try ──────────────────────────────────────


def test_redirect_swallowed_by_catch(tmp_path: Path):
    _write(
        tmp_path,
        "src/routes/login/+page.server.ts",
        "import { fail, redirect as go } from '@sveltejs/kit';\n"
        "export const actions = {\n"
        "  default: async ({ request }) => {\n"
        "    try {\n"
        "      await signIn(request);\n"
        "      go(303, '/');\n"
        "    } catch (e) {\n"
        "      return fail(400, { message: 'Login failed' });\n"
        "    }\n"
        "  },\n"
        "};\n",
    )
    entries, scanned = scan_redirects_in_try(tmp_path)
    assert scanned == 1
    assert _lines(entries) == [("+page.server.ts", 6)]


def test_redirect_rethrown_or_outside_try_passes(tmp_path: Path):
    _write(
        tmp_path,
        "src/routes/a/+page.server.ts",
        "import { redirect, isRedirect } from '@sveltejs/kit';\n"
        "export const actions = {\n"
        "  a: async () => {\n"
        "    try { redirect(303, '/'); } catch (e) { if (isRedirect(e)) return; }\n"
        "  },\n"
        "  b: async () => {\n"
        "    try { redirect(303, '/'); } catch (e) { console.error(e); throw e; }\n"
        "  },\n"
        "  c: async () => {\n"
        "    try { await save(); } catch { return { ok: false }; }\n"
        "    redirect(303, '/');\n"
        "  },\n"
        "  d: async () => {\n"
        "    try { redirect(303, '/'); } finally { done(); }\n"
        "  },\n"
        "};\n",
    )
    # Not SvelteKit's redirect.
    _write(
        tmp_path,
        "src/lib/nav.ts",
        "import { redirect } from './router';\ntry { redirect('/'); } catch {}\n",
    )
    entries, _ = scan_redirects_in_try(tmp_path)
    assert entries == []


def test_spec_wires_scanners(tmp_path: Path):
    assert [rule.id for rule in SVELTEKIT_SPEC.scanners] == [
        "server_import_in_client",
        "load_global_fetch",
        "redirect_in_try",
    ]
    _write(tmp_path, "src/routes/+page.svelte", "<script>\n  import { K } from '$env/static/private';\n</script>\n")
    rule = SVELTEKIT_SPEC.scanners[0]
    entries, _ = rule.scan(tmp_path, None)
    issue = rule.issue_factory(entries[0])
    assert issue["detector"] == "sveltekit"
    assert issue["id"].endswith("::server_import_in_client::$env/static/private")
    assert issue["tier"] == 2 and issue["confidence"] == "high"
