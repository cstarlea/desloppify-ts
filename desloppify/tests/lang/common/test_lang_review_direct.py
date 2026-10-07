"""Direct tests for the TypeScript review helper module."""

from __future__ import annotations

import os

import desloppify.languages.typescript.review as ts_review_mod


def test_typescript_module_patterns_and_api_surface():
    content = """
export function run() { return 1; }
export async function runAsync() { return 2; }
export default function Entry() { return null; }
"""
    patterns = ts_review_mod.module_patterns(content)
    assert "default_export" in patterns
    assert "named_export" in patterns

    file_contents = {os.path.abspath("feature.ts"): content}
    surface = ts_review_mod.api_surface(file_contents)
    assert "sync_async_mix" in surface
    assert len(surface["sync_async_mix"]) == 1
