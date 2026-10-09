"""Stale @deprecated symbol detection.

With tree-sitter, each ``/** ... @deprecated ... */`` comment is attached to
the declaration, member or export specifier it documents, as TypeScript
does. Importers are the files whose imports reach the symbol through the
import graph, following re-export chains and namespace members
(``z.cuid()``); same-file uses are identifier references. A deprecated
overload of a function whose other signatures aren't deprecated is an
``overload``, not a deprecated symbol. Without tree-sitter a regex finds the
tag and the next declaration line, and importers are files mentioning the
name. Ambient declaration files (``.d.ts``) are skipped.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files, read_file_text
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.output.terminal import colorize, print_table
from desloppify.base.search.grep import grep_files_containing
from desloppify.languages._framework.treesitter import (
    disable_parse_cache,
    enable_parse_cache,
    is_parse_cache_enabled,
)
from desloppify.languages.typescript.detectors.contracts import DetectorResult
from desloppify.languages.typescript.detectors.deps.reexports import (
    NAMESPACE,
    module_exports,
)
from desloppify.languages.typescript.detectors.deps.resolver import project_resolver
from desloppify.languages.typescript.syntax.nodes import binding_names
from desloppify.languages.typescript.syntax.queries import descendants, exports, imports
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    get_parser,
    parsed_file,
)

logger = logging.getLogger(__name__)

_DECLARATION_FILE_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
# The JSDoc tag TypeScript understands. Bare words like "deprecated" in
# strings, identifiers or prose are not deprecation markers.
_JSDOC_DEPRECATED_RE = re.compile(r"@deprecated\b", re.IGNORECASE)
_MAX_HOPS = 64

# ── Syntax tree ─────────────────────────────────────────────

_NAMED_DECLARATIONS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_signature",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "module",
        "internal_module",
    }
)
_VARIABLE_DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})
_FUNCTIONS = frozenset(
    {"function_declaration", "generator_function_declaration", "function_signature"}
)
_MEMBERS = {
    "property_signature": "name",
    "method_signature": "name",
    "abstract_method_signature": "name",
    "public_field_definition": "name",
    "method_definition": "name",
    "pair": "key",
    "enum_assignment": "name",
}
_REFERENCES = frozenset(
    {
        "identifier",
        "type_identifier",
        "shorthand_property_identifier",
        "shorthand_property_identifier_pattern",
    }
)
_DECLARED_NAME_PARENTS = _NAMED_DECLARATIONS | {"variable_declarator"}
_CHAIN_NODES = frozenset(
    {"member_expression", "nested_type_identifier", "nested_identifier"}
)
_CHAIN_RE = re.compile(r"[\w$]+(?:\.[\w$]+)+")


def detect_deprecated_result(path: Path) -> DetectorResult[dict[str, Any]]:
    """Find deprecated symbols with explicit population semantics."""
    ts_files = [
        f
        for f in find_ts_and_js_files(path)
        if not f.endswith(_DECLARATION_FILE_SUFFIXES)
    ]
    if get_parser("typescript") is not None and get_parser("tsx") is not None:
        # Outside a scan (``detect deprecated``), parse each file once here too.
        owns_cache = not is_parse_cache_enabled()
        if owns_cache:
            enable_parse_cache()
        try:
            entries = _tree_entries(ts_files)
        finally:
            if owns_cache:
                disable_parse_cache()
    else:
        entries = _regex_entries(ts_files, scan_root=path)
    sorted_entries = sorted(entries, key=lambda e: e["importers"])
    return DetectorResult(
        entries=sorted_entries,
        population_kind="deprecated_symbols",
        population_size=len(sorted_entries),
    )


def _tree_entries(ts_files: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    export_names: dict[int, set[str]] = {}
    for filepath in ts_files:
        parsed = parsed_file(filepath)
        if parsed is None or not _JSDOC_DEPRECATED_RE.search(
            parsed.source.decode("utf-8", "replace")
        ):
            continue
        for entry, names in _file_entries(filepath, parsed):
            export_names[len(entries)] = names
            entries.append(entry)
    top_level = [i for i, e in enumerate(entries) if e["kind"] == "top-level"]
    if top_level:
        importers = _ImportGraph(ts_files).importers(
            {i: (resolve_path(entries[i]["file"]), export_names[i]) for i in top_level}
        )
        for i in top_level:
            entries[i]["importers"] = len(importers.get(i, ()))
    return entries


def _file_entries(
    filepath: str, parsed: ParsedSource
) -> Iterator[tuple[dict[str, Any], set[str]]]:
    """(entry, names the file exports it under) for each deprecated symbol in one file."""
    documented: dict[
        tuple[int, int], tuple[Any, int]
    ] = {}  # node span -> (node, line of the tag)
    for comment in descendants(parsed.root, ("comment",)):
        text = parsed.text(comment)
        match = _JSDOC_DEPRECATED_RE.search(text) if text.startswith("/**") else None
        target = _documented_node(comment) if match else None
        if match is not None and target is not None:
            line = comment.start_point[0] + 1 + text.count("\n", 0, match.start())
            documented.setdefault(_key(target), (target, line))
    if not documented:
        return
    exported_as = _exported_names(parsed)
    commonjs = parsed.source.decode("utf-8", "replace")
    references = Counter(
        parsed.text(n)
        for n in descendants(parsed.root, _REFERENCES)
        if not _is_declared_name(n)
    )
    seen: set[tuple[str, bool]] = set()  # a member may share a top-level symbol's name
    for node, line in documented.values():
        for symbol, kind, names in _targets(parsed, node, documented, exported_as):
            if (symbol, kind == "top-level") in seen:
                continue
            seen.add((symbol, kind == "top-level"))
            exported = bool(names) or _commonjs_export(commonjs, symbol)
            entry = {
                "file": filepath,
                "line": line,
                "symbol": symbol,
                "kind": kind,
                "importers": 0 if kind == "top-level" else -1,
                "exported": exported if kind == "top-level" else False,
                "same_file_uses": references[symbol] if kind == "top-level" else 0,
            }
            yield entry, names


def _key(node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def _documented_node(comment):
    """The node a JSDoc comment documents: the next sibling that isn't a comment.

    As in TypeScript, a comment that trails a sibling on the same line
    belongs to that sibling, not the next one.
    """
    previous = comment.prev_named_sibling
    while previous is not None and previous.type == "comment":
        previous = previous.prev_named_sibling
    if previous is not None and previous.end_point[0] == comment.start_point[0]:
        return None
    node = comment.next_named_sibling
    while node is not None and node.type == "comment":
        node = node.next_named_sibling
    return node


def _targets(
    parsed: ParsedSource,
    node,
    documented: dict[tuple[int, int], tuple[Any, int]],
    exported_as: dict[str, set[str]],
) -> list[tuple[str, str, set[str]]]:
    """(symbol, kind, exported names) for each name the documented node declares."""
    if node.type == "export_statement":
        declaration = node.child_by_field_name("declaration")
        if declaration is None:
            return []
        node = declaration
    if node.type == "ambient_declaration":
        inner = [c for c in node.named_children if c.type != "comment"]
        if not inner:
            return []
        node = inner[0]
    if node.type == "export_specifier":
        alias = node.child_by_field_name("alias") or node.child_by_field_name("name")
        name = _name_text(parsed, alias) if alias is not None else None
        return [(name, "top-level", {name})] if name else []
    if node.type in _NAMED_DECLARATIONS:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return []
        name = parsed.text(name_node)
        kind = (
            "overload"
            if node.type in _FUNCTIONS
            and _partly_deprecated(parsed, node, name, documented)
            else "top-level"
        )
        return [(name, kind, exported_as.get(name, set()))]
    if node.type in _VARIABLE_DECLARATIONS:
        targets = []
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            bound = binding_names(declarator.child_by_field_name("name"))
            for name_node in sorted(bound, key=lambda n: n.start_byte):
                name = parsed.text(name_node)
                targets.append((name, "top-level", exported_as.get(name, set())))
        return targets
    if node.type in _MEMBERS:
        name_node = node.child_by_field_name(_MEMBERS[node.type])
        return (
            [(_name_text(parsed, name_node), "property", set())]
            if name_node is not None
            else []
        )
    if node.type in (
        "property_identifier",
        "shorthand_property_identifier",
    ):  # enum member, ``{ a }``
        return [(parsed.text(node), "property", set())]
    return []


def _name_text(parsed: ParsedSource, node) -> str:
    text = parsed.text(node)
    return text[1:-1] if node.type == "string" else text


def _statement(node):
    parent = node.parent
    return (
        parent
        if parent is not None
        and parent.type in ("export_statement", "ambient_declaration")
        else node
    )


def _partly_deprecated(
    parsed: ParsedSource,
    node,
    name: str,
    documented: dict[tuple[int, int], tuple[Any, int]],
) -> bool:
    """A function overload whose sibling signatures aren't all deprecated.

    With overload signatures, callers see only those, so the symbol is
    deprecated when every signature is.
    """
    holder = _statement(node)
    container = holder.parent
    if container is None:
        return False
    overloads = []
    for sibling in container.named_children:
        inner = (
            sibling.child_by_field_name("declaration")
            if sibling.type == "export_statement"
            else sibling
        )
        if inner is not None and inner.type == "ambient_declaration":
            inner = inner.named_children[0] if inner.named_children else None
        if inner is None or inner.type not in _FUNCTIONS:
            continue
        inner_name = inner.child_by_field_name("name")
        if inner_name is not None and parsed.text(inner_name) == name:
            overloads.append(inner)
    signatures = [o for o in overloads if o.type == "function_signature"] or overloads
    return not all(
        _key(o) in documented or _key(_statement(o)) in documented for o in signatures
    )


def _is_declared_name(node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    if parent.type in ("export_specifier", "import_specifier"):
        return True
    if parent.type not in _DECLARED_NAME_PARENTS:
        return False
    name = parent.child_by_field_name("name")
    return name is not None and _key(name) == _key(node)


def _exported_names(parsed: ParsedSource) -> dict[str, set[str]]:
    """Local name -> the names this file exports it under (``export``, ``export { a as b }``)."""
    names: dict[str, set[str]] = {}
    for info in exports(parsed):
        if info.kind not in ("declaration", "named", "default"):
            continue
        for binding in info.bindings:
            if binding.name is not None:
                names.setdefault(binding.name, set()).add(binding.exported)
    return names


def _commonjs_export(text: str, name: str) -> bool:
    """``exports.name =``, ``module.exports.name`` or a ``module.exports = {...}`` naming it."""
    escaped = re.escape(name)
    return bool(
        re.search(rf"\bexports\.{escaped}\b", text)
        or re.search(rf"\bmodule\.exports\s*=\s*\{{[^}}]*\b{escaped}\b", text)
    )


class _ImportGraph:
    """Which files' imports reach which exports, through re-export chains."""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        self._resolver = project_resolver(get_project_root())
        self._walks: dict[tuple[str, tuple[str, ...]], frozenset[tuple[str, str]]] = {}

    def importers(
        self, targets: dict[int, tuple[str, set[str]]]
    ) -> dict[int, set[str]]:
        """For each target (declaring file, exported names), the other files importing it."""
        by_export: dict[tuple[str, str], list[int]] = {}
        for key, (path, names) in targets.items():
            for name in names:
                by_export.setdefault((path, name), []).append(key)
        member_names = {name for _path, name in by_export}
        found: dict[int, set[str]] = {}
        for filepath in self.files:
            importer = resolve_path(filepath)
            for module, names in self._imported_paths(importer, member_names):
                for step in self._walk(module, names):
                    for key in by_export.get(step, ()):
                        if step[0] != importer:
                            found.setdefault(key, set()).add(importer)
        return found

    def _imported_paths(
        self, path: str, member_names: set[str]
    ) -> Iterator[tuple[str, tuple[str, ...]]]:
        """(module, member path) for each name the file imports, and members used on it."""
        parsed = parsed_file(path)
        if parsed is None:
            return
        # local -> (member prefix, module, names): ``z.iso.x`` on ``const z = { iso: _iso }``
        # has prefix ``("iso",)`` and continues as ``_iso.x``.
        bound: dict[str, list[tuple[tuple[str, ...], str, tuple[str, ...]]]] = {}
        for info in imports(parsed):
            if info.kind == "side_effect":
                continue
            module = self._resolve(info.source, path)
            if module is None:
                continue
            for binding in info.bindings:
                names = (
                    () if binding.imported in (NAMESPACE, "=") else (binding.imported,)
                )
                bound[binding.local] = [((), module, names)]
                if names:
                    yield module, names
        if not bound:
            return
        _bind_object_aliases(parsed, bound)
        for chain in _member_chains(parsed, set(bound), member_names):
            for prefix, module, names in bound[chain[0]]:
                if chain[1 : 1 + len(prefix)] == prefix and len(chain) > 1 + len(
                    prefix
                ):
                    yield module, names + chain[1 + len(prefix) :]

    def _resolve(self, specifier: str, from_file: str) -> str | None:
        try:
            return self._resolver.resolve(specifier, from_file)
        except OSError as exc:
            log_best_effort_failure(
                logger, f"resolve {specifier} from {from_file}", exc
            )
            return None

    def _walk(self, path: str, names: tuple[str, ...]) -> frozenset[tuple[str, str]]:
        """Every (file, exported name) an import of ``names`` from ``path`` passes through."""
        key = (path, names)
        if key not in self._walks:
            self._walks[key] = frozenset(self._steps(path, names, set(), 0) or ())
        return self._walks[key]

    def _steps(
        self, path: str, names: tuple[str, ...], seen: set, hops: int
    ) -> set[tuple[str, str]] | None:
        if not names or (path, names) in seen or hops > _MAX_HOPS:
            return None
        seen.add((path, names))
        summary = module_exports(path, types=True)
        if summary is None:
            return None
        name, rest = names[0], names[1:]
        here = {(path, name)}
        if name in summary.local:
            return here
        forward = summary.forwarded.get(name)
        if forward is not None:
            spec, original = forward
            target = self._resolve(spec, path)
            inner = rest if original == NAMESPACE else (original, *rest)
            further = (
                self._steps(target, inner, seen, hops + 1)
                if target is not None
                else None
            )
            return here | (further or set())
        if name == "default":
            return None  # ``export *`` never forwards the default export
        for spec in summary.stars:
            target = self._resolve(spec, path)
            further = (
                self._steps(target, names, seen, hops + 1)
                if target is not None
                else None
            )
            if further:
                return here | further
        return None


def _bind_object_aliases(parsed: ParsedSource, bound: dict) -> None:
    """``const z = { ...ns, iso: _iso }``: members of ``z`` reach the imports it spreads or holds."""
    for declarator in descendants(parsed.root, ("variable_declarator",)):
        name, value = (
            declarator.child_by_field_name("name"),
            declarator.child_by_field_name("value"),
        )
        if (
            name is None
            or value is None
            or name.type != "identifier"
            or value.type != "object"
        ):
            continue
        aliases = []
        for member in value.named_children:
            if member.type == "spread_element" and member.named_children:
                source, key = member.named_children[0], ()
            elif (
                member.type == "pair"
                and member.child_by_field_name("key").type == "property_identifier"
            ):  # type: ignore[union-attr]
                source, key = (
                    member.child_by_field_name("value"),
                    (parsed.text(member.child_by_field_name("key")),),
                )
            else:
                continue
            if source is not None and source.type == "identifier":
                aliases.extend(
                    (key + prefix, module, names)
                    for prefix, module, names in bound.get(parsed.text(source), ())
                )
        if aliases:
            bound.setdefault(parsed.text(name), []).extend(aliases)


def _member_chains(
    parsed: ParsedSource, locals_: set[str], member_names: set[str]
) -> set[tuple[str, ...]]:
    """``("z", "cuid")`` for each ``z.cuid`` (value or type) on an imported binding,
    kept when a member is one of ``member_names``."""
    chains: set[tuple[str, ...]] = set()
    for node in descendants(parsed.root, _CHAIN_NODES):
        parent = node.parent
        if (
            parent is not None
            and parent.type in _CHAIN_NODES
            and node.start_byte == parent.start_byte
        ):
            continue  # only the outermost of ``a.b.c``
        text = parsed.text(node)
        if not _CHAIN_RE.fullmatch(text):
            # ``z.string().min``: the inner ``z.string`` is its own node
            continue
        parts = tuple(text.split("."))
        if parts[0] in locals_ and member_names.intersection(parts[1:]):
            chains.add(parts)
    return chains


# ── Regex fallback (no tree-sitter) ─────────────────────────

_INLINE_PROPERTY_RE = re.compile(r"(\w+)\s*[?:=]")
_INLINE_DECLARATION_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var|function|class|type|interface|enum)\s+(\w+)"
)
_DECLARATION_LINE_RE = re.compile(
    r"(?:export\s+)?(?:declare\s+)?(?:const|let|var|function|class|type|interface|enum)\s+(\w+)"
)
_PROPERTY_LINE_RE = re.compile(r"(\w+)\s*[?:]")


def _regex_entries(ts_files: list[str], *, scan_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    for filepath in ts_files:
        text = _read_source(filepath, scan_root=scan_root)
        if text is None or not _JSDOC_DEPRECATED_RE.search(text):
            continue
        texts[filepath] = text
        lines = text.splitlines()
        seen: set[str] = (
            set()
        )  # same symbol in the same file, e.g. several tags on one interface
        for lineno, content in enumerate(lines, 1):
            if not _JSDOC_DEPRECATED_RE.search(content):
                continue
            symbol, kind = _extract_deprecated_symbol(lines, lineno, content)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            exported, same_file_uses = (
                _export_and_local_uses(symbol, text)
                if kind == "top-level"
                else (False, 0)
            )
            entries.append(
                {
                    "file": filepath,
                    "line": lineno,
                    "symbol": symbol,
                    "kind": kind,
                    "importers": -1,
                    "exported": exported,
                    "same_file_uses": same_file_uses,
                }
            )
    names = {e["symbol"] for e in entries if e["kind"] == "top-level"}
    mentioned = grep_files_containing(names, ts_files) if names else {}
    for entry in entries:
        if entry["kind"] == "top-level":
            entry["importers"] = len(
                mentioned.get(entry["symbol"], set()) - {entry["file"]}
            )
    return entries


def _read_source(filepath: str, *, scan_root: Path) -> str | None:
    path = Path(filepath)
    if not path.is_absolute():
        candidate = scan_root / filepath
        path = candidate if candidate.exists() else Path(resolve_path(filepath))
    text = read_file_text(str(path))
    if text is None:
        log_best_effort_failure(
            logger, f"read deprecated source context {filepath}", OSError(filepath)
        )
    return text


def _extract_deprecated_symbol(
    lines: list[str], lineno: int, content: str
) -> tuple[str | None, str]:
    """The deprecated symbol name for a tag on line ``lineno``, and its kind."""
    content_stripped = content.strip()
    if "/**" in content_stripped and "*/" in content_stripped:
        inline_jsdoc = content_stripped.split("*/", 1)[1].strip()
        if inline_jsdoc:
            inline_match = _match_inline_deprecated_target(inline_jsdoc)
            if inline_match is not None:
                return inline_match
    scanned_line = _scan_following_declaration_line(lines, lineno)
    if scanned_line is not None:
        return scanned_line
    inline_comment_symbol = _match_inline_comment_property(content_stripped)
    if inline_comment_symbol is not None:
        return inline_comment_symbol, "property"
    return None, "unknown"


def _match_inline_deprecated_target(line: str) -> tuple[str, str] | None:
    property_match = _INLINE_PROPERTY_RE.match(line)
    if property_match:
        return property_match.group(1), "property"
    declaration_match = _INLINE_DECLARATION_RE.match(line)
    if declaration_match:
        return declaration_match.group(1), "top-level"
    return None


def _scan_following_declaration_line(
    lines: list[str], lineno: int
) -> tuple[str, str] | None:
    for offset in range(1, 8):
        idx = lineno - 1 + offset
        if idx >= len(lines):
            break
        src = lines[idx].strip()
        if not src or src.startswith("*") or src.startswith("//"):
            continue
        declaration_match = _DECLARATION_LINE_RE.match(src)
        if declaration_match:
            return declaration_match.group(1), "top-level"
        property_match = _PROPERTY_LINE_RE.match(src)
        if property_match:
            return property_match.group(1), "property"
        break
    return None


def _match_inline_comment_property(content: str) -> str | None:
    if "//" not in content and "*" not in content:
        return None
    tag_match = _JSDOC_DEPRECATED_RE.search(content)
    if tag_match is None:
        return None
    line_before = content[: tag_match.start()].strip().rstrip("/*").rstrip("*").strip()
    property_match = _PROPERTY_LINE_RE.search(line_before)
    if property_match:
        return property_match.group(1)
    return None


def _export_and_local_uses(name: str, text: str) -> tuple[bool, int]:
    """Return (is exported, uses in the declaring file besides the declaration)."""
    escaped = re.escape(name)
    exported = bool(
        re.search(
            r"\bexport\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
            rf"(?:const|let|var|function\*?|class|type|interface|enum|namespace)\s+{escaped}\b",
            text,
        )
        or re.search(rf"\bexport\s*(?:type\s*)?\{{[^}}]*\b{escaped}\b", text)
        or re.search(rf"\bexport\s+default\s+{escaped}\b", text)
        or _commonjs_export(text, name)
    )
    occurrences = len(re.findall(rf"(?<![\w$]){escaped}(?![\w$])", text))
    return exported, max(0, occurrences - 1)


def cmd_deprecated(args: Any) -> None:
    result = detect_deprecated_result(Path(args.path))
    entries = result.entries
    if args.json:
        print(
            json.dumps(
                {
                    "count": len(entries),
                    "entries": entries,
                    "population_size": result.population_size,
                },
                indent=2,
            )
        )
        return

    if not entries:
        print(colorize("No @deprecated annotations found.", "green"))
        return

    # Separate top-level symbols from members and single overloads
    top_level = [e for e in entries if e["kind"] == "top-level"]
    members = [e for e in entries if e["kind"] != "top-level"]

    print(
        colorize(
            f"\nDeprecated symbols: {len(entries)} ({len(top_level)} top-level, {len(members)} members/overloads)\n",
            "bold",
        )
    )

    if top_level:
        print(colorize("Top-level (importable):", "cyan"))
        rows = []
        for e in top_level[: args.top]:
            imp = str(e["importers"]) if e["importers"] >= 0 else "?"
            if e.get("exported") and e["importers"] == 0:
                status = "exported, no local importers"
            elif e["importers"] == 0 and not e.get("same_file_uses"):
                status = colorize("unused", "green")
            else:
                status = f"{imp} importers"
            rows.append([e["symbol"], rel(e["file"]), status])
        print_table(["Symbol", "File", "Status"], rows, [30, 55, 20])
        print()

    if members:
        print(colorize("Members and overloads (inline):", "cyan"))
        rows = []
        for e in members[: args.top]:
            rows.append([e["symbol"], rel(e["file"]), f"line {e['line']}"])
        print_table(["Member", "File", "Line"], rows, [30, 55, 10])
