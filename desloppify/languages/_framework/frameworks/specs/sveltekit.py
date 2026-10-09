"""SvelteKit framework spec (Node ecosystem): detection and entry conventions."""

from __future__ import annotations

from ..types import DetectionConfig, EntryConventions, FrameworkSpec
from . import SCRIPT_EXTENSIONS, config_names

_SVELTE_CONFIG_FILES = config_names("svelte.config")

# Route files (+page.svelte, +layout.server.ts, +server.js, +page@.svelte)
# anywhere under routes/, hooks and the service worker in src/, and param
# matchers in src/params/. A plain Svelte + Vite app has a svelte.config
# too, so these apply only where @sveltejs/kit is a dependency.
SVELTEKIT_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_SVELTE_CONFIG_FILES,
    extensions=SCRIPT_EXTENSIONS | {".svelte"},
    root_stems=frozenset(
        {"hooks", "hooks.server", "hooks.client", "service-worker", "instrumentation.server"}
    ),
    route_dir="routes",
    route_stem_prefix="+",
    entry_dirs=("src/params", "src/service-worker"),
    dependencies=("@sveltejs/kit",),
)

SVELTEKIT_SPEC = FrameworkSpec(
    id="sveltekit",
    label="SvelteKit",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("@sveltejs/kit",),
        dev_dependencies=("@sveltejs/kit",),
        script_pattern=r"(?:^|\s)svelte-kit\s",
    ),
    entry_conventions=SVELTEKIT_ENTRY_CONVENTIONS,
    # $env/static/public and $env/dynamic/public.
    public_env_prefixes=("PUBLIC_",),
)


__all__ = ["SVELTEKIT_ENTRY_CONVENTIONS", "SVELTEKIT_SPEC"]
