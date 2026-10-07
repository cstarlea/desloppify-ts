"""TypeScript-specific test coverage heuristics and mappings."""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.text_utils import strip_c_style_comments
from desloppify.languages.typescript.detectors.deps.imports import (
    DYNAMIC_PREFIX,
    GLOB,
    MOCK,
    ImportExtractor,
)
from desloppify.languages.typescript.detectors.deps.resolver import project_resolver

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

BARREL_BASENAMES = {"index.ts", "index.tsx"}
logger = logging.getLogger(__name__)


def _relative_if_under_root(path_str: str) -> str:
    """Return project-relative path when possible; else return original."""
    try:
        return str(Path(resolve_path(path_str)).relative_to(get_project_root())).replace("\\", "/")
    except (OSError, ValueError):
        return path_str


def has_testable_logic(filepath: str, content: str) -> bool:
    """Return True if a TypeScript file has runtime logic worth testing."""
    if filepath.endswith(".d.ts"):
        return False

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
        content = Path(resolve_path(filepath)).read_text()
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


def map_test_to_source(test_path: str, production_set: set[str]) -> str | None:
    """Map a TypeScript test file path to a production file by naming convention."""
    basename = os.path.basename(test_path)
    dirname = os.path.dirname(test_path)
    parent = os.path.dirname(dirname)

    candidates: list[str] = []

    for pattern in (".test.", ".spec."):
        if pattern in basename:
            src = basename.replace(pattern, ".")
            for alt in _cross_extension_candidates(src):
                candidates.append(os.path.join(dirname, alt))
                if parent:
                    candidates.append(os.path.join(parent, alt))

    dir_basename = os.path.basename(dirname)
    if dir_basename == "__tests__" and parent:
        candidates.append(os.path.join(parent, basename))

    for c in candidates:
        if c in production_set:
            return c

    # Same file name elsewhere (tests/ beside src/): only within the package,
    # preferring the file whose directory shares the most with the test's.
    names = {os.path.basename(c) for c in candidates}
    matches = [
        prod
        for prod in production_set
        if os.path.basename(prod) in names and basename_match_allowed(test_path, prod)
    ]
    if not matches:
        return None
    test_parts = Path(dirname).parts
    return max(
        sorted(matches),
        key=lambda prod: len(os.path.commonprefix([Path(prod).parent.parts, test_parts])),
    )


def strip_test_markers(basename: str) -> str | None:
    """Strip TypeScript test naming markers to derive a source basename.

    Returns the direct replacement (e.g. ``Foo.test.ts`` → ``Foo.ts``).
    Cross-extension matching (``Foo.test.ts`` → ``Foo.tsx``) is handled by
    ``map_test_to_source`` which tries all TS/JS extensions.
    """
    for marker in (".test.", ".spec."):
        if marker in basename:
            return basename.replace(marker, ".")
    return None


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


def is_placeholder_test(
    content: str, *, assertions: int, test_functions: int
) -> bool:
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
        if tautological >= assertions and (has_placeholder_label or assertions <= test_functions):
            return True
        if has_placeholder_label and (tautological / max(assertions, 1)) >= 0.5:
            return True
    if weak_to_be_defined >= assertions:
        dynamic_import_calls = len(re.findall(r"\bimport\s*\(", content))
        if has_placeholder_label or dynamic_import_calls >= 3:
            return True
    return False
