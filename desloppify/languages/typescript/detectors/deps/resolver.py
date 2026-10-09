"""One module resolver for the dependency graph, test coverage and ``move``.

Resolves a specifier the way TypeScript does for the importing file:
relative paths, then the nearest tsconfig's ``paths``/``baseUrl``, then
``#subpath`` imports from the nearest package.json, then the workspace
package a bare name would symlink to through ``node_modules``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from desloppify.languages.typescript.detectors.deps.packages import (
    Package,
    WorkspaceResolver,
    declared_dependencies,
    discover_packages,
    load_package,
    package_import_targets,
    resolve_package_path,
)
from desloppify.languages.typescript.detectors.deps.resolve import (
    _TSCONFIG_NAMES,
    find_tsconfig_root,
    load_tsconfig_paths,
    resolve_alias,
    resolve_target,
    specifier_target,
)

_NODE_BUILTINS = frozenset(
    {
        "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
        "constants", "crypto", "dgram", "diagnostics_channel", "dns", "domain",
        "events", "fs", "http", "http2", "https", "inspector", "module", "net",
        "os", "path", "perf_hooks", "process", "punycode", "querystring",
        "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls",
        "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads",
        "zlib",
    }
)


def package_name(specifier: str) -> str:
    parts = specifier.split("/")
    return "/".join(parts[:2]) if specifier.startswith("@") and len(parts) > 1 else parts[0]


def is_bare(specifier: str) -> bool:
    return not specifier.startswith((".", "/"))


class _TsconfigLookup:
    """Path aliases from the tsconfig nearest to each file.

    Packages in a monorepo each have their own tsconfig (and ``paths``), so
    one scan-wide config resolves aliases against the wrong directory.
    Files with no tsconfig between them and the project root use the
    scan-wide one.
    """

    def __init__(self, scan_path: Path, project_root: Path) -> None:
        self._project_root = project_root.resolve()
        default_root = find_tsconfig_root(scan_path, project_root)
        self.default = (default_root, load_tsconfig_paths(default_root))
        self._by_dir: dict[Path, tuple[Path, dict[str, str]]] = {}

    def for_file(self, filepath: str) -> tuple[Path, dict[str, str]]:
        directory = Path(filepath).parent
        if not directory.is_relative_to(self._project_root):
            return self.default
        visited: list[Path] = []
        result = self.default
        while True:
            cached = self._by_dir.get(directory)
            if cached is not None:
                result = cached
                break
            visited.append(directory)
            if any((directory / name).is_file() for name in _TSCONFIG_NAMES):
                result = (directory, load_tsconfig_paths(directory))
                break
            if directory == self._project_root or directory == directory.parent:
                break
            directory = directory.parent
        for seen in visited:
            self._by_dir[seen] = result
        return result


class _PackageScopeLookup:
    """The package.json nearest to each file, whose ``imports`` map ``#`` specifiers.

    As in Node, the nearest package.json is the scope even when it has no
    ``imports`` field. Files with no package.json between them and the
    project root have no scope.
    """

    def __init__(self, project_root: Path, packages: list[Package]) -> None:
        self._project_root = project_root
        self._by_dir: dict[Path, Package | None] = {p.directory: p for p in packages}

    def for_file(self, filepath: str) -> Package | None:
        directory = Path(filepath).parent
        if not directory.is_relative_to(self._project_root):
            return None
        visited: list[Path] = []
        result: Package | None = None
        while True:
            if directory in self._by_dir:
                result = self._by_dir[directory]
                break
            visited.append(directory)
            if (directory / "package.json").is_file():
                result = load_package(directory)
                break
            if directory == self._project_root or directory == directory.parent:
                break
            directory = directory.parent
        for seen in visited:
            self._by_dir[seen] = result
        return result


_CONFIG_EXTS = ("ts", "mts", "js", "mjs", "cjs")
_DOCUSAURUS_SITE_PREFIX = "@site/"
_DOCUSAURUS_CONFIGS = tuple(f"docusaurus.config.{ext}" for ext in _CONFIG_EXTS)


@lru_cache(maxsize=4096)
def _nearest_dir_with(directory: str, names: tuple[str, ...]) -> str | None:
    """The nearest directory, from *directory* up, holding one of the files *names*."""
    path = Path(directory)
    if any((path / name).is_file() for name in names):
        return directory
    parent = path.parent
    return None if parent == path else _nearest_dir_with(str(parent), names)


def docusaurus_site_root(filepath: str) -> Path | None:
    """The Docusaurus site holding *filepath*: the nearest directory with a ``docusaurus.config``."""
    root = _nearest_dir_with(str(Path(filepath).parent), _DOCUSAURUS_CONFIGS)
    return Path(root) if root is not None else None


def _resolve_docusaurus_site(specifier: str, from_abs: str) -> str | None:
    """``@site/x``: Docusaurus's alias for the site directory, set by its
    bundler config, not a tsconfig."""
    root = docusaurus_site_root(from_abs)
    if root is None:
        return None
    return resolve_target(root / specifier[len(_DOCUSAURUS_SITE_PREFIX) :])


_NUXT_CONFIGS = tuple(f"nuxt.config.{ext}" for ext in _CONFIG_EXTS)
_SVELTEKIT_CONFIGS = tuple(f"svelte.config.{ext}" for ext in _CONFIG_EXTS)
# Nuxt's aliases for its source directory and its root, set in the
# generated .nuxt/tsconfig.json that a fresh checkout doesn't have.
_NUXT_SRC_ALIASES = ("~/", "@/")
_NUXT_ROOT_ALIASES = ("~~/", "@@/")
# SvelteKit's modules provided by the framework, not by a file.
_SVELTEKIT_VIRTUAL = ("$app/", "$env/", "$service-worker")


def _nuxt_src_dir(root: Path) -> Path:
    """Nuxt 4's ``app/`` when the project has one, else the root (Nuxt 3)."""
    app = root / "app"
    if any((app / name).exists() for name in ("app.vue", "pages", "components", "layouts")):
        return app
    return root


def _resolve_framework_alias(specifier: str, from_abs: str) -> str | None:
    """Aliases Nuxt and SvelteKit define in a tsconfig they generate on install:
    ``~/x``/``@/x`` (Nuxt source dir), ``~~/x``/``@@/x`` and ``#shared/x``
    (Nuxt root), ``$lib/x`` (SvelteKit ``src/lib``)."""
    directory = str(Path(from_abs).parent)
    if specifier == "$lib" or specifier.startswith("$lib/"):
        root = _nearest_dir_with(directory, _SVELTEKIT_CONFIGS)
        if root is None:
            return None
        return resolve_target(Path(root) / "src" / "lib" / specifier[len("$lib/") :])
    if not specifier.startswith((*_NUXT_SRC_ALIASES, *_NUXT_ROOT_ALIASES, "#shared")):
        return None
    root = _nearest_dir_with(directory, _NUXT_CONFIGS)
    if root is None:
        return None
    if specifier.startswith(_NUXT_SRC_ALIASES):
        return resolve_target(_nuxt_src_dir(Path(root)) / specifier[2:])
    if specifier.startswith(_NUXT_ROOT_ALIASES):
        return resolve_target(Path(root) / specifier[3:])
    return resolve_target(Path(root) / "shared" / specifier[len("#shared/") :])


def _is_framework_virtual(specifier: str, from_abs: str) -> bool:
    """SvelteKit's ``$app/*``/``$env/*`` modules and Nuxt's ``#imports``-style ones."""
    directory = str(Path(from_abs).parent)
    if specifier.startswith(_SVELTEKIT_VIRTUAL):
        return _nearest_dir_with(directory, _SVELTEKIT_CONFIGS) is not None
    if specifier.startswith("#"):
        return _nearest_dir_with(directory, _NUXT_CONFIGS) is not None
    return False


class ModuleResolver:
    """Resolve TypeScript import specifiers for files under one project."""

    def __init__(self, scan_path: Path, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.tsconfigs = _TsconfigLookup(scan_path, project_root)
        self.packages = discover_packages(scan_path, project_root)
        self.workspace = WorkspaceResolver(self.packages)
        self.dependencies = declared_dependencies(self.packages)
        self.package_scopes = _PackageScopeLookup(self.project_root, self.packages)

    def resolve(self, specifier: str, from_file: str) -> str | None:
        """Absolute path of the source file *specifier* names, or None."""
        from_abs = self._absolute(from_file)
        tsconfig_root, tsconfig_paths = self.tsconfigs.for_file(from_abs)
        target = specifier_target(
            specifier, from_abs, tsconfig_paths, tsconfig_root, source_root=self.project_root
        )
        resolved = resolve_target(target) if target is not None else None
        if resolved is None and specifier.startswith("#"):
            resolved = self._resolve_package_import(specifier, from_abs)
            if resolved is None and specifier.startswith("#shared"):
                resolved = _resolve_framework_alias(specifier, from_abs)
            return resolved
        if resolved is None and specifier.startswith(_DOCUSAURUS_SITE_PREFIX):
            return _resolve_docusaurus_site(specifier, from_abs)
        if resolved is None and specifier.startswith(("$lib", *_NUXT_SRC_ALIASES, *_NUXT_ROOT_ALIASES)):
            return _resolve_framework_alias(specifier, from_abs)
        if resolved is None and self.workspace and is_bare(specifier):
            # tsconfig paths take precedence, as in TypeScript; then the
            # workspace package that node_modules would symlink to.
            resolved = self.workspace.resolve(specifier)
        return resolved

    def _resolve_package_import(self, specifier: str, from_abs: str) -> str | None:
        """``#subpath`` through the ``imports`` of the importer's package.json."""
        package = self.package_scopes.for_file(from_abs)
        if package is None:
            return None
        for target in package_import_targets(package, specifier):
            if target.startswith("./"):
                found = resolve_package_path(package, target)
            elif is_bare(target) and not target.startswith("#") and self.workspace:
                found = self.workspace.resolve(target)
            else:
                found = None
            if found is not None:
                return found
        return None

    def is_external(self, specifier: str, from_file: str | None = None) -> bool:
        """npm dependencies, Node builtins and bundler virtual modules (``virtual:x``).

        A ``#subpath`` is external when the importer's package.json maps it
        to such a package (``"#fetch": "node-fetch"``). SvelteKit's ``$app/*``
        and ``$env/*`` and Nuxt's ``#imports`` are the framework's own.
        """
        if from_file is not None and _is_framework_virtual(specifier, self._absolute(from_file)):
            return True
        if specifier.startswith("#"):
            if from_file is None:
                return False
            package = self.package_scopes.for_file(self._absolute(from_file))
            return package is not None and any(
                is_bare(target) and not target.startswith("#") and self.is_external(target)
                for target in package_import_targets(package, specifier)
            )
        if ":" in specifier:
            return True
        name = package_name(specifier)
        return name in self.dependencies or name in _NODE_BUILTINS

    def alias_prefix(self, specifier: str, from_file: str) -> str | None:
        """The tsconfig ``paths`` prefix *specifier* resolves through, if it does."""
        if not is_bare(specifier):
            return None
        tsconfig_root, paths = self.tsconfigs.for_file(self._absolute(from_file))
        for prefix in sorted(paths, key=len, reverse=True):
            if specifier.startswith(prefix):
                target = resolve_alias(specifier, {prefix: paths[prefix]}, tsconfig_root)
                return prefix if target is not None and resolve_target(target) else None
        return None

    def alias_specifier(self, target: str, from_file: str, prefer: str | None = None) -> str | None:
        """An alias specifier (without extension) for *target*, as seen from *from_file*.

        Uses *prefer* when its directory contains the target, else the
        wildcard alias with the deepest directory that does. None when no
        alias covers the target.
        """
        tsconfig_root, paths = self.tsconfigs.for_file(self._absolute(from_file))
        target_path = Path(target)
        options: list[tuple[int, str, Path]] = []
        for prefix, mapped in paths.items():
            if not (mapped.endswith("/") or mapped == ""):
                continue  # exact alias for a single file
            directory = (tsconfig_root / mapped).resolve()
            if target_path.is_relative_to(directory):
                rank = 0 if prefix == prefer else 1
                options.append((rank, prefix, directory))
        if not options:
            return None
        options.sort(key=lambda o: (o[0], -len(o[2].parts)))
        _rank, prefix, directory = options[0]
        if prefer is None and prefix == "":
            return None  # bare baseUrl paths only when the importer already uses them
        remainder = target_path.relative_to(directory).as_posix()
        return prefix + remainder

    def _absolute(self, filepath: str) -> str:
        path = Path(filepath)
        return str(path if path.is_absolute() else self.project_root / path)


@lru_cache(maxsize=4)
def _cached_resolver(project_root: str) -> ModuleResolver:
    root = Path(project_root)
    return ModuleResolver(root, root)


def project_resolver(project_root: Path) -> ModuleResolver:
    """A resolver for the whole project, reused across calls."""
    return _cached_resolver(os.fspath(project_root.resolve()))


def clear_resolver_cache() -> None:
    _cached_resolver.cache_clear()
    _nearest_dir_with.cache_clear()


__all__ = [
    "ModuleResolver",
    "clear_resolver_cache",
    "docusaurus_site_root",
    "is_bare",
    "package_name",
    "project_resolver",
]
