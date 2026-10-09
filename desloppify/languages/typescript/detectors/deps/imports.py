"""Import extraction for the TypeScript dependency graph.

Parses each file with tree-sitter through ``syntax.tree.parsed_file``, which
shares the parse with the scan's other users. Specifiers inside comments
and strings never become edges and every edge knows whether
it exists at runtime. Without tree-sitter, a regex fallback keeps the graph
usable (with comment-stripping but no type information).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.sfc import is_sfc, read_sfc

# Edge kinds. ``deferred`` kinds can't take part in an initialization cycle.
STATIC = "static"  # import ... from / export ... from / import x = require()
SIDE_EFFECT = "side_effect"  # import './x'
DYNAMIC = "dynamic"  # import('./x')
REQUIRE = "require"  # require('./x')
RESOLVE = "resolve"  # require.resolve('./x'), a path handed to a loader
MOCK = "mock"  # vi.mock('./x') / jest.mock('./x')
REFERENCE = "reference"  # /// <reference path="./x" />
GLOB = "glob"  # import.meta.glob('./pages/*.tsx'), specifier is the pattern
DYNAMIC_PREFIX = "dynamic_prefix"  # import(`./pages/${name}`), specifier is the prefix

DEFERRED_KINDS = frozenset({DYNAMIC, MOCK, REFERENCE, RESOLVE, GLOB, DYNAMIC_PREFIX})


@dataclass(frozen=True)
class ImportRef:
    specifier: str
    kind: str = STATIC
    type_only: bool = False

    @property
    def runtime(self) -> bool:
        """True when the edge exists at module-initialization time."""
        return not self.type_only and self.kind not in DEFERRED_KINDS


_REFERENCE_RE = re.compile(r"""^///\s*<reference\s+path\s*=\s*['"]([^'"]+)['"]""")
_MOCK_OBJECTS = frozenset({"vi", "jest"})
_MOCK_METHODS = frozenset({"mock", "doMock", "unmock", "requireActual", "importActual"})


def _text(node) -> str:
    raw = node.text
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def _string_value(node) -> str | None:
    """Value of a string or substitution-free template literal node."""
    if node is None:
        return None
    if node.type == "string":
        return "".join(_text(c) for c in node.children if c.type == "string_fragment")
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.children):
            return None
        return _text(node)[1:-1]
    return None


def _template_prefix(node) -> str | None:
    """Static prefix of a template literal with substitutions (``./a/${x}``)."""
    if node is None or node.type != "template_string":
        return None
    prefix = []
    for child in node.children:
        if child.type == "template_substitution":
            return "".join(prefix)
        if child.type == "string_fragment":
            prefix.append(_text(child))
    return None


def _first_argument(call):
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.named_children:
        if child.type != "comment":
            return child
    return None


def _has_type_keyword(node) -> bool:
    return any(child.type == "type" for child in node.children)


def _clause_is_type_only(statement, clause_type: str, specifier_type: str) -> bool:
    """``import type {..}``, or a clause whose every specifier is ``type X``."""
    if _has_type_keyword(statement):
        return True
    clause = next((c for c in statement.named_children if c.type == clause_type), None)
    if clause is None:
        return False
    if clause_type == "import_clause":
        if any(
            c.type in ("identifier", "namespace_import") for c in clause.named_children
        ):
            return False
        named = next(
            (c for c in clause.named_children if c.type == "named_imports"), None
        )
        if named is None:
            return False
        clause = named
    specifiers = [c for c in clause.named_children if c.type == specifier_type]
    return bool(specifiers) and all(_has_type_keyword(s) for s in specifiers)


def _import_statement(node, out: list[ImportRef]) -> None:
    source = node.child_by_field_name("source")
    if source is not None:
        value = _string_value(source)
        if value is None:
            return
        has_clause = any(c.type == "import_clause" for c in node.named_children)
        if not has_clause:
            out.append(ImportRef(value, SIDE_EFFECT))
            return
        out.append(
            ImportRef(
                value,
                STATIC,
                _clause_is_type_only(node, "import_clause", "import_specifier"),
            )
        )
        return
    require_clause = next(
        (c for c in node.named_children if c.type == "import_require_clause"), None
    )
    if require_clause is not None:
        string = next(
            (c for c in require_clause.named_children if c.type == "string"), None
        )
        value = _string_value(string)
        if value is not None:
            out.append(ImportRef(value, STATIC, _has_type_keyword(node)))


def _export_statement(node, out: list[ImportRef]) -> None:
    source = node.child_by_field_name("source")
    value = _string_value(source)
    if value is None:
        return
    out.append(
        ImportRef(
            value,
            STATIC,
            _clause_is_type_only(node, "export_clause", "export_specifier"),
        )
    )


def _glob_patterns(arg) -> list[str]:
    value = _string_value(arg)
    if value is not None:
        return [value]
    if arg is not None and arg.type == "array":
        return [
            v for v in (_string_value(c) for c in arg.named_children) if v is not None
        ]
    return []


def _call_expression(node, out: list[ImportRef]) -> None:
    function = node.child_by_field_name("function")
    if function is None:
        return
    if function.type == "import":
        arg = _first_argument(node)
        value = _string_value(arg)
        if value is not None:
            out.append(ImportRef(value, DYNAMIC))
            return
        prefix = _template_prefix(arg)
        if prefix:
            out.append(ImportRef(prefix, DYNAMIC_PREFIX))
        return
    if function.type == "identifier" and _text(function) == "require":
        value = _string_value(_first_argument(node))
        if value is not None:
            out.append(ImportRef(value, REQUIRE))
        return
    if function.type != "member_expression":
        return
    obj = function.child_by_field_name("object")
    prop = function.child_by_field_name("property")
    if obj is None or prop is None:
        return
    obj_text, prop_text = _text(obj), _text(prop)
    if obj_text in _MOCK_OBJECTS and prop_text in _MOCK_METHODS:
        value = _string_value(_first_argument(node))
        if value is not None:
            out.append(ImportRef(value, MOCK))
    elif obj_text == "require" and prop_text == "resolve":
        value = _string_value(_first_argument(node))
        if value is not None:
            out.append(ImportRef(value, RESOLVE))
    elif obj_text == "import.meta" and prop_text == "glob":
        for pattern in _glob_patterns(_first_argument(node)):
            if not pattern.startswith("!"):
                out.append(ImportRef(pattern, GLOB))


def _walk(root, out: list[ImportRef]) -> None:
    stack = [root]
    while stack:
        node = stack.pop()
        node_type = node.type
        if node_type == "import_statement":
            _import_statement(node, out)
            continue
        if node_type == "export_statement":
            _export_statement(node, out)
        elif node_type == "call_expression":
            _call_expression(node, out)
        elif node_type == "comment":
            match = _REFERENCE_RE.match(_text(node))
            if match:
                out.append(ImportRef(match.group(1), REFERENCE))
            continue
        stack.extend(reversed(node.children))


def _parser():
    """The tsx parser, or None when tree-sitter or the grammar is unavailable."""
    from desloppify.languages.typescript.syntax.tree import get_parser

    return get_parser("tsx")


def extract_imports_treesitter(filepath: str) -> list[ImportRef] | None:
    from desloppify.languages.typescript.syntax.tree import parsed_file

    parsed = parsed_file(filepath)
    if parsed is None:
        return None
    refs: list[ImportRef] = []
    _walk(parsed.root, refs)
    return refs


# ── Regex fallback ──────────────────────────────────────────

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"(^|[^:\\])//(?!/).*$", re.MULTILINE)
_FALLBACK_PATTERNS = (
    (
        re.compile(r"""\bimport\s+type\s[^'"]*?\bfrom\s*['"]([^'"]+)['"]"""),
        STATIC,
        True,
    ),
    (
        re.compile(r"""\bexport\s+type\s[^'"]*?\bfrom\s*['"]([^'"]+)['"]"""),
        STATIC,
        True,
    ),
    (
        re.compile(
            r"""\b(?:import|export)\s(?!\s*type\s)[^'";]*?\bfrom\s*['"]([^'"]+)['"]"""
        ),
        STATIC,
        False,
    ),
    (
        re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""", re.MULTILINE),
        SIDE_EFFECT,
        False,
    ),
    (re.compile(r"""\bimport\s*\(\s*['"`]([^'"`$]+)['"`]\s*\)"""), DYNAMIC, False),
    (re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)"""), REQUIRE, False),
    (
        re.compile(r"""\brequire\.resolve\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
        RESOLVE,
        False,
    ),
)


def extract_imports_regex(text: str) -> list[ImportRef]:
    """Best-effort extraction without a parser (comments removed first)."""
    refs = [
        ImportRef(m.group(1), REFERENCE)
        for m in re.finditer(
            r"""^///\s*<reference\s+path\s*=\s*['"]([^'"]+)['"]""", text, re.MULTILINE
        )
    ]
    code = _LINE_COMMENT_RE.sub(r"\1", _BLOCK_COMMENT_RE.sub("", text))
    for pattern, kind, type_only in _FALLBACK_PATTERNS:
        refs.extend(
            ImportRef(m.group(1), kind, type_only) for m in pattern.finditer(code)
        )
    return refs


# ── MDX ─────────────────────────────────────────────────────

_MDX_FENCE_RE = re.compile(
    r"^ {0,3}(`{3,}|~{3,}).*?^ {0,3}\1[ \t]*$", re.MULTILINE | re.DOTALL
)
# MDX ESM: ``import``/``export ... from`` at the start of a line, outside code.
_MDX_ESM_RE = re.compile(
    r"""^(?:import|export)\s(?:(?!^\s*$)[^'"])*?\bfrom\s*['"]([^'"]+)['"]|^import\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def extract_mdx_imports(text: str) -> list[ImportRef]:
    """The ESM imports of an MDX document; code fences and prose don't count."""
    code = _MDX_FENCE_RE.sub("", text)
    refs = []
    for match in _MDX_ESM_RE.finditer(code):
        if match.group(1):
            refs.append(ImportRef(match.group(1), STATIC))
        else:
            refs.append(ImportRef(match.group(2), SIDE_EFFECT))
    return refs


class ImportExtractor:
    """Extract imports from files, using tree-sitter when it is available."""

    def __init__(self) -> None:
        self._parser = _parser()

    @property
    def uses_treesitter(self) -> bool:
        return self._parser is not None

    def extract_text(self, text: str) -> list[ImportRef]:
        """Imports in source *text* (no file, no parse cache)."""
        if self._parser is not None:
            refs: list[ImportRef] = []
            _walk(self._parser.parse(text.encode("utf-8")).root_node, refs)
            return refs
        return extract_imports_regex(text)

    def extract(self, filepath: str) -> list[ImportRef]:
        if filepath.endswith((".mdx", ".md")):
            try:
                return extract_mdx_imports(
                    Path(filepath).read_text(encoding="utf-8", errors="replace")
                )
            except OSError:
                return []
        if is_sfc(filepath):
            return self._component_imports(filepath)
        if self._parser is not None:
            refs = extract_imports_treesitter(filepath)
            if refs is not None:
                return refs
        try:
            text = Path(filepath).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return extract_imports_regex(text)

    def _component_imports(self, filepath: str) -> list[ImportRef]:
        """A component's script imports, plus each external ``<script src>``."""
        component = read_sfc(filepath)
        if component is None:
            return []
        refs = [
            ImportRef(block.src, SIDE_EFFECT) for block in component.blocks if block.src
        ]
        found = (
            extract_imports_treesitter(filepath) if self._parser is not None else None
        )
        refs.extend(
            found if found is not None else extract_imports_regex(component.view)
        )
        return refs


__all__ = [
    "DEFERRED_KINDS",
    "DYNAMIC",
    "DYNAMIC_PREFIX",
    "GLOB",
    "ImportExtractor",
    "ImportRef",
    "MOCK",
    "REFERENCE",
    "REQUIRE",
    "RESOLVE",
    "SIDE_EFFECT",
    "STATIC",
    "extract_imports_regex",
    "extract_mdx_imports",
    "extract_imports_treesitter",
]
