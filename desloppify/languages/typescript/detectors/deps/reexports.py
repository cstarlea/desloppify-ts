"""Follow exported names through re-export chains to the file that defines them.

``import { parse } from '@pkg/rpc'`` may pass through any number of
``export { parse } from``, ``export * from`` and import-then-export hops
before reaching the declaration. ``definition_files`` walks those hops by
name, so a test importing one name from a package entry is credited to the
file that implements it, not to every file the barrels touch.

Type-only exports are skipped (importing a type runs no code) unless a
caller asks for ``types``. Needs tree-sitter; without it nothing is followed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from desloppify.languages.typescript.syntax.queries import exports, imports
from desloppify.languages.typescript.syntax.tree import parsed_file

NAMESPACE = "*"
# Deeper chains than this are cycles the seen-set missed or generated code.
_MAX_HOPS = 64


@dataclass
class ModuleExports:
    """Runtime names a module exports: defined here, forwarded, or via ``export *``."""

    local: set[str] = field(default_factory=set)
    # exported name -> (source specifier, name in that module; ``*`` for a namespace)
    forwarded: dict[str, tuple[str, str]] = field(default_factory=dict)
    stars: list[str] = field(default_factory=list)


_CACHE: dict[tuple[str, bool], tuple[bytes, ModuleExports]] = {}


def module_exports(path: str, *, types: bool = False) -> ModuleExports | None:
    """The export summary of the file at *path* (absolute), or None if unparseable.

    With ``types``, type-only imports and exports count too.
    """
    parsed = parsed_file(path)
    if parsed is None:
        return None
    cached = _CACHE.get((path, types))
    if cached is not None and cached[0] is parsed.source:
        return cached[1]

    imported: dict[str, tuple[str, str]] = {}
    for info in imports(parsed):
        if (info.type_only and not types) or info.kind == "side_effect":
            continue
        for binding in info.bindings:
            if types or not binding.type_only:
                name = NAMESPACE if binding.imported in (NAMESPACE, "=") else binding.imported
                imported[binding.local] = (info.source, name)

    summary = ModuleExports()
    for info in exports(parsed):
        if info.type_only and not types:
            continue
        if info.kind == "reexport":
            if info.star and not info.bindings:
                summary.stars.append(info.source or "")
            for binding in info.bindings:
                if (types or not binding.type_only) and binding.name is not None:
                    summary.forwarded[binding.exported] = (info.source or "", binding.name)
            continue
        for binding in info.bindings:
            if binding.type_only and not types:
                continue
            if binding.name in imported and info.kind in ("named", "default"):
                summary.forwarded[binding.exported] = imported[binding.name]
            else:
                summary.local.add(binding.exported)
        # ``export default function () {}`` and ``export default {...}`` bind no name.
        if info.is_default and not info.bindings and info.kind in ("declaration", "default"):
            summary.local.add("default")
    _CACHE[(path, types)] = (parsed.source, summary)
    return summary


def definition_files(path: str, names: tuple[str, ...], resolve) -> set[str]:
    """Files defining the member path *names* of module *path*.

    ``("parse",)`` is one exported name; ``("core", "parse")`` reaches through
    a namespace export (``export * as core``). A namespace with no member left
    to follow resolves to the namespace module itself. *resolve(spec, from)*
    returns an absolute path or None.
    """
    return _definitions(path, names, resolve, set(), 0)


def _definitions(path: str, names: tuple[str, ...], resolve, seen: set, hops: int) -> set[str]:
    if not names:
        return {path}
    key = (path, names)
    if key in seen or hops > _MAX_HOPS:
        return set()
    seen.add(key)
    summary = module_exports(path)
    if summary is None:
        return set()

    name, rest = names[0], names[1:]
    if name in summary.local:
        return {path}
    forward = summary.forwarded.get(name)
    if forward is not None:
        spec, original = forward
        target = resolve(spec, path)
        if target is None:
            return set()
        inner = rest if original == NAMESPACE else (original, *rest)
        return _definitions(target, inner, resolve, seen, hops + 1)
    if name == "default":
        return set()  # ``export *`` never forwards the default export
    for spec in summary.stars:
        target = resolve(spec, path)
        if target is None:
            continue
        found = _definitions(target, names, resolve, seen, hops + 1)
        if found:
            return found
    return set()


def clear_cache() -> None:
    _CACHE.clear()


__all__ = ["ModuleExports", "NAMESPACE", "clear_cache", "definition_files", "module_exports"]
