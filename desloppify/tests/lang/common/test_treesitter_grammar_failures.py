"""Grammar load failures must surface as reduced scan coverage."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

import desloppify.languages._framework.treesitter.parsing as parsing_mod
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
        parsing_mod._get_parser("tsx")
    failures = parsing_mod.grammar_load_failures()
    assert list(failures) == ["tsx"]
    assert "Failed to fetch manifest" in failures["tsx"]


def test_successful_load_records_nothing(monkeypatch):
    _fake_language_pack(monkeypatch, fail=False)
    assert parsing_mod._get_parser("tsx") == ("parser", "language")
    assert parsing_mod.grammar_load_failures() == {}


def test_failures_become_reduced_coverage_warning(monkeypatch):
    _fake_language_pack(monkeypatch, fail=True)
    with pytest.raises(ValueError):
        parsing_mod._get_parser("tsx")
    lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])

    record_grammar_load_failures(lang)

    record = lang.detector_coverage["treesitter"]
    assert record["status"] == "reduced"
    assert "tsx" in record["summary"]
    assert "desloppify setup --grammars" in record["remediation"]
    assert [w["detector"] for w in lang.coverage_warnings] == ["treesitter"]


def test_no_failures_records_nothing():
    lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])
    record_grammar_load_failures(lang)
    assert lang.detector_coverage == {}
    assert lang.coverage_warnings == []


def _fake_pack_with_cache(monkeypatch, *, cached, online):
    module = ModuleType("tree_sitter_language_pack")
    downloads: list[list[str]] = []

    def download(names):
        if not online:
            raise RuntimeError("Network is unreachable")
        downloads.append(list(names))
        cached.update(names)
        return len(names)

    def get_parser(grammar):
        if grammar not in cached:
            raise RuntimeError(f"Download error: {grammar} not cached")
        return "parser"

    module.downloaded_languages = lambda: sorted(cached)
    module.download = download
    module.get_parser = get_parser
    module.get_language = lambda grammar: "language"
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", module)
    return downloads


def test_prepare_grammars_downloads_only_missing(monkeypatch):
    downloads = _fake_pack_with_cache(monkeypatch, cached={"tsx"}, online=True)
    errors, missing = parsing_mod.prepare_grammars()
    assert errors == {"tsx": None, "typescript": None}
    assert missing == ["typescript"]
    assert downloads == [["typescript"]]


def test_prepare_grammars_offline_reports_the_download_error(monkeypatch):
    _fake_pack_with_cache(monkeypatch, cached=set(), online=False)
    errors, missing = parsing_mod.prepare_grammars()
    assert missing == ["tsx", "typescript"]
    assert errors["tsx"] == "RuntimeError: Network is unreachable"
    assert sorted(parsing_mod.grammar_load_failures()) == ["tsx", "typescript"]


@pytest.fixture
def fresh_availability(monkeypatch):
    import desloppify.languages._framework.treesitter as ts_mod

    monkeypatch.setattr(ts_mod, "_AVAILABLE", True)
    monkeypatch.setattr(ts_mod, "_TSX_ERROR", None)
    return ts_mod


def test_is_available_loads_the_tsx_grammar(monkeypatch, fresh_availability):
    _fake_language_pack(monkeypatch, fail=False)
    assert fresh_availability.is_available() is True
    assert parsing_mod.grammar_load_failures() == {}


def test_is_available_false_when_the_grammar_cannot_load(monkeypatch, fresh_availability):
    """An installed pack without its grammar (offline) is not available, and says so."""
    _fake_language_pack(monkeypatch, fail=True)
    assert fresh_availability.is_available() is False
    assert "tsx" in parsing_mod.grammar_load_failures()

    # The next scan resets failures; asking again records the failure again
    # without retrying the download.
    reset_grammar_load_failures()
    _fake_language_pack(monkeypatch, fail=False)
    assert fresh_availability.is_available() is False
    assert "Failed to fetch manifest" in parsing_mod.grammar_load_failures()["tsx"]
