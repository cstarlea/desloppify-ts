"""Tests for the NestJS framework spec (TypeScript)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from desloppify.languages._framework.frameworks.detection import (
    detect_ecosystem_frameworks,
    injected_class_decorators,
)
from desloppify.languages._framework.frameworks.specs.nestjs import (
    NESTJS_ENTRY_CONVENTIONS,
)
from desloppify.languages._framework.node.frameworks.nestjs import (
    scan_providers_missing_injectable,
    scan_unregistered_controllers,
)
from desloppify.languages._framework.node.js_classes import (
    constructor_params,
    iter_classes,
)
from desloppify.languages._framework.node.js_text import code_text
from desloppify.languages.typescript import TypeScriptConfig
from desloppify.languages.typescript.phases_coupling import _declares_injected_class

_NEST = '{"dependencies": {"@nestjs/core": "^11.0.0", "@nestjs/common": "^11.0.0"}}'


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


# ── class reader ─────────────────────────────────────────────


def test_class_reader_finds_decorators_and_constructor():
    text = (
        "@UseGuards(AuthGuard('jwt'), RolesGuard)\n"
        "@Controller({ path: 'users', version: '1' })\n"
        "export class UsersController extends Base<{ id: string }> implements OnInit {\n"
        "  constructor(\n"
        "    private readonly users: UsersService,\n"
        "    @Inject(forwardRef(() => Mail)) mail: Mail,\n"
        "  ) { super(); }\n"
        "  method() { const c = class Inner {}; }\n"
        "}\n"
    )
    code = code_text(text)
    outer, inner = iter_classes(text, code)
    assert (outer.name, outer.line) == ("UsersController", 3)
    assert [d.name for d in outer.decorators] == ["UseGuards", "Controller"]
    args = outer.decorators[1].args
    assert args is not None and "path" in text[args[0] : args[1]]
    params = constructor_params(code, outer)
    assert params is not None
    assert [text[a:b].strip().split(":")[0] for a, b in params] == [
        "private readonly users",
        "@Inject(forwardRef(() => Mail)) mail",
    ]
    assert inner.name == "Inner" and inner.decorators == ()
    assert constructor_params(code, inner) is None


def test_class_reader_ignores_strings_and_comments():
    text = "const s = 'class Fake {}';\n// class AlsoFake {}\nclass Real {}\n"
    assert [c.name for c in iter_classes(text)] == ["Real"]


# ── detection and entries ────────────────────────────────────


def test_detected_from_nest_core(tmp_path: Path):
    _write(tmp_path, "package.json", _NEST)
    assert "nestjs" in detect_ecosystem_frameworks(tmp_path, None, "node").present


def test_not_detected_without_nest(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"express": "^4.0.0"}}')
    assert "nestjs" not in detect_ecosystem_frameworks(tmp_path, None, "node").present


def test_nest_cli_entry_files(tmp_path: Path):
    _write(
        tmp_path,
        "nest-cli.json",
        '{"sourceRoot": "apps/api/src", "entryFile": "server", "monorepo": true,'
        ' "projects": {"worker": {"sourceRoot": "apps/worker/src"},'
        ' "lib": {"type": "library", "sourceRoot": "libs/lib/src", "entryFile": "index"}}}',
    )
    for name in ("apps/api/src/server.ts", "apps/worker/src/main.ts", "libs/lib/src/index.ts"):
        _write(tmp_path, name, "export {}\n")
    assert NESTJS_ENTRY_CONVENTIONS.applies_to(tmp_path)
    assert NESTJS_ENTRY_CONVENTIONS.declared_entries is not None
    assert NESTJS_ENTRY_CONVENTIONS.declared_entries(tmp_path) == {
        "apps/api/src/server.ts",
        "apps/worker/src/main.ts",
        "libs/lib/src/index.ts",
    }


# ── single-use suppression ───────────────────────────────────


def test_injected_classes_are_exempt_from_single_use(tmp_path: Path):
    _write(tmp_path, "package.json", _NEST)
    guard = _write(tmp_path, "src/roles.guard.ts", "@Injectable()\nexport class RolesGuard {}\n")
    helper = _write(tmp_path, "src/helper.ts", "export function helper() { return 1 }\n")
    decorators = injected_class_decorators(tmp_path, None)
    assert {"Module", "Injectable", "Controller"} <= decorators
    assert _declares_injected_class(str(guard), decorators)
    assert not _declares_injected_class(str(helper), decorators)


def test_no_exemption_without_nest(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"express": "^4.0.0"}}')
    assert injected_class_decorators(tmp_path, None) == frozenset()


# ── scanners ─────────────────────────────────────────────────


def _app(tmp_path: Path, module: str) -> None:
    _write(tmp_path, "package.json", _NEST)
    _write(tmp_path, "src/app.module.ts", module)
    _write(
        tmp_path,
        "src/cats.controller.ts",
        "@Controller('cats')\nexport class CatsController {\n"
        "  constructor(private readonly cats: CatsService) {}\n}\n",
    )
    _write(
        tmp_path,
        "src/dogs.controller.ts",
        "@Controller('dogs')\nexport class DogsController {}\n",
    )


def test_unregistered_controller(tmp_path: Path):
    _app(tmp_path, "@Module({ controllers: [CatsController], providers: [] })\nexport class AppModule {}\n")
    entries, _ = scan_unregistered_controllers(tmp_path)
    assert [(e["file"], e["name"], e["line"]) for e in entries] == [
        ("src/dogs.controller.ts", "DogsController", 2)
    ]


def test_controllers_hidden_behind_a_spread_are_not_judged(tmp_path: Path):
    _app(
        tmp_path,
        "const controllers = [CatsController, DogsController];\n"
        "@Module({ controllers: [...controllers] })\nexport class AppModule {}\n",
    )
    assert scan_unregistered_controllers(tmp_path)[0] == []


def test_controller_in_a_test_file_is_ignored(tmp_path: Path):
    _app(tmp_path, "@Module({ controllers: [CatsController, DogsController] })\nexport class AppModule {}\n")
    _write(tmp_path, "src/cats.controller.spec.ts", "@Controller()\nclass FakeController {}\n")
    assert scan_unregistered_controllers(tmp_path)[0] == []


def test_provider_missing_injectable(tmp_path: Path):
    _write(tmp_path, "package.json", _NEST)
    _write(
        tmp_path,
        "src/app.module.ts",
        "@Module({\n"
        "  providers: [\n"
        "    CatsService,\n"
        "    Plain,\n"
        "    Tokens,\n"
        "    { provide: APP_GUARD, useClass: RolesGuard },\n"
        "    { provide: Manual, useValue: new Manual(1) },\n"
        "    Decorated,\n"
        "  ],\n"
        "})\nexport class AppModule {}\n",
    )
    _write(tmp_path, "src/cats.service.ts", "export class CatsService {\n  constructor(private readonly repo: Repo) {}\n}\n")
    _write(tmp_path, "src/plain.ts", "export class Plain {\n  run() { return 1 }\n}\n")
    _write(tmp_path, "src/tokens.ts", "export class Tokens {\n  constructor(@Inject(TOKEN) private t: string) {}\n}\n")
    _write(tmp_path, "src/roles.guard.ts", "export class RolesGuard {\n  constructor(private reflector: Reflector) {}\n}\n")
    _write(tmp_path, "src/manual.ts", "export class Manual {\n  constructor(private n: number) {}\n}\n")
    _write(tmp_path, "src/decorated.ts", "@Injectable()\nexport class Decorated {\n  constructor(private a: A) {}\n}\n")

    entries, _ = scan_providers_missing_injectable(tmp_path)
    assert sorted(e["name"] for e in entries) == ["CatsService", "RolesGuard"]


def test_nestjs_smells_phase(tmp_path: Path):
    _app(tmp_path, "@Module({ controllers: [CatsController], providers: [CatsService] })\nexport class AppModule {}\n")
    _write(tmp_path, "src/cats.service.ts", "export class CatsService {\n  constructor(private readonly repo: Repo) {}\n}\n")

    phase = next(p for p in TypeScriptConfig().phases if p.label == "NestJS framework smells")
    issues, _ = phase.run(tmp_path, _FakeLang())
    ids = {issue["id"] for issue in issues}
    assert ids == {
        "nestjs::src/dogs.controller.ts::unregistered_controller::DogsController",
        "nestjs::src/cats.service.ts::provider_missing_injectable::CatsService",
    }
