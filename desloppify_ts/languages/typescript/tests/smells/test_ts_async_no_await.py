"""async_no_await: an async function that returns a promise has no await to add."""

from __future__ import annotations

import importlib.util

import pytest

import desloppify_ts.languages.typescript.detectors.smells.detector_flow as flow_mod
from desloppify_ts.languages.typescript.detectors.smells import TS_SMELL_CHECKS
from desloppify_ts.languages.typescript.detectors.smells.detector_flow import (
    _detect_async_no_await,
)
from desloppify_ts.languages.typescript.detectors.smells.helpers import _file_context


@pytest.fixture(params=["tree", "fallback"])
def mode(request, monkeypatch):
    if request.param == "tree" and not importlib.util.find_spec(
        "tree_sitter_language_pack"
    ):
        pytest.skip("needs tree-sitter")
    if request.param == "fallback":
        monkeypatch.setattr(flow_mod, "parsed_file", lambda _path: None)
        monkeypatch.setattr(flow_mod, "parse_text", lambda _text, _path: None)
    return request.param


@pytest.fixture
def tree_mode():
    if not importlib.util.find_spec("tree_sitter_language_pack"):
        pytest.skip("needs tree-sitter")


def _flagged(content: str) -> list[str]:
    counts: dict[str, list[dict]] = {s["id"]: [] for s in TS_SMELL_CHECKS}
    _detect_async_no_await(_file_context("lib.ts", content), counts)
    return [m["content"] for m in counts["async_no_await"]]


@pytest.mark.parametrize(
    "source",
    [
        # A call that takes an async callback: the transaction's promise.
        "export async function savePrefs(db: Db, p: Prefs) {\n"
        "  return db.transaction(async (tx) => {\n"
        "    await tx.insert(p)\n"
        "  })\n"
        "}\n",
        "export async function loadAll(ids: string[]) {\n"
        "  return Promise.all(ids.map(load))\n"
        "}\n",
        "export async function Banner() {\n"
        "  return getSession().then((s) => s?.user)\n"
        "}\n",
        "async function settle(jobs: Job[]) {\n"
        "  return Promise.allSettled(jobs.map(run)).finally(() => done())\n"
        "}\n",
        "async function fetchOrEmpty(url: string) {\n"
        "  return fetchJson(url).catch(() => [])\n"
        "}\n",
        "async function delay(ms: number) {\n"
        "  return new Promise((resolve) => setTimeout(resolve, ms))\n"
        "}\n",
        "async function cached(key: string) {\n"
        "  return Promise.resolve(store.get(key))\n"
        "}\n",
        # A direct call: the usual promise passthrough.
        "async function getUser(id: string) {\n  return api.users.get(id)\n}\n",
        # Every return is a promise, and a guard returns nothing.
        "async function sync(id?: string) {\n"
        "  if (!id) return;\n"
        "  if (id === 'all') {\n"
        "    return syncAll();\n"
        "  }\n"
        "  return syncOne(id);\n"
        "}\n",
        "const loadOne = async (id: string) => {\n  return repo.find(id)\n}\n",
    ],
)
def test_promise_returns_are_not_flagged(mode, source):
    assert _flagged(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "const loadOne = async (id: string) => repo.find(id)\n",
        "async function pick(fast: boolean) {\n"
        "  return fast ? cache.get() : (db.get() as Promise<Row>)\n"
        "}\n",
        "async function load() {\n  return import('./heavy')\n}\n",
    ],
)
def test_promise_returns_tree_only_shapes(tree_mode, source):
    assert _flagged(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "async function f() {\n  doSync();\n}\n",
        "async function count() {\n  const x = 1;\n  return x;\n}\n",
        "async function f() {\n  return;\n}\n",
        # One plain value is enough: not every path returns a promise.
        "async function find(id: string) {\n"
        "  if (!id) return null;\n"
        "  return repo.find(id);\n"
        "}\n",
    ],
)
def test_no_await_and_no_promise_return_is_flagged(mode, source):
    assert len(_flagged(source)) == 1


def test_return_in_a_nested_callback_is_flagged(tree_mode):
    # The call returns from the callback, not from the function.
    source = (
        "async function each(xs: number[]) {\n"
        "  xs.forEach((x) => {\n"
        "    return log(x);\n"
        "  });\n"
        "}\n"
    )
    assert len(_flagged(source)) == 1


def test_object_literal_async_methods_are_not_flagged(mode):
    source = "const stub = {\n  async getUser() {\n    return fakeUser\n  },\n}\n"
    assert _flagged(source) == []


def test_class_implementing_an_interface_is_not_flagged(tree_mode):
    source = (
        "class MemoryStore implements Store {\n"
        "  async get(key: string) {\n"
        "    return this.items.get(key)\n"
        "  }\n"
        "  async keys() {\n"
        "    return [...this.items.keys()]\n"
        "  }\n"
        "}\n"
    )
    assert _flagged(source) == []


def test_override_method_is_not_flagged(tree_mode):
    source = (
        "class QuietLogger extends Logger {\n"
        "  override async flush() {\n"
        "    this.buffer = []\n"
        "  }\n"
        "}\n"
    )
    assert _flagged(source) == []


def test_plain_class_method_is_still_flagged(tree_mode):
    source = "class Counter {\n  async bump() {\n    this.n += 1\n  }\n}\n"
    assert _flagged(source) == ["async Counter.bump has no await"]
