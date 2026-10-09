"""Layout presets and the project-layout settings the coupling phase reads.

Nothing here assumes a directory layout by default. A preset is a named,
documented layout (its layers and their import rules); it applies when the
top-level ``presets`` config lists it, or when the package depends on one of
its marker packages (a linter that enforces that layout). The ``presets``
key may also name a framework spec, turning it on where detection misses it.
``languages.typescript.layers`` replaces any preset's layers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desloppify.base.discovery.paths import get_project_root
from desloppify.engine.detectors.coupling import Layer
from desloppify.engine.detectors.orphaned import package_dependency_names
from desloppify.languages._framework.base.types import LangRuntimeContract
from desloppify.languages._framework.frameworks.detection import (
    detect_ecosystem_frameworks,
)
from desloppify.languages._framework.frameworks.registry import (
    ensure_builtin_specs_loaded,
    list_framework_specs,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    layers: tuple[Layer, ...] = ()
    # Depending on one of these turns the preset on without config.
    marker_dependencies: tuple[str, ...] = ()


# https://feature-sliced.design/docs/reference/layers: a layer imports only
# the layers below it; slices of one layer don't import each other, except
# through an entity's ``@x`` public API.
FEATURE_SLICED = Preset(
    name="feature-sliced",
    description="Feature-Sliced Design: src/{app,processes,pages,widgets,features,entities,shared}",
    layers=(
        Layer("app", ("src/app",)),
        Layer("processes", ("src/processes",), sliced=True),
        Layer("pages", ("src/pages",), sliced=True),
        Layer("widgets", ("src/widgets",), sliced=True),
        Layer("features", ("src/features",), sliced=True),
        Layer("entities", ("src/entities",), sliced=True, cross_import_dir="@x"),
        Layer("shared", ("src/shared",)),
    ),
    marker_dependencies=(
        "steiger",
        "@feature-sliced/steiger-plugin",
        "@feature-sliced/eslint-config",
        "@conarti/eslint-plugin-feature-sliced",
    ),
)

# https://github.com/alan2207/bulletproof-react/blob/master/docs/project-structure.md:
# the app composes features, features don't import each other, and shared
# code imports neither.
BULLETPROOF_REACT = Preset(
    name="bulletproof-react",
    description="Bulletproof React: src/app over src/features/* over shared src/{components,hooks,lib,...}",
    layers=(
        Layer("app", ("src/app",)),
        Layer("features", ("src/features",), sliced=True),
        Layer(
            "shared",
            (
                "src/components",
                "src/config",
                "src/hooks",
                "src/lib",
                "src/stores",
                "src/types",
                "src/utils",
            ),
        ),
    ),
)

PRESETS: dict[str, Preset] = {p.name: p for p in (FEATURE_SLICED, BULLETPROOF_REACT)}


def preset_catalog() -> dict[str, str]:
    """Every name the ``presets`` config accepts, with a description."""
    ensure_builtin_specs_loaded()
    catalog = {name: preset.description for name, preset in PRESETS.items()}
    for framework_id, spec in sorted(list_framework_specs(ecosystem="node").items()):
        catalog.setdefault(
            framework_id, f"{spec.label} (framework; detected from package.json)"
        )
    return catalog


def _configured(lang: LangRuntimeContract | None, key: str) -> Any:
    getter = getattr(lang, "runtime_setting", None)
    return getter(key) if callable(getter) else None


def active_presets(path: Path, lang: LangRuntimeContract | None) -> list[Preset]:
    """Presets the config selects, then those a dependency turns on."""
    selected = [
        PRESETS[name] for name in _configured(lang, "presets") or () if name in PRESETS
    ]
    markers = [
        p for p in PRESETS.values() if p.marker_dependencies and p not in selected
    ]
    if markers:
        package_root = detect_ecosystem_frameworks(path, lang, "node").package_root
        deps = package_dependency_names(package_root)
        selected.extend(p for p in markers if deps.intersection(p.marker_dependencies))
    return selected


def _parse_layers(raw: object) -> tuple[Layer, ...]:
    if not isinstance(raw, list):
        return ()
    layers: list[Layer] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        paths = item.get("paths")
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not paths:
            logger.warning(
                "languages.typescript.layers: layer %r has no paths", item.get("name")
            )
            continue
        cross = item.get("cross_import_dir")
        layers.append(
            Layer(
                name=str(item.get("name") or paths[0]),
                paths=tuple(str(p) for p in paths),
                sliced=bool(item.get("sliced", False)),
                cross_import_dir=str(cross) if cross else None,
            )
        )
    return tuple(layers)


def resolve_layers(path: Path, lang: LangRuntimeContract | None) -> tuple[Layer, ...]:
    """The configured layers, else the first active preset's; none by default."""
    configured = _parse_layers(_configured(lang, "layers"))
    if configured:
        return configured
    for preset in active_presets(path, lang):
        if preset.layers:
            return preset.layers
    return ()


def _alias_target(alias: str) -> str | None:
    """``@/components/ui`` → ``components/ui``: drop a path alias's first part."""
    head, sep, rest = alias.partition("/")
    if not sep or not rest:
        return None
    return rest if head in {"@", "~", "#", "$lib"} or not head[:1].isalnum() else None


def shadcn_ui_dirs(project_root: Path | None = None) -> tuple[str, ...]:
    """The UI kit directory a shadcn/ui ``components.json`` names, if any.

    shadcn/ui copies its components into the project; they are vendored
    code shared by intent, named by its convention rather than the project's.
    """
    root = project_root or get_project_root()
    try:
        payload = json.loads((root / "components.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ()
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(aliases, dict):
        return ()
    ui = aliases.get("ui")
    if not isinstance(ui, str):
        components = aliases.get("components")
        ui = f"{components}/ui" if isinstance(components, str) else None
    target = _alias_target(ui) if ui else None
    if not target:
        return ()
    return tuple(c for c in (f"src/{target}", target) if (root / c).is_dir())


__all__ = [
    "PRESETS",
    "Preset",
    "active_presets",
    "preset_catalog",
    "resolve_layers",
    "shadcn_ui_dirs",
]
