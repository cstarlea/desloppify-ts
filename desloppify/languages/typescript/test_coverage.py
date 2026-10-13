"""TypeScript-specific test coverage heuristics and mappings."""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.text_utils import strip_c_style_comments
from desloppify.languages._framework.frameworks.registry import (
    framework_entry_conventions,
)
from desloppify.languages._framework.node.frameworks.supabase import (
    is_edge_function_entry,
)
from desloppify.languages.typescript.detectors.deps.imports import (
    DYNAMIC_PREFIX,
    GLOB,
    MOCK,
    ImportExtractor,
)
from desloppify.languages.typescript.detectors.deps.packages import workspace_entries
from desloppify.languages.typescript.detectors.deps.reexports import (
    NAMESPACE,
    definition_files,
)
from desloppify.languages.typescript.detectors.deps.resolver import project_resolver
from desloppify.languages.typescript.plugin_contract import TS_BARREL_NAMES
from desloppify.languages.typescript.syntax.queries import (
    descendants,
    directive,
    imports,
)
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    get_parser,
    grammar_for,
    parse_text,
    parsed_file,
)

TS_REEXPORT_RE = re.compile(
    r"""^export\s+(?:\{[^}]*\}|\*)\s+from\s+['\"]([^'\"]+)['\"]""", re.MULTILINE
)

ASSERT_PATTERNS = [
    re.compile(p)
    for p in [
        r"expect\(",
        # Playwright/Vitest variants: expect.soft(...), expect.poll(...)
        r"\bexpect\.(?:soft|poll)\(",
        # Type-level tests (vitest/tsd/expect-type)
        r"\bexpectTypeOf\b",
        r"\bassertType\b",
        # AVA, tap and node:test use an assertion context: t.is(...), t.deepEqual(...)
        r"\bt\.(?:is|not|deepEqual|notDeepEqual|like|true|false|truthy|falsy|"
        r"throws|throwsAsync|notThrows|notThrowsAsync|regex|notRegex|snapshot|"
        r"pass|fail|equal|not[A-Z]\w*|same|strictSame|match|ok|rejects|resolves|"
        r"has\w*|assert\.\w+)\(",
        r"assert\.",
        r"\bassert(?:[A-Z]\w*)?\(",
        r"\.should\.",
        r"\b(?:getBy|findBy|getAllBy|findAllBy)\w+\(",
        r"\bwaitFor\(",
        r"\.toBeInTheDocument\(",
        r"\.toBeVisible\(",
        r"\.toHaveTextContent\(",
        r"\.toHaveAttribute\(",
    ]
]
MOCK_PATTERNS = [
    re.compile(p)
    for p in [
        r"jest\.mock\(",
        r"jest\.spyOn\(",
        r"vi\.mock\(",
        r"vi\.spyOn\(",
        r"sinon\.",
    ]
]
SNAPSHOT_PATTERNS = [
    re.compile(p)
    for p in [
        r"toMatchSnapshot",
        r"toMatchInlineSnapshot",
    ]
]
# it("..."), test.only(`...`), test.concurrent("..."), test.serial(...) (AVA),
# plus parameterized it.each / test.each tables.
TEST_FUNCTION_RE = re.compile(
    r"""\b(?:it|test)(?:\.(?!each\b)\w+)*\s*\(\s*['\"`]|\b(?:it|test|describe)\.each\b"""
)
PLACEHOLDER_LABEL_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bcoverage smoke\b",
        r"\bdirect test coverage entry\b",
        r"\bplaceholder\b",
    ]
]
EXPECT_COMPARISON_RE = re.compile(
    r"""expect\(\s*(?P<left>[^)]+?)\s*\)\s*\.(?:toBe|toEqual|toStrictEqual)\(\s*(?P<right>[^)]+?)\s*\)"""
)
EXPECT_TO_BE_DEFINED_RE = re.compile(r"""\.toBeDefined\s*\(""")

BARREL_BASENAMES = TS_BARREL_NAMES
logger = logging.getLogger(__name__)


def _relative_if_under_root(path_str: str) -> str:
    """Return project-relative path when possible; else return original."""
    try:
        return str(
            Path(resolve_path(path_str)).relative_to(get_project_root())
        ).replace("\\", "/")
    except (OSError, ValueError):
        return path_str


# Top-level statements that run no code: types, imports, ambient declarations.
_TYPE_ONLY_STATEMENTS = frozenset(
    {
        "comment",
        "hash_bang_line",
        "empty_statement",
        "import_statement",
        "type_alias_declaration",
        "interface_declaration",
        "ambient_declaration",
    }
)


def has_testable_logic(filepath: str, content: str) -> bool:
    """Return True if a TypeScript file has runtime logic worth testing.

    Decided on the syntax tree when tree-sitter is available, so multi-line
    type aliases (``type A =\\n  | B\\n  | C``) don't count as code; a
    line heuristic is the fallback.
    """
    if filepath.endswith(".d.ts"):
        return False
    parsed = parse_text(content, filepath)
    if parsed is not None:
        return any(
            _is_runtime_statement(parsed, node) for node in parsed.root.named_children
        )
    return _has_testable_logic_lines(content)


_CLASS_DECLARATIONS = frozenset({"class_declaration", "abstract_class_declaration"})
_LITERALS = frozenset(
    {
        "string",
        "number",
        "true",
        "false",
        "null",
        "undefined",
        "regex",
        "template_string",
    }
)


_FUNCTIONS = frozenset(
    {"arrow_function", "function_expression", "function", "generator_function"}
)
# ``@Type(() => Number)``: a thunk naming a type, not logic.
_THUNK_BODIES = frozenset(
    {"identifier", "member_expression", "nested_identifier", "array"}
)


def _is_shape_class(statement, node) -> bool:
    """A class that only declares fields: a DTO, an entity, an empty decorated module.

    Its decorators register metadata and its fields hold no logic, so there
    is nothing in it to test. A function in a decorator (a transform, a
    factory) is logic.
    """
    if node.type not in _CLASS_DECLARATIONS:
        return False
    body = node.child_by_field_name("body")
    if body is None:
        return False
    for member in body.named_children:
        if member.type in ("comment", "decorator", "index_signature"):
            continue
        if member.type != "public_field_definition":
            return False
        value = member.child_by_field_name("value")
        if value is not None and value.type not in _LITERALS:
            return False
    for function in descendants(statement, _FUNCTIONS):
        thunk = function.child_by_field_name("body")
        if (
            function.type != "arrow_function"
            or thunk is None
            or thunk.type not in _THUNK_BODIES
        ):
            return False
    return True


# Expressions that only build a value: object and array literals of
# literals and references (``export const metadata = {...}``, ``as const``).
_DATA_EXPRESSIONS = _LITERALS | {
    "object",
    "array",
    "pair",
    "spread_element",
    "shorthand_property_identifier",
    "property_identifier",
    "computed_property_name",
    "identifier",
    "member_expression",
    "unary_expression",
    "parenthesized_expression",
    "as_expression",
    "satisfies_expression",
    "string_fragment",
    "escape_sequence",
    "template_substitution",
}
_TYPE_NODES = frozenset(
    {"type_annotation", "type_identifier", "generic_type", "predefined_type"}
)
_DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})


def _is_data(node) -> bool:
    if node.type in _TYPE_NODES:
        return True
    if node.type not in _DATA_EXPRESSIONS:
        return False
    if node.type in ("as_expression", "satisfies_expression"):
        return _is_data(node.named_children[0])
    return all(_is_data(child) for child in node.named_children)


def _is_data_declaration(node) -> bool:
    """``const X = { ... }``: every declarator binds a plain data literal."""
    if node.type not in _DECLARATIONS:
        return False
    declarators = [c for c in node.named_children if c.type == "variable_declarator"]
    return bool(declarators) and all(
        (value := d.child_by_field_name("value")) is not None and _is_data(value)
        for d in declarators
    )


def _is_runtime_statement(parsed: ParsedSource, node) -> bool:
    if node.type in _TYPE_ONLY_STATEMENTS or directive(parsed, node) is not None:
        return False
    if _is_shape_class(node, node) or _is_data_declaration(node):
        return False
    if node.type != "export_statement":
        return True
    if node.child_by_field_name("source") is not None:
        return False  # re-export
    declaration = node.child_by_field_name("declaration")
    if declaration is not None:
        return (
            declaration.type not in _TYPE_ONLY_STATEMENTS
            and not _is_shape_class(node, declaration)
            and not _is_data_declaration(declaration)
        )
    # ``export { a }`` and ``export default a`` forward a binding;
    # ``export default <expr>`` / ``export =`` run code.
    value = node.child_by_field_name("value")
    if value is not None:
        return value.type != "identifier"
    return not any(child.type == "export_clause" for child in node.named_children)


def _has_testable_logic_lines(content: str) -> bool:
    in_block_comment = False
    brace_context = False  # True when inside type/interface/import/export braces
    brace_depth = 0

    for line in content.splitlines():
        stripped = line.strip()

        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block_comment = True
            continue

        if not stripped or stripped.startswith("//"):
            continue

        if brace_context:
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0:
                brace_context = False
                brace_depth = 0
            continue

        if re.match(r"(?:export\s+)?(?:type|interface)\s+\w+", stripped):
            opens = stripped.count("{")
            closes = stripped.count("}")
            if opens > closes:
                brace_context = True
                brace_depth = opens - closes
            continue

        if re.match(r"import\s+", stripped):
            if "{" in stripped and "}" not in stripped:
                brace_context = True
                brace_depth = stripped.count("{") - stripped.count("}")
            continue

        if re.match(r"export\s+(?:type\s+)?\{", stripped):
            if "}" not in stripped:
                brace_context = True
                brace_depth = stripped.count("{") - stripped.count("}")
            continue
        if re.match(r"export\s+\*\s*(?:as\s+\w+\s+)?from\s+", stripped):
            continue

        if re.match(r"export\s+default\s+(?:type|interface)\s+", stripped):
            opens = stripped.count("{")
            closes = stripped.count("}")
            if opens > closes:
                brace_context = True
                brace_depth = opens - closes
            continue

        if re.match(r"declare\s+", stripped):
            opens = stripped.count("{")
            closes = stripped.count("}")
            if opens > closes:
                brace_context = True
                brace_depth = opens - closes
            continue

        if re.match(r"^[}\])\s;,]*$", stripped):
            continue

        return True

    return False


def _production_key(resolved: str, production_files: set[str]) -> str | None:
    """*resolved* (absolute) in the form ``production_files`` uses, if it is one."""
    if resolved in production_files:
        return resolved
    relative = _relative_if_under_root(resolved)
    return relative if relative in production_files else None


def is_runtime_entrypoint(filepath: str, content: str) -> bool:
    """A module a runtime serves directly: a Supabase Edge Function's index,
    or a Deno ``serve(...)`` from the std http server."""
    if is_edge_function_entry(filepath):
        return True
    lowered = content.lower()
    return ("serve((" in lowered or "serve (" in lowered) and (
        "deno.land/std/http/server" in lowered or "jsr:@std/http/server" in lowered
    )


def is_served_asset(filepath: str) -> bool:
    """A file in a static directory the package's framework serves by URL
    (Next's ``public/``, SvelteKit's ``static/``): an asset, not a module."""
    package = _package_dir(filepath)
    try:
        relative = Path(resolve_path(filepath)).relative_to(package).as_posix()
    except ValueError:
        return False
    return any(
        relative.startswith(tuple(d + "/" for d in convention.static_dirs))
        and _framework_applies(convention, package)
        for convention in framework_entry_conventions()
        if convention.static_dirs
    )


@lru_cache(maxsize=256)
def _framework_applies(convention, package: str) -> bool:
    return convention.applies_to(Path(package))


_INTEGRATION_RE = re.compile(r"(?:^|[./_-])(?:integration|e2e)(?=[./_-])")


def is_integration_test(test_path: str) -> bool:
    """``x.integration.test.ts``, ``e2e/x.spec.ts``: a test driving the composed stack."""
    posix = test_path.replace("\\", "/")
    return any(
        part in ("integration", "__integration__", "e2e")
        for part in posix.split("/")[:-1]
    ) or bool(_INTEGRATION_RE.search(os.path.basename(posix)))


def public_entry_files(production_files: set[str]) -> set[str]:
    """Production files a workspace package exposes (``exports``, ``main``, ``types``...)."""
    root = get_project_root()
    candidates = sorted(
        str(root / path) if not os.path.isabs(path) else path
        for path in production_files
    )
    entries = workspace_entries(project_resolver(root).packages, candidates)
    return {
        key
        for path in entries.public
        if (key := _production_key(path, production_files))
    }


def resolve_import_spec(
    spec: str, test_path: str, production_files: set[str]
) -> str | None:
    """Resolve a TypeScript import specifier to a production file path.

    Uses the same resolver as the dependency graph (nearest tsconfig paths,
    workspace packages, ``.js`` → ``.ts`` specifiers).
    """
    resolver = project_resolver(get_project_root())
    try:
        resolved = resolver.resolve(spec, test_path)
    except OSError as exc:
        log_best_effort_failure(
            logger, f"resolve TypeScript import specifier {spec} from {test_path}", exc
        )
        return None
    return _production_key(resolved, production_files) if resolved else None


def parse_test_import_specs(content: str) -> list[str]:
    """Extract import specs from TypeScript test content.

    Imports inside comments and strings don't count, and neither do
    ``vi.mock``/``jest.mock`` targets: mocking a module replaces it.
    """
    return [
        ref.specifier
        for ref in _extractor().extract_text(content)
        if ref.kind not in (MOCK, GLOB, DYNAMIC_PREFIX)
    ]


@lru_cache(maxsize=1)
def _extractor() -> ImportExtractor:
    return ImportExtractor()


def resolve_barrel_reexports(filepath: str, production_files: set[str]) -> set[str]:
    """Resolve one-hop TypeScript barrel re-exports to concrete production files."""
    try:
        content = Path(resolve_path(filepath)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log_best_effort_failure(logger, f"read barrel re-export source {filepath}", exc)
        return set()

    results = set()
    for match in TS_REEXPORT_RE.finditer(content):
        spec = match.group(1)
        resolved = resolve_import_spec(spec, filepath, production_files)
        if resolved:
            results.add(resolved)
    return results


def follows_reexport_names() -> bool:
    """True when ``imported_definitions`` can parse, so name-blind barrel and
    facade expansion is unnecessary."""
    return (
        get_parser(grammar_for("x.ts")) is not None
        and get_parser(grammar_for("x.tsx")) is not None
    )


def imported_definitions(test_path: str, production_files: set[str]) -> set[str]:
    """Production files defining the names a test imports, through re-export chains.

    ``import { parse } from '@pkg/rpc'`` credits the file that declares
    ``parse``, however many barrels sit between. Namespace imports
    (``import * as z``) follow the members the test uses (``z.string``).
    """
    parsed = parsed_file(test_path)
    if parsed is None:
        return set()
    resolver = project_resolver(get_project_root())

    def resolve(spec: str, from_file: str) -> str | None:
        try:
            return resolver.resolve(spec, from_file)
        except OSError as exc:
            log_best_effort_failure(logger, f"resolve {spec} from {from_file}", exc)
            return None

    found: set[str] = set()
    namespaces: dict[str, str] = {}
    for info in imports(parsed):
        if info.type_only or info.kind == "side_effect":
            continue
        module = resolve(info.source, test_path)
        if module is None:
            continue
        for binding in info.bindings:
            if binding.type_only:
                continue
            if binding.imported in (NAMESPACE, "="):
                namespaces[binding.local] = module
            else:
                found |= definition_files(module, (binding.imported,), resolve)
    for local, members in _namespace_members(parsed, set(namespaces)):
        found |= definition_files(namespaces[local], members, resolve)
    return {key for path in found if (key := _production_key(path, production_files))}


def _namespace_members(
    parsed: ParsedSource, names: set[str]
) -> set[tuple[str, tuple[str, ...]]]:
    """``(ns, ("a", "b"))`` for each ``ns.a.b`` member chain on a namespace binding."""
    chains: set[tuple[str, tuple[str, ...]]] = set()
    if not names:
        return chains
    for node in descendants(parsed.root, ("member_expression",)):
        obj = node.child_by_field_name("object")
        if obj is None or obj.type != "identifier" or parsed.text(obj) not in names:
            continue
        members: list[str] = []
        current = node
        while current is not None and current.type == "member_expression":
            prop = current.child_by_field_name("property")
            if prop is None or prop.type != "property_identifier":
                break
            members.append(parsed.text(prop))
            parent = current.parent
            if parent is None or parent.child_by_field_name("object") != current:
                break
            current = parent
        if members:
            chains.add((parsed.text(obj), tuple(members)))
    return chains


_TS_SOURCE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")


def _cross_extension_candidates(src_basename: str) -> list[str]:
    """Yield the basename with each TS/JS extension swapped in."""
    stem, ext = os.path.splitext(src_basename)
    if ext not in _TS_SOURCE_EXTENSIONS:
        return [src_basename]
    return [stem + alt for alt in _TS_SOURCE_EXTENSIONS]


def _package_dir(path: str) -> str:
    """Absolute directory of the package (nearest package.json) holding *path*."""
    root = get_project_root()
    return _package_dir_cached(resolve_path(path), str(root))


@lru_cache(maxsize=4096)
def _package_dir_cached(path: str, root_str: str) -> str:
    root = Path(root_str)
    directory = Path(path).parent
    while directory.is_relative_to(root) and directory != root:
        if (directory / "package.json").is_file():
            return str(directory)
        directory = directory.parent
    return root_str


def basename_match_allowed(test_path: str, prod_path: str) -> bool:
    """Name-only test mapping stays inside one package: in a monorepo,
    ``packages/a/format.test.ts`` says nothing about ``packages/b/format.ts``."""
    return _package_dir(test_path) == _package_dir(prod_path)


def _source_basenames(basename: str) -> list[str]:
    """Source basenames a test name points at, most specific first.

    ``a.b.test.ts`` names ``a.b.ts`` and, failing that, ``a.ts``: the dotted
    qualifiers between the stem and the marker (``widget.rbac.integration``)
    are tried from longest to shortest.
    """
    for marker in (".test.", ".spec."):
        idx = basename.find(marker)
        if idx <= 0:
            continue
        parts = basename[:idx].split(".")
        rest = basename[idx + len(marker) :]
        return [".".join(parts[:n]) + "." + rest for n in range(len(parts), 0, -1)]
    return []


def _joined(directory: str, name: str) -> tuple[str, ...]:
    """*name* in *directory*, joined with either separator: the paths may use
    ``/`` even on Windows, where ``os.path`` would join with a backslash."""
    if not directory:
        return (name,)
    return (f"{directory}/{name}", f"{directory}\\{name}")


def map_test_to_source(test_path: str, production_set: set[str]) -> str | None:
    """Map a TypeScript test file path to a production file by naming convention."""
    basename = os.path.basename(test_path)
    dirname = os.path.dirname(test_path)
    parent = os.path.dirname(dirname)

    levels = [_cross_extension_candidates(n) for n in _source_basenames(basename)]
    if os.path.basename(dirname) == "__tests__" and parent:
        levels.insert(min(1, len(levels)), [basename])
        dirs = [dirname, parent]
    else:
        dirs = [dirname, parent] if parent else [dirname]

    test_parts = Path(dirname).parts
    for depth, names in enumerate(levels):
        for directory in dirs:
            for name in names:
                for candidate in _joined(directory, name):
                    if candidate in production_set:
                        return candidate
        # Same file name elsewhere (tests/ beside src/): only within the
        # package, preferring the file whose directory shares the most with
        # the test's. A qualifier-stripped ``index`` names only its own dir.
        if depth > 0 and names[0].startswith("index."):
            continue
        wanted = set(names)
        matches = [
            prod
            for prod in production_set
            if os.path.basename(prod) in wanted
            and basename_match_allowed(test_path, prod)
        ]
        if matches:
            return max(
                sorted(matches),
                key=lambda prod: len(
                    os.path.commonprefix([Path(prod).parent.parts, test_parts])
                ),
            )
    return None


def strip_test_markers(basename: str) -> str | None:
    """Strip TypeScript test naming markers to derive a source basename.

    Returns the direct replacement (e.g. ``Foo.test.ts`` → ``Foo.ts``).
    Qualified names (``Foo.rbac.test.ts`` → ``Foo.ts``) and cross-extension
    matches (``Foo.test.ts`` → ``Foo.tsx``) are handled by
    ``map_test_to_source``, which tries each of them.
    """
    names = _source_basenames(basename)
    return names[0] if names else None


def strip_comments(content: str) -> str:
    """Strip C-style comments for test quality analysis."""
    return strip_c_style_comments(content)


def _normalize_tautology_token(token: str) -> str | None:
    value = token.strip().rstrip(";")
    if not value:
        return None
    if value in {"true", "false", "null", "undefined"}:
        return value
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        return str(float(value)) if "." in value else str(int(value))
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "`"}:
        return f"str:{value[1:-1]}"
    return None


def is_placeholder_test(content: str, *, assertions: int, test_functions: int) -> bool:
    """Heuristic for synthetic coverage-smoke tests with tautological assertions."""
    if assertions <= 0 or test_functions <= 0:
        return False

    tautological = 0
    weak_to_be_defined = 0
    for line in content.splitlines():
        match = EXPECT_COMPARISON_RE.search(line)
        if not match:
            if EXPECT_TO_BE_DEFINED_RE.search(line):
                weak_to_be_defined += 1
            continue
        left = _normalize_tautology_token(match.group("left"))
        right = _normalize_tautology_token(match.group("right"))
        if left is not None and left == right:
            tautological += 1

    if tautological == 0 and weak_to_be_defined == 0:
        return False

    has_placeholder_label = any(p.search(content) for p in PLACEHOLDER_LABEL_PATTERNS)
    if tautological > 0:
        if tautological >= assertions and (
            has_placeholder_label or assertions <= test_functions
        ):
            return True
        if has_placeholder_label and (tautological / max(assertions, 1)) >= 0.5:
            return True
    if weak_to_be_defined >= assertions:
        dynamic_import_calls = len(re.findall(r"\bimport\s*\(", content))
        if has_placeholder_label or dynamic_import_calls >= 3:
            return True
    return False
