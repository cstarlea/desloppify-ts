"""Nuxt framework spec (Node ecosystem): detection and entry conventions."""

from __future__ import annotations

from ..types import DetectionConfig, EntryConventions, FrameworkSpec
from . import SCRIPT_EXTENSIONS, config_names

_NUXT_CONFIG_FILES = config_names("nuxt.config")

# Directories Nuxt loads by convention or auto-imports from: nothing imports
# their files. Nuxt 4 keeps them in app/, Nuxt 3 at the root.
_APP_DIRS = ("components", "composables", "layouts", "middleware", "pages", "plugins", "utils")

NUXT_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_NUXT_CONFIG_FILES,
    extensions=SCRIPT_EXTENSIONS | {".vue"},
    root_stems=frozenset({"app", "error", "app.config", "router.options"}),
    entry_dirs=(
        *_APP_DIRS,
        *(f"app/{name}" for name in _APP_DIRS),
        "server",  # Nitro: api/, routes/, middleware/, plugins/, auto-imported utils/
        "modules",
        "layers",
        "shared/utils",
        "shared/types",
    ),
)

NUXT_SPEC = FrameworkSpec(
    id="nuxt",
    label="Nuxt",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("nuxt",),
        dev_dependencies=("nuxt",),
        config_files=_NUXT_CONFIG_FILES,
        script_pattern=r"(?:^|\s)nuxt(?:i)?\s",
    ),
    excludes=("vue",),
    entry_conventions=NUXT_ENTRY_CONVENTIONS,
)


__all__ = ["NUXT_ENTRY_CONVENTIONS", "NUXT_SPEC"]
