"""Presets, configured layers and the coupling phase that reads them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from desloppify.app.commands.config import _known_preset
from desloppify.app.commands.helpers.lang import resolve_lang_settings
from desloppify.languages._framework.frameworks.detection import (
    detect_ecosystem_frameworks,
)
from desloppify.languages._framework.runtime_support.runtime import (
    LangRunOverrides,
    make_lang_run,
)
from desloppify.languages.framework import get_lang
from desloppify.languages.typescript import presets as presets_mod
from desloppify.languages.typescript.phases_coupling import phase_coupling


@pytest.fixture(autouse=True)
def _root(set_project_root):
    """Point the project root at tmp_path."""


def _write(root: Path, name: str, text: str = "export const x = 1;\n") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _lang(config: dict | None = None):
    ts = get_lang("typescript")
    settings = resolve_lang_settings(config or {}, ts)
    return make_lang_run(ts, overrides=LangRunOverrides(runtime_settings=settings))


def _coupling_issues(root: Path, config: dict | None = None) -> list[dict]:
    issues, _ = phase_coupling(root, _lang(config))
    return [i for i in issues if i["detector"] == "coupling"]


def _layered_app(root: Path) -> None:
    _write(root, "package.json", json.dumps({"name": "app"}))
    _write(root, "src/app/main.ts", "import { add } from '../features/cart/add';\nexport { add };\n")
    _write(root, "src/features/cart/add.ts", "import { s } from '../auth/session';\nexport const add = s;\n")
    _write(root, "src/features/auth/session.ts", "export const s = 1;\n")
    _write(root, "src/lib/format.ts", "import { add } from '../features/cart/add';\nexport const f = add;\n")


def test_no_layout_is_assumed(tmp_path):
    """The author's old src/shared + src/tools rule no longer applies by itself."""
    _write(tmp_path, "package.json", json.dumps({"name": "app"}))
    _write(tmp_path, "src/shared/a.ts", "import { t } from '../tools/editor/t';\nexport const a = t;\n")
    _write(tmp_path, "src/tools/editor/t.ts", "import { b } from '../viewer/b';\nexport const t = b;\n")
    _write(tmp_path, "src/tools/viewer/b.ts", "export const b = 1;\n")
    assert _coupling_issues(tmp_path) == []


def test_bulletproof_preset_reports_layer_and_slice_violations(tmp_path):
    _layered_app(tmp_path)
    issues = _coupling_issues(tmp_path, {"presets": ["bulletproof-react"]})
    summaries = sorted(i["summary"] for i in issues)
    assert summaries == [
        "Cross-slice import in features: cart→auth (src/features/auth/session.ts)",
        "Layer violation: shared imports features (src/features/cart/add.ts)",
    ]


def test_configured_layers_replace_presets(tmp_path):
    _layered_app(tmp_path)
    config = {
        "presets": ["bulletproof-react"],
        "languages": {
            "typescript": {
                "layers": [
                    {"name": "app", "paths": ["src/app"]},
                    {"name": "features", "paths": ["src/features"]},
                ]
            }
        },
    }
    assert _coupling_issues(tmp_path, config) == []


def test_marker_dependency_turns_preset_on(tmp_path):
    _write(tmp_path, "package.json", json.dumps({"devDependencies": {"steiger": "^0.5.0"}}))
    names = [p.name for p in presets_mod.active_presets(tmp_path, _lang())]
    assert names == ["feature-sliced"]
    layers = presets_mod.resolve_layers(tmp_path, _lang())
    assert [layer.name for layer in layers][-1] == "shared"


def test_presets_key_can_select_a_framework(tmp_path):
    _write(tmp_path, "package.json", json.dumps({"name": "app"}))
    lang = _lang({"presets": ["hono"]})
    present = detect_ecosystem_frameworks(tmp_path, lang, "node").present
    assert present["hono"]["selected"] is True
    assert "hono" not in detect_ecosystem_frameworks(tmp_path, _lang(), "node").present


def test_config_set_validates_preset_names():
    assert _known_preset("Feature-Sliced") == "feature-sliced"
    assert _known_preset("nextjs") == "nextjs"
    with pytest.raises(ValueError, match="Unknown preset 'tools'"):
        _known_preset("tools")


def test_shadcn_ui_dir_from_components_json(tmp_path):
    assert presets_mod.shadcn_ui_dirs(tmp_path) == ()
    _write(tmp_path, "components.json", json.dumps({"aliases": {"components": "@/components"}}))
    (tmp_path / "src/components/ui").mkdir(parents=True)
    assert presets_mod.shadcn_ui_dirs(tmp_path) == ("src/components/ui",)
    _write(tmp_path, "components.json", json.dumps({"aliases": {"ui": "~/ui"}}))
    (tmp_path / "ui").mkdir()
    assert presets_mod.shadcn_ui_dirs(tmp_path) == ("ui",)
