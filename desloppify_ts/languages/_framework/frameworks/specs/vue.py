"""Vue + Vite framework spec (Node ecosystem): detection and plugin entry conventions.

A Vue app's own files import each other, except where a Vite plugin loads
them by convention: each plugin's directory counts only where the plugin is
a dependency. Nuxt has its own spec.
"""

from __future__ import annotations

from ..types import DetectionConfig, EntryConventions, FrameworkSpec
from . import SCRIPT_EXTENSIONS, config_names

_VITE_CONFIG_FILES = config_names("vite.config")
_EXTENSIONS = SCRIPT_EXTENSIONS | {".vue"}

VUE_ENTRY_CONVENTIONS = (
    # unplugin-vue-components registers src/components/ globally.
    EntryConventions(
        config_files=_VITE_CONFIG_FILES,
        extensions=_EXTENSIONS,
        entry_dirs=("src/components",),
        dependencies=("unplugin-vue-components",),
    ),
    # File-based routing: vite-plugin-pages, unplugin-vue-router, and
    # vue-router's own Vite plugin (vue-router 5). A router that imports its
    # pages has edges to them anyway.
    EntryConventions(
        config_files=_VITE_CONFIG_FILES,
        extensions=_EXTENSIONS,
        entry_dirs=("src/pages",),
        dependencies=("vite-plugin-pages", "unplugin-vue-router", "vue-router"),
    ),
    EntryConventions(
        config_files=_VITE_CONFIG_FILES,
        extensions=_EXTENSIONS,
        entry_dirs=("src/layouts",),
        dependencies=("vite-plugin-vue-layouts", "vite-plugin-vue-layouts-next"),
    ),
)

VUE_SPEC = FrameworkSpec(
    id="vue",
    label="Vue",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("vue",),
        dev_dependencies=("vue",),
    ),
    entry_conventions=VUE_ENTRY_CONVENTIONS,
)


__all__ = ["VUE_ENTRY_CONVENTIONS", "VUE_SPEC"]
