"""Import-resolution helpers shared by coverage mapping."""

from __future__ import annotations

from pathlib import Path

from desloppify.engine.detectors.test_coverage.io import read_coverage_file
from desloppify.engine.hook_registry import get_lang_hook



def _load_lang_test_coverage_module(lang_name: str | None):
    """Load language-specific test coverage helpers from ``lang/<name>/test_coverage.py``."""
    return get_lang_hook(lang_name, "test_coverage") or object()


_TS_JS_EXTENSIONS = frozenset(
    {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
)


def _infer_lang_name(test_files: set[str], production_files: set[str]) -> str | None:
    """Return ``typescript`` when any path has a TS/JS extension, else None."""
    for file_path in (*test_files, *production_files):
        if Path(file_path).suffix.lower() in _TS_JS_EXTENSIONS:
            return "typescript"
    return None



def _discover_additional_test_mapping_files(
    test_files: set[str],
    production_files: set[str],
    lang_name: str | None = None,
) -> set[str]:
    """Allow language hooks to contribute mapping-only files for coverage discovery."""
    if lang_name is None:
        lang_name = _infer_lang_name(test_files, production_files)
    mod = _load_lang_test_coverage_module(lang_name)
    discover = getattr(mod, "discover_test_mapping_files", None)
    if not callable(discover):
        return set()

    discovered = discover(test_files, production_files)
    if not discovered:
        return set()

    result: set[str] = set()
    for path in discovered:
        if not path:
            continue
        result.add(str(Path(path).resolve()))
    return result



def _resolve_import(
    spec: str,
    test_path: str,
    production_files: set[str],
    lang_name: str | None,
) -> str | None:
    mod = _load_lang_test_coverage_module(lang_name)
    resolver = getattr(mod, "resolve_import_spec", None)
    if callable(resolver):
        return resolver(spec, test_path, production_files)
    return None



def _resolve_barrel_reexports(
    filepath: str,
    production_files: set[str],
    lang_name: str | None = None,
) -> set[str]:
    """Resolve one-hop re-exports using language-specific helpers."""
    if lang_name is None:
        lang_name = _infer_lang_name({filepath}, production_files)
    mod = _load_lang_test_coverage_module(lang_name)
    resolver = getattr(mod, "resolve_barrel_reexports", None)
    if callable(resolver):
        return resolver(filepath, production_files)
    return set()



def _parse_test_imports(
    test_path: str,
    production_files: set[str],
    prod_by_module: dict[str, str],
    lang_name: str | None = None,
) -> set[str]:
    """Parse import statements from a test file and resolve production files."""
    tested = set()
    read_result = read_coverage_file(test_path, context="coverage_import_mapping_parse")
    if not read_result.ok:
        return tested
    content = read_result.content

    if lang_name is None:
        lang_name = _infer_lang_name({test_path}, production_files)

    mod = _load_lang_test_coverage_module(lang_name)
    parse_specs = getattr(mod, "parse_test_import_specs", None)
    if not callable(parse_specs):
        return tested

    for spec in parse_specs(content):
        if not spec:
            continue

        resolved = _resolve_import(spec, test_path, production_files, lang_name)
        if resolved:
            tested.add(resolved)
            continue

        # Fallback: module-name lookup with progressively shorter prefixes.
        cleaned = spec.lstrip("./").replace("/", ".")
        parts = cleaned.split(".")
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in prod_by_module:
                tested.add(prod_by_module[candidate])
                break

    return tested


__all__ = [
    "_discover_additional_test_mapping_files",
    "_infer_lang_name",
    "_load_lang_test_coverage_module",
    "_parse_test_imports",
    "_resolve_barrel_reexports",
]
