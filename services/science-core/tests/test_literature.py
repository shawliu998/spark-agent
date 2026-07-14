from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from open_science_core.app import app as science_app
from open_science_core.app import ask_question
from open_science_core.config import settings
from open_science_core.db import Base
from open_science_core.literature import (
    LiteratureResult,
    PaperQaAdapter,
    _create_paperqa_settings,
    _paperqa_settings_kwargs,
    paper_qa_available,
)
from open_science_core.models import (
    AnswerRecord,
    ClaimRecord,
    EventRecord,
    ProjectRecord,
    SourcePageRecord,
    SourceRecord,
)
from open_science_core.schemas import QuestionIn


API_KEY = "paperqa-test-key-that-must-be-restored"


class PaperQaCredentialBoundaryTest(unittest.TestCase):
    @unittest.skipUnless(paper_qa_available(), "PaperQA2 is not installed")
    def test_real_paperqa_configs_are_provider_qualified_and_secret_safe(self) -> None:
        from paperqa import Settings as RealPaperQaSettings

        api_base = "https://models.example.test/v1"
        configured = _create_paperqa_settings(
            RealPaperQaSettings,
            _paperqa_settings_kwargs(
                "deepseek-chat",
                None,
                API_KEY,
                api_base,
            ),
        )
        serialized = (
            repr(configured)
            + repr(configured.model_dump())
            + repr(configured.model_dump(mode="json"))
            + configured.model_dump_json()
            + str(configured)
        )
        self.assertTrue(
            API_KEY not in serialized,
            "PaperQA settings exposed the model credential",
        )
        self.assertFalse(
            _contains_value(configured.model_dump(), API_KEY),
            "PaperQA's Python settings dump retained the model credential",
        )

        for get_model in (
            configured.get_llm,
            configured.get_summary_llm,
            configured.get_agent_llm,
            configured.get_enrichment_llm,
        ):
            llm = get_model()
            model_spec = llm.llm_config.models[0]
            self.assertEqual(llm.name, "openai/deepseek-chat")
            self.assertEqual(model_spec.name, "openai/deepseek-chat")
            self.assertEqual(model_spec.api_base, api_base)
            self.assertTrue(
                model_spec.api_key.get_secret_value() == API_KEY,
                "PaperQA did not receive the model credential",
            )

        embedding = configured.get_embedding_model()
        self.assertEqual(embedding.name, "openai/text-embedding-3-small")
        self.assertEqual(embedding.config["kwargs"]["api_base"], api_base)
        self.assertTrue(
            embedding.config["kwargs"]["api_key"] == API_KEY,
            "PaperQA did not receive the embedding credential",
        )

    @unittest.skipUnless(paper_qa_available(), "PaperQA2 is not installed")
    def test_real_paperqa_preserves_custom_raw_model_after_provider_prefix(self) -> None:
        from paperqa import Settings as RealPaperQaSettings

        configured = _create_paperqa_settings(
            RealPaperQaSettings,
            _paperqa_settings_kwargs(
                "vendor/custom-model",
                "vendor/custom-embedding",
                API_KEY,
                "https://models.example.test/v1",
            ),
        )
        self.assertEqual(configured.get_llm().name, "openai/vendor/custom-model")
        self.assertEqual(
            configured.get_embedding_model().name,
            "openai/vendor/custom-embedding",
        )

    def test_remote_data_approval_requires_a_json_boolean(self) -> None:
        self.assertTrue(
            QuestionIn.model_validate(
                {"question": "Question?", "remoteDataApproved": True}
            ).remote_data_approved
        )
        self.assertFalse(
            QuestionIn.model_validate(
                {"question": "Question?", "remoteDataApproved": False}
            ).remote_data_approved
        )
        for coerced_value in ("true", "yes", 1, 0):
            with self.subTest(coerced_value=coerced_value):
                with self.assertRaises(ValidationError):
                    QuestionIn.model_validate(
                        {
                            "question": "Question?",
                            "remoteDataApproved": coerced_value,
                        }
                    )

    def test_model_override_rejects_blank_oversized_and_control_characters(self) -> None:
        self.assertEqual(
            QuestionIn.model_validate(
                {
                    "question": "Question?",
                    "model": "  bounded-model  ",
                }
            ).model,
            "bounded-model",
        )
        for invalid_model in ("", "   ", "x" * 201, "model\nheader"):
            with self.subTest(invalid_model=repr(invalid_model)):
                with self.assertRaises(ValidationError):
                    QuestionIn.model_validate(
                        {
                            "question": "Question?",
                            "model": invalid_model,
                        }
                    )

    def test_invalid_model_override_returns_422_without_calling_paperqa(self) -> None:
        configured = replace(
            settings,
            bearer_token="test-token",
            model_gateway_configured=True,
            openai_api_key=API_KEY,
            llm_model="default-model",
        )
        paperqa_call = AsyncMock()
        with (
            patch("open_science_core.app.settings", configured),
            patch("open_science_core.app.paper_qa.ask", new=paperqa_call),
        ):
            client = TestClient(science_app)
            try:
                response = client.post(
                    "/v1/projects/project-1/questions",
                    headers={"Authorization": "Bearer test-token"},
                    json={
                        "question": "Question?",
                        "model": "model\nheader",
                        "remoteDataApproved": True,
                    },
                )
            finally:
                client.close()
        self.assertEqual(response.status_code, 422, response.text)
        paperqa_call.assert_not_awaited()

    def test_paperqa_answer_records_remote_provenance_without_claiming_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "paper.pdf"
            content = b"%PDF-paperqa-provenance"
            source_path.write_bytes(content)
            engine = create_engine(f"sqlite:///{root / 'paperqa.sqlite3'}")
            Base.metadata.create_all(engine)
            configured = replace(
                settings,
                model_gateway_configured=True,
                openai_api_base="https://models.internal.example/v1",
                openai_api_key=API_KEY,
                llm_model="default-model",
            )
            with Session(engine) as session:
                session.add(
                    ProjectRecord(
                        id="project-1",
                        title="PaperQA provenance",
                        description="",
                        project_path=str(root),
                        execution_mode="safe",
                    )
                )
                session.add(
                    SourceRecord(
                        id="source-1",
                        project_id="project-1",
                        title="Paper",
                        source_kind="pdf",
                        authors=[],
                        local_path=str(source_path),
                        ingestion_status="ready",
                        content_hash=hashlib.sha256(content).hexdigest(),
                        page_count=1,
                    )
                )
                session.add(
                    SourcePageRecord(
                        source_id="source-1",
                        page_index=0,
                        page_label="1",
                        width=500.0,
                        height=700.0,
                        text="A locally parsed page without a returned quote.",
                        words=[],
                    )
                )
                session.commit()
                with (
                    patch("open_science_core.app.settings", configured),
                    patch(
                        "open_science_core.app.paper_qa.ask",
                        new=AsyncMock(
                            return_value=LiteratureResult(
                                answer="A generated PaperQA answer.",
                                evidence_candidates=[],
                            )
                        ),
                    ),
                ):
                    output = asyncio.run(
                        ask_question(
                            "project-1",
                            QuestionIn(
                                question="What does the paper say?",
                                model="request-model",
                                remote_data_approved=True,
                            ),
                            session,
                        )
                    )
                answer = session.scalar(select(AnswerRecord))
                claim = session.scalar(select(ClaimRecord))
                approval_event = session.scalar(
                    select(EventRecord).where(
                        EventRecord.event_type == "literature.remote-data.approved"
                    )
                )
                self.assertEqual(answer.generator, "paperqa2-remote-v1")
                self.assertEqual(answer.model, "request-model")
                self.assertIsNone(answer.prompt_version)
                self.assertEqual(
                    answer.metadata_json["generationMode"],
                    "remote-model-assisted",
                )
                self.assertEqual(
                    answer.metadata_json["endpointHost"],
                    "models.internal.example",
                )
                self.assertTrue(
                    answer.metadata_json["endpointIdentity"].startswith("sha256:")
                )
                self.assertEqual(claim.review_status, "unreviewed")
                self.assertEqual(claim.confidence, 0.0)
                self.assertEqual(output.generator, answer.generator)
                self.assertEqual(output.model, answer.model)
                self.assertEqual(output.metadata, answer.metadata_json)
                self.assertNotIn(
                    "What does the paper say?",
                    str(approval_event.payload),
                )
            engine.dispose()

    def test_unconfigured_gateway_points_to_keychain_not_environment(self) -> None:
        session = MagicMock()
        session.scalars.return_value = [object()]
        unconfigured = replace(
            settings,
            model_gateway_configured=False,
            openai_api_key=None,
        )
        with (
            patch("open_science_core.app._project_or_404", return_value=object()),
            patch("open_science_core.app.settings", unconfigured),
        ):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(
                    ask_question(
                        "project-1",
                        QuestionIn(question="Question?", remote_data_approved=True),
                        session,
                    )
                )
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("pnpm model-key:set", caught.exception.detail)
        self.assertNotIn("OPENAI_API_KEY", caught.exception.detail)

    def test_provider_secret_is_not_chained_or_logged_on_failure(self) -> None:
        provider_secret = "provider-error-secret-that-must-not-leak"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "paper.pdf"
            content = b"%PDF-model-error-test"
            source_path.write_bytes(content)
            source = types.SimpleNamespace(
                id="source-1",
                local_path=str(source_path),
                content_hash=hashlib.sha256(content).hexdigest(),
            )
            session = MagicMock()
            session.scalars.return_value = [source]
            configured = replace(
                settings,
                model_gateway_configured=True,
                openai_api_key=API_KEY,
                llm_model="test-model",
            )
            with (
                patch(
                    "open_science_core.app._project_or_404",
                    return_value=types.SimpleNamespace(project_path=str(root)),
                ),
                patch("open_science_core.app.settings", configured),
                patch(
                    "open_science_core.app.paper_qa.ask",
                    new=AsyncMock(side_effect=RuntimeError(provider_secret)),
                ),
                self.assertLogs("open_science_core.app", level="WARNING") as logs,
            ):
                with self.assertRaises(HTTPException) as caught:
                    asyncio.run(
                        ask_question(
                            "project-1",
                            QuestionIn(
                                question="Question?",
                                remote_data_approved=True,
                            ),
                            session,
                        )
                    )

        self.assertEqual(caught.exception.status_code, 502)
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn(provider_secret, caught.exception.detail)
        self.assertNotIn(provider_secret, "\n".join(logs.output))

    def test_key_is_passed_explicitly_without_mutating_process_environment(self) -> None:
        observations: list[str | None] = []
        settings_calls: list[dict[str, object]] = []

        class FakeSettings:
            def __init__(self, **kwargs: object) -> None:
                settings_calls.append(kwargs)
                observations.append(os.environ.get("OPENAI_API_KEY"))

        class FakeDocs:
            async def aadd(self, _path: Path, *, settings: FakeSettings) -> None:
                observations.append(os.environ.get("OPENAI_API_KEY"))

            async def aquery(self, _question: str, *, settings: FakeSettings):
                observations.append(os.environ.get("OPENAI_API_KEY"))
                return types.SimpleNamespace(answer="A bounded answer", contexts=[])

        fake_module = types.ModuleType("paperqa")
        fake_module.Docs = FakeDocs
        fake_module.Settings = FakeSettings
        adapter = PaperQaAdapter()
        configured = replace(settings, openai_api_key=API_KEY, llm_model="test-model")
        with (
            patch.dict(sys.modules, {"paperqa": fake_module}),
            patch("open_science_core.literature.app_settings", configured),
            patch.dict(os.environ, {"OPENAI_API_KEY": "previous-value"}, clear=True),
        ):
            result = asyncio.run(
                adapter.ask("project-1", [Path("paper.pdf")], "Question?", None)
            )
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "previous-value")

        self.assertEqual(result.answer, "A bounded answer")
        self.assertEqual(
            observations,
            ["previous-value", "previous-value", "previous-value"],
        )
        self.assertEqual(len(settings_calls), 1)
        configured = settings_calls[0]
        self.assertEqual(configured["llm"], "openai/test-model")
        self.assertEqual(configured["summary_llm"], "openai/test-model")
        self.assertEqual(configured["embedding"], "openai/text-embedding-3-small")
        self.assertTrue(
            configured["llm_config"]["model_list"][0]["litellm_params"]["api_key"]
            == API_KEY
        )
        self.assertTrue(
            configured["embedding_config"]["kwargs"]["api_key"] == API_KEY
        )
        self.assertTrue(
            configured["agent"]["agent_llm_config"]["model_list"][0][
                "litellm_params"
            ]["api_key"]
            == API_KEY
        )
        self.assertTrue(
            configured["parsing"]["enrichment_llm_config"]["model_list"][0][
                "litellm_params"
            ]["api_key"]
            == API_KEY
        )

    def test_key_never_enters_process_environment_when_paperqa_raises(self) -> None:
        class FakeSettings:
            def __init__(self, **_kwargs: str) -> None:
                pass

        class FakeDocs:
            async def aadd(self, _path: Path, *, settings: FakeSettings) -> None:
                pass

            async def aquery(self, _question: str, *, settings: FakeSettings):
                self.seen_key = os.environ.get("OPENAI_API_KEY")
                raise RuntimeError("provider failed")

        fake_module = types.ModuleType("paperqa")
        fake_module.Docs = FakeDocs
        fake_module.Settings = FakeSettings
        adapter = PaperQaAdapter()
        configured = replace(settings, openai_api_key=API_KEY, llm_model="test-model")
        with (
            patch.dict(sys.modules, {"paperqa": fake_module}),
            patch("open_science_core.literature.app_settings", configured),
            patch.dict(os.environ, {}, clear=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                asyncio.run(
                    adapter.ask("project-1", [Path("paper.pdf")], "Question?", None)
                )
            self.assertNotIn("OPENAI_API_KEY", os.environ)


def _contains_value(value: object, expected: object) -> bool:
    if isinstance(value, dict):
        return any(_contains_value(item, expected) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_value(item, expected) for item in value)
    return value == expected


if __name__ == "__main__":
    unittest.main()
