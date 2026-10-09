"""Tests for the Supabase framework spec and the knowledge specs carry
(public env prefixes, data clients)."""

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
    framework_values,
)
from desloppify.languages._framework.frameworks.specs.supabase import (
    SUPABASE_ENTRY_CONVENTIONS,
)
from desloppify.languages._framework.node.frameworks.supabase import (
    edge_function_entries,
    scan_rls_disabled_in_public,
    scan_security_definer_views,
    strip_sql,
)
from desloppify.languages.typescript import TypeScriptConfig
from desloppify.languages.typescript.detectors.concerns import (
    configured_data_clients,
    detect_mixed_concerns,
)


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

    def __init__(self, **settings):
        super().__init__(review_cache={}, detector_coverage={}, coverage_warnings=[])
        self._settings = settings

    def runtime_setting(self, key, default=None):
        return self._settings.get(key, default)


def _migration(tmp_path: Path, name: str, sql: str) -> None:
    _write(tmp_path, f"supabase/migrations/{name}.sql", sql)


# ── detection ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "manifest",
    [
        '{"dependencies": {"@supabase/supabase-js": "^2"}}',
        '{"dependencies": {"@supabase/ssr": "^0.5"}}',
        '{"devDependencies": {"supabase": "^2"}}',
    ],
)
def test_detected_from_client_or_cli(tmp_path: Path, manifest: str):
    _write(tmp_path, "package.json", manifest)
    assert "supabase" in detect_ecosystem_frameworks(tmp_path, None, "node").present


def test_detected_from_cli_config_without_dependency(tmp_path: Path):
    _write(tmp_path, "package.json", '{"name": "app"}')
    assert "supabase" not in detect_ecosystem_frameworks(tmp_path, None, "node").present
    _write(tmp_path, "supabase/config.toml", 'project_id = "app"\n')
    assert "supabase" in detect_ecosystem_frameworks(tmp_path, None, "node").present


def test_framework_values_follow_detection(tmp_path: Path):
    _write(
        tmp_path,
        "package.json",
        '{"dependencies": {"next": "15", "@supabase/ssr": "0.5"}}',
    )
    assert framework_values(tmp_path, None, "data_clients") == ("supabase",)
    assert framework_values(tmp_path, None, "public_env_prefixes") == ("NEXT_PUBLIC_",)
    _write(tmp_path, "package.json", '{"devDependencies": {"vite": "6"}}')
    assert framework_values(tmp_path, None, "data_clients") == ()
    assert framework_values(tmp_path, None, "public_env_prefixes") == ("VITE_",)


# ── migrations: RLS ──────────────────────────────────────────


def test_public_table_without_rls(tmp_path: Path):
    _migration(
        tmp_path,
        "001_init",
        "create table todos (id bigint primary key);\n"
        'create table "public"."notes" (id bigint);\n'
        "alter table public.notes enable row level security;\n"
        "create table private.audit (id bigint);\n"
        "create temporary table scratch (id int);\n",
    )
    entries, scanned = scan_rls_disabled_in_public(tmp_path)
    assert scanned == 1
    assert entries == [
        {"file": "supabase/migrations/001_init.sql", "line": 1, "table": "public.todos"}
    ]


def test_later_migrations_are_replayed(tmp_path: Path):
    _migration(
        tmp_path,
        "001",
        "create table a (id int);\ncreate table b (id int);\ncreate table c (id int);\n",
    )
    _migration(
        tmp_path,
        "002",
        "alter table only a enable row level security;\n"
        "drop table if exists b;\n"
        "alter table c rename to d;\n"
        "alter table d force row level security;\n",
    )
    _migration(
        tmp_path,
        "003",
        "create table e (id int);\nalter table e enable row level security;\n"
        "alter table e disable row level security;\n",
    )
    entries, _ = scan_rls_disabled_in_public(tmp_path)
    assert [e["table"] for e in entries] == ["public.e"]


def test_comments_strings_and_function_bodies_are_not_statements(tmp_path: Path):
    _migration(
        tmp_path,
        "001",
        "-- create table commented (id int);\n"
        "/* create table blocked (id int); */\n"
        "select 'create table quoted (id int)';\n"
        "create function f() returns void as $$ begin create table dynamic (id int); end $$ language plpgsql;\n"
        "create function g() returns void as $body$ create table tagged (id int); $body$ language sql;\n",
    )
    assert scan_rls_disabled_in_public(tmp_path) == ([], 1)
    assert strip_sql("a -- b\nc").splitlines() == ["a     ", "c"]


def test_declarative_schemas_are_read(tmp_path: Path):
    _write(tmp_path, "supabase/schemas/todos.sql", "create table todos (id int);\n")
    entries, scanned = scan_rls_disabled_in_public(tmp_path)
    assert scanned == 1 and [e["table"] for e in entries] == ["public.todos"]


# ── migrations: views ────────────────────────────────────────


def test_view_without_security_invoker(tmp_path: Path):
    _migration(
        tmp_path,
        "001",
        "create view open_view as select 1;\n"
        "create or replace view public.safe with (security_invoker = true) as select 1;\n"
        "create view on_view with (security_invoker) as select 1;\n"
        "create view off_view with (security_invoker = false) as select 1;\n"
        "create view later as select 1;\n"
        "alter view later set (security_invoker = on);\n"
        "create view gone as select 1;\n"
        "drop view gone;\n"
        "create view internal.hidden as select 1;\n"
        "create materialized view mat as select 1;\n",
    )
    entries, _ = scan_security_definer_views(tmp_path)
    assert [(e["view"], e["line"]) for e in entries] == [
        ("public.off_view", 4),
        ("public.open_view", 1),
    ]


# ── phase ────────────────────────────────────────────────────


def test_supabase_phase_reports_both_rules(tmp_path: Path):
    _write(
        tmp_path, "package.json", '{"dependencies": {"@supabase/supabase-js": "^2"}}'
    )
    _migration(
        tmp_path,
        "001",
        "create table todos (id int);\ncreate view v as select * from todos;\n",
    )
    phase = next(
        p for p in TypeScriptConfig().phases if p.label == "Supabase framework smells"
    )
    issues, potentials = phase.run(tmp_path, _FakeLang())
    assert {i["id"] for i in issues} == {
        "supabase::supabase/migrations/001.sql::rls_disabled_in_public::public.todos",
        "supabase::supabase/migrations/001.sql::security_definer_view::public.v",
    }
    assert potentials == {"supabase": 1}


def test_supabase_phase_is_off_without_supabase(tmp_path: Path):
    _write(tmp_path, "package.json", '{"name": "app"}')
    _migration(tmp_path, "001", "create table todos (id int);\n")
    phase = next(
        p for p in TypeScriptConfig().phases if p.label == "Supabase framework smells"
    )
    assert phase.run(tmp_path, _FakeLang()) == ([], {})
    issues, _ = phase.run(tmp_path, _FakeLang(presets=["supabase"]))
    assert len(issues) == 1


def test_only_specs_with_scanners_get_a_phase():
    labels = {p.label for p in TypeScriptConfig().phases}
    assert "Supabase framework smells" in labels
    assert not {"Vite framework smells", "Expo framework smells"} & labels


# ── Edge Functions ───────────────────────────────────────────


def test_edge_functions_are_entries(tmp_path: Path):
    _write(tmp_path, "supabase/config.toml", "")
    body = "Deno.serve(() => new Response('ok'));\n" + "// pad\n" * 12
    entry = _write(tmp_path, "supabase/functions/hello/index.ts", body)
    shared = _write(tmp_path, "supabase/functions/_shared/cors.ts", body)
    assert edge_function_entries(tmp_path) == frozenset(
        {"supabase/functions/hello/index.ts"}
    )
    assert SUPABASE_ENTRY_CONVENTIONS.applies_to(tmp_path)
    graph = {str(p): {"importer_count": 0, "import_count": 0} for p in (entry, shared)}
    entries, _ = detect_orphaned_files(
        tmp_path,
        graph,
        [".ts"],
        options=OrphanedDetectionOptions(
            entry_conventions=(SUPABASE_ENTRY_CONVENTIONS,)
        ),
    )
    assert [e["file"] for e in entries] == [str(shared)]


# ── data clients ─────────────────────────────────────────────


_COMPONENT = (
    "export function Todos() {\n"
    "  const users = await supabase.auth.admin.listUsers();\n"
    + "  // pad\n" * 100
    + "  return (<ul>{users.map((r) => <li>{r.id}</li>)}</ul>);\n}\n"
)


def test_data_clients_come_from_detection_and_config(tmp_path: Path):
    _write(tmp_path, "package.json", '{"name": "app"}')
    _write(tmp_path, "src/Todos.tsx", _COMPONENT)
    assert configured_data_clients(tmp_path, None) == ()
    entries, _ = detect_mixed_concerns(tmp_path, ())
    assert entries == []

    _write(
        tmp_path, "package.json", '{"dependencies": {"@supabase/supabase-js": "^2"}}'
    )
    clients = configured_data_clients(tmp_path, _FakeLang(data_clients=["db"]))
    assert clients == ("supabase", "db")
    entries, _ = detect_mixed_concerns(tmp_path, clients)
    assert entries[0]["concerns"] == [
        "jsx_rendering",
        "data_fetching",
        "direct_supabase",
    ]


def test_expo_router_routes_are_entries(tmp_path: Path):
    from desloppify.languages._framework.frameworks.specs.bundlers import (
        EXPO_ROUTER_ENTRY_CONVENTIONS,
    )

    _write(
        tmp_path, "package.json", '{"dependencies": {"expo": "54", "expo-router": "6"}}'
    )
    body = "export default function Settings() { return null; }\n" + "// pad\n" * 12
    route = _write(tmp_path, "app/(app)/settings.tsx", body)
    orphan = _write(tmp_path, "lib/unused.ts", body)
    assert EXPO_ROUTER_ENTRY_CONVENTIONS.applies_to(tmp_path)
    graph = {str(p): {"importer_count": 0, "import_count": 0} for p in (route, orphan)}
    entries, _ = detect_orphaned_files(
        tmp_path,
        graph,
        [".ts", ".tsx"],
        options=OrphanedDetectionOptions(
            entry_conventions=(EXPO_ROUTER_ENTRY_CONVENTIONS,)
        ),
    )
    assert [e["file"] for e in entries] == [str(orphan)]
