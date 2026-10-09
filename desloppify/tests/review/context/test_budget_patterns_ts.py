"""Tests for the abstraction-budget scanners on TypeScript syntax trees."""

from __future__ import annotations

import pytest

from desloppify.intelligence.review.context_holistic.budget import (
    patterns_wrappers as wrappers_mod,
)
from desloppify.intelligence.review.context_holistic.budget.patterns_enums import (
    _census_type_strategies,
    _collect_enum_defs,
    _find_enum_bypass,
)
from desloppify.intelligence.review.context_holistic.budget.patterns_types import (
    _find_dict_any_annotations,
)
from desloppify.languages.typescript.syntax.tree import get_parser, parse_text

needs_treesitter = pytest.mark.skipif(
    get_parser("typescript") is None, reason="needs tree-sitter with the typescript grammar"
)


def _files(**sources: str) -> dict:
    return {f"src/{name}.ts": parse_text(text, f"src/{name}.ts") for name, text in sources.items()}


def _parsed(text: str):
    return parse_text(text, "src/mod.ts")


def test_budget_patterns_wrappers_exports_expected_symbols() -> None:
    assert set(wrappers_mod.__all__) == {
        "_delegate_member",
        "_find_delegation_heavy_classes",
        "_find_facade_modules",
        "_find_passthrough_wrappers",
        "_forwarded_call_target",
    }


# ── dict-any annotations ────────────────────────────────


@needs_treesitter
def test_dict_any_params_returns_and_members() -> None:
    files = _files(
        mod=(
            "export function load(a: Record<string, any>, b?: { [k: string]: any }): Record<PropertyKey, any> {\n"
            "  return {};\n"
            "}\n"
            "interface Opts { extra: Record<string, any>[]; ok: Record<string, unknown> }\n"
            "class Store { cache: { [k: string]: any } = {}; }\n"
        )
    )
    found = [(r["symbol"], r["slot"], r["line"]) for r in _find_dict_any_annotations(files)]
    assert found == [
        ("load", "a", 1),
        ("load", "b", 1),
        ("load", "(return)", 1),
        ("Opts", "extra", 4),
        ("Store", "cache", 5),
    ]


@needs_treesitter
def test_dict_any_ignores_precise_maps() -> None:
    files = _files(
        mod=(
            "function f(a: Record<string, number>, b: { [k: string]: string }, c: { id: any }): Map<string, any> {\n"
            "  return new Map();\n"
            "}\n"
        )
    )
    assert _find_dict_any_annotations(files) == []


# ── enum and union bypass ───────────────────────────────


@needs_treesitter
def test_enum_values_compared_as_raw_literals_elsewhere() -> None:
    files = _files(
        enums="export enum Mode { Fast = 'fast', Slow = 'slow', Zero = 0 }\nconst same = m === 'fast';\n",
        usage=(
            "export function pick(x: { mode: string }) {\n"
            "  if (x.mode === 'fast') return 1;\n"
            "  if (typeof x === 'slow') return 2;\n"
            "  switch (x.mode) { case 'slow': return 3; }\n"
            "  return x.mode === 0;\n"
            "}\n"
        ),
    )
    defs = _collect_enum_defs(files)
    assert defs[("src/enums.ts", "Mode")]["members"] == {"Fast": "fast", "Slow": "slow", "Zero": 0}
    hits = [(r["file"], r["line"], r["member"], r["compared"]) for r in _find_enum_bypass(files, defs)]
    assert hits == [("src/usage.ts", 2, "Fast", "x.mode"), ("src/usage.ts", 4, "Slow", "x.mode")]


@needs_treesitter
def test_union_literals_flagged_only_against_plain_strings() -> None:
    files = _files(
        mod=(
            "export type Method = 'get' | 'post';\n"
            "export function send(method: string, typed: Method, maybe?: string | undefined) {\n"
            "  if (method === 'get') {}\n"
            "  if (typed === 'post') {}\n"
            "  if (maybe !== 'post') {}\n"
            "  const local: string = method;\n"
            "  return local == 'get';\n"
            "}\n"
        )
    )
    defs = _collect_enum_defs(files)
    assert defs[("src/mod.ts", "Method")]["kind"] == "union"
    hits = [(r["line"], r["raw_value"], r["compared"], r["kind"]) for r in _find_enum_bypass(files, defs)]
    assert hits == [(3, "'get'", "method", "union"), (5, "'post'", "maybe", "union"), (7, "'get'", "local", "union")]


@needs_treesitter
def test_enum_bypass_needs_definitions() -> None:
    files = _files(mod="if (x === 'a') {}\n")
    assert _find_enum_bypass(files, _collect_enum_defs(files)) == []


# ── type-strategy census ────────────────────────────────


@needs_treesitter
def test_census_counts_each_strategy() -> None:
    files = _files(
        mod=(
            "interface User { id: string }\n"
            "type Account = { id: string };\n"
            "type Input = z.infer<typeof InputSchema>;\n"
            "type Out = v.InferOutput<typeof OutSchema>;\n"
            "type Kind = 'a' | 'b';\n"
            "type Alias = User | Account;\n"
            "enum Color { Red }\n"
            "class Point { x = 0; }\n"
            "class Repo { constructor(private db: Db) {} }\n"
            "class Service { run() {} }\n"
        )
    )
    census = {name: [e["name"] for e in items] for name, items in _census_type_strategies(files).items()}
    assert census == {
        "interface": ["User"],
        "object_type_alias": ["Account"],
        "schema_inferred": ["Input", "Out"],
        "string_union": ["Kind"],
        "class": ["Point", "Repo"],
        "enum": ["Color"],
    }


@needs_treesitter
def test_census_empty_file() -> None:
    assert _census_type_strategies(_files(mod="const x = 1;\n")) == {}


# ── pass-through wrappers ───────────────────────────────


@needs_treesitter
def test_passthrough_wrappers_forward_exact_arguments() -> None:
    parsed = _parsed(
        "export function wrap(a, b) { return build(a, b); }\n"
        "export const rest = async (x, ...more) => await api.send(x, ...more);\n"
        "function none() { return run(); }\n"
        "function partial(a) { return build('GET', a); }\n"
        "function reorder(a, b) { return build(b, a); }\n"
        "function chained(a) { return make().send(a); }\n"
        "function twoSteps(a) { log(a); return build(a); }\n"
        "function self(a) { return self(a); }\n"
        "class Client { get(url) { return this.http.get(url); } }\n"
        "const observer = { next: (v) => sink.next(v) };\n"
    )
    assert wrappers_mod._find_passthrough_wrappers(parsed) == [
        ("wrap", "build"),
        ("rest", "api.send"),
        ("none", "run"),
        ("Client.get", "this.http.get"),
    ]


# ── delegation-heavy classes ────────────────────────────


@needs_treesitter
def test_delegation_heavy_class_detected() -> None:
    parsed = _parsed(
        "class Proxy {\n"
        "  constructor(private inner: Inner) {}\n"
        "  alpha() { return this.inner.alpha(); }\n"
        "  beta(x) { return this.inner.beta(x); }\n"
        "  get gamma() { return this.inner.gamma; }\n"
        "  async delta() { await this.inner.delta(); }\n"
        "  epsilon() { this.inner.epsilon(); }\n"
        "  realWork() { return 42; }\n"
        "}\n"
    )
    results = wrappers_mod._find_delegation_heavy_classes(parsed)
    assert len(results) == 1
    r = results[0]
    assert r["class_name"] == "Proxy"
    assert r["delegation_ratio"] == round(5 / 6, 2)
    assert r["method_count"] == 6
    assert r["delegate_count"] == 5
    assert r["delegate_target"] == "inner"
    assert r["sample_methods"] == ["alpha", "beta", "gamma", "delta", "epsilon"]
    assert r["line"] == 1


@needs_treesitter
def test_delegation_below_threshold_or_small_class_not_flagged() -> None:
    mixed = _parsed(
        "class Mixed {\n"
        "  a() { return this.dep.a(); }\n"
        "  b() { return this.dep.b(); }\n"
        "  c() { return 1; }\n"
        "  d() { return this.count; }\n"
        "  e() { return 3; }\n"
        "}\n"
    )
    small = _parsed("class Small { a() { return this.dep.a(); } b() { return this.dep.b(); } }\n")
    assert wrappers_mod._find_delegation_heavy_classes(mixed) == []
    assert wrappers_mod._find_delegation_heavy_classes(small) == []


# ── facade modules ──────────────────────────────────────


@needs_treesitter
def test_facade_module_uses_the_facade_detector() -> None:
    content = "export * from './a';\nexport { b } from './b';\nexport type { T } from './a';\n"
    result = wrappers_mod._find_facade_modules(content, _parsed(content), loc=3)
    assert result == {"imports_from": ["./a", "./b"], "source_count": 2, "loc": 3}


@needs_treesitter
def test_facade_module_not_flagged_with_definitions_or_boundary() -> None:
    defined = "export * from './a';\nexport const x = 1;\n"
    client = "'use client';\nexport * from './a';\n"
    assert wrappers_mod._find_facade_modules(defined, _parsed(defined), loc=2) is None
    assert wrappers_mod._find_facade_modules(client, _parsed(client), loc=2) is None


def test_facade_module_regex_fallback_without_tree() -> None:
    content = "export * from './a';\n"
    result = wrappers_mod._find_facade_modules(content, None, loc=1)
    assert result == {"imports_from": ["./a"], "source_count": 1, "loc": 1}
