from __future__ import annotations

import re
import unicodedata


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold()


def find_mentions(text: str, keywords: list[str]) -> list[str]:
    normalized_text = normalize(text or "")
    matches: list[str] = []
    seen: set[str] = set()

    for keyword in keywords or []:
        keyword = keyword.strip()
        if not keyword:
            continue

        normalized_keyword = normalize(keyword)
        if not normalized_keyword or normalized_keyword in seen:
            continue
        seen.add(normalized_keyword)

        if normalized_keyword.startswith("@"):
            pattern = r"(?<![\w@])" + re.escape(normalized_keyword) + r"(?!\w)"
        elif "." in normalized_keyword:
            pattern = r"(?<![\w.])" + re.escape(normalized_keyword) + r"(?![\w.])"
        else:
            pattern = r"(?<!\w)" + re.escape(normalized_keyword) + r"(?!\w)"

        if re.search(pattern, normalized_text):
            matches.append(keyword)

    return matches
