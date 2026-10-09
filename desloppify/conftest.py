"""Shared pytest fixtures for desloppify test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from desloppify.base.discovery.source import clear_source_file_cache_for_tests
from desloppify.base.runtime_state import RuntimeContext, runtime_scope

_REPO_STATE_DIR = Path(__file__).resolve().parent.parent / ".desloppify"


def _state_dir_snapshot() -> dict[str, tuple[int, int]]:
    if not _REPO_STATE_DIR.is_dir():
        return {}
    return {
        entry.name: (entry.stat().st_size, entry.stat().st_mtime_ns)
        for entry in _REPO_STATE_DIR.iterdir()
    }


@pytest.fixture(autouse=True)
def _isolate_project_root(tmp_path_factory, monkeypatch):
    """Keep tests out of the checkout's own ``.desloppify``.

    Code that falls back to the default project root (progression log,
    query.json, default state/plan paths) would otherwise write to the cwd.
    Fails the test if the checkout's ``.desloppify`` changes anyway.
    """
    monkeypatch.setenv("DESLOPPIFY_ROOT", str(tmp_path_factory.mktemp("project_root")))
    before = _state_dir_snapshot()
    yield
    after = _state_dir_snapshot()
    if after != before:
        changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
        pytest.fail(f"test wrote to {_REPO_STATE_DIR}: {', '.join(changed)}", pytrace=False)


@pytest.fixture()
def set_project_root(tmp_path: Path):
    """Set PROJECT_ROOT to tmp_path via RuntimeContext for the duration of a test."""
    ctx = RuntimeContext(project_root=tmp_path)
    with runtime_scope(ctx):
        clear_source_file_cache_for_tests()
        yield tmp_path
        clear_source_file_cache_for_tests()
