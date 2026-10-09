"""Focused unit tests for context_holistic.budget helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.intelligence.review.context_holistic import budget as budget_mod
from desloppify.languages.typescript.detectors.deps.resolver import clear_resolver_cache
from desloppify.languages.typescript.syntax.tree import get_parser

needs_treesitter = pytest.mark.skipif(
    get_parser("typescript") is None, reason="needs tree-sitter with the typescript grammar"
)


def test_count_signature_params_ignores_instance_receiver_tokens():
    assert budget_mod._count_signature_params("self, a, b, cls, this, c") == 3
    assert budget_mod._count_signature_params("   ") == 0


def test_extract_type_names_handles_generics_and_qualified_names():
    raw = "IRepo, pkg.Service<T>, (BaseProtocol), invalid-token"
    names = budget_mod._extract_type_names(raw)
    assert names == ["IRepo", "Service", "BaseProtocol"]


def test_abstractions_context_reports_wrapper_and_indirection_signals(tmp_path):
    util_file = tmp_path / "pkg" / "utils.ts"
    contracts_file = tmp_path / "pkg" / "contracts.ts"
    service_file = tmp_path / "pkg" / "service.ts"

    util_content = (
        "export function wrapUser(value) {\n"
        "  return makeUser(value);\n"
        "}\n\n"
        "function makeUser(value) {\n"
        "  return value;\n"
        "}\n"
    )
    contracts_content = "interface Repo {}\nclass SqlRepo implements Repo {}\n"
    service_content = (
        "function build(a, b, c, d, e, f, g) {\n"
        "  return a;\n"
        "}\n\n"
        "const value = root.one.two.three.four;\n"
        "// config config config config config config config config config config\n"
    )

    util_file.parent.mkdir(parents=True, exist_ok=True)
    util_file.write_text(util_content)
    contracts_file.write_text(contracts_content)
    service_file.write_text(service_content)

    file_contents = {
        str(util_file): util_content,
        str(contracts_file): contracts_content,
        str(service_file): service_content,
    }

    context = budget_mod._abstractions_context(file_contents)

    assert context["summary"]["total_wrappers"] >= 1
    assert context["summary"]["one_impl_interface_count"] == 1
    assert context["util_files"][0]["file"].endswith("utils.ts")
    assert "pass_through_wrappers" in context
    assert "indirection_hotspots" in context
    assert "wide_param_bags" in context
    assert 0 <= context["sub_axes"]["abstraction_leverage"] <= 100
    assert 0 <= context["sub_axes"]["indirection_cost"] <= 100
    assert 0 <= context["sub_axes"]["interface_honesty"] <= 100


def test_codebase_stats_counts_files_and_loc():
    stats = budget_mod._codebase_stats({"a.py": "x\n", "b.py": "one\ntwo\nthree\n"})
    assert stats == {"total_files": 2, "total_loc": 4}


# ── _score_clamped ────────────────────────────────────────


def test_score_clamped_typical_value():
    assert budget_mod._score_clamped(75.3) == 75


def test_score_clamped_rounds_half_up():
    assert budget_mod._score_clamped(50.5) == 50  # Python banker's rounding


def test_score_clamped_clamps_below_zero():
    assert budget_mod._score_clamped(-15.0) == 0


def test_score_clamped_clamps_above_100():
    assert budget_mod._score_clamped(200.0) == 100


def test_score_clamped_zero_and_100_boundaries():
    assert budget_mod._score_clamped(0.0) == 0
    assert budget_mod._score_clamped(100.0) == 100


# ── _count_signature_params edge cases ────────────────────


def test_count_signature_params_empty_string():
    assert budget_mod._count_signature_params("") == 0


def test_count_signature_params_only_receivers():
    assert budget_mod._count_signature_params("self") == 0
    assert budget_mod._count_signature_params("cls") == 0
    assert budget_mod._count_signature_params("this") == 0
    assert budget_mod._count_signature_params("self, cls") == 0


def test_count_signature_params_with_type_annotations():
    assert budget_mod._count_signature_params("a: int, b: str, c: float") == 3


def test_count_signature_params_with_defaults():
    assert budget_mod._count_signature_params("a=1, b=None") == 2


def test_count_signature_params_trailing_comma():
    # trailing comma produces empty split elements that get filtered
    assert budget_mod._count_signature_params("a, b,") == 2


# ── _extract_type_names edge cases ────────────────────────


def test_extract_type_names_empty_string():
    assert budget_mod._extract_type_names("") == []


def test_extract_type_names_single_name():
    assert budget_mod._extract_type_names("Foo") == ["Foo"]


def test_extract_type_names_strips_colon_suffix():
    assert budget_mod._extract_type_names("Foo:") == ["Foo"]


def test_extract_type_names_rejects_non_identifiers():
    assert budget_mod._extract_type_names("123, -, !@#") == []


# ── _abstractions_context scoring edge cases ──────────────


def test_abstractions_context_empty_input():
    """Empty file dict produces zero-count summary."""
    context = budget_mod._abstractions_context({})
    assert context["summary"]["total_wrappers"] == 0
    assert context["summary"]["total_function_signatures"] == 0
    assert context["summary"]["one_impl_interface_count"] == 0
    assert context["sub_axes"]["abstraction_leverage"] == 100
    assert context["sub_axes"]["indirection_cost"] == 100
    assert context["sub_axes"]["interface_honesty"] == 100
    assert context["util_files"] == []
    assert "pass_through_wrappers" not in context
    assert "one_impl_interfaces" not in context
    # No syntax trees, so no delegation or type data: those axes are left out.
    assert "delegation_density" not in context["sub_axes"]
    assert "type_discipline" not in context["sub_axes"]


def test_abstractions_context_interface_with_multiple_impls_not_reported(tmp_path):
    """Interfaces with 2+ implementations should not appear in one_impl_interfaces."""
    f1 = tmp_path / "pkg" / "contract.ts"
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text("interface IRepo {}\n")

    f2 = tmp_path / "pkg" / "sql.ts"
    f2.write_text("class SqlRepo implements IRepo {}\n")

    f3 = tmp_path / "pkg" / "mongo.ts"
    f3.write_text("class MongoRepo implements IRepo {}\n")

    file_contents = {
        str(f1): f1.read_text(),
        str(f2): f2.read_text(),
        str(f3): f3.read_text(),
    }

    context = budget_mod._abstractions_context(file_contents)
    assert context["summary"]["one_impl_interface_count"] == 0
    assert "one_impl_interfaces" not in context


@needs_treesitter
def test_abstractions_context_wrapper_rate_calculation(tmp_path):
    """Verify wrapper rate = total_wrappers / total_function_signatures."""
    f = tmp_path / "pkg" / "mod.ts"
    f.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "function alpha(x) {\n"
        "  return beta(x);\n"
        "}\n"
        "const beta = (x) => x;\n"
        "class Doubler {\n"
        "  gamma(x) {\n"
        "    return x * 2;\n"
        "  }\n"
        "}\n"
    )
    f.write_text(content)

    context = budget_mod._abstractions_context({str(f): content})
    summary = context["summary"]
    assert summary["total_wrappers"] == 1  # alpha -> beta
    assert summary["total_function_signatures"] == 3
    assert summary["wrapper_rate"] == round(1 / 3, 3)
    assert context["pass_through_wrappers"][0]["samples"] == ["alpha->beta"]


def test_abstractions_context_regex_wrappers_without_tree(tmp_path, monkeypatch):
    """Without tree-sitter, ``function f() { return g(`` still counts as a wrapper."""
    from desloppify.intelligence.review.context_holistic.budget import scan as scan_mod

    monkeypatch.setattr(scan_mod, "parse_text", lambda _content, _path: None)
    content = "function alpha(x) {\n  return beta(x);\n}\n"
    context = budget_mod._abstractions_context({str(tmp_path / "mod.ts"): content})
    assert context["summary"]["total_wrappers"] == 1
    assert "type_discipline" not in context["sub_axes"]


# ── Economy sub-axes in _abstractions_context ─────────────


@needs_treesitter
def test_abstractions_context_includes_economy_sub_axes(tmp_path):
    """With TypeScript files parsed, every sub-axis is scored from real data."""
    content = (
        "export function load(opts: Record<string, any>) {\n"
        "  return opts;\n"
        "}\n"
    )
    context = budget_mod._abstractions_context({str(tmp_path / "mod.ts"): content})
    sub = context["sub_axes"]
    assert sub["delegation_density"] == 100
    assert sub["definition_directness"] == 100
    assert sub["type_discipline"] == 99
    assert context["summary"]["dict_any_annotation_count"] == 1
    assert context["dict_any_annotations"][0]["slot"] == "opts"


def test_abstractions_context_economy_summary_keys():
    """New summary keys appear in the context output."""
    context = budget_mod._abstractions_context({})
    summary = context["summary"]
    assert summary["delegation_heavy_class_count"] == 0
    assert summary["facade_module_count"] == 0
    assert "typed_dict_violation_count" not in summary


@needs_treesitter
def test_delegation_density_decreases_with_violations(tmp_path):
    """delegation_density sub-axis drops when delegation-heavy classes exist."""
    content = (
        "export class Proxy {\n"
        "  constructor(private dep: Dep) {}\n"
        "  a() { return this.dep.a(); }\n"
        "  b() { return this.dep.b(); }\n"
        "  c() { return this.dep.c(); }\n"
        "  d() { return this.dep.d(); }\n"
        "  e() { return this.dep.e(); }\n"
        "}\n"
    )
    f = tmp_path / "proxy.ts"
    f.write_text(content)
    context = budget_mod._abstractions_context({str(f): content})
    assert context["sub_axes"]["delegation_density"] < 100
    assert context["summary"]["delegation_heavy_class_count"] == 1


@needs_treesitter
def test_definition_directness_counts_facades(tmp_path):
    content = "export * from './a';\nexport * from './b';\n"
    context = budget_mod._abstractions_context({str(tmp_path / "index.ts"): content})
    assert context["summary"]["facade_module_count"] == 1
    assert context["sub_axes"]["definition_directness"] == 92
    assert context["facade_modules"][0]["imports_from"] == ["./a", "./b"]


@pytest.mark.parametrize("private", [False, True])
def test_definition_directness_skips_published_entry_barrels(tmp_path, private):
    """A barrel a published package's ``exports`` points at is its public API, not a facade."""
    manifest = {"name": "lib", "exports": {".": "./dist/index.js"}, "private": private}
    (tmp_path / "package.json").write_text(json.dumps(manifest))
    (tmp_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"outDir": "dist", "rootDir": "src"}}))
    (tmp_path / "src" / "inner").mkdir(parents=True)
    files = {
        str(tmp_path / "src" / "index.ts"): "export * from './a';\nexport * from './inner/index';\n",
        str(tmp_path / "src" / "inner" / "index.ts"): "export * from './b';\n",
    }
    for path, text in files.items():
        Path(path).write_text(text)

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        clear_resolver_cache()
        try:
            context = budget_mod._abstractions_context(files)
        finally:
            clear_resolver_cache()
    facades = [f["file"] for f in context.get("facade_modules", [])]
    if private:
        assert facades == ["src/index.ts", "src/inner/index.ts"]
    else:
        assert facades == ["src/inner/index.ts"]
        assert context["sub_axes"]["definition_directness"] == 92
