from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from open_science_core.analysis_spec import (
    AnalysisSpec,
    ClarificationProposal,
    CorrelationOperation,
    MethodSelection,
    TwoGroupComparisonOperation,
    UnsupportedAnalysis,
    deterministic_method_selection,
    select_analysis_method,
)
from open_science_core.analysis_spec.prompts import (
    METHOD_REPAIR_PROMPT_VERSION,
    METHOD_SELECTOR_SYSTEM_PROMPT,
)
from open_science_core.workflow.schemas import DatasetProfile

CONTENT_HASH = "a" * 64
PROFILE_HASH = "b" * 64
SOURCE_ID = "dataset-1"


def _column(
    name: str,
    inferred_type: str,
    *,
    values: Sequence[str] | None = None,
    unique_count: int = 40,
) -> dict[str, object]:
    numeric = inferred_type in {"integer", "number"}
    return {
        "name": name,
        "inferred_type": inferred_type,
        "missing_count": 0,
        "unique_count": unique_count,
        "numeric_range": {"minimum": 0.0, "maximum": 100.0} if numeric else None,
        "low_cardinality": (
            {"values": list(values), "truncated": False}
            if values is not None
            else None
        ),
        "potential_date": False,
        "potential_id": False,
        "mixed_type": False,
    }


def _profile(*columns: dict[str, object]) -> DatasetProfile:
    indexed_columns = [dict(column, index=index) for index, column in enumerate(columns)]
    return DatasetProfile.model_validate(
        {
            "schema_version": "1",
            "dataset_source_id": SOURCE_ID,
            "filename": "raw-observations-must-not-enter-the-prompt.csv",
            "content_hash": CONTENT_HASH,
            "file_size_bytes": 1_024,
            "encoding": "utf-8",
            "delimiter": ",",
            "row_count": 40,
            "column_count": len(indexed_columns),
            "columns": indexed_columns,
            "sampling": {
                "method": "head-and-reservoir-v1",
                "rows_read": 40,
                "rows_profiled": 40,
                "max_sample_rows": 100,
                "seed": 17,
            },
            "warnings": [],
        }
    )


def _standard_profile() -> DatasetProfile:
    return _profile(
        _column("group", "string", values=["treatment", "control"], unique_count=2),
        _column("score", "number"),
        _column("accuracy", "number"),
        _column("sleep_hours", "number"),
        _column("cognitive_score", "number"),
    )


def _select(
    goal: str,
    profile: DatasetProfile,
    *,
    answered_context: Sequence[Mapping[str, object]] = (),
) -> MethodSelection:
    return deterministic_method_selection(
        goal,
        profile,
        dataset_source_id=SOURCE_ID,
        dataset_content_hash=CONTENT_HASH,
        dataset_profile_hash=PROFILE_HASH,
        answered_context=answered_context,
    )


def _clarification_types(decision: ClarificationProposal) -> set[str]:
    return {request.type for request in decision.requests}


class SequenceGateway:
    configured = True
    default_model = "test-method-selector"
    endpoint_identity = "sha256:" + ("c" * 64)

    def __init__(self, outputs: Sequence[dict[str, Any]]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        output, _usage = await self.complete_json_with_metadata(
            system_prompt,
            user_prompt,
            model,
        )
        return output

    async def complete_json_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        index = len(self.calls)
        self.calls.append(
            {
                "systemPrompt": system_prompt,
                "userPrompt": user_prompt,
                "model": model,
            }
        )
        return self.outputs[min(index, len(self.outputs) - 1)], {
            "inputTokens": 10,
            "outputTokens": 5,
        }


def test_selects_explicit_two_group_columns_and_values() -> None:
    decision = _select(
        "Compare treatment and control groups in group on score with Welch.",
        _standard_profile(),
    )

    assert isinstance(decision, AnalysisSpec)
    assert isinstance(decision.operation, TwoGroupComparisonOperation)
    assert decision.operation.outcome_column == "score"
    assert decision.operation.group_column == "group"
    assert decision.operation.groups == ("treatment", "control")
    assert decision.operation.method == "welch-t-test"
    assert decision.operation.effect_size == "hedges-g"


def test_selects_descriptive_and_chinese_correlation_goals() -> None:
    descriptive = _select("Describe the score distribution.", _standard_profile())
    correlation = _select(
        "检查 sleep_hours 和 cognitive_score 是否相关。",
        _standard_profile(),
    )

    assert isinstance(descriptive, AnalysisSpec)
    assert descriptive.operation.type == "descriptive"
    assert descriptive.operation.columns == ["score"]
    assert isinstance(correlation, AnalysisSpec)
    assert isinstance(correlation.operation, CorrelationOperation)
    assert correlation.operation.x_column == "sleep_hours"
    assert correlation.operation.y_column == "cognitive_score"
    assert correlation.operation.method == "auto"


def test_chinese_column_names_are_matched_without_ascii_spacing() -> None:
    profile = _profile(
        _column("睡眠时长", "number"),
        _column("认知得分", "number"),
    )

    decision = _select("检查睡眠时长和认知得分是否相关。", profile)

    assert isinstance(decision, AnalysisSpec)
    assert isinstance(decision.operation, CorrelationOperation)
    assert decision.operation.x_column == "睡眠时长"
    assert decision.operation.y_column == "认知得分"


def test_ambiguous_two_group_goal_asks_for_group_and_outcome() -> None:
    profile = _profile(
        _column("group", "string", values=["treatment", "control"], unique_count=2),
        _column("condition", "string", values=["a", "b"], unique_count=2),
        _column("score", "number"),
        _column("accuracy", "number"),
    )

    decision = _select("比较两组结果有没有差异。", profile)

    assert isinstance(decision, ClarificationProposal)
    assert _clarification_types(decision) == {"group-column", "outcome-column"}


def test_multi_value_group_requires_exact_group_values() -> None:
    profile = _profile(
        _column("group", "string", values=["control", "low", "high"], unique_count=3),
        _column("score", "number"),
    )

    decision = _select("Compare score between groups in group with Welch.", profile)

    assert isinstance(decision, ClarificationProposal)
    assert _clarification_types(decision) == {"group-values"}
    assert [option.value for option in decision.requests[0].options] == [
        "control",
        "low",
        "high",
    ]


def test_ambiguous_method_requests_method_confirmation() -> None:
    decision = _select(
        "Check sleep_hours and cognitive_score correlation using Pearson or Spearman.",
        _standard_profile(),
    )

    assert isinstance(decision, ClarificationProposal)
    assert _clarification_types(decision) == {"method-confirmation"}
    assert [option.value for option in decision.requests[0].options] == [
        "auto",
        "pearson",
        "spearman",
    ]


def test_answered_scientific_context_continues_to_analysis_spec() -> None:
    profile = _profile(
        _column("group", "string", values=["treatment", "control", "pilot"], unique_count=3),
        _column("condition", "string", values=["a", "b"], unique_count=2),
        _column("score", "number"),
        _column("accuracy", "number"),
    )
    answered_context = [
        {"type": "outcome-column", "response": "score"},
        {"type": "group-column", "response": "group"},
        {"type": "group-values", "response": ["treatment", "control"]},
        {"type": "method-confirmation", "response": "mann-whitney-u"},
    ]

    decision = _select(
        "比较两组结果有没有差异。",
        profile,
        answered_context=answered_context,
    )

    assert isinstance(decision, AnalysisSpec)
    assert isinstance(decision.operation, TwoGroupComparisonOperation)
    assert decision.operation.outcome_column == "score"
    assert decision.operation.group_column == "group"
    assert decision.operation.groups == ("treatment", "control")
    assert decision.operation.method == "mann-whitney-u"
    assert decision.operation.effect_size == "rank-biserial"


@pytest.mark.parametrize(
    ("goal", "capability"),
    [
        ("Run a paired test on score before and after treatment.", "paired-test"),
        ("Fit a structural equation model for the latent paths.", "structural-equation-modeling"),
        ("Run ANOVA across all groups.", "anova"),
        ("Fit a regression model for score.", "regression"),
        ("Forecast this time-series.", "time-series"),
        ("Estimate the causal treatment effect.", "causal-inference"),
    ],
)
def test_unsupported_methods_never_fall_back(goal: str, capability: str) -> None:
    decision = _select(goal, _standard_profile())

    assert isinstance(decision, UnsupportedAnalysis)
    assert decision.capability == capability


@pytest.mark.asyncio
async def test_remote_fabricated_column_is_repaired_twice_then_falls_back() -> None:
    profile = _standard_profile()
    local = _select(
        "Check whether sleep_hours and cognitive_score are correlated.",
        profile,
    )
    assert isinstance(local, AnalysisSpec)
    forged = local.model_dump(mode="json", by_alias=True)
    forged["operation"]["xColumn"] = "invented_column"
    gateway = SequenceGateway([{"confidence": 0.95, "decision": forged}])

    result = await select_analysis_method(
        "Check whether sleep_hours and cognitive_score are correlated.",
        profile,
        dataset_source_id=SOURCE_ID,
        dataset_content_hash=CONTENT_HASH,
        dataset_profile_hash=PROFILE_HASH,
        gateway=gateway,
    )

    assert result.parse_result == "model-output-invalid"
    assert result.decision == local
    assert result.retry_count == 2
    assert len(gateway.calls) == 3
    assert result.validation_errors == ("analysis-spec-column-not-found",) * 3
    assert result.token_usage == {"inputTokens": 30, "outputTokens": 15}
    assert "raw-observations-must-not-enter-the-prompt.csv" not in str(
        gateway.calls[0]["userPrompt"]
    )
    assert gateway.calls[0]["systemPrompt"] == METHOD_SELECTOR_SYSTEM_PROMPT
    first_payload = json.loads(str(gateway.calls[0]["userPrompt"]))
    assert first_payload["outputContract"]["jsonSchema"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_invalid_envelope_can_be_repaired_once() -> None:
    profile = _standard_profile()
    decision = _select(
        "Check whether sleep_hours and cognitive_score are correlated.",
        profile,
    )
    assert isinstance(decision, AnalysisSpec)
    valid = {
        "confidence": 0.96,
        "decision": decision.model_dump(mode="json", by_alias=True),
    }
    invalid = dict(valid, python="import os")
    gateway = SequenceGateway([invalid, valid])

    result = await select_analysis_method(
        "Check whether sleep_hours and cognitive_score are correlated.",
        profile,
        dataset_source_id=SOURCE_ID,
        dataset_content_hash=CONTENT_HASH,
        dataset_profile_hash=PROFILE_HASH,
        gateway=gateway,
    )

    assert result.decision == decision
    assert result.parse_result == "valid-after-repair"
    assert result.prompt_version == METHOD_REPAIR_PROMPT_VERSION
    assert result.retry_count == 1
    assert result.validation_errors == ("model-output-schema-invalid",)
    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_low_confidence_and_non_json_outputs_are_bounded() -> None:
    profile = _standard_profile()
    local = _select("Describe score distribution.", profile)
    assert isinstance(local, AnalysisSpec)
    low_confidence = {
        "confidence": 0.2,
        "decision": local.model_dump(mode="json", by_alias=True),
    }
    low_gateway = SequenceGateway([low_confidence])
    non_json_gateway = SequenceGateway([{"bad": {"not-json"}}])

    low_result = await select_analysis_method(
        "Describe score distribution.",
        profile,
        dataset_source_id=SOURCE_ID,
        dataset_content_hash=CONTENT_HASH,
        dataset_profile_hash=PROFILE_HASH,
        gateway=low_gateway,
    )
    non_json_result = await select_analysis_method(
        "Describe score distribution.",
        profile,
        dataset_source_id=SOURCE_ID,
        dataset_content_hash=CONTENT_HASH,
        dataset_profile_hash=PROFILE_HASH,
        gateway=non_json_gateway,
    )

    assert low_result.parse_result == "model-output-invalid"
    assert low_result.validation_errors == ("low-confidence-method-selection",) * 3
    assert non_json_result.parse_result == "model-output-invalid"
    assert non_json_result.validation_errors == ("model-output-json-invalid",) * 3
