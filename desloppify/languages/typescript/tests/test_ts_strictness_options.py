"""tsconfig strictness checks (roadmap 3.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from desloppify.languages.typescript.detectors.tsconfig_health import (
    _catalog_entry,
    detect_tsconfig_health,
)


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _write(root: Path, files: dict[str, object]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))


def _found(root: Path) -> set[tuple[str, str]]:
    result = detect_tsconfig_health(root)
    return {
        (Path(e["file"]).relative_to(root).as_posix(), e["check"])
        for e in result.entries
    }


def _app(
    options: dict, *, typescript: str = "^5.8.0", source: str = "export const a = 1;\n"
) -> dict[str, object]:
    return {
        "package.json": {
            "name": "app",
            "private": True,
            "type": "module",
            "devDependencies": {"typescript": typescript},
        },
        "tsconfig.json": {"compilerOptions": {"module": "esnext", **options}},
        "src/a.ts": source,
    }


_ALL_SET = {"noUncheckedIndexedAccess": True, "verbatimModuleSyntax": True}


def test_strict_never_turned_on(tmp_path):
    _write(tmp_path, _app({}))
    result = detect_tsconfig_health(tmp_path)
    assert [(e["check"], e["confidence"], e["tier"]) for e in result.entries] == [
        ("strict", "high", 3)
    ]
    # With strict off, the finer options aren't checked yet.
    assert result.population_size == 1


def test_strict_turned_off_is_a_decision_but_still_reported(tmp_path):
    _write(tmp_path, _app({"strict": False, **_ALL_SET}))
    entries = detect_tsconfig_health(tmp_path).entries
    assert [(e["check"], e["confidence"]) for e in entries] == [("strict", "medium")]


@pytest.mark.parametrize(
    "options, typescript",
    [
        ({"strict": True}, "^5.8.0"),
        ({}, "^6.0.0"),  # strict is the default from TypeScript 6
        ({"noImplicitAny": True, "strictNullChecks": True}, "^5.8.0"),
    ],
)
def test_strict_on(tmp_path, options, typescript):
    _write(tmp_path, _app({**options, **_ALL_SET}, typescript=typescript))
    assert _found(tmp_path) == set()


def test_unset_options_are_reported_and_set_ones_are_decisions(tmp_path):
    _write(tmp_path, _app({"strict": True}))
    assert _found(tmp_path) == {
        ("tsconfig.json", "noUncheckedIndexedAccess"),
        ("tsconfig.json", "verbatimModuleSyntax"),
    }
    _write(
        tmp_path,
        _app(
            {
                "strict": True,
                "noUncheckedIndexedAccess": False,
                "verbatimModuleSyntax": False,
            }
        ),
    )
    assert _found(tmp_path) == set()


def test_no_implicit_override_needs_a_subclass(tmp_path):
    source = "class Base {}\nexport class A<T extends object> extends Base {}\n"
    _write(tmp_path, _app({"strict": True, **_ALL_SET}, source=source))
    assert _found(tmp_path) == {("tsconfig.json", "noImplicitOverride")}


@pytest.mark.parametrize(
    "module, package_type",
    [("commonjs", "module"), ("nodenext", "commonjs"), (None, "module")],
)
def test_verbatim_module_syntax_only_for_es_modules(tmp_path, module, package_type):
    files = _app({"strict": True, "noUncheckedIndexedAccess": True})
    files["package.json"]["type"] = package_type  # type: ignore[index]
    options = files["tsconfig.json"]["compilerOptions"]  # type: ignore[index]
    if module is None:
        del options["module"]  # TypeScript 5's default target is ES5, so CommonJS
    else:
        options["module"] = module
    _write(tmp_path, files)
    assert _found(tmp_path) == set()


def test_well_known_bases_count_and_unknown_ones_are_never_reported(tmp_path):
    _write(tmp_path, _app({}))
    _write(
        tmp_path,
        {
            "tsconfig.json": {
                "extends": "@tsconfig/strictest/tsconfig.json",
                "compilerOptions": {"module": "esnext"},
            }
        },
    )
    # strictest sets everything but verbatimModuleSyntax (not installed: from the table).
    assert _found(tmp_path) == {("tsconfig.json", "verbatimModuleSyntax")}
    _write(tmp_path, {"tsconfig.json": {"extends": "@acme/tsconfig"}})
    assert _found(tmp_path) == set()


def test_installed_base_is_read(tmp_path):
    _write(tmp_path, _app({}))
    _write(
        tmp_path,
        {
            "tsconfig.json": {
                "extends": "@acme/tsconfig",
                "compilerOptions": {"module": "esnext"},
            },
            "node_modules/@acme/tsconfig/tsconfig.json": {
                "compilerOptions": {"strict": False}
            },
        },
    )
    assert _found(tmp_path) == {("tsconfig.json", "strict")}


def _monorepo(packages: dict[str, dict], base: dict) -> dict[str, object]:
    files: dict[str, object] = {
        "package.json": {
            "name": "root",
            "private": True,
            "devDependencies": {"typescript": "catalog:"},
        },
        "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\ncatalog:\n  typescript: ^5.9.0\n",
        "tsconfig.base.json": {"compilerOptions": {"module": "esnext", **base}},
    }
    for name, options in packages.items():
        files[f"packages/{name}/package.json"] = {"name": name, "type": "module"}
        files[f"packages/{name}/tsconfig.json"] = {
            "extends": "../../tsconfig.base.json",
            "compilerOptions": options,
        }
        files[f"packages/{name}/src/index.ts"] = "export const x = 1;\n"
    return files


def test_monorepo_reports_unset_options_once_on_the_shared_base(tmp_path):
    _write(
        tmp_path,
        _monorepo({"a": {}, "b": {}, "c": {"strict": False}}, {"strict": True}),
    )
    assert _found(tmp_path) == {
        ("tsconfig.base.json", "noUncheckedIndexedAccess"),
        ("tsconfig.base.json", "verbatimModuleSyntax"),
        ("packages/c/tsconfig.json", "strict"),
    }


def test_drift_between_packages(tmp_path):
    strictest = {"strict": True, **_ALL_SET, "noImplicitReturns": True}
    _write(
        tmp_path,
        _monorepo({"a": {}, "b": {}, "c": {"noImplicitReturns": False}}, strictest),
    )
    entries = detect_tsconfig_health(tmp_path).entries
    assert [
        (Path(e["file"]).relative_to(tmp_path).as_posix(), e["check"]) for e in entries
    ] == [("packages/c/tsconfig.json", "drift")]
    assert entries[0]["detail"]["options"] == ["noImplicitReturns (2/3)"]


def test_private_projects_in_a_monorepo_get_only_the_strict_check(tmp_path):
    files = _monorepo({"a": {}}, {"strict": True, **_ALL_SET})
    files["examples/demo/package.json"] = {
        "name": "demo",
        "private": True,
        "type": "module",
    }
    files["examples/demo/tsconfig.json"] = {
        "compilerOptions": {"module": "esnext", "strict": True}
    }
    files["examples/demo/index.ts"] = "export {};\n"
    _write(tmp_path, files)
    assert _found(tmp_path) == set()


def test_pnpm_catalog_entries():
    lines = (
        "catalog:\n  typescript: ^5.9.0\n  'zod': 3\n"
        "catalogs:\n  ts-next:\n    typescript: '^6.0.3' # next\n  other:\n    vite: 8\n"
    ).splitlines()
    assert _catalog_entry(lines, "default", "typescript") == "^5.9.0"
    assert _catalog_entry(lines, "ts-next", "typescript") == "^6.0.3"
    assert _catalog_entry(lines, "other", "typescript") is None
