"""Public framework facade for non-language-package consumers.

Use this module from app/engine layers instead of importing
``desloppify.languages._framework`` directly.
"""

from __future__ import annotations

from desloppify.languages._framework.registry import discovery as _discovery_mod
from desloppify.languages._framework.registry import state as registry_state
from desloppify.languages._framework.base.types import (
    BoundaryRule,
    DetectorCoverageRecord,
    DetectorCoverageStatus,
    DetectorPhase,
    FixerConfig,
    FixResult,
    LangConfig,
    LangRuntimeContract,
    LangSecurityResult,
    ScanCoverageRecord,
)
from desloppify.languages._framework.runtime_support.runtime import (
    LangRun,
    LangRunOverrides,
    make_lang_run,
)
from desloppify.languages._framework.registry.resolution import (
    available_langs,
    get_lang,
    make_lang_config,
)

load_all = _discovery_mod.load_all

DEFAULT_LANG = "typescript"


def default_lang() -> LangConfig:
    """The TypeScript language config (the only language plugin)."""
    return get_lang(DEFAULT_LANG)


def enable_parse_cache() -> None:
    """Enable tree-sitter parse cache via facade boundary."""
    from desloppify.languages._framework.treesitter import enable_parse_cache as _enable_parse_cache

    _enable_parse_cache()


def disable_parse_cache() -> None:
    """Disable tree-sitter parse cache via facade boundary."""
    from desloppify.languages._framework.treesitter import disable_parse_cache as _disable_parse_cache

    _disable_parse_cache()


def reset_grammar_load_failures() -> None:
    """Forget tree-sitter grammar load failures from earlier scans."""
    from desloppify.languages._framework.treesitter import (
        reset_grammar_load_failures as _reset,
    )

    _reset()


def record_grammar_load_failures(lang) -> None:
    """Report grammars that failed to load as reduced scan coverage.

    Tree-sitter phases skip a language whose grammar can't load (for example
    when the language pack can't download it offline). Without this the
    skipped detectors would read as clean.
    """
    from desloppify.languages._framework.base.shared_phases_helpers import (
        record_reduced_coverage,
    )
    from desloppify.languages._framework.treesitter import grammar_load_failures

    failures = grammar_load_failures()
    if not failures:
        return
    grammars = ", ".join(sorted(failures))
    first_error = next(iter(failures.values()))
    record_reduced_coverage(
        lang,
        DetectorCoverageStatus(
            detector="treesitter",
            status="reduced",
            confidence=0.5,
            summary=f"tree-sitter grammar(s) failed to load: {grammars} ({first_error[:160]})",
            impact="AST-based detectors (imports, complexity, cohesion, smells) were skipped for these languages.",
            remediation=(
                "Run `python -c \"import tree_sitter_language_pack as t; "
                f"t.download({sorted(failures)!r})\"` with network access, then rerun scan."
            ),
            tool="tree-sitter-language-pack",
            reason="grammar_unavailable",
        ),
    )


def prewarm_review_phase_detectors(path, lang, phases) -> None:
    """Prime expensive shared review detectors for overlap during scan."""
    from desloppify.languages._framework.base.shared_phases_review import (
        prewarm_review_phase_detectors as _prewarm_review_phase_detectors,
    )

    _prewarm_review_phase_detectors(path, lang, phases)


def clear_review_phase_prefetch(lang) -> None:
    """Clear in-memory shared review detector prefetch state."""
    from desloppify.languages._framework.base.shared_phases_review import (
        clear_review_phase_prefetch as _clear_review_phase_prefetch,
    )

    _clear_review_phase_prefetch(lang)


__all__ = [
    "BoundaryRule",
    "DEFAULT_LANG",
    "LangConfig",
    "LangRun",
    "LangRunOverrides",
    "DetectorCoverageRecord",
    "DetectorPhase",
    "FixerConfig",
    "FixResult",
    "LangRuntimeContract",
    "LangSecurityResult",
    "ScanCoverageRecord",
    "available_langs",
    "clear_review_phase_prefetch",
    "default_lang",
    "disable_parse_cache",
    "enable_parse_cache",
    "get_lang",
    "load_all",
    "make_lang_run",
    "make_lang_config",
    "prewarm_review_phase_detectors",
    "record_grammar_load_failures",
    "reset_grammar_load_failures",
    "registry_state",
]
