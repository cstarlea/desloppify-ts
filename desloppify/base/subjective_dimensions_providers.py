"""Provider wiring for subjective-dimension metadata loading."""

from __future__ import annotations

from collections.abc import Callable

def default_load_dimensions_payload() -> tuple[
    list[str], dict[str, dict[str, object]], str
]:
    from desloppify.intelligence.review.dimensions.data import load_dimensions

    return load_dimensions()


def default_load_dimensions_payload_for_lang(
    lang_name: str,
) -> tuple[list[str], dict[str, dict[str, object]], str]:
    from desloppify.intelligence.review.dimensions.data import (
        load_dimensions_for_lang,
    )

    return load_dimensions_for_lang(lang_name)


class SubjectiveProviderState:
    def __init__(self) -> None:
        self.load_dimensions_payload_provider: Callable[
            [], tuple[list[str], dict[str, dict[str, object]], str]
        ] = default_load_dimensions_payload
        self.load_dimensions_payload_for_lang_provider: Callable[
            [str], tuple[list[str], dict[str, dict[str, object]], str]
        ] = default_load_dimensions_payload_for_lang


PROVIDER_STATE = SubjectiveProviderState()


def load_dimensions_payload() -> tuple[list[str], dict[str, dict[str, object]], str]:
    return PROVIDER_STATE.load_dimensions_payload_provider()


def load_dimensions_payload_for_lang(
    lang_name: str,
) -> tuple[list[str], dict[str, dict[str, object]], str]:
    return PROVIDER_STATE.load_dimensions_payload_for_lang_provider(lang_name)


__all__ = [
    "PROVIDER_STATE",
    "default_load_dimensions_payload",
    "default_load_dimensions_payload_for_lang",
    "load_dimensions_payload",
    "load_dimensions_payload_for_lang",
]
