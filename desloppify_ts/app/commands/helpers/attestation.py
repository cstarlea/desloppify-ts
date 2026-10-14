"""Shared attestation and note validation helpers."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from desloppify_ts.base.output.terminal import colorize

_REQUIRED_ATTESTATION_PHRASES = ("i have actually", "not gaming")
_ATTESTATION_KEYWORD_HINT = ("I have actually", "not gaming")
_MIN_NOTE_LENGTH = 50


def _emit_warning(message: str) -> None:
    """Write resolve preflight warnings to stderr consistently."""
    print(colorize(message, "yellow"), file=sys.stderr)


def _missing_attestation_keywords(
    attestation: str | None,
    *,
    required_phrases: Sequence[str] | None = None,
    any_of_phrases: Sequence[Sequence[str]] | None = None,
) -> list[str]:
    normalized = " ".join((attestation or "").strip().lower().split())
    phrases = tuple(required_phrases or _REQUIRED_ATTESTATION_PHRASES)
    missing = [phrase for phrase in phrases if phrase not in normalized]
    for phrase_group in any_of_phrases or ():
        normalized_group = tuple(
            phrase.strip().lower() for phrase in phrase_group if phrase
        )
        if normalized_group and not any(
            phrase in normalized for phrase in normalized_group
        ):
            missing.append(" or ".join(normalized_group))
    return missing


def validate_attestation(
    attestation: str | None,
    *,
    required_phrases: Sequence[str] | None = None,
    any_of_phrases: Sequence[Sequence[str]] | None = None,
) -> bool:
    return not _missing_attestation_keywords(
        attestation,
        required_phrases=required_phrases,
        any_of_phrases=any_of_phrases,
    )


def _attestation_problem(
    label: str, attestation: str | None, missing: list[str]
) -> str:
    if not attestation:
        return f"{label} requires --attest."
    missing_str = ", ".join(f"'{keyword}'" for keyword in missing)
    return f"{label} attestation is missing required keyword(s): {missing_str}."


def attestation_refusal(label: str, attestation: str | None) -> str:
    """One-line reason ``attestation`` fails the default keyword check."""
    return _attestation_problem(
        label, attestation, _missing_attestation_keywords(attestation)
    )


def show_attestation_requirement(
    label: str,
    attestation: str | None,
    example: str,
    *,
    required_phrases: Sequence[str] | None = None,
    any_of_phrases: Sequence[Sequence[str]] | None = None,
) -> None:
    phrases = tuple(required_phrases or _REQUIRED_ATTESTATION_PHRASES)
    missing = _missing_attestation_keywords(
        attestation,
        required_phrases=phrases,
        any_of_phrases=any_of_phrases,
    )
    if not attestation or missing:
        _emit_warning(_attestation_problem(label, attestation, missing))
    display_phrases = list(
        _ATTESTATION_KEYWORD_HINT
        if required_phrases is None
        else tuple(required_phrases)
    )
    display_phrases.extend(
        " or ".join(f"'{phrase}'" for phrase in group)
        for group in any_of_phrases or ()
        if group
    )
    if len(display_phrases) == 2:
        phrase_text = f"{_quote_phrase(display_phrases[0])} and {_quote_phrase(display_phrases[1])}"
    else:
        phrase_text = ", ".join(_quote_phrase(phrase) for phrase in display_phrases)
    _emit_warning(f"Required keywords: {phrase_text}.")
    print(colorize(f'Example: --attest "{example}"', "dim"), file=sys.stderr)


def _quote_phrase(phrase: str) -> str:
    """Quote plain phrases while leaving already-formatted alternatives alone."""
    return phrase if "'" in phrase else f"'{phrase}'"


def validate_note_length(note: str | None) -> bool:
    """Return True if the note meets the minimum length requirement."""
    return note is not None and len(note.strip()) >= _MIN_NOTE_LENGTH


def note_length_refusal(note: str | None) -> str:
    """One-line reason ``note`` fails the minimum length check."""
    current = len((note or "").strip())
    return (
        f"Note must be at least {_MIN_NOTE_LENGTH} characters (got {current}). "
        "Describe what you actually did."
    )


def show_note_length_requirement(note: str | None) -> None:
    """Emit a warning about minimum note length."""
    _emit_warning(note_length_refusal(note))


__all__ = [
    "_MIN_NOTE_LENGTH",
    "attestation_refusal",
    "note_length_refusal",
    "show_attestation_requirement",
    "show_note_length_requirement",
    "validate_attestation",
    "validate_note_length",
]
