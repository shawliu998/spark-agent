from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from ..schemas import to_camel
from .prompts.intent_router_v1 import (
    INTENT_ROUTER_PROMPT_VERSION,
    INTENT_ROUTER_SYSTEM_PROMPT,
    build_intent_router_input_payload,
    build_intent_router_request_payload,
    build_intent_router_user_prompt,
)

Intent = Literal[
    "literature-synthesis",
    "dataset-analysis",
    "mixed-research",
    "clarification-required",
    "unsupported",
]
ResolvedWorkflowType = Literal[
    "literature-synthesis",
    "dataset-analysis",
    "mixed-research",
]
RouterParseResult = Literal[
    "valid",
    "model-not-configured",
    "model-request-failed",
    "model-request-outcome-unknown",
    "model-output-invalid",
    "deterministic-capability-guard",
]
SourceKind = Literal["pdf", "dataset"]

MINIMUM_ROUTING_CONFIDENCE = 0.70
_RESOLVED_WORKFLOW_TYPES: frozenset[str] = frozenset(
    {"literature-synthesis", "dataset-analysis", "mixed-research"}
)
_SOURCE_KIND_FOR_SINGLE_WORKFLOW: dict[str, SourceKind] = {
    "literature-synthesis": "pdf",
    "dataset-analysis": "dataset",
}

_UNSUPPORTED_CAPABILITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "structural-equation-modeling",
        re.compile(
            r"(?:\bsem\b|structural\s+equation\s+model(?:ing)?|结构方程(?:模型)?)",
            re.IGNORECASE,
        ),
    ),
    (
        "arbitrary-code-execution",
        re.compile(
            r"(?:arbitrary\s+(?:python\s+)?code|任意\s*(?:python|代码)|"
            r"自由(?:生成|执行)\s*(?:python|代码))",
            re.IGNORECASE,
        ),
    ),
    (
        "shell-execution",
        re.compile(
            r"(?:execute|run|执行|运行).{0,24}(?:shell|bash|zsh|shell\s*command|"
            r"命令行|系统命令)",
            re.IGNORECASE,
        ),
    ),
    (
        "remote-compute-execution",
        re.compile(r"(?:\bssh\b|\bhpc\b|高性能计算集群)", re.IGNORECASE),
    ),
    (
        "unsupported-language-runtime",
        re.compile(
            r"(?:execute|run|执行|运行|using|使用).{0,16}(?:\br\b|\bjulia\b)(?:\s+code|代码)?",
            re.IGNORECASE,
        ),
    ),
    (
        "deep-learning-training",
        re.compile(
            r"(?:(?:train|fine[- ]?tune|训练|微调).{0,24}(?:deep\s+learning|"
            r"neural\s+network|深度学习|神经网络)|(?:deep\s+learning|神经网络|"
            r"深度学习).{0,24}(?:train|fine[- ]?tune|训练|微调))",
            re.IGNORECASE,
        ),
    ),
)

_NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
_SourceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
_IngestionStatus = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=32),
]


class _StrictRouterModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class IntentSource(_StrictRouterModel):
    id: _SourceId
    source_kind: SourceKind
    ingestion_status: _IngestionStatus


class IntentDecision(_StrictRouterModel):
    intent: Intent
    confidence: Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
    reasoning_summary: _NonEmptyText
    selected_source_ids: Annotated[list[_SourceId], Field(max_length=256)]
    missing_inputs: Annotated[list[_NonEmptyText], Field(max_length=32)]
    proposed_workflow_type: ResolvedWorkflowType | None

    @field_validator("selected_source_ids", "missing_inputs")
    @classmethod
    def require_unique_items(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("items must be unique")
        return value

    @model_validator(mode="after")
    def validate_intent_shape(self) -> IntentDecision:
        if self.intent in _RESOLVED_WORKFLOW_TYPES:
            if self.proposed_workflow_type != self.intent:
                raise ValueError("proposed workflow type must match the resolved intent")
            if not self.selected_source_ids:
                raise ValueError("a resolved intent must select at least one source")
        elif self.proposed_workflow_type is not None:
            raise ValueError("unresolved intents cannot propose a workflow type")
        if self.intent == "clarification-required" and not self.missing_inputs:
            raise ValueError("clarification-required must identify missing input")
        return self


class IntentModelGateway(Protocol):
    @property
    def configured(self) -> bool: ...

    @property
    def default_model(self) -> str | None: ...

    @property
    def endpoint_identity(self) -> str: ...

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class IntentRouterResult:
    decision: IntentDecision
    prompt_version: str
    input_sha256: str
    output_sha256: str
    model_output_sha256: str | None
    model_used: str | None
    endpoint_identity: str | None
    used_model: bool
    parse_result: RouterParseResult
    validation_errors: tuple[str, ...]
    token_usage: dict[str, int]


class IntentDecisionValidationError(ValueError):
    """The model decision passed JSON parsing but violated deterministic policy."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def intent_router_input_sha256(
    goal: str,
    sources: Sequence[IntentSource],
    answered_context: Sequence[Mapping[str, object]] = (),
    *,
    model: str | None = None,
) -> str:
    return canonical_json_sha256(
        build_intent_router_request_payload(
            goal,
            _prompt_source_descriptors(sources),
            answered_context,
            model=model,
        )
    )


def intent_decision_sha256(decision: IntentDecision) -> str:
    return canonical_json_sha256(
        decision.model_dump(mode="json", by_alias=True, exclude_none=False)
    )


def recover_unknown_model_request(
    goal: str,
    sources: Sequence[IntentSource],
    answered_context: Sequence[Mapping[str, object]],
    *,
    model: str,
    endpoint_identity: str,
) -> IntentRouterResult:
    """Safely resume a request that may have reached the remote provider."""

    normalized_goal = goal.strip()
    decision = deterministic_fallback_decision(
        normalized_goal,
        sources,
        answered_context,
    )
    return _result(
        decision,
        input_sha256=intent_router_input_sha256(
            normalized_goal,
            sources,
            answered_context,
            model=model,
        ),
        model_used=model,
        endpoint_identity=endpoint_identity,
        used_model=True,
        parse_result="model-request-outcome-unknown",
        validation_errors=("model-request-outcome-unknown",),
    )


def parse_intent_decision(value: object) -> IntentDecision:
    """Strictly parse an untrusted model JSON object."""

    return IntentDecision.model_validate(value, strict=True)


def unsupported_capabilities(goal: str) -> tuple[str, ...]:
    return tuple(
        capability
        for capability, pattern in _UNSUPPORTED_CAPABILITY_PATTERNS
        if pattern.search(goal)
    )


def validate_intent_decision(
    decision: IntentDecision,
    sources: Sequence[IntentSource],
) -> IntentDecision:
    """Apply deterministic source and confidence policy to a parsed model decision."""

    source_by_id, source_error = _normalized_source_map(sources)
    if source_error is not None:
        raise IntentDecisionValidationError(source_error)
    selected = set(decision.selected_source_ids)
    if not selected.issubset(source_by_id):
        raise IntentDecisionValidationError("selected-source-not-authorized")

    if decision.intent in _RESOLVED_WORKFLOW_TYPES:
        _validate_source_capability(decision.intent, selected, source_by_id)

    canonical_selected_ids = sorted(selected)
    if decision.confidence < MINIMUM_ROUTING_CONFIDENCE:
        missing_inputs = list(decision.missing_inputs)
        if not missing_inputs:
            missing_inputs = ["clarify-low-confidence-intent"]
        return IntentDecision(
            intent="clarification-required",
            confidence=decision.confidence,
            reasoning_summary=(
                "The model confidence is below the routing threshold; clarification is required."
            ),
            selected_source_ids=canonical_selected_ids,
            missing_inputs=missing_inputs,
            proposed_workflow_type=None,
        )

    if canonical_selected_ids == decision.selected_source_ids:
        return decision
    return decision.model_copy(update={"selected_source_ids": canonical_selected_ids})


def deterministic_fallback_decision(
    goal: str,
    sources: Sequence[IntentSource],
    answered_context: Sequence[Mapping[str, object]] = (),
) -> IntentDecision:
    capabilities = unsupported_capabilities(goal)
    if capabilities:
        return _unsupported_decision(sources, capabilities)

    source_by_id, source_error = _normalized_source_map(sources)
    if source_error is not None:
        return _clarification_decision(
            [],
            "The selected sources have conflicting or unsupported source metadata.",
            "resolve-source-metadata",
        )
    source_ids_by_kind = {
        "pdf": sorted(
            source_id
            for source_id, (kind, status) in source_by_id.items()
            if kind == "pdf" and status == "ready"
        ),
        "dataset": sorted(
            source_id
            for source_id, (kind, status) in source_by_id.items()
            if kind == "dataset" and status == "ready"
        ),
    }
    answered_workflow = _latest_single_choice_workflow(answered_context)
    if answered_workflow is not None:
        answered = _decision_for_answered_workflow(answered_workflow, source_ids_by_kind)
        if answered is not None:
            return answered
        return _clarification_decision(
            sorted(source_by_id),
            "The latest workflow choice conflicts with the available source kinds.",
            _missing_input_for_workflow(answered_workflow),
        )

    pdf_ids = source_ids_by_kind["pdf"]
    dataset_ids = source_ids_by_kind["dataset"]
    if pdf_ids and dataset_ids:
        return _clarification_decision(
            pdf_ids + dataset_ids,
            (
                "The selected sources contain both literature and dataset inputs, but "
                "mixed research execution is not available. Choose one supported path."
            ),
            "select-supported-single-workflow",
        )
    if pdf_ids:
        return _resolved_fallback_decision(
            "literature-synthesis",
            pdf_ids,
            "All selected sources are PDF literature sources.",
        )
    if dataset_ids:
        if len(dataset_ids) != 1:
            return _clarification_decision(
                dataset_ids,
                "Dataset analysis requires exactly one ready dataset source.",
                "select-exactly-one-ready-dataset",
            )
        return _resolved_fallback_decision(
            "dataset-analysis",
            dataset_ids,
            "All selected sources are dataset sources.",
        )
    return _clarification_decision(
        [],
        "No supported source was selected.",
        "select-ready-supported-source",
    )


async def route_intent(
    goal: str,
    sources: Sequence[IntentSource],
    gateway: IntentModelGateway | None = None,
    *,
    model: str | None = None,
    answered_context: Sequence[Mapping[str, object]] = (),
) -> IntentRouterResult:
    """Route a research goal without granting the model control-plane authority."""

    normalized_goal = goal.strip()
    gateway_configured = gateway is not None and gateway.configured
    selected_model: str | None = None
    if gateway_configured and gateway is not None:
        selected_model = model or gateway.default_model
    input_sha256 = intent_router_input_sha256(
        normalized_goal,
        sources,
        answered_context,
        model=selected_model,
    )
    capability_gaps = unsupported_capabilities(normalized_goal)
    if capability_gaps:
        decision = _unsupported_decision(sources, capability_gaps)
        return _result(
            decision,
            input_sha256=input_sha256,
            parse_result="deterministic-capability-guard",
            validation_errors=capability_gaps,
        )

    if not gateway_configured or gateway is None:
        decision = deterministic_fallback_decision(
            normalized_goal,
            sources,
            answered_context,
        )
        return _result(
            decision,
            input_sha256=input_sha256,
            parse_result="model-not-configured",
            validation_errors=("model-not-configured",),
        )

    endpoint_identity = gateway.endpoint_identity or None
    user_prompt = build_intent_router_user_prompt(
        normalized_goal,
        _prompt_source_descriptors(sources),
        answered_context,
    )
    token_usage: dict[str, int] = {}
    try:
        complete_with_metadata = cast(
            Callable[
                [str, str, str | None],
                Awaitable[tuple[dict[str, Any], dict[str, int]]],
            ]
            | None,
            getattr(gateway, "complete_json_with_metadata", None),
        )
        if complete_with_metadata is not None:
            model_output, token_usage = await complete_with_metadata(
                INTENT_ROUTER_SYSTEM_PROMPT,
                user_prompt,
                model,
            )
        else:
            model_output = await gateway.complete_json(
                INTENT_ROUTER_SYSTEM_PROMPT,
                user_prompt,
                model,
            )
    except Exception:
        decision = deterministic_fallback_decision(
            normalized_goal,
            sources,
            answered_context,
        )
        return _result(
            decision,
            input_sha256=input_sha256,
            model_used=selected_model,
            endpoint_identity=endpoint_identity,
            used_model=True,
            parse_result="model-request-failed",
            validation_errors=("model-request-failed",),
        )
    model_output_sha256 = canonical_json_sha256(model_output)

    try:
        parsed = parse_intent_decision(model_output)
        decision = validate_intent_decision(parsed, sources)
    except ValidationError:
        validation_errors = ("model-output-schema-invalid",)
    except IntentDecisionValidationError as error:
        validation_errors = (error.code,)
    else:
        low_confidence_adjusted = (
            parsed.confidence < MINIMUM_ROUTING_CONFIDENCE
            and parsed.intent != "clarification-required"
        )
        return _result(
            decision,
            input_sha256=input_sha256,
            model_output_sha256=model_output_sha256,
            model_used=selected_model,
            endpoint_identity=endpoint_identity,
            used_model=True,
            parse_result="valid",
            validation_errors=(
                ("low-confidence-forced-clarification",)
                if low_confidence_adjusted
                else ()
            ),
            token_usage=token_usage,
        )

    decision = deterministic_fallback_decision(
        normalized_goal,
        sources,
        answered_context,
    )
    return _result(
        decision,
        input_sha256=input_sha256,
        model_output_sha256=model_output_sha256,
        model_used=selected_model,
        endpoint_identity=endpoint_identity,
        used_model=True,
        parse_result="model-output-invalid",
        validation_errors=validation_errors,
        token_usage=token_usage,
    )


def _prompt_source_descriptors(
    sources: Sequence[IntentSource],
) -> list[dict[str, str]]:
    return [
        {
            "id": source.id,
            "sourceKind": source.source_kind,
            "ingestionStatus": source.ingestion_status,
        }
        for source in sources
    ]


def _normalized_source_map(
    sources: Sequence[IntentSource],
) -> tuple[dict[str, tuple[SourceKind, str]], str | None]:
    if len(sources) > 256:
        return {}, "too-many-sources"
    source_by_id: dict[str, tuple[SourceKind, str]] = {}
    for source in sources:
        existing = source_by_id.get(source.id)
        capability = (source.source_kind, source.ingestion_status)
        if existing is not None and existing != capability:
            return {}, "conflicting-source-kind"
        source_by_id[source.id] = capability
    return source_by_id, None


def _validate_source_capability(
    intent: str,
    selected: set[str],
    source_by_id: Mapping[str, tuple[SourceKind, str]],
) -> None:
    selected_capabilities = {source_by_id[source_id] for source_id in selected}
    if any(status != "ready" for _kind, status in selected_capabilities):
        raise IntentDecisionValidationError("selected-source-not-ready")
    selected_kinds = {kind for kind, _status in selected_capabilities}
    if intent == "literature-synthesis" and selected_kinds != {"pdf"}:
        raise IntentDecisionValidationError("literature-intent-requires-pdf-sources")
    if intent == "dataset-analysis":
        if selected_kinds != {"dataset"}:
            raise IntentDecisionValidationError("dataset-intent-requires-dataset-sources")
        if len(selected) != 1:
            raise IntentDecisionValidationError("dataset-intent-requires-one-dataset")
    if intent == "mixed-research" and selected_kinds != {"pdf", "dataset"}:
        raise IntentDecisionValidationError("mixed-intent-requires-pdf-and-dataset-sources")


def _latest_single_choice_workflow(
    answered_context: Sequence[Mapping[str, object]],
) -> ResolvedWorkflowType | None:
    for answer in reversed(answered_context):
        request_type = answer.get("requestType", answer.get("request_type"))
        if request_type != "single-choice":
            continue
        raw_response = answer.get("response")
        if isinstance(raw_response, dict):
            response_mapping = cast(dict[object, object], raw_response)
            raw_response = response_mapping.get("value")
        if isinstance(raw_response, str) and raw_response in _RESOLVED_WORKFLOW_TYPES:
            return cast(ResolvedWorkflowType, raw_response)
    return None


def _decision_for_answered_workflow(
    workflow_type: ResolvedWorkflowType,
    source_ids_by_kind: Mapping[str, list[str]],
) -> IntentDecision | None:
    pdf_ids = source_ids_by_kind["pdf"]
    dataset_ids = source_ids_by_kind["dataset"]
    if workflow_type == "literature-synthesis" and pdf_ids:
        selected = pdf_ids
    elif workflow_type == "dataset-analysis" and len(dataset_ids) == 1:
        selected = dataset_ids
    elif workflow_type == "mixed-research" and pdf_ids and dataset_ids:
        selected = pdf_ids + dataset_ids
    else:
        return None
    return _resolved_fallback_decision(
        workflow_type,
        selected,
        "The latest explicit workflow choice is compatible with the selected sources.",
    )


def _missing_input_for_workflow(workflow_type: ResolvedWorkflowType) -> str:
    if workflow_type == "literature-synthesis":
        return "select-ready-pdf-source"
    if workflow_type == "dataset-analysis":
        return "select-exactly-one-ready-dataset"
    return "select-ready-pdf-and-dataset-sources"


def _resolved_fallback_decision(
    workflow_type: ResolvedWorkflowType,
    source_ids: Sequence[str],
    reasoning_summary: str,
) -> IntentDecision:
    return IntentDecision(
        intent=workflow_type,
        confidence=1.0,
        reasoning_summary=reasoning_summary,
        selected_source_ids=sorted(set(source_ids)),
        missing_inputs=[],
        proposed_workflow_type=workflow_type,
    )


def _clarification_decision(
    source_ids: Sequence[str],
    reasoning_summary: str,
    missing_input: str,
) -> IntentDecision:
    return IntentDecision(
        intent="clarification-required",
        confidence=1.0,
        reasoning_summary=reasoning_summary,
        selected_source_ids=sorted(set(source_ids)),
        missing_inputs=[missing_input],
        proposed_workflow_type=None,
    )


def _unsupported_decision(
    sources: Sequence[IntentSource],
    capabilities: Sequence[str],
) -> IntentDecision:
    source_by_id, _source_error = _normalized_source_map(sources)
    return IntentDecision(
        intent="unsupported",
        confidence=1.0,
        reasoning_summary=(
            "The request requires a capability outside the bounded research workflow."
        ),
        selected_source_ids=sorted(
            source_id
            for source_id, (_kind, status) in source_by_id.items()
            if status == "ready"
        ),
        missing_inputs=[],
        proposed_workflow_type=None,
    )


def _result(
    decision: IntentDecision,
    *,
    input_sha256: str,
    model_output_sha256: str | None = None,
    model_used: str | None = None,
    endpoint_identity: str | None = None,
    used_model: bool = False,
    parse_result: RouterParseResult,
    validation_errors: tuple[str, ...],
    token_usage: dict[str, int] | None = None,
) -> IntentRouterResult:
    return IntentRouterResult(
        decision=decision,
        prompt_version=INTENT_ROUTER_PROMPT_VERSION,
        input_sha256=input_sha256,
        output_sha256=intent_decision_sha256(decision),
        model_output_sha256=model_output_sha256,
        model_used=model_used,
        endpoint_identity=endpoint_identity,
        used_model=used_model,
        parse_result=parse_result,
        validation_errors=validation_errors,
        token_usage=dict(token_usage or {}),
    )


__all__ = [
    "INTENT_ROUTER_PROMPT_VERSION",
    "INTENT_ROUTER_SYSTEM_PROMPT",
    "MINIMUM_ROUTING_CONFIDENCE",
    "IntentDecision",
    "IntentDecisionValidationError",
    "IntentModelGateway",
    "IntentRouterResult",
    "IntentSource",
    "RouterParseResult",
    "build_intent_router_input_payload",
    "build_intent_router_request_payload",
    "build_intent_router_user_prompt",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "deterministic_fallback_decision",
    "intent_decision_sha256",
    "intent_router_input_sha256",
    "parse_intent_decision",
    "recover_unknown_model_request",
    "route_intent",
    "unsupported_capabilities",
    "validate_intent_decision",
]
