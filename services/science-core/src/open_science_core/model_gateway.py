from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from .config import (
    Settings,
    canonical_model_api_endpoint,
    is_valid_model_api_base,
    is_valid_model_identifier,
    normalize_model_identifier,
    settings,
)


class ModelGatewayError(RuntimeError):
    """A safe-to-report model gateway failure."""

    code = "model_gateway_error"
    retryable = False


class ModelGatewayConfigurationError(ModelGatewayError):
    code = "model_gateway_not_configured"

    def __init__(self) -> None:
        super().__init__("The model gateway is not configured")


class ModelGatewayTimeoutError(ModelGatewayError):
    code = "model_gateway_timeout"
    retryable = True

    def __init__(self) -> None:
        super().__init__("The model gateway request timed out")


class ModelGatewayTransportError(ModelGatewayError):
    code = "model_gateway_transport_error"
    retryable = True

    def __init__(self) -> None:
        super().__init__("The model gateway could not be reached")


class ModelGatewayHTTPError(ModelGatewayError):
    code = "model_gateway_http_error"

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.retryable = status_code == 429 or status_code >= 500
        super().__init__(f"The model gateway returned HTTP {status_code}")


class ModelGatewayEmptyResponseError(ModelGatewayError):
    code = "model_gateway_empty_response"
    retryable = True

    def __init__(self) -> None:
        super().__init__("The model gateway returned an empty response")


class ModelGatewayInvalidResponseError(ModelGatewayError):
    code = "model_gateway_invalid_response"
    retryable = True

    def __init__(self) -> None:
        super().__init__("The model gateway returned an invalid JSON object")


class ModelGatewayResponseTooLargeError(ModelGatewayError):
    code = "model_gateway_response_too_large"

    def __init__(self) -> None:
        super().__init__("The model gateway response exceeded the size limit")


class OpenAICompatibleModelGateway:
    """Stateless OpenAI-compatible JSON completion adapter."""

    _HARD_MAX_RESPONSE_BYTES = 1024 * 1024

    def __init__(
        self,
        gateway_settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 1024 * 1024,
    ) -> None:
        self._settings = gateway_settings
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = min(
            max(1, max_response_bytes), self._HARD_MAX_RESPONSE_BYTES
        )

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.openai_api_key
            and is_valid_model_identifier(self._settings.llm_model)
            and is_valid_model_api_base(self._settings.openai_api_base)
        )

    @property
    def default_model(self) -> str | None:
        return self._settings.llm_model

    @property
    def endpoint_host(self) -> str:
        try:
            return urlsplit(self._settings.openai_api_base).hostname or ""
        except ValueError:
            return ""

    @property
    def endpoint_identity(self) -> str:
        endpoint = canonical_model_api_endpoint(self._settings.openai_api_base)
        if endpoint is None:
            return ""
        return f"sha256:{hashlib.sha256(endpoint.encode('utf-8')).hexdigest()}"

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        result, _token_usage = await self.complete_json_with_metadata(
            system_prompt,
            user_prompt,
            model,
        )
        return result

    async def complete_json_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        try:
            selected_model = normalize_model_identifier(model or self._settings.llm_model)
        except ValueError:
            raise ModelGatewayConfigurationError() from None
        api_key = self._settings.openai_api_key
        api_base = self._settings.openai_api_base
        if (
            not system_prompt.strip()
            or not user_prompt.strip()
            or not selected_model
            or not api_key
            or not is_valid_model_api_base(api_base)
        ):
            raise ModelGatewayConfigurationError()

        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._complete_json_within_deadline(
                    api_base=api_base,
                    api_key=api_key,
                    selected_model=selected_model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
        except (TimeoutError, httpx.TimeoutException):
            raise ModelGatewayTimeoutError() from None
        except httpx.InvalidURL:
            raise ModelGatewayConfigurationError() from None
        except httpx.HTTPError:
            raise ModelGatewayTransportError() from None
        return result

    async def _complete_json_within_deadline(
        self,
        *,
        api_base: str,
        api_key: str,
        selected_model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        response_body = bytearray()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds),
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            async with client.stream(
                "POST",
                f"{api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept-Encoding": "identity",
                },
                json={
                    "model": selected_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ModelGatewayHTTPError(response.status_code)
                content_encoding = response.headers.get("content-encoding", "identity")
                if content_encoding.strip().lower() not in {"", "identity"}:
                    raise ModelGatewayInvalidResponseError()
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length, 10)
                    except ValueError:
                        raise ModelGatewayInvalidResponseError() from None
                    if declared_length < 0:
                        raise ModelGatewayInvalidResponseError()
                    if declared_length > self._max_response_bytes:
                        raise ModelGatewayResponseTooLargeError()
                chunk_size = min(64 * 1024, self._max_response_bytes + 1)
                async for chunk in response.aiter_raw(chunk_size=chunk_size):
                    if len(response_body) + len(chunk) > self._max_response_bytes:
                        raise ModelGatewayResponseTooLargeError()
                    response_body.extend(chunk)

        content, token_usage = _response_content_and_token_usage(bytes(response_body))
        if not content.strip():
            raise ModelGatewayEmptyResponseError()
        try:
            result_object: object = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            raise ModelGatewayInvalidResponseError() from None
        if not isinstance(result_object, dict):
            raise ModelGatewayInvalidResponseError()
        return cast(dict[str, Any], result_object), token_usage


def _response_content_and_token_usage(
    response_body: bytes,
) -> tuple[str, dict[str, int]]:
    try:
        payload_object: object = json.loads(response_body)
        if not isinstance(payload_object, dict):
            raise ModelGatewayInvalidResponseError()
        payload = cast(dict[str, Any], payload_object)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelGatewayEmptyResponseError()
        first_choice = cast(list[object], choices)[0]
        if not isinstance(first_choice, dict):
            raise ModelGatewayInvalidResponseError()
        choice = cast(dict[str, Any], first_choice)
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelGatewayInvalidResponseError()
        content = cast(dict[str, Any], message).get("content")
        if content is None:
            raise ModelGatewayEmptyResponseError()
        if not isinstance(content, str):
            raise ModelGatewayInvalidResponseError()
        usage_object = payload.get("usage")
        token_usage: dict[str, int] = {}
        if isinstance(usage_object, dict):
            usage = cast(dict[str, object], usage_object)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    token_usage[key] = value
        return content, token_usage
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ModelGatewayInvalidResponseError() from None


model_gateway = OpenAICompatibleModelGateway(settings)


async def complete_json(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> dict[str, Any]:
    return await model_gateway.complete_json(system_prompt, user_prompt, model)
