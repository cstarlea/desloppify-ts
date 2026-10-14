"""Entries loaded by URL: static asset dirs, <script src>, service workers; config files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import desloppify_ts.languages.typescript.detectors.deps as deps_detector_mod
import desloppify_ts.languages.typescript.detectors.deps.resolve as deps_resolve_mod
from desloppify_ts.engine.detectors.orphaned import (
    OrphanedDetectionOptions,
    detect_orphaned_files,
)
from desloppify_ts.engine.detectors.test_coverage.detector import run_test_coverage
from desloppify_ts.engine.policy.zones import (
    COMMON_ZONE_RULES,
    FileZoneMap,
    Zone,
    classify_file,
    should_skip_detector_in_zone,
)
from desloppify_ts.languages._framework.frameworks.registry import (
    framework_entry_conventions,
    framework_static_dirs,
)
from desloppify_ts.languages._framework.frameworks.specs.bundlers import (
    VITE_ENTRY_CONVENTIONS,
)
from desloppify_ts.languages._framework.frameworks.specs.nextjs import (
    NEXTJS_ENTRY_CONVENTIONS,
)
from desloppify_ts.languages._framework.frameworks.specs.sveltekit import (
    SVELTEKIT_ENTRY_CONVENTIONS,
)
from desloppify_ts.languages.typescript._zones import TS_ZONE_RULES
from desloppify_ts.languages.typescript.detectors.deps.resolver import (
    clear_resolver_cache,
)
from desloppify_ts.languages.typescript.detectors.deps.url_entries import url_entries

_FILLER = "\n".join(f"export const v{i} = {i};" for i in range(12)) + "\n"


@pytest.fixture(autouse=True)
def _root(set_project_root):
    deps_resolve_mod.load_tsconfig_paths_cached.cache_clear()
    clear_resolver_cache()
    yield
    clear_resolver_cache()


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _rel(root: Path, paths: set[str]) -> set[str]:
    return {Path(p).relative_to(root.resolve()).as_posix() for p in paths}


def _orphans(root: Path) -> list[str]:
    graph = deps_detector_mod.build_dep_graph(root)
    entries, _ = detect_orphaned_files(
        root,
        graph,
        extensions=[".ts", ".tsx", ".js", ".mjs"],
        options=OrphanedDetectionOptions(
            entry_files=url_entries(root, framework_static_dirs()),
            entry_conventions=framework_entry_conventions(),
        ),
    )
    return sorted(
        Path(e["file"]).relative_to(root.resolve()).as_posix() for e in entries
    )


def test_static_asset_dirs_are_entries():
    assert NEXTJS_ENTRY_CONVENTIONS.is_entry("public/sw.js")
    assert NEXTJS_ENTRY_CONVENTIONS.is_entry("public/vendor/lib.mjs")
    assert not NEXTJS_ENTRY_CONVENTIONS.is_entry("publicity/sw.js")
    assert not NEXTJS_ENTRY_CONVENTIONS.is_entry("src/public/sw.js")
    assert SVELTEKIT_ENTRY_CONVENTIONS.is_entry("static/sw.js")
    assert VITE_ENTRY_CONVENTIONS.is_entry("public/worker.js")
    assert set(framework_static_dirs()) == {"public", "static"}


def test_next_public_script_is_not_orphaned(tmp_path):
    _write(
        tmp_path,
        {
            "package.json": '{"dependencies": {"next": "15.0.0"}}',
            "next.config.ts": "export default {};\n",
            "public/sw.js": _FILLER,
            "src/lib/unused.ts": _FILLER,
        },
    )
    assert _orphans(tmp_path) == ["src/lib/unused.ts"]


def test_vite_public_needs_vite(tmp_path):
    _write(tmp_path, {"package.json": "{}", "public/worker.js": _FILLER})
    assert _orphans(tmp_path) == ["public/worker.js"]
    _write(tmp_path, {"package.json": '{"devDependencies": {"vite": "5.0.0"}}'})
    assert _orphans(tmp_path) == []


def test_html_script_src_relative_and_absolute(tmp_path):
    _write(
        tmp_path,
        {
            "tools/dash/public/index.html": (
                '<html><script type="module" src="/app.mjs"></script>\n'
                "<SCRIPT SRC='./lib/chart.js?v=2'></SCRIPT>\n"
                '<script src="https://cdn.example.com/x.js"></script>\n'
                '<script src="//cdn.example.com/y.js"></script></html>\n'
            ),
            "tools/dash/public/app.mjs": _FILLER,
            "tools/dash/public/lib/chart.js": _FILLER,
            "index.html": '<script type="module" src="/src/main.ts"></script>\n',
            "src/main.ts": _FILLER,
            "src/unused.ts": _FILLER,
        },
    )
    assert _rel(tmp_path, url_entries(tmp_path)) == {
        "tools/dash/public/app.mjs",
        "tools/dash/public/lib/chart.js",
        "src/main.ts",
    }


def test_service_worker_registration(tmp_path):
    _write(
        tmp_path,
        {
            "src/register.ts": (
                "export function register() {\n"
                "  navigator.serviceWorker.register('/sw.js', { scope: '/' });\n"
                "  navigator.serviceWorker.register(new URL('./worker.ts', import.meta.url));\n"
                "  navigator.serviceWorker.register(`/${name}.js`);\n"
                "}\n"
            ),
            "src/worker.ts": _FILLER,
            "public/sw.js": _FILLER,
        },
    )
    assert _rel(tmp_path, url_entries(tmp_path, ("public",))) == {
        "src/worker.ts",
        "public/sw.js",
    }
    # Without static dirs the absolute URL resolves from the root only.
    assert _rel(tmp_path, url_entries(tmp_path)) == {"src/worker.ts"}


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_gitignored_html_is_ignored(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write(
        tmp_path,
        {
            ".gitignore": "build/\n",
            "build/index.html": '<script src="/src/a.ts"></script>\n',
            "index.html": '<script src="/src/b.ts"></script>\n',
            "src/a.ts": _FILLER,
            "src/b.ts": _FILLER,
        },
    )
    assert _rel(tmp_path, url_entries(tmp_path)) == {"src/b.ts"}


@pytest.mark.parametrize(
    "rel_path",
    ["capacitor.config.ts", "vitest.integration.config.ts", "drizzle.config.ts"],
)
def test_config_files_are_never_orphaned_or_untested(rel_path):
    zone = classify_file(rel_path, TS_ZONE_RULES + COMMON_ZONE_RULES)
    assert zone == Zone.CONFIG
    assert should_skip_detector_in_zone(zone, "orphaned")
    assert should_skip_detector_in_zone(zone, "test_coverage")


def test_config_file_gets_no_coverage_issue(tmp_path):
    logic = "export function run(n: number): number {\n" + "  n += 1;\n" * 12
    logic += "  return n;\n}\n"
    _write(
        tmp_path,
        {
            "capacitor.config.ts": logic,
            "src/core.ts": logic,
            "src/core.test.ts": "import { run } from './core';\nit('x', () => run(1));\n",
        },
    )
    files = ["capacitor.config.ts", "src/core.ts", "src/core.test.ts"]
    graph = {
        "capacitor.config.ts": {"imports": set(), "importer_count": 0},
        "src/core.ts": {"imports": set(), "importer_count": 1},
        "src/core.test.ts": {"imports": {"src/core.ts"}},
    }
    zone_map = FileZoneMap(files, TS_ZONE_RULES + COMMON_ZONE_RULES)
    result = run_test_coverage(graph, zone_map, "typescript")
    assert "capacitor.config.ts" not in {e["file"] for e in result.entries}
    assert result.scored_files == 1
