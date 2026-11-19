from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx


class OpenRouterError(RuntimeError):
    """Базовая ошибка запроса к OpenRouter."""


class OpenRouterAuthError(OpenRouterError):
    """Ключ OpenRouter не принят."""


class OpenRouterPaymentError(OpenRouterError):
    """Для запроса недостаточно средств или кредитов."""


class OpenRouterRateLimitError(OpenRouterError):
    """OpenRouter ограничил частоту запросов."""


class OpenRouterTimeoutError(OpenRouterError):
    """OpenRouter не ответил вовремя."""


class OpenRouterUnavailableError(OpenRouterError):
    """OpenRouter недоступен по сети."""


class OpenRouterRequestError(OpenRouterError):
    """Некорректный запрос или недоступная модель."""


class OpenRouterServerError(OpenRouterError):
    """OpenRouter временно не справился с запросом."""


class OpenRouterResponseError(OpenRouterError):
    """OpenRouter вернул неожиданный или пустой ответ."""


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 45.0,
        max_concurrent_requests: int = 2,
        http_client: httpx.AsyncClient | None = None,
        allow_paid: bool = False,
    ) -> None:
        model = model.strip()
        if not api_key.strip():
            raise ValueError("OPENROUTER_API_KEY не может быть пустым")
        if not model:
            raise ValueError("OPENROUTER_MODEL не может быть пустым")
        if not allow_paid and not model.endswith(":free"):
            raise ValueError(
                "OPENROUTER_MODEL должен заканчиваться на :free, либо передайте allow_paid=True"
            )

        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._client = http_client
        self._owns_client = http_client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))
        return self._client

    async def analyze_photo(self, image_bytes: bytes, prompt: str) -> str:
        encoded_image = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 2000,
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"},
                        }
                    ],
                },
            ],
        }

        async with self._semaphore:
            try:
                client = await self._get_client()
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.TimeoutException as error:
                raise OpenRouterTimeoutError from error
            except httpx.RequestError as error:
                raise OpenRouterUnavailableError from error

        if response.status_code in {401, 403}:
            raise OpenRouterAuthError
        if response.status_code == 402:
            raise OpenRouterPaymentError
        if response.status_code == 429:
            raise OpenRouterRateLimitError
        if response.status_code in {400, 404}:
            raise OpenRouterRequestError
        if response.status_code in {500, 502, 503, 504}:
            raise OpenRouterServerError
        if response.is_error:
            raise OpenRouterResponseError

        try:
            response_data: dict[str, Any] = response.json()
            content = response_data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise OpenRouterResponseError from error

        if isinstance(content, list):
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str) or not content.strip():
            raise OpenRouterResponseError
        return content.strip()

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
