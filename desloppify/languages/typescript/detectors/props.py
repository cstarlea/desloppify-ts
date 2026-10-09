"""Bloated prop interface detection (>14 props = prop drilling signal).

With tree-sitter, an interface or type alias counts its distinct property
names, including those it inherits through ``extends``, intersections and
``Partial``/``Omit``/``Pick``-style wrappers of types declared in the scan.
Types from packages (``React.HTMLAttributes``) add nothing. Without
tree-sitter a regex counts the property lines of the declaration's body.
"""

import argparse
import json
import logging
import re
from pathlib import Path

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.output.terminal import colorize, print_table
from desloppify.languages.typescript.detectors.deps.resolver import (
    ModuleResolver,
    project_resolver,
)
from desloppify.languages.typescript.syntax.queries import (
    TypeDeclaration,
    exports,
    imports,
    string_value,
    type_declarations,
)
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    get_parser,
    grammar_for,
    parsed_file,
)

logger = logging.getLogger(__name__)

# Props, Context and State as whole words of the name: ``ButtonProps``,
# ``AppContextValue``, ``UIState``; not ``Statement`` or ``Contextual``.
_BLOAT_WORD_RE = re.compile(r"(?:Props|Context|State)(?![a-z])")
_INTERFACE_RE = re.compile(
    r"(?:export\s+)?(?:interface|type)\s+(\w+)\s*(?:=\s*)?{",
    re.MULTILINE,
)
_MEMBER_TYPES = frozenset({"property_signature", "method_signature"})
_PASSTHROUGH = frozenset({"Partial", "Required", "Readonly", "NonNullable"})
_WRAPPERS = _PASSTHROUGH | {"Omit", "Pick"}


def _bloat_kind(name: str) -> str | None:
    words = {m.group(0) for m in _BLOAT_WORD_RE.finditer(name)}
    if not words:
        return None
    return "context" if "Context" in words else "state" if "State" in words else "props"


def detect_prop_interface_bloat(
    path: Path, *, threshold: int = 14
) -> tuple[list[dict], int]:
    """Find interfaces/types with >threshold properties — signals need for composition or context.

    Returns (entries, total_interfaces_checked).
    """
    entries: list[dict] = []
    total_interfaces = 0
    index = _TypeIndex()
    for filepath in find_ts_and_js_files(path):
        if get_parser(grammar_for(filepath)) is None:
            found, total = _regex_file(filepath, threshold)
        else:
            found, total = _tree_file(filepath, index, threshold)
        entries.extend(found)
        total_interfaces += total
    return sorted(entries, key=lambda e: -e["prop_count"]), total_interfaces


def _entry(filepath: str, name: str, count: int, line: int, kind: str) -> dict:
    return {
        "file": filepath,
        "interface": name,
        "prop_count": count,
        "line": line,
        "kind": kind,
    }


def _tree_file(
    filepath: str, index: "_TypeIndex", threshold: int
) -> tuple[list[dict], int]:
    module = index.module(resolve_path(filepath))
    if module is None:
        return [], 0
    entries = []
    total = 0
    for name, decls in module.declarations.items():
        kind = _bloat_kind(name)
        if kind is None or _renames_checked_type(index, module, decls[0]):
            continue
        count = len(index.members(module, name, set()))
        if count == 0:
            continue  # ``type State = 'idle' | 'busy'``: no properties to count
        total += 1
        if count > threshold:
            entries.append(_entry(filepath, name, count, decls[0].line, kind))
    return entries, total


def _renames_checked_type(
    index: "_TypeIndex", module: "_Module", decl: TypeDeclaration
) -> bool:
    """``type ProviderContext = TRPCContextState<T>``: the named type is checked itself."""
    value = decl.value
    if decl.kind != "alias" or value.type not in ("type_identifier", "generic_type"):  # type: ignore[attr-defined]
        return False
    name_node = value if value.type == "type_identifier" else value.named_children[0]  # type: ignore[attr-defined]
    if name_node.type != "type_identifier":
        return False
    target = module.parsed.text(name_node)
    if target in _WRAPPERS or _bloat_kind(target) is None:
        return False
    return (
        target in module.declarations or index.import_target(module, target) is not None
    )


class _Module:
    """One file's type declarations (same-name interfaces merge), and the names
    it imports or re-exports, mapped to (specifier, name there)."""

    def __init__(self, path: str, parsed: ParsedSource):
        self.path = path
        self.parsed = parsed
        self.declarations: dict[str, list[TypeDeclaration]] = {}
        for decl in type_declarations(parsed):
            self.declarations.setdefault(decl.name, []).append(decl)
        self.imported = {
            binding.local: (info.source, binding.imported)
            for info in imports(parsed)
            for binding in info.bindings
            if binding.imported not in ("*", "=", "default")
        }
        self.stars: list[str] = []
        for info in exports(parsed):
            if info.kind != "reexport" or info.source is None:
                continue
            if info.star and not info.bindings:
                self.stars.append(info.source)
            for binding in info.bindings:
                if binding.name not in (None, "*"):
                    self.imported[binding.exported] = (info.source, binding.name)


class _TypeIndex:
    """Property names of the scan's type declarations, resolved across imports."""

    def __init__(self) -> None:
        self._modules: dict[str, _Module | None] = {}
        self._members: dict[tuple[str, str], set[str]] = {}
        self._resolver: ModuleResolver | None = None

    def module(self, path: str) -> _Module | None:
        if path in self._modules:
            return self._modules[path]
        parsed = parsed_file(path)
        module = _Module(path, parsed) if parsed is not None else None
        self._modules[path] = module
        return module

    def members(self, module: _Module, name: str, seen: set) -> set[str]:
        """Property names of the type ``name`` as seen from ``module``."""
        key = (module.path, name)
        if key in self._members:
            return self._members[key]
        if key in seen:
            return set()
        seen = seen | {key}
        names: set[str] = set()
        decls = module.declarations.get(name)
        if decls is None:
            target = self.import_target(module, name, seen)
            if target is not None:
                names = self.members(target[0], target[1], seen)
        for decl in decls or ():
            params = set(decl.type_parameters)
            for base in decl.extends:
                names |= self._type_members(module, base, params, seen)
            names |= self._type_members(module, decl.value, params, seen)
        if len(seen) == 1:  # a nested result may be cut short by a cycle
            self._members[key] = names
        return names

    def import_target(
        self, module: _Module, name: str, seen: frozenset | set = frozenset()
    ) -> tuple[_Module, str] | None:
        """The module declaring the imported or re-exported ``name``, and its name there."""
        imported = module.imported.get(name)
        if imported is not None:
            target = self._resolve(imported[0], module)
            return (target, imported[1]) if target is not None else None
        for source in module.stars:  # ``export * from``
            target = self._resolve(source, module)
            if target is None or (target.path, name) in seen:
                continue
            if name in target.declarations:
                return target, name
            found = self.import_target(target, name, seen | {(target.path, name)})
            if found is not None:
                return found
        return None

    def _resolve(self, source: str, module: _Module) -> _Module | None:
        if self._resolver is None:
            self._resolver = project_resolver(get_project_root())
        try:
            path = self._resolver.resolve(source, module.path)
        except OSError as exc:
            log_best_effort_failure(logger, f"resolve {source} from {module.path}", exc)
            return None
        return self.module(path) if path is not None else None

    def _type_members(
        self, module: _Module, node, params: set[str], seen: set
    ) -> set[str]:
        parsed = module.parsed
        kind = node.type
        if kind in ("object_type", "interface_body"):
            return {
                _member_name(parsed, member)
                for member in node.named_children
                if member.type in _MEMBER_TYPES
                and member.child_by_field_name("name") is not None
            }
        if kind == "intersection_type":
            names: set[str] = set()
            for part in node.named_children:
                names |= self._type_members(module, part, params, seen)
            return names
        if kind == "union_type":
            variants = [
                self._type_members(module, part, params, seen)
                for part in node.named_children
            ]
            return max(variants, key=len, default=set())
        if kind == "parenthesized_type":
            inner = node.named_children
            return (
                self._type_members(module, inner[0], params, seen) if inner else set()
            )
        if kind == "type_identifier":
            name = parsed.text(node)
            return set() if name in params else self.members(module, name, seen)
        if kind == "generic_type":
            return self._generic_members(module, node, params, seen)
        return set()

    def _generic_members(
        self, module: _Module, node, params: set[str], seen: set
    ) -> set[str]:
        parsed = module.parsed
        name_node = node.child_by_field_name("name") or node.named_children[0]
        args_node = node.child_by_field_name("type_arguments")
        args = args_node.named_children if args_node is not None else []
        if name_node.type != "type_identifier":
            return set()  # ``React.HTMLAttributes<T>``: from a package
        name = parsed.text(name_node)
        if name in _PASSTHROUGH and args:
            return self._type_members(module, args[0], params, seen)
        if name in ("Omit", "Pick") and len(args) == 2:
            base = self._type_members(module, args[0], params, seen)
            keys = _literal_keys(parsed, args[1])
            if keys is None:
                return base if name == "Omit" else set()
            return base - keys if name == "Omit" else base & keys
        return set() if name in params else self.members(module, name, seen)


def _member_name(parsed, member) -> str:
    name = member.child_by_field_name("name")
    return string_value(parsed, name) if name.type == "string" else parsed.text(name)


def _literal_keys(parsed, node) -> set[str] | None:
    """The string keys of ``'a' | 'b'``, or None when it isn't only string literals."""
    parts = node.named_children if node.type == "union_type" else [node]
    keys = set()
    for part in parts:
        if part.type == "union_type":
            inner = _literal_keys(parsed, part)
            if inner is None:
                return None
            keys |= inner
            continue
        literal = (
            part.named_children[0]
            if part.type == "literal_type" and part.named_children
            else None
        )
        if literal is None or literal.type != "string":
            return None
        keys.add(string_value(parsed, literal))
    return keys


def _regex_file(filepath: str, threshold: int) -> tuple[list[dict], int]:
    """Without tree-sitter: count the non-comment lines of the body's top level."""
    entries: list[dict] = []
    total = 0
    try:
        p = (
            Path(filepath)
            if Path(filepath).is_absolute()
            else get_project_root() / filepath
        )
        content = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log_best_effort_failure(
            logger, f"read TypeScript interface file {filepath}", exc
        )
        return entries, total
    for m in _INTERFACE_RE.finditer(content):
        name = m.group(1)
        kind = _bloat_kind(name)
        if kind is None:
            continue
        total += 1
        brace_depth = 1
        pos = m.end()
        prop_count = 0
        while pos < len(content) and brace_depth > 0:
            ch = content[pos]
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
            elif ch == "\n" and brace_depth == 1:
                line_end = content.find("\n", pos + 1)
                line = content[
                    pos + 1 : line_end if line_end != -1 else len(content)
                ].strip()
                if line and not line.startswith(("//", "*", "/**")) and line != "}":
                    prop_count += 1
            pos += 1
        if prop_count > threshold:
            entries.append(
                _entry(
                    filepath,
                    name,
                    prop_count,
                    content[: m.start()].count("\n") + 1,
                    kind,
                )
            )
    return entries, total


def cmd_props(args: argparse.Namespace) -> None:
    entries, _ = detect_prop_interface_bloat(Path(args.path))
    if args.json:
        print(json.dumps({"count": len(entries), "entries": entries}, indent=2))
        return
    if not entries:
        print(colorize("No bloated prop interfaces found.", "green"))
        return
    print(colorize(f"\nBloated prop interfaces (>14 props): {len(entries)}\n", "bold"))
    rows = []
    for e in entries[: args.top]:
        rows.append(
            [e["interface"], rel(e["file"]), str(e["prop_count"]), str(e["line"])]
        )
    print_table(["Interface", "File", "Props", "Line"], rows, [35, 50, 6, 6])
