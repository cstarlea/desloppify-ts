"""Framework spec registry (analogous to tree-sitter spec registry)."""

from __future__ import annotations

from .types import EntryConventions, FrameworkSpec

FRAMEWORK_SPECS: dict[str, FrameworkSpec] = {}


def register_framework_spec(spec: FrameworkSpec) -> None:
    """Register a framework spec by id."""
    key = str(spec.id or "").strip()
    if not key:
        raise ValueError("FrameworkSpec.id must be non-empty")
    FRAMEWORK_SPECS[key] = spec


def get_framework_spec(framework_id: str) -> FrameworkSpec | None:
    """Return a registered framework spec by id."""
    key = str(framework_id or "").strip()
    if not key:
        return None
    return FRAMEWORK_SPECS.get(key)


def list_framework_specs(*, ecosystem: str | None = None) -> dict[str, FrameworkSpec]:
    """Return a copy of the framework registry, optionally filtered by ecosystem."""
    if ecosystem is None:
        return dict(FRAMEWORK_SPECS)
    eco = str(ecosystem or "").strip().lower()
    if not eco:
        return dict(FRAMEWORK_SPECS)
    return {k: v for k, v in FRAMEWORK_SPECS.items() if str(v.ecosystem).lower() == eco}


def _register_builtin_specs() -> None:
    """Register built-in framework specs shipped with the repo."""
    if FRAMEWORK_SPECS:
        return
    from .specs.angular import ANGULAR_SPEC
    from .specs.astro import ASTRO_SPEC
    from .specs.nestjs import NESTJS_SPEC
    from .specs.nextjs import NEXTJS_SPEC
    from .specs.nuxt import NUXT_SPEC
    from .specs.react_router import REACT_ROUTER_SPEC
    from .specs.servers import EXPRESS_SPEC, FASTIFY_SPEC, HONO_SPEC
    from .specs.sveltekit import SVELTEKIT_SPEC
    from .specs.vue import VUE_SPEC

    for spec in (
        NEXTJS_SPEC,
        NUXT_SPEC,
        VUE_SPEC,
        SVELTEKIT_SPEC,
        ASTRO_SPEC,
        REACT_ROUTER_SPEC,
        NESTJS_SPEC,
        EXPRESS_SPEC,
        HONO_SPEC,
        FASTIFY_SPEC,
        ANGULAR_SPEC,
    ):
        register_framework_spec(spec)


def ensure_builtin_specs_loaded() -> None:
    """Idempotently load built-in framework specs."""
    _register_builtin_specs()


def framework_entry_conventions(*, ecosystem: str = "node") -> tuple[EntryConventions, ...]:
    """File-system entry conventions declared by the built-in framework specs."""
    ensure_builtin_specs_loaded()
    conventions: list[EntryConventions] = []
    for spec in list_framework_specs(ecosystem=ecosystem).values():
        declared = spec.entry_conventions
        if isinstance(declared, EntryConventions):
            conventions.append(declared)
        elif declared:
            conventions.extend(declared)
    return tuple(conventions)


__all__ = [
    "FRAMEWORK_SPECS",
    "ensure_builtin_specs_loaded",
    "framework_entry_conventions",
    "get_framework_spec",
    "list_framework_specs",
    "register_framework_spec",
]
