"""Tests for the Express, Hono and Fastify framework specs (TypeScript)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from desloppify.engine.detectors.orphaned import (
    OrphanedDetectionOptions,
    detect_orphaned_files,
)
from desloppify.languages._framework.frameworks.detection import (
    detect_ecosystem_frameworks,
)
from desloppify.languages._framework.frameworks.specs.servers import (
    FASTIFY_ENTRY_CONVENTIONS,
    HONOX_ENTRY_CONVENTIONS,
    WRANGLER_ENTRY_CONVENTIONS,
)
from desloppify.languages._framework.node.frameworks.servers import (
    autoload_dirs,
    honox_entries,
    scan_express_misshapen_error_handlers,
    scan_express_unhandled_async_handlers,
    scan_fastify_async_with_done,
    scan_hono_unawaited_next,
    scan_hono_unreturned_responses,
    wrangler_main,
)
from desloppify.languages._framework.node.js_functions import function_at
from desloppify.languages._framework.node.js_text import code_text
from desloppify.languages.typescript import TypeScriptConfig


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


class _FakeLang(SimpleNamespace):
    zone_map = None
    dep_graph = None
    file_finder = None

    def __init__(self):
        super().__init__(review_cache={}, detector_coverage={}, coverage_warnings=[])


# ── function reader ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "is_async", "params", "block"),
    [
        ("async (req: Request, res: Response): Promise<void> => { x }", True, ("req", "res"), True),
        ("function handler(err, req, res, next) { x }", False, ("err", "req", "res", "next"), True),
        ("c => c.text('hi')", False, ("c",), False),
        ("async ({ params }, reply) => { x }", True, ("", "reply"), True),
    ],
)
def test_function_reader(source, is_async, params, block):
    function = function_at(code_text(source), 0)
    assert function is not None
    assert (function.is_async, function.params, function.body is not None) == (is_async, params, block)


def test_function_reader_rejects_a_call():
    assert function_at(code_text("(a, b) + 1"), 0) is None


# ── detection ────────────────────────────────────────────────


@pytest.mark.parametrize(("dep", "framework"), [("express", "express"), ("hono", "hono"), ("fastify", "fastify")])
def test_detection(tmp_path: Path, dep: str, framework: str):
    _write(tmp_path, "package.json", f'{{"dependencies": {{"{dep}": "^4.0.0"}}}}')
    assert framework in detect_ecosystem_frameworks(tmp_path, None, "node").present


# ── Express ──────────────────────────────────────────────────

_ROUTES = """\
import { Router } from 'express';
const router = Router();
router.get('/a', async (req, res) => {
  const user = await load(req.params.id);
  res.json(user);
});
router.get('/b', async (req, res, next) => {
  try { res.json(await load()); } catch (e) { next(e); }
});
router.get('/c', asyncHandler(async (req, res) => { res.json(await load()); }));
router.get('/d', async (req, res, next) => { load().then((u) => res.json(u)).catch(next); });
router.get('/e', (req, res) => { res.json(1); });
export default router;
"""


def test_express4_unhandled_async_handlers(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"express": "~4.18.1"}}')
    _write(tmp_path, "src/routes.ts", _ROUTES)
    entries, scanned = scan_express_unhandled_async_handlers(tmp_path)
    assert scanned == 1
    assert [(e["file"], e["line"]) for e in entries] == [("src/routes.ts", 3)]


@pytest.mark.parametrize(
    "package",
    [
        '{"dependencies": {"express": "^5.1.0"}}',
        '{"dependencies": {"express": "^4.18.1", "express-async-errors": "^3.1.1"}}',
    ],
)
def test_express_forwarding_rejections_is_fine(tmp_path: Path, package: str):
    _write(tmp_path, "package.json", package)
    _write(tmp_path, "src/routes.ts", _ROUTES)
    assert scan_express_unhandled_async_handlers(tmp_path)[0] == []


def test_express_error_handler_arity(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"express": "^4.0.0"}}')
    _write(
        tmp_path,
        "src/app.js",
        "const express = require('express');\nconst app = express();\n"
        "app.use((err, req, res) => { res.status(500).send(err.message); });\n"
        "app.use((err, req, res, next) => { res.status(500).end(); });\n"
        "app.use((req, res, next) => next());\n",
    )
    entries, _ = scan_express_misshapen_error_handlers(tmp_path)
    assert [(e["line"], e["param"]) for e in entries] == [(3, "err")]


# ── Hono ─────────────────────────────────────────────────────

_HONO = """\
import { Hono } from 'hono';
const app = new Hono();
app.get('/a', (c) => {
  if (!c.req.query('id')) {
    c.json({ error: 'missing' }, 400);
  }
  return c.json({ ok: true });
});
app.get('/b', (c) => c.text('ok'));
app.get('/c', async (ctx) => { ctx.status(201); return ctx.json(await load()); });
app.use(async (c, next) => { const t = Date.now(); next(); c.header('x-t', `${Date.now() - t}`); });
app.use(async (c, next) => { await next(); });
app.use((c, next) => next());
export default app;
"""


def test_hono_unreturned_response(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"hono": "^4.0.0"}}')
    _write(tmp_path, "src/index.ts", _HONO)
    entries, _ = scan_hono_unreturned_responses(tmp_path)
    assert [(e["line"], e["helper"]) for e in entries] == [(5, "json")]


def test_hono_unawaited_next(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"hono": "^4.0.0"}}')
    _write(tmp_path, "src/index.ts", _HONO)
    entries, _ = scan_hono_unawaited_next(tmp_path)
    assert [e["line"] for e in entries] == [11]


def test_hono_rules_ignore_other_files(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"hono": "^4.0.0", "express": "^4.0.0"}}')
    _write(
        tmp_path,
        "src/express.ts",
        "import express from 'express';\nconst app = express();\n"
        "app.get('/', (req, res) => { res.json(1); });\n",
    )
    assert scan_hono_unreturned_responses(tmp_path)[0] == []


def test_wrangler_main(tmp_path: Path):
    _write(tmp_path, "wrangler.jsonc", '{\n  // worker\n  "name": "x",\n  "main": "./workers/app.ts",\n}\n')
    _write(tmp_path, "workers/app.ts", "export default {}\n")
    assert WRANGLER_ENTRY_CONVENTIONS.applies_to(tmp_path)
    assert wrangler_main(tmp_path) == {"workers/app.ts"}


def test_wrangler_toml_main(tmp_path: Path):
    _write(tmp_path, "wrangler.toml", 'name = "x"\nmain = "src/worker.ts"\n')
    _write(tmp_path, "src/worker.ts", "export default {}\n")
    assert wrangler_main(tmp_path) == {"src/worker.ts"}


def test_honox_entries(tmp_path: Path):
    _write(tmp_path, "vite.config.ts", "import honox from 'honox/vite'\nexport default { plugins: [honox()] }\n")
    _write(tmp_path, "app/client.ts", "export {}\n")
    assert HONOX_ENTRY_CONVENTIONS.applies_to(tmp_path)
    assert honox_entries(tmp_path) == {"app/routes/", "app/islands/", "app/client.ts"}


def test_honox_needs_its_vite_plugin(tmp_path: Path):
    _write(tmp_path, "vite.config.ts", "export default {}\n")
    assert honox_entries(tmp_path) == frozenset()


# ── Fastify ──────────────────────────────────────────────────


def test_fastify_async_with_done(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"fastify": "^5.0.0"}}')
    _write(
        tmp_path,
        "src/plugin.ts",
        "import fp from 'fastify-plugin';\n"
        "export default fp(async function (fastify, opts, done) {\n  fastify.decorate('x', 1);\n  done();\n});\n"
        "export const hook = async (request, reply) => { await check(request); };\n"
        "export function sync(fastify, opts, done) { done(); }\n",
    )
    entries, _ = scan_fastify_async_with_done(tmp_path)
    assert [e["line"] for e in entries] == [2]


def test_fastify_autoload_dirs_are_entries(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"fastify": "^5.0.0", "@fastify/autoload": "^6.0.0"}}')
    _write(
        tmp_path,
        "src/app.ts",
        "import path from 'node:path'\nimport fastifyAutoload from '@fastify/autoload'\n"
        "export default async function app (fastify) {\n"
        "  fastify.register(fastifyAutoload, { dir: path.join(import.meta.dirname, 'plugins/external') })\n"
        "  fastify.register(fastifyAutoload, { dir: path.join(__dirname, 'routes', 'api') })\n"
        "}\n",
    )
    assert FASTIFY_ENTRY_CONVENTIONS.applies_to(tmp_path)
    assert autoload_dirs(tmp_path) == {"src/plugins/external/", "src/routes/api/"}

    body = "export default async function (fastify) {}\n" + "// pad\n" * 12
    plugin = _write(tmp_path, "src/plugins/external/cors.ts", body)
    route = _write(tmp_path, "src/routes/api/users.ts", body)
    orphan = _write(tmp_path, "src/lib/unused.ts", body)
    graph = {str(p): {"importer_count": 0, "import_count": 0} for p in (plugin, route, orphan)}
    entries, _ = detect_orphaned_files(
        tmp_path,
        graph,
        [".ts"],
        options=OrphanedDetectionOptions(entry_conventions=(FASTIFY_ENTRY_CONVENTIONS,)),
    )
    assert [e["file"] for e in entries] == [str(orphan)]


# ── phases ───────────────────────────────────────────────────


def test_server_phases(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"hono": "^4.0.0"}}')
    _write(tmp_path, "src/index.ts", _HONO)
    labels = {p.label: p for p in TypeScriptConfig().phases}
    assert {"Express framework smells", "Fastify framework smells"} <= set(labels)
    issues, _ = labels["Hono framework smells"].run(tmp_path, _FakeLang())
    assert {issue["id"] for issue in issues} == {
        "hono::src/index.ts::unreturned_response::5",
        "hono::src/index.ts::unawaited_next::11",
    }
