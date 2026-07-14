from __future__ import annotations

import asyncio
import gzip
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from open_science_core.config import (
    Settings,
    canonical_model_api_endpoint,
    is_valid_model_api_base,
    settings,
)
from open_science_core.model_gateway import (
    ModelGatewayConfigurationError,
    ModelGatewayEmptyResponseError,
    ModelGatewayHTTPError,
    ModelGatewayInvalidResponseError,
    ModelGatewayResponseTooLargeError,
    ModelGatewayTimeoutError,
    ModelGatewayTransportError,
    OpenAICompatibleModelGateway,
)


API_KEY = "test-api-key-that-must-not-leak"


class SlowDripStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        for chunk in (b'{"choices":', b"[]}"):
            await asyncio.sleep(0.04)
            yield chunk

    async def aclose(self) -> None:
        return None


class StaticByteStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def __aiter__(self):
        yield self._content

    async def aclose(self) -> None:
        return None


def streaming_json_response(payload: object) -> httpx.Response:
    content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return httpx.Response(
        200,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(content)),
        },
        stream=StaticByteStream(content),
    )


def configured_settings() -> Settings:
    return replace(
        settings,
        llm_model="test-model",
        model_gateway_configured=True,
        openai_api_base="https://models.example.test/v1",
        openai_api_key=API_KEY,
    )


class ModelGatewayTest(unittest.TestCase):
    def test_settings_load_openai_compatible_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            secret_path = Path(temporary_directory) / "model-key"
            secret_path.write_text(API_KEY, encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_BASE": "https://gateway.example.test/compatible/v1/",
                    "OPENAI_API_KEY": "ignored-host-environment-value",
                    "SPARK_AGENT_OPENAI_API_KEY_FILE": str(secret_path),
                    "SPARK_AGENT_LLM_MODEL": "environment-model",
                },
                clear=True,
            ):
                loaded = Settings.from_environment()
        self.assertEqual(loaded.openai_api_base, "https://gateway.example.test/compatible/v1")
        self.assertEqual(loaded.openai_api_key, API_KEY)
        self.assertEqual(loaded.llm_model, "environment-model")
        self.assertTrue(loaded.model_gateway_configured)

        for missing_or_invalid in (
            {"OPENAI_API_KEY": API_KEY, "SPARK_AGENT_LLM_MODEL": "model"},
            {
                "SPARK_AGENT_OPENAI_API_KEY_FILE": "/missing/credential",
                "SPARK_AGENT_LLM_MODEL": "model",
                "OPENAI_API_BASE": "ftp://invalid",
            },
        ):
            with self.subTest(environment=missing_or_invalid):
                with patch.dict(os.environ, missing_or_invalid, clear=True):
                    self.assertFalse(Settings.from_environment().model_gateway_configured)

    def test_secret_file_read_is_bounded_and_environment_key_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            secret_path = Path(temporary_directory) / "oversized-key"
            secret_path.write_bytes(b"x" * 4097)
            with patch.dict(
                os.environ,
                {"SPARK_AGENT_OPENAI_API_KEY_FILE": str(secret_path)},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "safe size limit"):
                    Settings.from_environment()

            secret_path.write_bytes(b"safe-prefix\r\nInjected: value")
            with patch.dict(
                os.environ,
                {"SPARK_AGENT_OPENAI_API_KEY_FILE": str(secret_path)},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "invalid data") as caught:
                    Settings.from_environment()
                self.assertNotIn("Injected", str(caught.exception))

            secret_path.write_bytes(b"\xffprovider-secret")
            with patch.dict(
                os.environ,
                {"SPARK_AGENT_OPENAI_API_KEY_FILE": str(secret_path)},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "valid UTF-8") as caught:
                    Settings.from_environment()
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertNotIn("provider-secret", str(caught.exception))

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": API_KEY,
                "SPARK_AGENT_LLM_MODEL": "model",
            },
            clear=True,
        ):
            loaded = Settings.from_environment()
        self.assertIsNone(loaded.openai_api_key)
        self.assertFalse(loaded.model_gateway_configured)

    def test_model_and_endpoint_environment_values_are_bounded_and_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            secret_path = Path(temporary_directory) / "model-key"
            secret_path.write_text(API_KEY, encoding="utf-8")
            base_environment = {
                "SPARK_AGENT_OPENAI_API_KEY_FILE": str(secret_path),
                "OPENAI_API_BASE": "  https://models.example.test/v1/  ",
                "SPARK_AGENT_LLM_MODEL": "  provider-model  ",
                "SPARK_AGENT_EMBEDDING_MODEL": "  embedding-model  ",
            }
            with patch.dict(os.environ, base_environment, clear=True):
                loaded = Settings.from_environment()
            self.assertEqual(loaded.openai_api_base, "https://models.example.test/v1")
            self.assertEqual(loaded.llm_model, "provider-model")
            self.assertEqual(loaded.embedding_model, "embedding-model")
            self.assertTrue(loaded.model_gateway_configured)

            invalid_values = (
                {"SPARK_AGENT_LLM_MODEL": "x" * 201},
                {"SPARK_AGENT_LLM_MODEL": "model\nInjected: value"},
                {"OPENAI_API_BASE": "https://models.example.test/" + "x" * 2048},
                {"OPENAI_API_BASE": "https://models.example.test/v1\rInjected"},
            )
            for override in invalid_values:
                with self.subTest(override=list(override)):
                    environment = {**base_environment, **override}
                    with patch.dict(os.environ, environment, clear=True):
                        with self.assertRaises(ValueError) as caught:
                            Settings.from_environment()
                    self.assertNotIn("Injected", str(caught.exception))
                    self.assertNotIn("x" * 201, str(caught.exception))

    def test_model_api_base_requires_https_except_literal_loopback(self) -> None:
        for value in (
            "https://models.example.test/v1",
            "https://mödels.example/v1",
            "http://localhost:8080/v1",
            "http://127.0.0.1:8080/v1",
            "http://[::1]:8080/v1",
        ):
            with self.subTest(valid=value):
                self.assertTrue(is_valid_model_api_base(value))

        for value in (
            "http://models.example.test/v1",
            "http://192.168.1.20:8080/v1",
            "http://10.0.0.4/v1",
            "https://models.example.test:99999/v1",
            "https://name:secret@models.example.test/v1",
            "https://models.example.test/v1?key=secret",
            "https://models.example.test/v1#fragment",
            "https://bad_host.example/v1",
            "https://models.example.test/" + "x" * 2048,
        ):
            with self.subTest(invalid=value):
                self.assertFalse(is_valid_model_api_base(value))

    def test_endpoint_identity_binds_canonical_full_path_without_exposing_it(self) -> None:
        canonical = canonical_model_api_endpoint("HTTPS://MÖDELS.example:443/v1/")
        self.assertEqual(canonical, "https://xn--mdels-jua.example/v1/chat/completions")
        gateway = OpenAICompatibleModelGateway(
            replace(
                configured_settings(),
                openai_api_base="HTTPS://MÖDELS.example:443/v1/",
            )
        )
        equivalent = OpenAICompatibleModelGateway(
            replace(
                configured_settings(),
                openai_api_base="https://xn--mdels-jua.example/v1",
            )
        )
        different_path = OpenAICompatibleModelGateway(
            replace(
                configured_settings(),
                openai_api_base="https://xn--mdels-jua.example/other-v1",
            )
        )
        self.assertRegex(gateway.endpoint_identity, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(gateway.endpoint_identity, equivalent.endpoint_identity)
        self.assertNotEqual(gateway.endpoint_identity, different_path.endpoint_identity)
        self.assertNotIn("xn--", gateway.endpoint_identity)

    def test_exposes_only_safe_configuration_metadata(self) -> None:
        gateway = OpenAICompatibleModelGateway(configured_settings())
        self.assertTrue(gateway.configured)
        self.assertEqual(gateway.default_model, "test-model")
        self.assertEqual(gateway.endpoint_host, "models.example.test")

    def test_returns_strict_json_object_and_sends_required_contract(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url, "https://models.example.test/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], f"Bearer {API_KEY}")
            self.assertEqual(request.headers["accept-encoding"], "identity")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "override-model")
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            self.assertEqual(
                payload["messages"],
                [
                    {"role": "system", "content": "Return a plan."},
                    {"role": "user", "content": "Review local papers."},
                ],
            )
            return streaming_json_response(
                {"choices": [{"message": {"content": '{"plan":["read"]}'}}]}
            )

        gateway = OpenAICompatibleModelGateway(
            configured_settings(), transport=httpx.MockTransport(handler)
        )
        result = asyncio.run(
            gateway.complete_json("Return a plan.", "Review local papers.", "override-model")
        )
        self.assertEqual(result, {"plan": ["read"]})

    def test_timeout_is_safe_and_typed(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("raw timeout details", request=request)

        gateway = OpenAICompatibleModelGateway(
            configured_settings(), transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(ModelGatewayTimeoutError) as caught:
            asyncio.run(gateway.complete_json("system", "user"))
        self._assert_safe(caught.exception)
        self.assertTrue(caught.exception.retryable)

    def test_total_deadline_rejects_a_slow_drip_response(self) -> None:
        gateway = OpenAICompatibleModelGateway(
            configured_settings(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, stream=SlowDripStream())
            ),
            timeout_seconds=0.06,
        )
        with self.assertRaises(ModelGatewayTimeoutError) as caught:
            asyncio.run(gateway.complete_json("system", "user"))
        self._assert_safe(caught.exception)

    def test_invalid_url_from_client_is_a_safe_configuration_error(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.InvalidURL(f"raw invalid URL with {API_KEY}")

        gateway = OpenAICompatibleModelGateway(
            configured_settings(), transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(ModelGatewayConfigurationError) as caught:
            asyncio.run(gateway.complete_json("system", "user"))
        self._assert_safe(caught.exception)

    def test_transport_error_is_safe_and_typed(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("raw transport details", request=request)

        gateway = OpenAICompatibleModelGateway(
            configured_settings(), transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(ModelGatewayTransportError) as caught:
            asyncio.run(gateway.complete_json("system", "user"))
        self._assert_safe(caught.exception)

    def test_http_error_does_not_include_body_or_key(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text=f"provider body with {API_KEY}")

        gateway = OpenAICompatibleModelGateway(
            configured_settings(), transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(ModelGatewayHTTPError) as caught:
            asyncio.run(gateway.complete_json("system", "user"))
        self.assertEqual(caught.exception.status_code, 429)
        self.assertTrue(caught.exception.retryable)
        self._assert_safe(caught.exception)

    def test_rejects_invalid_json_and_non_object_json(self) -> None:
        for content in ("not json", "[]"):
            with self.subTest(content=content):
                transport = httpx.MockTransport(
                    lambda _request, value=content: streaming_json_response(
                        {"choices": [{"message": {"content": value}}]}
                    )
                )
                gateway = OpenAICompatibleModelGateway(
                    configured_settings(), transport=transport
                )
                with self.assertRaises(ModelGatewayInvalidResponseError) as caught:
                    asyncio.run(gateway.complete_json("system", "user"))
                self._assert_safe(caught.exception)

    def test_rejects_empty_response(self) -> None:
        for response in (
            {"choices": []},
            {"choices": [{"message": {"content": "  "}}]},
        ):
            with self.subTest(response=response):
                gateway = OpenAICompatibleModelGateway(
                    configured_settings(),
                    transport=httpx.MockTransport(
                        lambda _request, value=response: streaming_json_response(value)
                    ),
                )
                with self.assertRaises(ModelGatewayEmptyResponseError) as caught:
                    asyncio.run(gateway.complete_json("system", "user"))
                self._assert_safe(caught.exception)

    def test_rejects_oversized_response_without_parsing_it(self) -> None:
        gateway = OpenAICompatibleModelGateway(
            configured_settings(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"x" * 129)
            ),
            max_response_bytes=128,
        )
        with self.assertRaises(ModelGatewayResponseTooLargeError) as caught:
            asyncio.run(gateway.complete_json("system", "user"))
        self._assert_safe(caught.exception)

    def test_rejects_compressed_response_before_decompression(self) -> None:
        compressed_bomb = gzip.compress(b"x" * (2 * 1024 * 1024))
        gateway = OpenAICompatibleModelGateway(
            configured_settings(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"Content-Encoding": "gzip"},
                    content=compressed_bomb,
                )
            ),
        )
        with self.assertRaises(ModelGatewayInvalidResponseError) as caught:
            asyncio.run(gateway.complete_json("system", "user"))
        self._assert_safe(caught.exception)

    def test_hard_response_limit_cannot_be_raised_above_one_mebibyte(self) -> None:
        gateway = OpenAICompatibleModelGateway(
            configured_settings(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
            ),
            max_response_bytes=2 * 1024 * 1024,
        )
        with self.assertRaises(ModelGatewayResponseTooLargeError):
            asyncio.run(gateway.complete_json("system", "user"))

    def test_redirects_are_not_followed(self) -> None:
        calls = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                307,
                headers={"Location": "https://different.example.test/v1"},
            )

        gateway = OpenAICompatibleModelGateway(
            configured_settings(), transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(ModelGatewayHTTPError) as caught:
            asyncio.run(gateway.complete_json("system", "user"))
        self.assertEqual(caught.exception.status_code, 307)
        self.assertEqual(calls, 1)

    def test_missing_configuration_and_invalid_base_fail_closed(self) -> None:
        cases = (
            replace(configured_settings(), openai_api_key=None),
            replace(configured_settings(), llm_model=None),
            replace(configured_settings(), llm_model="model\nInjected: value"),
            replace(configured_settings(), llm_model="x" * 201),
            replace(configured_settings(), openai_api_base="https://name:secret@example.test/v1"),
        )
        for case in cases:
            with self.subTest(case=case):
                gateway = OpenAICompatibleModelGateway(case)
                with self.assertRaises(ModelGatewayConfigurationError) as caught:
                    asyncio.run(gateway.complete_json("system", "user"))
                self._assert_safe(caught.exception)

    def test_settings_repr_does_not_include_api_key(self) -> None:
        self.assertNotIn(API_KEY, repr(configured_settings()))

    def _assert_safe(self, error: Exception) -> None:
        rendered = str(error)
        self.assertNotIn(API_KEY, rendered)
        self.assertNotIn("provider body", rendered)
        self.assertNotIn("raw ", rendered)


if __name__ == "__main__":
    unittest.main()
