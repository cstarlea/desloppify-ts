"""Dead exports without Knip: a conservative import-graph check.

An exported binding is reported only when no file in the project imports
that name from its module, directly or through ``export ... from`` /
``export *`` chains. A module counts as wholly used when something imports
it as a namespace (``import * as``, ``import x = require``), loads it with
``import()``/``require()``/``import.meta.glob``, or names it in a way the
resolver can't follow. Entry files (framework conventions, package.json
``exports``/``main``/``bin``/scripts, the TypeScript entry patterns) and
files outside the production zone are never candidates, and their exports
count as used. Default exports and declarations inside ``declare module``
are left alone.

Only exports of files under the scan path are reported, but importers come
from the whole project (minus the usual exclusions): root configs,
``scripts/`` and ``test/`` use ``src/`` exports too. Files outside the scan
path are never candidates either, and their exports count as used.

Use within the defining file doesn't count, as with Knip. Needs tree-sitter;
without it the detector returns None.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import desloppify_ts.languages.typescript.detectors.deps as deps_mod
from desloppify_ts.base.discovery.file_paths import rel, resolve_path
from desloppify_ts.base.discovery.paths import get_project_root
from desloppify_ts.base.discovery.source import (
    find_component_files,
    find_source_files,
    find_ts_and_js_files,
)
from desloppify_ts.engine.policy.zones import Zone
from desloppify_ts.languages._framework.frameworks.registry import (
    framework_entry_conventions,
    framework_static_dirs,
)
from desloppify_ts.languages.typescript.detectors.deps import imports as refs_mod
from desloppify_ts.languages.typescript.detectors.deps import packages as packages_mod
from desloppify_ts.languages.typescript.detectors.deps.auto_imports import (
    auto_import_entries,
)
from desloppify_ts.languages.typescript.detectors.deps.public_api import (
    ALL_NAMES,
    exposed_names,
)
from desloppify_ts.languages.typescript.detectors.deps.resolver import (
    is_bare,
    project_resolver,
)
from desloppify_ts.languages.typescript.detectors.deps.url_entries import url_entries
from desloppify_ts.languages.typescript.plugin_contract import TS_ENTRY_PATTERNS
from desloppify_ts.languages.typescript.syntax.queries import (
    descendants,
    exports,
    imports,
    string_value,
)
from desloppify_ts.languages.typescript.syntax.tree import parsed_file

logger = logging.getLogger(__name__)

_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
_TYPE_DECLARATIONS = frozenset({"interface_declaration", "type_alias_declaration"})
# Whole-module loads: the importer may use any export. ``vi.mock`` replaces a
# module rather than using its exports, so it doesn't count.
_WHOLE_MODULE_KINDS = frozenset({refs_mod.REQUIRE, refs_mod.RESOLVE})
_PATTERN_KINDS = frozenset({refs_mod.GLOB, refs_mod.DYNAMIC_PREFIX})
# ``import('./x')`` in type positions and JSDoc (``@type {import('./x').T}``).
_IMPORT_TYPE_RE = re.compile(rb"""\bimport\(\s*['"]([^'"]+)['"]\s*\)""")
# Relative paths named in config files (``schema: './src/db/schema.ts'``).
_CONFIG_PATH_RE = re.compile(r"""['"](\.{1,2}/[^'"\s]+)['"]""")
_DOCUMENT_EXTENSIONS = [".mdx"]
# Config keys whose globs name files a tool reads or watches, not modules it
# loads: Tailwind's ``content``, test and coverage ``include``/``exclude``,
# vitest's ``projects`` and watch triggers.
_FILE_GLOB_KEYS = frozenset(
    {"content", "include", "exclude", "projects", "workspace", "forceRerunTriggers"}
)


@dataclass
class _Usage:
    seeds: set[tuple[str, str]] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    # Modules something imports, loads or re-exports.
    reached: set[str] = field(default_factory=set)

    def whole(self, path: str | None) -> None:
        if path is not None:
            self.seeds.add((path, ALL_NAMES))
            self.reached.add(path)


def _could_name(specifier: str, filepath: str) -> bool:
    """Whether an unresolved ``~/lib/format`` could be ``.../lib/format.ts``."""
    parts = specifier.split("/")
    rest = parts[2:] if specifier.startswith("@") and len(parts) > 2 else parts[1:]
    if not rest or not rest[-1]:
        return False
    tail = "/" + "/".join(rest)
    path = Path(filepath)
    if str(path.with_suffix("")).replace("\\", "/").endswith(tail):
        return True
    return path.stem == "index" and str(path.parent).replace("\\", "/").endswith(tail)


class _Scan:
    def __init__(self, path: Path, zone_map) -> None:
        self.path = path
        self.zone_map = zone_map
        root = get_project_root()
        self.resolver = project_resolver(root)
        self.modules = _modules(path)
        self.scanned = set(self.modules)
        # Importers: the scan path plus the rest of the project.
        self.project_modules = sorted(self.scanned | set(_modules(root)))
        self.usage = _Usage()

    def parse(self, filepath: str):
        # Files outside the scan path are read once here; keep their trees
        # out of the scan's parse cache.
        return parsed_file(filepath, store=filepath in self.scanned)

    def resolve(self, specifier: str, from_file: str) -> str | None:
        try:
            return self.resolver.resolve(specifier, from_file)
        except OSError as exc:
            logger.debug("resolve %s from %s: %s", specifier, from_file, exc)
            return None

    def zone(self, filepath: str) -> Zone:
        if self.zone_map is None:
            return Zone.PRODUCTION
        return self.zone_map.get(rel(filepath))

    def _note_unresolved(self, specifier: str, from_file: str) -> None:
        if is_bare(specifier) and self.resolver.is_external(specifier, from_file):
            return
        self.usage.unresolved.add(specifier)

    def collect_module(self, filepath: str) -> bool:
        """Record what *filepath* uses; False when it can't be parsed."""
        parsed = self.parse(filepath)
        if parsed is None:
            return False
        for info in imports(parsed):
            target = self.resolve(info.source, filepath)
            if target is None:
                self._note_unresolved(info.source, filepath)
                continue
            self.usage.reached.add(target)
            for binding in info.bindings:
                if binding.imported in ("*", "="):
                    self.usage.whole(target)
                else:
                    self.usage.seeds.add((target, binding.imported))
        for info in exports(parsed):
            if info.kind == "reexport" and info.source is not None:
                target = self.resolve(info.source, filepath)
                if target is None:
                    self._note_unresolved(info.source, filepath)
                elif filepath in self.scanned:
                    self.usage.reached.add(target)
                else:
                    self._outside_reexport(info, target)
        runtime_imports = self._calls(parsed, filepath)
        if b"import(" in parsed.source:
            for match in _IMPORT_TYPE_RE.finditer(parsed.source):
                if match.start() in runtime_imports:
                    continue
                specifier = match.group(1).decode("utf-8", errors="replace")
                target = self.resolve(specifier, filepath)
                if target is None:
                    self._note_unresolved(specifier, filepath)
                self.usage.whole(target)
        return True

    def _outside_reexport(self, info, target: str) -> None:
        """Exports of files outside the scan path count as used, so what they
        re-export does too (``exposed_names`` follows chains from here)."""
        if info.star:
            self.usage.whole(target)
            return
        self.usage.reached.add(target)
        self.usage.seeds.update(
            (target, b.name) for b in info.bindings if b.name is not None
        )

    def _calls(self, parsed, filepath: str) -> set[int]:
        """Record module loads made by calls; return the start offsets of the
        ``import('x')`` calls.

        ``const { a, b } = await import('x')`` uses ``a`` and ``b``; any other
        use of the module object, ``require``, ``require.resolve`` and
        ``import.meta.glob`` may reach every export.
        """
        starts: set[int] = set()
        for call in descendants(parsed.root, {"call_expression"}):
            function = call.child_by_field_name("function")
            if function is None:
                continue
            arguments = call.child_by_field_name("arguments")
            args = arguments.named_children if arguments is not None else []
            first = args[0] if args else None
            if function.type != "import" or first is None or first.type != "string":
                self._whole_module_call(call, filepath)
                continue
            starts.add(call.start_byte)
            specifier = string_value(parsed, first)
            target = self.resolve(specifier, filepath)
            if target is None:
                self._note_unresolved(specifier, filepath)
                continue
            names = _destructured_names(parsed, call)
            if names is None:
                self.usage.whole(target)
            else:
                self.usage.reached.add(target)
                self.usage.seeds.update((target, name) for name in names)
        return starts

    def _whole_module_call(self, call, filepath: str) -> None:
        refs: list[refs_mod.ImportRef] = []
        refs_mod._call_expression(call, refs)
        for ref in refs:
            if ref.kind in _PATTERN_KINDS:
                self._pattern(ref, filepath)
            elif ref.kind in _WHOLE_MODULE_KINDS:
                target = self.resolve(ref.specifier, filepath)
                if target is None:
                    self._note_unresolved(ref.specifier, filepath)
                self.usage.whole(target)

    def _pattern(self, ref, filepath: str) -> None:
        """``import.meta.glob`` / ``import(`./x/${y}`)``: every module it matches."""
        root, paths = self.resolver.tsconfigs.for_file(filepath)
        for target in deps_mod._pattern_targets(
            ref, filepath, paths, root, self.resolver.project_root, self.project_modules
        ):
            self.usage.whole(target)

    def collect_document(self, filepath: str) -> None:
        try:
            text = Path(filepath).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for ref in refs_mod.extract_mdx_imports(text):
            self.usage.whole(self.resolve(ref.specifier, filepath))

    def collect_config(self, filepath: str) -> None:
        """Paths a config file names: tools load those modules' exports."""
        try:
            text = Path(filepath).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        base = Path(filepath).parent
        parsed = self.parse(filepath)
        sources = (
            {info.source for info in imports(parsed)}
            | {info.source for info in exports(parsed) if info.source}
            if parsed is not None
            else set()
        )
        file_globs = _file_globs(parsed) if parsed is not None else set()
        for match in _CONFIG_PATH_RE.finditer(text):
            value = match.group(1)
            if value in sources:
                continue  # an import, already counted by name
            if any(c in value for c in "*?{"):
                if value in file_globs:
                    continue
                # ``*`` stays within one directory, ``**`` spans any number.
                pattern = deps_mod._glob_regex(
                    str((base / value).resolve(strict=False))
                )
                for module in self.project_modules:
                    if pattern.match(module):
                        self.usage.whole(module)
                continue
            self.usage.whole(self.resolve(value, filepath))

    def entry_files(self) -> set[str]:
        packages = self.resolver.packages
        entries = packages_mod.workspace_entries(packages, self.modules).all
        entries |= auto_import_entries(self.path)
        entries |= url_entries(self.path, framework_static_dirs())
        conventions = _conventions(self.path, packages)
        for module in self.modules:
            relative = rel(module)
            if any(p in relative for p in TS_ENTRY_PATTERNS) or conventions.is_entry(
                module
            ):
                entries.add(module)
        return {resolve_path(e) for e in entries}


def _file_globs(parsed) -> set[str]:
    """Glob strings under a ``_FILE_GLOB_KEYS`` key (``content: ['./src/**']``)."""
    found: set[str] = set()
    for node in descendants(parsed.root, {"string"}):
        value = string_value(parsed, node)
        if not any(c in value for c in "*?{"):
            continue
        parent = node.parent
        while parent is not None and parent.type == "array":
            parent = parent.parent
        if parent is None or parent.type != "pair":
            continue
        key = parent.child_by_field_name("key")
        if key is None:
            continue
        name = string_value(parsed, key) if key.type == "string" else parsed.text(key)
        if name in _FILE_GLOB_KEYS:
            found.add(value)
    return found


def _modules(path: Path) -> list[str]:
    found = [*find_ts_and_js_files(path), *find_component_files(path)]
    return sorted({resolve_path(f) for f in found})


def _destructured_names(parsed, call) -> list[str] | None:
    """Names taken from ``const { a, b: c } = await import(...)``, else None."""
    parent = call.parent
    if parent is not None and parent.type == "await_expression":
        parent = parent.parent
    if parent is None or parent.type != "variable_declarator":
        return None
    pattern = parent.child_by_field_name("name")
    if pattern is None or pattern.type != "object_pattern":
        return None
    names: list[str] = []
    for prop in pattern.named_children:
        if prop.type == "shorthand_property_identifier_pattern":
            names.append(parsed.text(prop))
        elif prop.type == "pair_pattern":
            key = prop.child_by_field_name("key")
            if key is None or key.type not in ("property_identifier", "string"):
                return None
            names.append(
                string_value(parsed, key) if key.type == "string" else parsed.text(key)
            )
        elif prop.type == "object_assignment_pattern":
            left = prop.child_by_field_name("left")
            if left is None:
                return None
            names.append(parsed.text(left))
        else:
            return None  # ``...rest`` or something unexpected
    return names


def _conventions(path: Path, packages: Iterable[packages_mod.Package]):
    from desloppify_ts.engine.detectors.orphaned import FrameworkConventions

    return FrameworkConventions(
        path, [p.directory for p in packages], framework_entry_conventions()
    )


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text) is not None


def _candidates(filepath: str) -> tuple[list[dict], dict[str, str]]:
    """Exported local bindings of *filepath* that importers would name, and
    the text of the export statement declaring each exported value."""
    parsed = parsed_file(filepath)
    if parsed is None:
        return [], {}
    imported = {
        binding.local
        for info in imports(parsed)
        for binding in info.bindings
        if info.kind != "side_effect"
    }
    found = []
    statements: dict[str, str] = {}
    for info in exports(parsed):
        node = info.node
        declaration = node.child_by_field_name("declaration")
        is_type = info.type_only or (
            declaration is not None and declaration.type in _TYPE_DECLARATIONS
        )
        if not is_type:
            for binding in info.bindings:
                statements[binding.exported] = parsed.text(node)
            if info.is_default and not info.bindings:
                statements["default"] = parsed.text(node)
        if info.kind not in ("declaration", "named") or info.is_default:
            continue
        if getattr(node.parent, "type", "program") != "program":
            continue  # inside ``declare module`` / ``namespace``
        for binding in info.bindings:
            if binding.name is None or binding.exported == "default":
                continue
            if info.kind == "named" and binding.name in imported:
                continue  # import-then-export: a forward, not a definition
            found.append(
                {
                    "file": rel(filepath),
                    "name": binding.exported,
                    "line": info.line,
                    "kind": "type" if is_type or binding.type_only else "export",
                }
            )
    return found, statements


def _dead_in_module(
    candidates: list[dict], statements: dict[str, str], live: set[str]
) -> list[dict]:
    """Candidates not in *live*. A type named in a live value export's
    declaration (``Props`` of a used component) is part of that export's
    signature, so it is live too, as Knip sees it."""
    live = set(live)
    pending = [c for c in candidates if c["name"] not in live]
    changed = True
    while changed:
        changed = False
        for candidate in pending:
            name = candidate["name"]
            if name in live or candidate["kind"] != "type":
                continue
            if any(
                other != name and _mentions(text, name)
                for other, text in statements.items()
                if other in live
            ):
                live.add(name)
                changed = True
    return [c for c in pending if c["name"] not in live]


def detect_dead_exports_fallback(path: Path, zone_map=None) -> list[dict] | None:
    """Exports nothing imports, or None when tree-sitter is unavailable."""
    scan = _Scan(path, zone_map)
    if not scan.modules:
        return []
    parsed_any = False
    for module in scan.project_modules:
        parsed = scan.collect_module(module)
        parsed_any |= parsed and module in scan.scanned
        if scan.zone(module) == Zone.CONFIG:
            scan.collect_config(module)
    if not parsed_any:
        return None
    documents = {
        resolve_path(d)
        for base in (path, get_project_root())
        for d in find_source_files(base, _DOCUMENT_EXTENSIONS)
    }
    for document in sorted(documents):
        scan.collect_document(document)

    entries = scan.entry_files()
    candidates = [
        module
        for module in scan.modules
        if module not in entries
        and not module.endswith(_DECLARATION_SUFFIXES)
        and scan.zone(module) == Zone.PRODUCTION
        # A module nothing imports is an orphan, which ``orphaned`` reports.
        and module in scan.usage.reached
        and not any(_could_name(spec, module) for spec in scan.usage.unresolved)
    ]
    # Uses of files outside the scan path matter only through their re-exports,
    # which ``_outside_reexport`` already seeded; don't parse them again.
    seeds = {seed for seed in scan.usage.seeds if seed[0] in scan.scanned}
    seeds |= {(entry, ALL_NAMES) for entry in entries}
    seeds |= {(m, ALL_NAMES) for m in scan.modules if scan.zone(m) != Zone.PRODUCTION}
    used = exposed_names(seeds, scan.resolve)
    wholly_used = {path for path, name in seeds if name == ALL_NAMES}

    used_by_module: dict[str, set[str]] = {}
    for module, name in used:
        used_by_module.setdefault(module, set()).add(name)
    dead: list[dict] = []
    for module in candidates:
        if module in wholly_used:
            continue
        found, statements = _candidates(module)
        dead.extend(
            _dead_in_module(found, statements, used_by_module.get(module, set()))
        )
    return dead


__all__ = ["detect_dead_exports_fallback"]
