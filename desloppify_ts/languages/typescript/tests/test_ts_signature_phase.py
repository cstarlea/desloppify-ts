"""Signature variance on TypeScript sources: exported names, per package."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from desloppify_ts.languages._framework.base.shared_phases_review import phase_signature
from desloppify_ts.languages.typescript.detectors.deps.imports import ImportExtractor
from desloppify_ts.languages.typescript.extractors_functions import extract_ts_functions

pytestmark = pytest.mark.skipif(
    not ImportExtractor().uses_treesitter,
    reason="needs tree-sitter with the tsx grammar",
)


def _body(expr: str) -> str:
    return f"  const a = {expr};\n  const b = a + 1;\n  return String(b);\n"


def _write(root: Path, files: dict[str, str]) -> list[str]:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)
    return [str(root / name) for name in files if name.endswith((".ts", ".tsx"))]


def _summaries(root: Path, files: list[str]) -> list[str]:
    lang = SimpleNamespace(
        extract_functions=lambda _path: [
            fn for f in files for fn in extract_ts_functions(f)
        ],
    )
    issues, _ = phase_signature(root, lang)
    return [issue["summary"] for issue in issues]


def test_exported_helpers_with_different_params_are_reported(tmp_path):
    files = _write(
        tmp_path,
        {
            "package.json": "{}",
            "src/a.ts": f"export function formatPrice(cents: number) {{\n{_body('cents')}}}\n",
            "src/b.ts": f"export const formatPrice = (amount: number, currency: string) => {{\n{_body('amount')}}};\n",
            "src/c.ts": f"function formatPrice(value: number) {{\n{_body('value')}}}\nexport {{ formatPrice }};\n",
        },
    )
    assert _summaries(tmp_path, files) == [
        "'formatPrice' has 3 different signatures across 3 files"
    ]


def test_local_components_and_separate_packages_are_not_reported(tmp_path):
    files = _write(
        tmp_path,
        {
            "package.json": "{}",
            "src/panel.tsx": f"function StatRow({{ label, value }}) {{\n{_body('label')}}}\n",
            "src/preview.tsx": f"function StatRow({{ color }}) {{\n{_body('color')}}}\n",
            "src/board.tsx": f"const StatRow = () => {{\n{_body('1')}}};\n",
            "examples/a/package.json": "{}",
            "examples/a/server.ts": f"export function createContext(opts) {{\n{_body('opts')}}}\n",
            "examples/b/package.json": "{}",
            "examples/b/server.ts": f"export function createContext(req, res) {{\n{_body('req')}}}\n",
            "examples/c/package.json": "{}",
            "examples/c/server.ts": f"export function createContext(event) {{\n{_body('event')}}}\n",
        },
    )
    assert _summaries(tmp_path, files) == []
