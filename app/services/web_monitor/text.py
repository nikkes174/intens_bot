from __future__ import annotations

from html import unescape

from bs4 import BeautifulSoup


def clean_article_text(value: str | None) -> str:
    """Return article text without markup, scripts, styles, or HTML entities."""

    if not value:
        return ""
    if not any(marker in value for marker in ("<", ">", "&")):
        return " ".join(value.split())

    soup = BeautifulSoup(unescape(value), "html.parser")

    for tag in soup(["script", "style", "iframe"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)
    return " ".join(unescape(text).split())
