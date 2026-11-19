import base64
import json

import httpx
import pytest

from tgbot.services.openrouter import (
    OpenRouterAuthError,
    OpenRouterClient,
    OpenRouterPaymentError,
    OpenRouterRateLimitError,
    OpenRouterRequestError,
    OpenRouterResponseError,
    OpenRouterServerError,
)


@pytest.mark.asyncio
async def test_openrouter_builds_multimodal_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"recognized": false}'}}]},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenRouterClient(
        api_key="secret-key",
        model="provider/model:free",
        base_url="https://example.test/api/v1",
        http_client=http_client,
    )
    try:
        result = await client.analyze_photo(b"jpeg-bytes", "system prompt")
    finally:
        await http_client.aclose()

    assert result == '{"recognized": false}'
    request = requests[0]
    payload = json.loads(request.content)
    assert request.url == "https://example.test/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret-key"
    assert payload["model"] == "provider/model:free"
    assert payload["messages"][0] == {"role": "system", "content": "system prompt"}
    image_url = payload["messages"][1]["content"][0]["image_url"]["url"]
    assert image_url == "data:image/jpeg;base64," + base64.b64encode(b"jpeg-bytes").decode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "exception"),
    [
        (401, OpenRouterAuthError),
        (402, OpenRouterPaymentError),
        (404, OpenRouterRequestError),
        (429, OpenRouterRateLimitError),
        (500, OpenRouterServerError),
    ],
)
async def test_openrouter_maps_api_errors(status_code: int, exception: type[Exception]) -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code))
    )
    client = OpenRouterClient("key", "model:free", http_client=http_client)
    try:
        with pytest.raises(exception):
            await client.analyze_photo(b"image", "prompt")
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_openrouter_rejects_empty_model_answer() -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": [{"message": {"content": " "}}]})
        )
    )
    client = OpenRouterClient("key", "model:free", http_client=http_client)
    try:
        with pytest.raises(OpenRouterResponseError):
            await client.analyze_photo(b"image", "prompt")
    finally:
        await http_client.aclose()
