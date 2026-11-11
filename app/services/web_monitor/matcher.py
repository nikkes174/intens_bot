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


_CASES = ("nom", "gen", "dat", "acc", "ins", "prep")


def _first_name_case_forms(word: str) -> dict[str, str]:
    """Return aligned case forms for a common Russian given name."""

    word = normalize_text(word)
    irregular = {
        "пётр": ("пётр", "петра", "петру", "петра", "петром", "петре"),
        "петр": ("петр", "петра", "петру", "петра", "петром", "петре"),
        "лев": ("лев", "льва", "льву", "льва", "львом", "льве"),
        "павел": ("павел", "павла", "павлу", "павла", "павлом", "павле"),
        "любовь": ("любовь", "любви", "любви", "любовь", "любовью", "любви"),
    }
    if word in irregular:
        return dict(zip(_CASES, irregular[word], strict=True))

    if word.endswith(("й", "ь")):
        stem = word[:-1]
        prepositional_ending = "и" if word.endswith("ий") else "е"
        values = (
            word,
            stem + "я",
            stem + "ю",
            stem + "я",
            stem + "ем",
            stem + prepositional_ending,
        )
    elif word.endswith("я"):
        stem = word[:-1]
        soft_ya_ending = word.endswith("ия")
        dative_ending = "и" if soft_ya_ending else "е"
        values = (
            word,
            stem + "и",
            stem + dative_ending,
            stem + "ю",
            stem + "ей",
            stem + dative_ending,
        )
    elif word.endswith("а"):
        stem = word[:-1]
        genitive_ending = "и" if word.endswith(("га", "ка", "ха")) else "ы"
        values = (
            word,
            stem + genitive_ending,
            stem + "е",
            stem + "у",
            stem + "ой",
            stem + "е",
        )
    else:
        values = (word, word + "а", word + "у", word + "а", word + "ом", word + "е")
    return dict(zip(_CASES, values, strict=True))


def _surname_case_forms(word: str) -> dict[str, str]:
    """Return aligned masculine/feminine surname forms."""

    word = normalize_text(word)
    if word.endswith(("ова", "ева", "ёва", "ина", "ына")):
        stem = word[:-1]
        values = (word, stem + "ой", stem + "ой", stem + "у", stem + "ой", stem + "ой")
    elif word.endswith(("ов", "ев", "ёв", "ин", "ын")):
        values = (word, word + "а", word + "у", word + "а", word + "ым", word + "е")
    elif word.endswith(("ская", "цкая")):
        stem = word[:-4]
        values = (word, stem + "ской", stem + "ской", stem + "скую", stem + "ской", stem + "ской")
    elif word.endswith(("ский", "цкий")):
        stem = word[:-2]
        values = (word, stem + "ого", stem + "ому", stem + "ого", stem + "им", stem + "ом")
    elif word.endswith("ая"):
        stem = word[:-2]
        values = (word, stem + "ой", stem + "ой", stem + "ую", stem + "ой", stem + "ой")
    elif word.endswith(("ый", "ий", "ой")):
        stem = word[:-2]
        instrumental = "им" if word.endswith("ий") else "ым"
        prepositional = "ем" if word.endswith("ий") else "ом"
        values = (
            word,
            stem + "ого",
            stem + "ому",
            stem + "ого",
            stem + instrumental,
            stem + prepositional,
        )
    elif word.endswith("ец"):
        stem = word[:-2]
        values = (word, stem + "ца", stem + "цу", stem + "ца", stem + "цем", stem + "це")
    else:
        return {"nom": word}
    return dict(zip(_CASES, values, strict=True))


def _first_name_forms(word: str) -> list[str]:
    return list(_first_name_case_forms(word).values())


def _surname_forms(word: str) -> list[str]:
    return list(_surname_case_forms(word).values())


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
    first_forms = _first_name_case_forms(first)
    surname_forms = _surname_case_forms(surname)
    variants = {
        f"{first_forms[case]} {surname_forms[case]}"
        for case in _CASES
        if case in first_forms and case in surname_forms
    }
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
