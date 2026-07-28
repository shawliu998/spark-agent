from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..analysis_spec.schemas import ClarificationProposal, ScientificClarification
from ..models import (
    InteractionRequestRecord,
    UserResponseRecord,
    WorkflowRecord,
)
from ._service.events import append_workflow_events
from ._service.integrity import content_sha256
from .schemas import (
    AnalysisClarificationRequestedEventData,
    InteractionRequestedEventData,
)

SCIENTIFIC_INTERACTION_STEP_ID = "select-analysis-method"


def answered_scientific_context(
    session: Session,
    workflow_id: str,
) -> tuple[Mapping[str, object], ...]:
    interactions = list(
        session.scalars(
            select(InteractionRequestRecord)
            .where(
                InteractionRequestRecord.workflow_id == workflow_id,
                InteractionRequestRecord.step_id == SCIENTIFIC_INTERACTION_STEP_ID,
                InteractionRequestRecord.status == "answered",
            )
            .order_by(InteractionRequestRecord.created_at, InteractionRequestRecord.id)
        )
    )
    latest_by_type: dict[str, Mapping[str, object]] = {}
    for interaction in interactions:
        response = session.scalar(
            select(UserResponseRecord)
            .where(UserResponseRecord.interaction_id == interaction.id)
            .order_by(UserResponseRecord.revision.desc())
        )
        clarification_type = interaction.response_schema.get("clarificationType")
        if response is None or not isinstance(clarification_type, str):
            continue
        latest_by_type[clarification_type] = {
            "type": clarification_type,
            "response": response.response_json,
        }
    return tuple(latest_by_type.values())


def create_scientific_interaction(
    session: Session,
    workflow: WorkflowRecord,
    proposal: ClarificationProposal,
    *,
    selector_input_sha256: str,
    selector_output_sha256: str,
) -> InteractionRequestRecord:
    request = proposal.requests[0]
    request_type, response_schema = _interaction_contract(request)
    options = [
        option.model_dump(mode="json", by_alias=True, exclude_none=True)
        for option in request.options
    ]
    request_key = f"analysis-clarification:{request.type}"
    request_payload = {
        "clarificationType": request.type,
        "options": options,
        "question": request.question,
        "reason": proposal.reason,
        "requestType": request_type,
        "responseSchema": response_schema,
        "selectorInputSha256": selector_input_sha256,
        "selectorOutputSha256": selector_output_sha256,
        "workflowRevision": workflow.row_version,
    }
    request_sha256 = content_sha256(request_payload)
    existing = session.scalar(
        select(InteractionRequestRecord)
        .where(
            InteractionRequestRecord.workflow_id == workflow.id,
            InteractionRequestRecord.step_id == SCIENTIFIC_INTERACTION_STEP_ID,
            InteractionRequestRecord.request_key == request_key,
            InteractionRequestRecord.status == "pending",
        )
        .order_by(InteractionRequestRecord.revision.desc())
    )
    if (
        existing is not None
        and existing.workflow_revision == workflow.row_version
        and existing.request_sha256 == request_sha256
    ):
        return existing

    session.execute(
        update(InteractionRequestRecord)
        .where(
            InteractionRequestRecord.workflow_id == workflow.id,
            InteractionRequestRecord.step_id == SCIENTIFIC_INTERACTION_STEP_ID,
            InteractionRequestRecord.status == "pending",
        )
        .values(status="superseded")
    )
    revision = (
        session.scalar(
            select(InteractionRequestRecord.revision)
            .where(
                InteractionRequestRecord.workflow_id == workflow.id,
                InteractionRequestRecord.request_key == request_key,
            )
            .order_by(InteractionRequestRecord.revision.desc())
        )
        or 0
    ) + 1
    record = InteractionRequestRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        step_id=SCIENTIFIC_INTERACTION_STEP_ID,
        request_key=request_key,
        revision=revision,
        workflow_revision=workflow.row_version,
        request_type=request_type,
        question=request.question,
        options=options,
        required=True,
        status="pending",
        response_schema=response_schema,
        request_sha256=request_sha256,
    )
    session.add(record)
    session.flush()
    append_workflow_events(
        session,
        workflow,
        [
            (
                "interaction.requested",
                InteractionRequestedEventData(
                    interaction_id=record.id,
                    request_type=cast(Any, record.request_type),
                    required=True,
                    expected_workflow_revision=workflow.row_version,
                ),
                None,
                None,
            ),
            (
                "analysis.clarification-requested",
                AnalysisClarificationRequestedEventData(
                    interaction_id=record.id,
                    clarification_type=request.type,
                    selector_input_sha256=selector_input_sha256,
                    selector_output_sha256=selector_output_sha256,
                ),
                None,
                None,
            ),
        ],
    )
    return record


def _interaction_contract(
    request: ScientificClarification,
) -> tuple[str, dict[str, Any]]:
    options = [option.value for option in request.options]
    base: dict[str, Any] = {
        "clarificationType": request.type,
        "semantic": f"scientific:{request.type}",
    }
    if request.type == "group-values":
        return "column-selection", {
            **base,
            "type": "array",
            "items": {"type": "string", "enum": options},
            "minItems": 2,
            "maxItems": 2,
        }
    if request.type in {"outcome-column", "group-column", "x-column", "y-column"}:
        return "column-selection", {**base, "type": "string", "enum": options}
    if request.type == "method-confirmation":
        return "method-confirmation", {**base, "type": "string", "enum": options}
    if request.type == "independence-assumption":
        return "assumption-confirmation", {
            **base,
            "type": "string" if options else "boolean",
            **({"enum": options} if options else {}),
        }
    if request.type == "missing-value-policy":
        return "single-choice", {**base, "type": "string", "enum": options}
    if request.type == "analysis-objective" and options:
        return "single-choice", {**base, "type": "string", "enum": options}
    return "text", {**base, "type": "string", "minLength": 1, "maxLength": 8_000}


def is_scientific_interaction(interaction: InteractionRequestRecord) -> bool:
    return interaction.step_id == SCIENTIFIC_INTERACTION_STEP_ID


__all__: Sequence[str] = (
    "SCIENTIFIC_INTERACTION_STEP_ID",
    "answered_scientific_context",
    "create_scientific_interaction",
    "is_scientific_interaction",
)
