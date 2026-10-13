"""Scanning ``src/`` (the default ``--path``) still applies the package's frameworks.

``package.json`` and ``next.config.*`` / ``svelte.config.js`` live in the
package root above ``src/``; framework conventions must be detected there,
not in the scan path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.engine.detectors.orphaned import (
    FrameworkConventions,
    framework_package_roots,
    nearest_package_root,
)
from desloppify.languages._framework.frameworks.phases import framework_phases
from desloppify.languages._framework.frameworks.registry import (
    framework_entry_conventions,
    framework_route_roots,
)
from desloppify.languages.framework import (
    framework_summary_lines,
    get_lang,
    make_lang_run,
)
from desloppify.languages.typescript.tests.golden import harness

_COMPONENT = (
    "export default function Page() {\n"
    + "".join(f"  const v{i} = {i};\n" for i in range(10))
    + "  return <main>{v0 + v9}</main>;\n}\n"
)
_MODULE = "".join(f"export const v{i} = {i};\n" for i in range(12))


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _next_project(root: Path) -> Path:
    _write(
        root,
        {
            "package.json": '{"name": "web", "dependencies": {"next": "16.0.0", '
            '"react": "19.0.0"}}',
            "next.config.ts": "export default {};\n",
            "tsconfig.json": '{"compilerOptions": {"jsx": "preserve", "strict": true}}',
            "public/sw.js": "self.addEventListener('fetch', () => {});\n",
            "src/app/layout.tsx": _COMPONENT,
            "src/app/page.tsx": _COMPONENT,
            "src/app/(app)/projects/[id]/settings/export/page.tsx": (
                _COMPONENT
            ),
            "src/middleware.ts": _MODULE,
            **{f"src/app/segment{i}/page.tsx": _COMPONENT for i in range(17)},
            **{f"src/components/c{i}.tsx": _MODULE for i in range(22)},
        },
    )
    return root


def _ids(snapshot: dict, detector: str) -> list[str]:
    prefix = f"{detector}::"
    return [i["id"] for i in snapshot["issues"] if i["id"].startswith(prefix)]


def _framework_lines(project: Path, scan_path: Path) -> list[str]:
    with runtime_scope(RuntimeContext(project_root=project)):
        return framework_summary_lines(scan_path, make_lang_run(get_lang("typescript")))


def _applicable_framework_phases(project: Path, scan_path: Path) -> set[str]:
    with runtime_scope(RuntimeContext(project_root=project)):
        lang = make_lang_run(get_lang("typescript"))
        return {
            phase.label
            for phase in framework_phases("typescript")
            if phase.applies is not None and phase.applies(scan_path, lang)
        }


@pytest.fixture
def next_project(tmp_path):
    return _next_project(tmp_path / "web")


class TestNextScannedFromSrc:
    def test_orphaned_and_flat_dirs(self, next_project):
        snapshot = harness.scan_project(next_project, subdir="src")
        orphans = _ids(snapshot, "orphaned")
        assert not [o for o in orphans if o.startswith("orphaned::src/app/")]
        assert "orphaned::src/middleware.ts" not in orphans
        # The scan did run: plain components nobody imports are orphans.
        assert "orphaned::src/components/c0.tsx" in orphans

        flat = _ids(snapshot, "flat_dirs")
        assert not [f for f in flat if f.startswith("flat_dirs::src/app")]
        assert any(f.startswith("flat_dirs::src/components") for f in flat)

    def test_same_orphans_as_whole_project_scan(self, next_project):
        in_src = set(_ids(harness.scan_project(next_project, subdir="src"), "orphaned"))
        whole = set(_ids(harness.scan_project(next_project), "orphaned"))
        assert in_src == {o for o in whole if o.startswith("orphaned::src/")}

    def test_conventions_route_roots_and_static_dirs(self, next_project):
        src = next_project / "src"
        with runtime_scope(RuntimeContext(project_root=next_project)):
            assert nearest_package_root(src) == next_project.resolve()
            assert framework_package_roots(src) == [next_project.resolve()]
            conventions = FrameworkConventions(src, None, framework_entry_conventions())
            page = src / "app/(app)/projects/[id]/settings/export/page.tsx"
            assert conventions.is_entry(str(page))
            assert conventions.is_reserved_name(str(page))
            assert conventions.is_entry(str(next_project / "public/sw.js"))
            assert not conventions.is_entry(str(src / "components/c0.tsx"))
            assert framework_route_roots(src) == {str((src / "app").resolve())}

    def test_frameworks_line_and_phases(self, next_project):
        src = next_project / "src"
        assert _framework_lines(next_project, src) == ["Frameworks: nextjs"]
        assert "Next.js framework smells" in _applicable_framework_phases(
            next_project, src
        )

    def test_relative_scan_path_resolves_against_project_root(
        self, next_project, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        with runtime_scope(RuntimeContext(project_root=next_project)):
            assert framework_package_roots(Path("src")) == [next_project.resolve()]


class TestSvelteKitScannedFromSrc:
    @pytest.fixture
    def project(self, tmp_path):
        project = harness.copy_project("sveltekit-app", tmp_path)
        _write(
            project,
            {
                "static/sw.js": "self.addEventListener('fetch', () => {});\n",
                **{f"src/routes/blog{i}/+page.ts": _MODULE for i in range(17)},
                **{f"src/lib/util/u{i}.ts": _MODULE for i in range(22)},
            },
        )
        return project

    def test_orphaned_and_flat_dirs(self, project):
        snapshot = harness.scan_project(project, subdir="src")
        orphans = _ids(snapshot, "orphaned")
        assert "orphaned::src/lib/components/Unused.svelte" in orphans
        for path in ("src/routes/", "src/hooks.server.ts", "src/params/"):
            assert not [o for o in orphans if o.startswith(f"orphaned::{path}")]

        flat = _ids(snapshot, "flat_dirs")
        assert not [f for f in flat if f.startswith("flat_dirs::src/routes")]
        assert any(f.startswith("flat_dirs::src/lib/util") for f in flat)

    def test_route_roots_static_dirs_and_frameworks_line(self, project):
        src = project / "src"
        with runtime_scope(RuntimeContext(project_root=project)):
            assert framework_route_roots(src) == {str((src / "routes").resolve())}
            conventions = FrameworkConventions(src, None, framework_entry_conventions())
            assert conventions.is_entry(str(project / "static/sw.js"))
            assert conventions.is_entry(str(src / "routes/blog0/+page.ts"))
        line = _framework_lines(project, src)
        assert len(line) == 1 and "sveltekit" in line[0]
        assert "SvelteKit framework smells" in _applicable_framework_phases(
            project, src
        )


def test_monorepo_package_src_uses_that_package(tmp_path):
    root = tmp_path / "repo"
    _write(
        root,
        {
            "package.json": '{"private": true, "workspaces": ["apps/*"]}',
            "apps/web/package.json": '{"dependencies": {"next": "16.0.0"}}',
            "apps/web/next.config.ts": "export default {};\n",
            "apps/web/src/app/page.tsx": _COMPONENT,
        },
    )
    web = root / "apps/web"
    with runtime_scope(RuntimeContext(project_root=root)):
        assert framework_package_roots(web / "src") == [web.resolve()]
        conventions = FrameworkConventions(
            web / "src", None, framework_entry_conventions()
        )
        assert conventions.is_entry(str(web / "src/app/page.tsx"))
        assert framework_route_roots(web / "src") == {str((web / "src/app").resolve())}


def test_scan_without_any_package_falls_back_to_the_scan_path(tmp_path):
    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        (tmp_path / "src").mkdir()
        assert nearest_package_root(tmp_path / "src") is None
        assert framework_package_roots(tmp_path / "src") == [
            (tmp_path / "src").resolve()
        ]


def test_package_lookup_stops_at_the_project_root(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    with runtime_scope(RuntimeContext(project_root=project)):
        assert nearest_package_root(project / "src") is None
