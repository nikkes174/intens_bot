from __future__ import annotations

"""Deterministic matching for a person's full name in publication text.

This intentionally does not match either a first name or a surname by itself.
The small inflection engine covers the common Russian personal-name patterns
without adding a heavyweight NLP dependency to the bot.
"""

import re
import unicodedata
from dataclasses import dataclass

_SPACE_RE = re.compile(r"\s+")
_WORD_CHARS = r"\w"


def normalize_text(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u00a0", " ")
    return _SPACE_RE.sub(" ", value).strip().casefold()


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    normalized = normalize_text(phrase)
    # Whitespace is normalized before matching, but allowing arbitrary
    # whitespace makes feeds with line breaks behave as expected.
    escaped = re.escape(normalized).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!{_WORD_CHARS}){escaped}(?!{_WORD_CHARS})", re.IGNORECASE)


def _replace_suffix(word: str, suffixes: tuple[str, ...], replacement: str) -> str | None:
    for suffix in suffixes:
        if word.endswith(suffix) and len(word) > len(suffix):
            return word[: -len(suffix)] + replacement
    return None


def _first_name_forms(word: str) -> list[str]:
    forms = {word}
    lower = word.casefold()

    # Common masculine Russian names (Иван, Пётр, Сергей, Дмитрий, ...).
    if lower.endswith(("й", "ь")):
        forms.update(
            {
                word[:-1] + "я",
                word[:-1] + "ю",
                word[:-1] + "ем",
                word[:-1] + "е",
            }
        )
    elif lower.endswith("а"):
        forms.update({word[:-1] + "ы", word[:-1] + "е", word[:-1] + "у", word[:-1] + "ой"})
        if lower.endswith(("га", "ка", "ха")):
            forms.add(word[:-1] + "и")
    elif lower.endswith("я"):
        forms.update({word[:-1] + "и", word[:-1] + "е", word[:-1] + "ю", word[:-1] + "ей"})
    elif lower.endswith(("н", "р", "л", "м", "в", "д", "т", "с", "з", "б", "п", "к", "г", "ф", "х", "ч", "ш")):
        forms.update({word + "а", word + "у", word + "ом", word + "е"})

    # A few frequent names do not follow the productive endings above.
    irregular = {
        "петр": ("петра", "петру", "петром", "петре"),
        "лев": ("льва", "льву", "львом", "льве"),
        "любовь": ("любови", "любови", "любовью", "любови"),
    }
    forms.update(irregular.get(lower, ()))
    return list(forms)


def _surname_forms(word: str) -> list[str]:
    forms = {word}
    lower = word.casefold()

    if lower.endswith(("ова", "ева", "ёва", "ина", "ына")):
        forms.update({word[:-1] + "ой", word[:-1] + "у", word[:-1] + "ою"})
    elif lower.endswith(("ов", "ев", "ёв", "ин", "ын")):
        forms.update({word + "а", word + "у", word + "ым", word + "е"})
    elif lower.endswith(("ский", "цкий", "ской", "цкой")):
        if lower.endswith(("ский", "цкий")):
            forms.update({word[:-2] + "ого", word[:-2] + "ому", word[:-2] + "им", word[:-2] + "ом"})
        else:
            forms.update({word[:-2] + "ого", word[:-2] + "ому", word[:-2] + "им", word[:-2] + "ом"})
    elif lower.endswith(("ый", "ий", "ой")):
        forms.update({word[:-2] + "ого", word[:-2] + "ому", word[:-2] + "ым", word[:-2] + "ом"})
    elif lower.endswith("ец"):
        forms.update({word[:-2] + "ца", word[:-2] + "цу", word[:-2] + "цем", word[:-2] + "це"})

    return list(forms)


def generate_name_variants(query: str) -> list[str]:
    """Return the full-name forms that can be matched for *query*.

    For an arbitrary multi-word query the exact phrase is retained.  The
    inflection expansion is applied only to the usual two-token full name.
    """

    normalized_query = normalize_text(query)
    words = normalized_query.split()
    if len(words) != 2:
        return [normalized_query] if normalized_query else []

    first, surname = words
    first_forms = _first_name_forms(first)
    surname_forms = _surname_forms(surname)
    variants = {f"{first} {surname}"}

    # Aligned case forms are preferred; the full cartesian product is also
    # useful for mixed editorial forms (e.g. first name left unchanged).
    for first_form, surname_form in zip(first_forms[1:], surname_forms[1:]):
        variants.add(f"{first_form} {surname_form}")
    variants.update(f"{first_form} {surname_form}" for first_form in first_forms for surname_form in surname_forms)
    return sorted(variants, key=lambda item: (len(item), item))


@dataclass(frozen=True)
class WebMatch:
    query: str
    matched_text: str


def find_web_match(text: str | None, query: str, clarification: str | None = None) -> WebMatch | None:
    """Find a full-name phrase and optional clarification in *text*."""

    normalized_text = normalize_text(text)
    if not normalized_text or len(normalize_text(query).split()) < 2:
        return None

    for variant in generate_name_variants(query):
        match = _phrase_pattern(variant).search(normalized_text)
        if match is None:
            continue
        if clarification and not any(
            _phrase_pattern(form).search(normalized_text)
            for form in _clarification_variants(clarification)
        ):
            continue
        return WebMatch(query=query, matched_text=match.group(0))
    return None


def _clarification_variants(value: str) -> list[str]:
    normalized = normalize_text(value)
    words = normalized.split()
    if len(words) == 1:
        return _first_name_forms(words[0])
    return [normalized]


def matches_web_watch(text: str | None, query: str, clarification: str | None = None) -> bool:
    return find_web_match(text, query, clarification) is not None


def validate_watch_query(query: str) -> str:
    normalized = normalize_text(query)
    if len(normalized.split()) < 2:
        raise ValueError("Укажите имя и фамилию, например: Иван Иванов")
    if len(normalized) > 255:
        raise ValueError("Запрос слишком длинный")
    return normalized


# Friendly aliases for callers/tests that use the generic matcher vocabulary.
match_web_article = find_web_match
is_match = matches_web_watch
