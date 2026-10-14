"""flat_dirs in the TypeScript structural phase: test files and file-based routers."""

from __future__ import annotations

from pathlib import Path

from desloppify_ts.base.discovery.file_paths import rel
from desloppify_ts.engine.policy.zones import FileZoneMap
from desloppify_ts.languages._framework.frameworks.registry import framework_route_roots
from desloppify_ts.languages.framework import get_lang, make_lang_run
from desloppify_ts.languages.typescript.phases_structural import _detect_flat_dirs


def _write(root: Path, names: list[str]) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export const x = 1;\n")


def _flat_dirs(root: Path) -> dict[str, str]:
    lang = make_lang_run(get_lang("typescript"))
    lang.zone_map = FileZoneMap(lang.file_finder(root), lang.zone_rules, rel_fn=rel)
    issues, _ = _detect_flat_dirs(root, lang)
    return {issue["file"]: issue["summary"] for issue in issues}


def test_co_located_tests_are_not_counted(set_project_root):
    root = set_project_root
    _write(root, ["package.json"])
    _write(root, [f"src/hooks/use-thing{i}.ts" for i in range(12)])
    _write(root, [f"src/hooks/use-thing{i}.test.ts" for i in range(12)])
    _write(root, [f"src/lib/mod{i}.ts" for i in range(22)])
    _write(root, [f"src/lib/mod{i}.test.ts" for i in range(22)])

    flagged = _flat_dirs(root)
    assert list(flagged) == ["src/lib"]
    assert "22 files" in flagged["src/lib"]


def test_next_app_router_dirs_skipped(set_project_root):
    root = set_project_root
    _write(root, ["package.json", "next.config.ts", "src/app/page.tsx"])
    _write(root, [f"src/app/segment{i}/page.tsx" for i in range(17)])
    _write(root, ["src/app/api/auth.ts"])
    _write(root, [f"src/app/api/v{i}/route.ts" for i in range(19)])
    _write(root, [f"src/app/(app)/part{i}.tsx" for i in range(22)])
    _write(root, [f"src/app/projects/[id]/part{i}.tsx" for i in range(22)])
    _write(root, [f"src/components/c{i}.tsx" for i in range(22)])

    assert framework_route_roots(root) == {str((root / "src/app").resolve())}
    assert list(_flat_dirs(root)) == ["src/components"]


def test_without_a_framework_app_dirs_are_ordinary(set_project_root):
    root = set_project_root
    _write(root, ["package.json"])
    _write(root, [f"src/app/api/v{i}/route.ts" for i in range(19)])
    _write(root, ["src/app/api/auth.ts"])

    assert framework_route_roots(root) == frozenset()
    assert list(_flat_dirs(root)) == ["src/app/api"]


def test_sveltekit_routes_root(set_project_root):
    root = set_project_root
    _write(root, ["svelte.config.js"])
    (root / "package.json").write_text('{"devDependencies": {"@sveltejs/kit": "2"}}')
    _write(root, ["src/routes/+page.ts"])
    _write(root, [f"src/routes/blog{i}/+page.ts" for i in range(17)])
    _write(root, [f"src/routes/[slug]/part{i}.ts" for i in range(22)])

    assert framework_route_roots(root) == {str((root / "src/routes").resolve())}
    assert _flat_dirs(root) == {}
