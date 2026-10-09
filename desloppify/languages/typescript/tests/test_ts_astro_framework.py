"""Tests for the Astro framework scanners (TypeScript)."""

from __future__ import annotations

from pathlib import Path

import pytest

from desloppify.languages._framework.frameworks.specs.astro import ASTRO_SPEC
from desloppify.languages._framework.node.frameworks.astro import (
    scan_astro_glob,
    scan_client_directives_on_astro_components,
    scan_server_env_in_client_scripts,
)


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _package(tmp_path: Path, version: str = "^5.1.0") -> None:
    _write(tmp_path, "package.json", f'{{"dependencies": {{"astro": "{version}"}}}}')


# ── Astro.glob ───────────────────────────────────────────────


def test_astro_glob(tmp_path: Path):
    _package(tmp_path)
    _write(
        tmp_path,
        "src/pages/blog.astro",
        "---\nconst posts = await Astro.glob('../posts/*.md');\nconst more = await Astro.glob('../more/*.md');\n---\n"
        "<p>Astro.glob() is gone</p>\n",
    )
    _write(tmp_path, "src/pages/index.astro", "---\nconst posts = import.meta.glob('../posts/*.md');\n---\n")
    entries, scanned = scan_astro_glob(tmp_path)
    assert scanned == 2
    assert [(Path(e["file"]).name, e["line"], e["count"]) for e in entries] == [("blog.astro", 2, 2)]


def test_astro_glob_fine_before_astro_5(tmp_path: Path):
    _package(tmp_path, "^4.16.0")
    _write(tmp_path, "src/pages/blog.astro", "---\nconst posts = await Astro.glob('../posts/*.md');\n---\n")
    assert scan_astro_glob(tmp_path) == ([], 0)


# ── client directives on Astro components ────────────────────


def test_client_directive_on_astro_component(tmp_path: Path):
    _package(tmp_path)
    _write(
        tmp_path,
        "src/pages/index.astro",
        "---\n"
        "import Header from '../components/Header.astro';\n"
        "import Counter from '../components/Counter.tsx';\n"
        "import Card, { type Props } from '../components/Card.astro';\n"
        "---\n"
        "<Header\n  title=\"x\"\n  client:load\n/>\n"
        "<Counter client:visible />\n"
        "<!-- <Card client:idle /> -->\n"
        "<Card title={`a > b`} client:idle>text</Card>\n"
        "<CardList client:load />\n",
    )
    entries, scanned = scan_client_directives_on_astro_components(tmp_path)
    assert scanned == 1
    assert sorted((e["component"], e["line"], e["directive"]) for e in entries) == [
        ("Card", 12, "client:idle"),
        ("Header", 8, "client:load"),
    ]


# ── server env in client scripts ─────────────────────────────


def test_server_env_in_client_script(tmp_path: Path):
    _package(tmp_path)
    _write(
        tmp_path,
        "src/components/Analytics.astro",
        "---\nconst key = import.meta.env.SECRET_KEY;\n---\n"
        "<div data-key={key}></div>\n"
        "<script>\n"
        "  import { API_TOKEN } from 'astro:env/server';\n"
        "  const id = import.meta.env.PUBLIC_ANALYTICS_ID;\n"
        "  const token = import.meta.env.ANALYTICS_TOKEN;\n"
        "  if (import.meta.env.DEV) console.log(import.meta.env.BASE_URL);\n"
        "</script>\n"
        "<script is:inline>\n  const t = import.meta.env.OTHER;\n</script>\n",
    )
    entries, scanned = scan_server_env_in_client_scripts(tmp_path)
    assert scanned == 1
    assert sorted((e["line"], e["name"]) for e in entries) == [(6, "astro:env/server"), (8, "ANALYTICS_TOKEN")]


def test_custom_env_prefix_skips_env_check(tmp_path: Path):
    _package(tmp_path)
    _write(tmp_path, "astro.config.mjs", "export default { vite: { envPrefix: ['PUBLIC_', 'APP_'] } };\n")
    _write(tmp_path, "src/components/A.astro", "<script>\n  const id = import.meta.env.APP_ID;\n</script>\n")
    assert scan_server_env_in_client_scripts(tmp_path) == ([], 0)


def test_spec_wires_scanners(tmp_path: Path):
    assert [rule.id for rule in ASTRO_SPEC.scanners] == [
        "server_env_in_client_script",
        "client_directive_on_astro_component",
        "deprecated_astro_glob",
    ]
    _package(tmp_path)
    _write(tmp_path, "src/pages/a.astro", "---\nimport B from './B.astro';\n---\n<B client:only=\"react\" />\n")
    rule = ASTRO_SPEC.scanners[1]
    entries, _ = rule.scan(tmp_path, None)
    issue = rule.issue_factory(entries[0])
    assert issue["detector"] == "astro"
    assert issue["id"].endswith("a.astro::client_directive_on_astro_component::B")
