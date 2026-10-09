"""Astro framework spec (Node ecosystem): detection and entry conventions."""

from __future__ import annotations

from ..types import DetectionConfig, EntryConventions, FrameworkSpec
from . import SCRIPT_EXTENSIONS, config_names

_ASTRO_CONFIG_FILES = config_names("astro.config")

# Every file in src/pages/ is a page or an endpoint; middleware, content
# collections and actions are loaded by name.
ASTRO_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_ASTRO_CONFIG_FILES,
    extensions=SCRIPT_EXTENSIONS | {".astro", ".vue", ".svelte"},
    root_stems=frozenset({"middleware", "content.config", "live.config"}),
    entry_dirs=("src/pages",),
    entry_paths=frozenset({"src/content/config", "src/middleware/index", "src/actions/index"}),
)

ASTRO_SPEC = FrameworkSpec(
    id="astro",
    label="Astro",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("astro",),
        dev_dependencies=("astro",),
        config_files=_ASTRO_CONFIG_FILES,
        script_pattern=r"(?:^|\s)astro\s",
    ),
    entry_conventions=ASTRO_ENTRY_CONVENTIONS,
    public_env_prefixes=("PUBLIC_",),
)


__all__ = ["ASTRO_ENTRY_CONVENTIONS", "ASTRO_SPEC"]
