"""Vite is a framework only when the project builds with it, not when it is
just vitest's peer dependency."""

from __future__ import annotations

import json

import pytest

from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.languages._framework.frameworks.detection import (
    detect_ecosystem_frameworks,
)


def _present(tmp_path, package: dict, files: tuple[str, ...] = ()) -> set[str]:
    (tmp_path / "package.json").write_text(json.dumps(package))
    for name in files:
        (tmp_path / name).write_text("export default {};\n")
    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        return set(detect_ecosystem_frameworks(tmp_path, None, "node").present)


_VITEST_PEER = {
    "dependencies": {"next": "16.0.0"},
    "devDependencies": {"vite": "^8.0.0", "vitest": "^5.0.0"},
    "scripts": {
        "dev": "next dev",
        "test": "vitest run",
        "test:integration": "vitest --config vitest.integration.config.ts",
    },
}


def test_vite_listed_only_for_vitest_is_not_a_framework(tmp_path):
    assert _present(tmp_path, _VITEST_PEER, ("vitest.config.ts",)) == {"nextjs"}


@pytest.mark.parametrize(
    ("change", "files"),
    [
        ({"scripts": {"dev": "vite", "test": "vitest"}}, ()),
        ({"scripts": {"build": "tsc && vite build"}}, ()),
        ({}, ("vite.config.ts",)),
        ({"dependencies": {"vite": "^8.0.0"}}, ()),
        ({"devDependencies": {"vite": "^8.0.0"}}, ()),
    ],
)
def test_vite_still_detected_when_the_project_builds_with_it(tmp_path, change, files):
    package = {
        "devDependencies": {"vite": "^8.0.0", "vitest": "^5.0.0"},
        "scripts": {"test": "vitest run"},
    }
    for key, value in change.items():
        if key == "devDependencies":
            package[key] = value  # vite without vitest
        else:
            package[key] = {**package.get(key, {}), **value}
    assert "vite" in _present(tmp_path, package, files)
