"""Grammar load failures must surface as reduced scan coverage."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

import desloppify.languages._framework.treesitter.analysis.extractors as extractors_mod
from desloppify.languages.framework import (
    record_grammar_load_failures,
    reset_grammar_load_failures,
)


@pytest.fixture(autouse=True)
def _clean_failures():
    reset_grammar_load_failures()
    yield
    reset_grammar_load_failures()


def _fake_language_pack(monkeypatch, *, fail: bool) -> None:
    module = ModuleType("tree_sitter_language_pack")

    def get_parser(grammar):
        if fail:
            raise ValueError(f"Download error: Failed to fetch manifest for {grammar}")
        return "parser"

    module.get_parser = get_parser
    module.get_language = lambda grammar: "language"
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", module)


def test_failed_grammar_is_recorded_and_reraised(monkeypatch):
    _fake_language_pack(monkeypatch, fail=True)
    with pytest.raises(ValueError):
        extractors_mod._get_parser("tsx")
    failures = extractors_mod.grammar_load_failures()
    assert list(failures) == ["tsx"]
    assert "Failed to fetch manifest" in failures["tsx"]


def test_successful_load_records_nothing(monkeypatch):
    _fake_language_pack(monkeypatch, fail=False)
    assert extractors_mod._get_parser("tsx") == ("parser", "language")
    assert extractors_mod.grammar_load_failures() == {}


def test_failures_become_reduced_coverage_warning(monkeypatch):
    _fake_language_pack(monkeypatch, fail=True)
    with pytest.raises(ValueError):
        extractors_mod._get_parser("tsx")
    lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])

    record_grammar_load_failures(lang)

    record = lang.detector_coverage["treesitter"]
    assert record["status"] == "reduced"
    assert "tsx" in record["summary"]
    assert "t.download(['tsx'])" in record["remediation"]
    assert [w["detector"] for w in lang.coverage_warnings] == ["treesitter"]


def test_no_failures_records_nothing():
    lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])
    record_grammar_load_failures(lang)
    assert lang.detector_coverage == {}
    assert lang.coverage_warnings == []
