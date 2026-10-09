"""Tests for the Angular framework spec (TypeScript)."""

from __future__ import annotations

from pathlib import Path

import pytest

from desloppify.languages._framework.frameworks.detection import (
    detect_ecosystem_frameworks,
    injected_class_decorators,
)
from desloppify.languages._framework.frameworks.specs.angular import (
    ANGULAR_ENTRY_CONVENTIONS,
)
from desloppify.languages._framework.node.frameworks.angular import (
    scan_missing_component_resources,
    scan_standalone_mismatches,
    workspace_entries,
)
from desloppify.languages.typescript import TypeScriptConfig


@pytest.fixture(autouse=True)
def _root(tmp_path, set_project_root):
    """Point PROJECT_ROOT at the tmp directory via RuntimeContext."""


def _write(tmp_path: Path, name: str, content: str = "export {}\n") -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _package(tmp_path: Path, version: str) -> None:
    _write(
        tmp_path,
        "package.json",
        f'{{"dependencies": {{"@angular/core": "{version}"}}}}',
    )


def test_detected_from_angular_core(tmp_path: Path):
    _package(tmp_path, "^19.0.0")
    assert "angular" in detect_ecosystem_frameworks(tmp_path, None, "node").present
    assert {"Component", "NgModule", "Injectable"} <= injected_class_decorators(
        tmp_path, None
    )


# ── workspace entries ────────────────────────────────────────


def test_angular_json_entries(tmp_path: Path):
    _write(
        tmp_path,
        "angular.json",
        """{"projects": {"app": {"architect": {
          "build": {"options": {"browser": "src/main.ts", "polyfills": ["zone.js", "src/polyfills.ts"],
                    "server": "src/main.server.ts", "ssr": {"entry": "src/server.ts"}, "tsConfig": "tsconfig.app.json"},
                    "configurations": {"production": {"fileReplacements": [
                      {"replace": "src/environments/environment.ts", "with": "src/environments/environment.prod.ts"}]}}},
          "test": {"options": {"main": "src/test.ts", "karmaConfig": "karma.conf.js"}}}}}}""",
    )
    for name in (
        "src/main.ts",
        "src/polyfills.ts",
        "src/main.server.ts",
        "src/server.ts",
        "src/environments/environment.ts",
        "src/environments/environment.prod.ts",
        "src/test.ts",
        "karma.conf.js",
    ):
        _write(tmp_path, name)
    assert workspace_entries(tmp_path) == {
        "src/main.ts",
        "src/polyfills.ts",
        "src/main.server.ts",
        "src/server.ts",
        "src/environments/environment.ts",
        "src/environments/environment.prod.ts",
        "src/test.ts",
        "karma.conf.js",
    }


def test_nx_project_json_entries(tmp_path: Path):
    _package(tmp_path, "17.3.2")
    _write(
        tmp_path,
        "apps/web/project.json",
        '{"targets": {"build": {"options": {"main": "apps/web/src/main.ts",'
        ' "polyfills": "apps/web/src/polyfills.ts"}}}}',
    )
    _write(tmp_path, "apps/web/src/main.ts")
    _write(tmp_path, "apps/web/src/polyfills.ts")
    _write(
        tmp_path,
        "node_modules/x/project.json",
        '{"targets": {"b": {"options": {"main": "x.ts"}}}}',
    )
    assert ANGULAR_ENTRY_CONVENTIONS.applies_to(tmp_path)
    assert workspace_entries(tmp_path) == {
        "apps/web/src/main.ts",
        "apps/web/src/polyfills.ts",
    }


def test_test_setup_is_an_entry_at_any_depth():
    assert ANGULAR_ENTRY_CONVENTIONS.is_entry(
        "libs/web/album/feature/detail/src/test-setup.ts"
    )
    assert not ANGULAR_ENTRY_CONVENTIONS.is_entry(
        "libs/web/album/feature/detail/src/setup.ts"
    )


# ── scanners ─────────────────────────────────────────────────


def test_missing_component_resources(tmp_path: Path):
    _package(tmp_path, "^19.0.0")
    _write(
        tmp_path,
        "src/app/card.component.ts",
        "import { Component } from '@angular/core';\n"
        "@Component({\n"
        "  selector: 'app-card',\n"
        "  templateUrl: './card.component.html',\n"
        "  styleUrls: ['./card.component.scss', './missing.scss'],\n"
        "})\n"
        "export class CardComponent {}\n",
    )
    _write(tmp_path, "src/app/card.component.scss", "")
    entries, scanned = scan_missing_component_resources(tmp_path)
    assert scanned == 1
    assert [(e["line"], e["resource"]) for e in entries] == [
        (4, "./card.component.html"),
        (5, "./missing.scss"),
    ]


_DECLARABLES = """\
import { Component, Pipe, NgModule } from '@angular/core';
@Component({ selector: 'a-new', template: '' })
export class NewComponent {}
@Component({ selector: 'a-old', template: '', standalone: false })
export class OldComponent {}
@Pipe({ name: 'p', standalone: true })
export class PPipe {}
@NgModule({ declarations: [OldComponent, NewComponent], imports: [PPipe, OldComponent] })
export class FeatureModule {}
"""


def test_standalone_mismatches_angular_19(tmp_path: Path):
    _package(tmp_path, "^19.2.0")
    _write(tmp_path, "src/app/feature.ts", _DECLARABLES)
    entries, _ = scan_standalone_mismatches(tmp_path)
    assert sorted((e["key"], e["name"]) for e in entries) == [
        ("declarations", "NewComponent"),
        ("imports", "OldComponent"),
    ]


def test_standalone_default_before_19(tmp_path: Path):
    # Before Angular 19 a declarable is not standalone unless it says so.
    _package(tmp_path, "~17.3.0")
    _write(tmp_path, "src/app/feature.ts", _DECLARABLES)
    entries, _ = scan_standalone_mismatches(tmp_path)
    assert sorted((e["key"], e["name"]) for e in entries) == [
        ("imports", "OldComponent")
    ]


def test_angular_phase(tmp_path: Path):
    _package(tmp_path, "^19.0.0")
    _write(tmp_path, "src/app/feature.ts", _DECLARABLES)
    phase = next(
        p for p in TypeScriptConfig().phases if p.label == "Angular framework smells"
    )
    issues, _ = phase.run(tmp_path, None)
    assert (
        "angular::src/app/feature.ts::standalone_mismatch::declarations::NewComponent"
        in {issue["id"] for issue in issues}
    )
