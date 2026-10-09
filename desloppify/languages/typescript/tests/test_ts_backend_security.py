"""Backend security rules on the syntax tree (roadmap 3.8): raw SQL and shell
commands built by interpolation, and Next.js server actions and route handlers
with no auth check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from desloppify.languages.typescript.detectors.security.detector import (
    detect_ts_security,
)
from desloppify.languages.typescript.syntax.tree import get_parser

needs_treesitter = pytest.mark.skipif(
    get_parser("tsx") is None, reason="needs tree-sitter with the tsx grammar"
)
pytestmark = needs_treesitter

_BACKEND_KINDS = {
    "sql_injection",
    "shell_injection",
    "server_action_missing_auth",
    "route_handler_missing_auth",
}


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _scan(
    tmp_path: Path, files: dict[str, str], settings: dict | None = None
) -> list[dict]:
    paths = []
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if not name.endswith(".json"):
            paths.append(str(path))
    entries = detect_ts_security(paths, None, settings).entries
    return [e for e in entries if e["detail"]["kind"] in _BACKEND_KINDS]


def _names(entries: list[dict]) -> list[str]:
    return [e["name"] for e in entries]


# ── SQL ─────────────────────────────────────────────────────


def test_sql_interpolation_into_raw_sinks_is_reported(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "db.ts": (
                "export async function byName(prisma, name: string) {\n"
                "  return prisma.$queryRawUnsafe(`SELECT * FROM users WHERE name = '${name}'`);\n"
                "}\n"
                "export function order(col: string) {\n"
                "  return sql`SELECT * FROM t ORDER BY ${sql.raw(`${col} DESC`)}`;\n"
                "}\n"
                "export async function find(pool, id: string) {\n"
                "  await pool.query('SELECT * FROM users WHERE id = ' + id);\n"
                "  const text = `DELETE FROM sessions WHERE user_id = ${id}`;\n"
                "  await pool.query(text);\n"
                "  await pool.query({ text: `UPDATE users SET seen = now() WHERE id = ${id}` });\n"
                "}\n"
                "export const search = (knex, term: string) => knex('t').whereRaw(`name LIKE '%${term}%'`);\n"
            )
        },
    )
    assert _names(entries) == [
        "sql_injection::byName:prisma.$queryRawUnsafe",
        "sql_injection::order:sql.raw",
        "sql_injection::find:pool.query",
        "sql_injection::find:pool.query#2",
        "sql_injection::find:pool.query#3",
        "sql_injection::search:knex('t').whereRaw",
    ]
    assert entries[0]["detail"]["line"] == 2
    assert entries[0]["confidence"] == "high"
    assert entries[0]["tier"] == 2


def test_sql_parameters_and_constants_are_not_reported(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "db.ts": (
                "const TABLE = 'users';\n"
                "const columns = 'id, name';\n"
                "export async function safe(prisma, pool, db, ids: string[], id: string) {\n"
                "  await prisma.$queryRaw`SELECT * FROM users WHERE id = ${id}`;\n"  # tagged: parameters
                "  await db.execute(sql`SELECT * FROM users WHERE id = ${id}`);\n"
                "  await pool.query('SELECT * FROM users WHERE id = $1', [id]);\n"
                "  await pool.query(`SELECT ${columns} FROM ${TABLE} WHERE id = $1`, [id]);\n"
                "  const placeholders = ids.map(() => '?').join(',');\n"
                "  await db.query(`SELECT * FROM users WHERE id IN (${placeholders})`, ids);\n"
                "  await prisma.$queryRawUnsafe('SELECT * FROM users WHERE id = $1', id);\n"
                "  await prisma.$queryRawUnsafe(query, id);\n"  # unknown variable: not reported
                "}\n"
                "export function page(db, limit: number, offset: string, ids: string[]) {\n"
                "  return db.query(`SELECT * FROM t LIMIT ${limit} OFFSET ${Number(offset)} -- ${ids.length}`);\n"
                "}\n"
            )
        },
    )
    assert entries == []


def test_generic_query_methods_need_sql_text(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "client.ts": (
                "export async function f(ky, cache, url: string, id: string) {\n"
                "  await ky.query(`${url}/test`);\n"
                "  cache.get(`user:${id}`);\n"
                "  router.get(`/users/${id}`);\n"
                "  console.log(`Select from ${id} failed`);\n"
                "  db.get(`SELECT name FROM users WHERE id = ${id}`);\n"
                "}\n"
            )
        },
    )
    assert _names(entries) == ["sql_injection::f:db.get"]
    assert entries[0]["confidence"] == "medium"


# ── child_process ───────────────────────────────────────────


def test_shell_commands_built_from_values_are_reported(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "run.ts": (
                "import { exec, execSync as sh, spawn } from 'node:child_process';\n"
                "import * as cp from 'child_process';\n"
                "import { promisify } from 'node:util';\n"
                "const run = promisify(exec);\n"
                "export function install(pkg: string, dir: string) {\n"
                "  exec(`npm install ${pkg}`);\n"
                "  sh('git checkout ' + dir);\n"
                "  cp.execSync(`rm -rf ${dir}`);\n"
                "  spawn(`ls ${dir}`, { shell: true });\n"
                "  return run(`npm view ${pkg} version`);\n"
                "}\n"
            ),
            "legacy.js": (
                "const { exec } = require('child_process');\n"
                "const childProcess = require('child_process');\n"
                "function go(name) {\n"
                "  const command = 'convert ' + name + ' out.png';\n"
                "  exec(command);\n"
                "  childProcess.exec(`open ${name}`);\n"
                "}\n"
            ),
        },
    )
    assert _names(entries) == [
        "shell_injection::install:exec",
        "shell_injection::install:sh",
        "shell_injection::install:cp.execSync",
        "shell_injection::install:spawn",
        "shell_injection::install:run",
        "shell_injection::go:exec",
        "shell_injection::go:childProcess.exec",
    ]
    assert {e["confidence"] for e in entries} == {"high"}


def test_shell_wrapper_from_a_local_module_is_followed(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "lib/execa.ts": (
                "import { exec, execFile } from 'node:child_process';\n"
                "import { promisify } from 'node:util';\n"
                "export const execa = promisify(exec);\n"
                "export const run = promisify(execFile);\n"
            ),
            "lib/pkg.ts": (
                "import { execa, run } from './execa';\n"
                "import { other } from './other';\n"
                "export async function install(name: string) {\n"
                "  await execa(`npm install ${name}`);\n"
                "  await run(`npm`, ['install', name]);\n"
                "  await other(`npm install ${name}`);\n"
                "}\n"
            ),
            "lib/other.ts": "export async function other(cmd: string) { return cmd; }\n",
        },
    )
    assert _names(entries) == ["shell_injection::install:execa"]
    assert entries[0]["file"].endswith("lib/pkg.ts")


def test_shell_safe_forms_are_not_reported(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "run.ts": (
                "import { exec, execFile, execFileSync, spawn } from 'node:child_process';\n"
                "const GIT = 'git';\n"
                "export function ok(dir: string, pattern: RegExp, text: string) {\n"
                "  exec('git status');\n"
                "  exec(`${GIT} status`);\n"
                "  execFile('git', ['checkout', dir]);\n"
                "  execFileSync(`${dir}/bin/tool`, ['--version']);\n"
                "  spawn('ls', [dir]);\n"
                "  spawn(`ls ${dir}`, { shell: false });\n"
                "  pattern.exec(`${text}`);\n"  # RegExp#exec is not child_process
                "}\n"
            ),
            "other.ts": "export function f(x: string) { exec(`echo ${x}`); }\n",  # exec not from child_process
        },
    )
    assert entries == []


# ── Server actions and route handlers ───────────────────────

_AUTH_APP = json.dumps({"dependencies": {"next": "15.0.0", "next-auth": "5.0.0"}})
_PUBLIC_APP = json.dumps({"dependencies": {"next": "15.0.0"}})


def test_server_actions_without_auth_are_reported(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "package.json": _AUTH_APP,
            "app/actions.ts": (
                "'use server';\n"
                "import { auth } from '@/auth';\n"
                "import { db } from '@/db';\n"
                "export async function deletePost(id: string) {\n"
                "  await db.post.delete({ where: { id } });\n"
                "}\n"
                "export const renamePost = async (id: string, name: string) => {\n"
                "  await db.post.update({ where: { id }, data: { name } });\n"
                "};\n"
                "async function archive(id: string) { await db.post.archive(id); }\n"
                "export { archive as archivePost };\n"
                "export async function createPost(title: string) {\n"
                "  const session = await auth();\n"
                "  if (!session) throw new Error('Unauthorized');\n"
                "  await db.post.create({ data: { title } });\n"
                "}\n"
            ),
            "app/page.tsx": (
                "export async function save(data: FormData) {\n"
                "  'use server';\n"
                "  await db.save(data);\n"
                "}\n"
                "export default function Page() { return <form action={save} />; }\n"
            ),
        },
    )
    assert _names(entries) == [
        "server_action_missing_auth::deletePost",
        "server_action_missing_auth::renamePost",
        "server_action_missing_auth::archivePost",
        "server_action_missing_auth::save",
    ]
    assert entries[0]["detail"]["line"] == 4
    assert entries[0]["confidence"] == "medium"
    assert entries[0]["tier"] == 3


def test_server_actions_with_auth_helpers_or_wrappers_are_not_reported(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "package.json": _AUTH_APP,
            "app/actions.ts": (
                "'use server';\n"
                "import { currentUser } from '@clerk/nextjs/server';\n"
                "import { actionClient } from '@/lib/safe-action';\n"
                "async function requireOwner() {\n"
                "  const user = await currentUser();\n"
                "  if (!user) throw new Error('nope');\n"
                "  return user;\n"
                "}\n"
                "export async function a() { await requireOwner(); }\n"
                "export async function b() { const s = await getServerSession(authOptions); }\n"
                "export async function c() { const { data } = await supabase.auth.getUser(); }\n"
                "export const d = actionClient(async () => {});\n"  # imported wrapper: can't be seen
                "export const e = withAuth(async () => {});\n"
                "export const listPosts = procedure.query(async () => []);\n"  # not a function written here
                "export async function f() { await ensureSignedIn(); }\n"
            ),
        },
    )
    assert entries == []


def test_apps_without_accounts_are_not_checked(tmp_path):
    files = {
        "package.json": _PUBLIC_APP,
        "components/cart/actions.ts": "'use server';\nexport async function addItem(id: string) { await addToCart(id); }\n",
    }
    assert _scan(tmp_path, files) == []
    # A configured auth function says the app has accounts, and counts as a check.
    configured = {"auth_functions": ["getShopper"]}
    assert _names(_scan(tmp_path, files, configured)) == [
        "server_action_missing_auth::addItem"
    ]
    files["components/cart/actions.ts"] += (
        "export async function removeItem() { await getShopper(); }\n"
    )
    assert _names(_scan(tmp_path, files, configured)) == [
        "server_action_missing_auth::addItem"
    ]


def test_route_handlers_that_mutate_without_auth_are_reported(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "package.json": _AUTH_APP,
            "src/app/api/posts/route.ts": (
                "import { NextResponse } from 'next/server';\n"
                "export async function GET() { return NextResponse.json(await db.posts()); }\n"
                "export async function POST(req: Request) {\n"
                "  const body = await req.json();\n"
                "  await db.create(body);\n"
                "  return NextResponse.json({ ok: true });\n"
                "}\n"
                "const remove = async () => { await db.clear(); return new Response(null); };\n"
                "export const DELETE = remove;\n"
            ),
            "src/app/api/secure/route.ts": (
                "export async function POST(req: Request) {\n"
                "  if (req.headers.get('authorization') !== `Bearer ${process.env.WEBHOOK_TOKEN}`) {\n"
                "    return new Response('Unauthorized', { status: 401 });\n"
                "  }\n"
                "}\n"
                "export async function PUT() { const session = await auth(); }\n"
            ),
            "src/app/api/trpc/[trpc]/route.ts": (
                "const handler = (req: Request) => fetchRequestHandler({ router, req, endpoint: '/api/trpc' });\n"
                "export { handler as GET, handler as POST };\n"
            ),
            "src/app/api/revalidate/route.ts": "export async function POST(req) { return revalidate(req); }\n",
            "src/app/api/auth/[...nextauth]/route.ts": "export const { GET, POST } = handlers;\n",
            "src/app/api/re/route.ts": "export { POST } from '@trpc/next/app-dir/server';\n",
            "src/lib/route.ts": "export async function POST() { await db.clear(); }\n",  # not under app/
        },
    )
    assert _names(entries) == [
        "route_handler_missing_auth::POST",
        "route_handler_missing_auth::DELETE",
    ]
    assert [e["file"].endswith("src/app/api/posts/route.ts") for e in entries] == [
        True,
        True,
    ]


def test_middleware_auth_lowers_confidence(tmp_path):
    entries = _scan(
        tmp_path,
        {
            "package.json": _AUTH_APP,
            "src/middleware.ts": "export { auth as middleware } from '@/auth';\n",
            "src/app/api/posts/route.ts": "export async function POST() { await db.clear(); }\n",
        },
    )
    assert [(e["name"], e["confidence"]) for e in entries] == [
        ("route_handler_missing_auth::POST", "low")
    ]


def test_ids_do_not_depend_on_line_numbers(tmp_path):
    action = (
        "'use server';\nexport async function drop(id: string) { await db.drop(id); }\n"
    )
    first = _names(_scan(tmp_path, {"package.json": _AUTH_APP, "app/a.ts": action}))
    moved = _names(
        _scan(
            tmp_path, {"package.json": _AUTH_APP, "app/a.ts": "\n\n// moved\n" + action}
        )
    )
    assert first == moved == ["server_action_missing_auth::drop"]


def test_without_treesitter_nothing_is_reported(tmp_path, monkeypatch):
    import desloppify.languages.typescript.syntax.tree as tree_mod

    monkeypatch.setattr(tree_mod, "get_parser", lambda grammar: None)
    files = {
        "run.ts": "import { exec } from 'child_process';\nexport function f(x) { exec(`rm ${x}`); }\n"
    }
    assert _scan(tmp_path, files) == []
