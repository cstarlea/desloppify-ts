"""Tests for the React Router v7 / Remix framework spec (TypeScript)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from desloppify.engine.detectors.orphaned import (
    OrphanedDetectionOptions,
    detect_orphaned_files,
)
from desloppify.languages._framework.frameworks.detection import detect_ecosystem_frameworks
from desloppify.languages._framework.frameworks.specs.react_router import (
    REACT_ROUTER_ENTRY_CONVENTIONS,
)
from desloppify.languages._framework.node.frameworks.react_router import (
    declared_route_modules,
    route_config,
    scan_data_hooks_without_export,
    scan_remix_imports_in_react_router_v7,
    scan_route_config_missing_modules,
)
from desloppify.languages.typescript import TypeScriptConfig

_V7 = '{"dependencies": {"react-router": "^7.1.0"}, "devDependencies": {"@react-router/dev": "^7.1.0"}}'


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


# ── detection ────────────────────────────────────────────────


def test_detected_from_the_dev_package(tmp_path: Path):
    _write(tmp_path, "package.json", _V7)
    assert "react_router" in detect_ecosystem_frameworks(tmp_path, None, "node").present


def test_not_detected_for_a_library_mode_spa(tmp_path: Path):
    # react-router alone is the routing library; framework mode needs the dev package.
    _write(tmp_path, "package.json", '{"dependencies": {"react-router": "^7.1.0"}}')
    assert "react_router" not in detect_ecosystem_frameworks(tmp_path, None, "node").present


def test_detected_for_remix_v2(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"@remix-run/react": "^2.0.0"}}')
    assert "react_router" in detect_ecosystem_frameworks(tmp_path, None, "node").present


# ── route config ─────────────────────────────────────────────

_ROUTES = """\
import { type RouteConfig, index, layout, prefix, route } from "@react-router/dev/routes";

export default [
  index("./home.tsx"),
  // route("old", "routes/old.tsx"),
  layout("layouts/main.tsx", [
    route("about", "pages/about.tsx"),
    route(null, "pages/pathless"),
  ]),
  ...prefix("admin", [route(":id", "../shared/admin.tsx")]),
  { path: "legacy", file: "pages/legacy.tsx" },
] satisfies RouteConfig;
"""


def test_route_config_names_every_module(tmp_path: Path):
    _write(tmp_path, "app/routes.ts", _ROUTES)
    for name in ("app/home.tsx", "app/layouts/main.tsx", "app/pages/about.tsx",
                 "app/pages/pathless.tsx", "shared/admin.tsx", "app/pages/legacy.tsx"):
        _write(tmp_path, name, "export default function X() { return null }\n")

    assert declared_route_modules(tmp_path) == {
        "app/home.tsx",
        "app/layouts/main.tsx",
        "app/pages/about.tsx",
        "app/pages/pathless.tsx",
        "shared/admin.tsx",
        "app/pages/legacy.tsx",
    }


def test_route_config_honours_app_directory(tmp_path: Path):
    _write(tmp_path, "react-router.config.ts", 'export default { appDirectory: "src" }\n')
    _write(tmp_path, "src/routes.ts", 'export default [index("pages/home.tsx")]\n')
    _write(tmp_path, "src/pages/home.tsx", "export default function Home() { return null }\n")

    config = route_config(tmp_path)
    assert config.config_file == "src/routes.ts"
    assert declared_route_modules(tmp_path) == {"src/pages/home.tsx"}


def test_missing_route_module_is_reported(tmp_path: Path):
    _write(tmp_path, "app/routes.ts", _ROUTES)
    _write(tmp_path, "app/home.tsx", "export default function X() { return null }\n")

    entries, _ = scan_route_config_missing_modules(tmp_path)
    missing = {e["module"] for e in entries}
    assert "./home.tsx" not in missing
    assert "pages/about.tsx" in missing
    assert "routes/old.tsx" not in missing  # commented out
    assert all(e["file"] == "app/routes.ts" for e in entries)
    about = next(e for e in entries if e["module"] == "pages/about.tsx")
    assert about["line"] == 7


def test_no_route_config_no_findings(tmp_path: Path):
    _write(tmp_path, "app/routes/home.tsx", "export default function X() { return null }\n")
    assert scan_route_config_missing_modules(tmp_path) == ([], 0)


# ── Remix imports in v7 ──────────────────────────────────────


def test_remix_import_in_v7_app(tmp_path: Path):
    _write(tmp_path, "package.json", _V7)
    _write(
        tmp_path,
        "app/routes/home.tsx",
        'import { useLoaderData } from "@remix-run/react";\n'
        'import { json } from "@remix-run/node";\n'
        "export const loader = () => json({});\n",
    )
    _write(tmp_path, "app/root.tsx", 'import { Outlet } from "react-router";\n')

    entries, _ = scan_remix_imports_in_react_router_v7(tmp_path)
    assert [(e["file"], e["module"], e["successor"]) for e in entries] == [
        ("app/routes/home.tsx", "@remix-run/react", "react-router")
    ]


def test_remix_imports_fine_in_remix_v2(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"@remix-run/react": "^2.0.0"}}')
    _write(tmp_path, "app/routes/home.tsx", 'import { useLoaderData } from "@remix-run/react";\n')
    assert scan_remix_imports_in_react_router_v7(tmp_path) == ([], 0)


# ── data hooks without a loader/action ───────────────────────


def test_data_hook_without_loader(tmp_path: Path):
    _write(
        tmp_path,
        "app/routes/about.tsx",
        'import { useLoaderData, useActionData } from "react-router";\n'
        "export async function action() { return null }\n"
        "export default function About() {\n"
        "  const data = useLoaderData<{ x: number }>();\n"
        "  const result = useActionData();\n"
        "  return data.x;\n"
        "}\n",
    )
    entries, scanned = scan_data_hooks_without_export(tmp_path)
    assert scanned == 1
    assert [(e["file"], e["hook"], e["line"]) for e in entries] == [
        ("app/routes/about.tsx", "useLoaderData", 4)
    ]


@pytest.mark.parametrize(
    "export",
    [
        "export async function loader() { return {} }",
        "export const clientLoader = async () => ({})",
        'export { loader } from "./about.server"',
        "const loader = () => ({});\nexport { loader as other, loader };",
        'export * from "./about.server"',
    ],
)
def test_data_hook_with_loader(tmp_path: Path, export: str):
    _write(
        tmp_path,
        "app/routes/about.tsx",
        f"{export}\nexport default function About() {{ return useLoaderData() }}\n",
    )
    assert scan_data_hooks_without_export(tmp_path)[0] == []


def test_data_hook_outside_route_modules_is_fine(tmp_path: Path):
    # A component reads the data of the route that renders it.
    _write(tmp_path, "app/routes/notes/editor.tsx", "export function E() { return useLoaderData() }\n")
    _write(tmp_path, "app/components/user.tsx", "export function U() { return useLoaderData() }\n")
    _write(tmp_path, "app/routes/home.test.tsx", "test('x', () => useLoaderData())\n")
    assert scan_data_hooks_without_export(tmp_path)[0] == []


def test_data_hook_in_root_and_folder_route_modules(tmp_path: Path):
    _write(tmp_path, "app/root.tsx", "export default function App() { return useLoaderData() }\n")
    _write(tmp_path, "app/routes/notes/route.tsx", "export default function N() { return useLoaderData() }\n")
    files = {e["file"] for e in scan_data_hooks_without_export(tmp_path)[0]}
    assert files == {"app/root.tsx", "app/routes/notes/route.tsx"}


def test_data_hook_in_a_configured_route_module(tmp_path: Path):
    _write(tmp_path, "app/routes.ts", 'export default [index("pages/home.tsx")]\n')
    _write(tmp_path, "app/pages/home.tsx", "export default function H() { return useLoaderData() }\n")
    files = {e["file"] for e in scan_data_hooks_without_export(tmp_path)[0]}
    assert files == {"app/pages/home.tsx"}


# ── entry conventions ────────────────────────────────────────


def test_route_config_modules_are_not_orphans(tmp_path: Path):
    _write(tmp_path, "package.json", _V7)
    _write(tmp_path, "app/routes.ts", 'export default [index("pages/home.tsx")]\n' + "//\n" * 12)
    body = "export default function X() { return null }\n" + "// pad\n" * 12
    home = _write(tmp_path, "app/pages/home.tsx", body)
    rsc = _write(tmp_path, "app/entry.rsc.tsx", body)
    orphan = _write(tmp_path, "app/pages/unused.tsx", body)
    graph = {
        str(p): {"importer_count": 0, "import_count": 0}
        for p in (tmp_path / "app/routes.ts", home, rsc, orphan)
    }

    entries, _ = detect_orphaned_files(
        tmp_path,
        graph,
        [".ts", ".tsx"],
        options=OrphanedDetectionOptions(entry_conventions=(REACT_ROUTER_ENTRY_CONVENTIONS,)),
    )
    assert [e["file"] for e in entries] == [str(orphan)]


def test_conventions_need_the_framework(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies": {"express": "^4.0.0"}}')
    assert REACT_ROUTER_ENTRY_CONVENTIONS.applies_to(tmp_path) is False


# ── phase ────────────────────────────────────────────────────


def test_react_router_smells_phase(tmp_path: Path):
    _write(tmp_path, "package.json", _V7)
    _write(tmp_path, "app/routes.ts", 'export default [index("routes/missing.tsx")]\n')
    _write(tmp_path, "app/root.tsx", 'import { Outlet } from "@remix-run/react";\n')

    phase = next(
        p for p in TypeScriptConfig().phases if p.label == "React Router framework smells"
    )
    issues, potentials = phase.run(tmp_path, _FakeLang())
    ids = {issue["id"] for issue in issues}
    assert "react_router::app/routes.ts::route_module_missing::routes/missing.tsx" in ids
    assert "react_router::app/root.tsx::remix_import_in_v7::@remix-run/react" in ids
    assert potentials.get("react_router", 0) >= 1
