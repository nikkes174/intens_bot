from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.web_monitor import article_extractor
from app.services.web_monitor.article_extractor import extract_article


@pytest.mark.asyncio
async def test_extracts_russian_main_text_and_ignores_navigation(monkeypatch):
    html = """
    <html><body>
      <nav>Меню Реклама</nav>
      <article><h1>Заголовок</h1><p>Иван Иванов сообщил о новом решении.</p></article>
      <footer>Комментарии и подвал</footer>
    </body></html>
    """

    async def handler(request):
        return httpx.Response(
            200, headers={"Content-Type": "text/html; charset=utf-8"}, content=html, request=request
        )

    monkeypatch.setattr(article_extractor, "_validate_public_url", AsyncMock(return_value=True))
    monkeypatch.setattr(article_extractor, "_fetch_robots", AsyncMock(return_value=True))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        text = await extract_article(client, "https://example.com/story")
    assert text is not None
    assert "Иван Иванов сообщил" in text
    assert "Меню Реклама" not in text
    assert "Комментарии и подвал" not in text


@pytest.mark.asyncio
async def test_extract_article_returns_none_for_auth_and_oversized_response(monkeypatch):
    async def auth_handler(request):
        return httpx.Response(401, request=request)

    monkeypatch.setattr(article_extractor, "_validate_public_url", AsyncMock(return_value=True))
    monkeypatch.setattr(article_extractor, "_fetch_robots", AsyncMock(return_value=True))
    async with httpx.AsyncClient(transport=httpx.MockTransport(auth_handler)) as client:
        assert await extract_article(client, "https://example.com/private") is None

    async def oversized_handler(request):
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/html",
                "Content-Length": str(article_extractor.MAX_RESPONSE_BYTES + 1),
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized_handler)) as client:
        assert await extract_article(client, "https://example.com/large") is None


@pytest.mark.asyncio
async def test_extract_article_respects_robots_txt(monkeypatch):
    article_extractor._robots_cache.clear()

    async def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                content=b"User-agent: *\nDisallow: /private\n",
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=b"<article>secret</article>",
            request=request,
        )

    monkeypatch.setattr(article_extractor, "_validate_public_url", AsyncMock(return_value=True))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await extract_article(client, "https://example.com/private") is None


@pytest.mark.asyncio
async def test_private_url_and_unsafe_redirect_are_rejected(monkeypatch):
    assert await article_extractor._validate_public_url("http://127.0.0.1/story") is False
    assert await article_extractor._validate_public_url("http://localhost/story") is False

    async def handler(request):
        return httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/internal"},
            request=request,
        )

    monkeypatch.setattr(article_extractor, "_fetch_robots", AsyncMock(return_value=True))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await extract_article(client, "https://example.com/redirect") is None
