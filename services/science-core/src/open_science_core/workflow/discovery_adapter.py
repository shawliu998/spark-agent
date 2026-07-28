"""Bounded adapter for the pinned ``paper-search-mcp==0.1.4+spark.3`` search tools.

This module deliberately owns no MCP transport. A concrete broker implements
:class:`McpToolBroker` and passes its structured response here. Keeping that
boundary explicit prevents the control plane from claiming that a remote search
happened when no safe bridge is available.

Only provider-specific search tools are exposed.  In particular, unified search,
download, and every Sci-Hub-related tool are intentionally absent from the
allowlist.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping, Protocol, cast
from urllib.parse import urlparse

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    CandidateOccurrenceRecord,
    DiscoveryCandidateRecord,
    DiscoverySpecRecord,
    JobRecord,
    PlanRecord,
    TaskRecord,
    ToolInvocationRecord,
    WorkflowRecord,
    utc_now,
)
from ._service.integrity import WorkflowConflict, assert_plan_approval_integrity
from .discovery_schemas import (
    DISCOVERY_TERMINAL_RESULT_SCHEMA_VERSION,
    DiscoveryCandidate,
    DiscoveryProvider,
    DiscoveryQuery,
    DiscoverySpec,
    DiscoveryTerminalOccurrenceRef,
    DiscoveryTerminalResultProjection,
    discovery_candidate_sha256,
    discovery_sha256,
)

PAPER_SEARCH_CONNECTOR_NAME = "paper-search-mcp"
PAPER_SEARCH_CONNECTOR_VERSION = "0.1.4+spark.3"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RAW_ITEM_BYTES = 128 * 1024

_TOOL_BY_PROVIDER: dict[DiscoveryProvider, str] = {
    "arxiv": "search_arxiv",
    "crossref": "search_crossref",
    "openalex": "search_openalex",
    "pubmed": "search_pubmed",
}
_BROKER_SAFE_RETRY_CODES = frozenset({"connector-unavailable", "rate-limited"})
_SAFE_RETRY_CODES = _BROKER_SAFE_RETRY_CODES | {"prepared-not-sent"}
PAPER_SEARCH_ALLOWED_TOOLS = frozenset(
    {"search_arxiv", "search_crossref", "search_openalex", "search_pubmed"}
)


class McpToolBroker(Protocol):
    """The only bridge this control-plane adapter needs from a runtime MCP client."""

    def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]) -> object:
        """Call an already-approved MCP tool and return its decoded JSON value."""


class KnownMcpToolFailure(Exception):
    """A broker raises this only for a received, classified terminal MCP failure."""

    def __init__(self, code: str, message: str, *, safe_to_retry: bool) -> None:
        super().__init__(message)
        normalized = _bounded_error_code(code)
        # The durable row has an error code but no free-form retry boolean.  Keep
        # its recovery semantics deterministic by encoding an unapproved retry as
        # a permanent code rather than allowing a broker-local disagreement.
        self.safe_to_retry = safe_to_retry and normalized in _BROKER_SAFE_RETRY_CODES
        self.code = (
            normalized
            if self.safe_to_retry or normalized == "provider-disabled"
            else f"{normalized}-permanent"[:100]
        )


class DiscoveryAdapterError(RuntimeError):
    """A local policy or durable-state failure which never sends an MCP request."""


class DiscoveryOutcomeUnknown(DiscoveryAdapterError):
    """A previously sent operation cannot be proven terminal and must not be replayed."""


class DiscoveryOperationInProgress(DiscoveryAdapterError):
    """The same prepared or authorized operation is still owned by its live lease."""


@dataclass(frozen=True, slots=True)
class BoundedMcpRequest:
    tool_name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class DiscoveryOperationObservation:
    """Structured, non-prose result consumed by the later discovery agent loop."""

    invocation_id: str
    query_id: str
    provider: DiscoveryProvider
    status: Literal["succeeded", "failed", "cancelled", "outcome-unknown", "existing"]
    returned_count: int
    novel_candidate_count: int
    duplicate_count: int
    candidate_set_sha256: str | None
    error_code: str | None
    retry_classification: Literal["safe-to-retry", "never-retry", "manual-review"]


def build_paper_search_request(
    query: DiscoveryQuery, provider: DiscoveryProvider
) -> BoundedMcpRequest:
    """Map one approved provider operation to the exact pinned MCP contract.

    The upstream 0.1.4 provider tools do not all expose the same filter surface.
    Unsupported approved constraints are rejected before a network request rather
    than quietly omitted or approximated.
    """

    if provider not in query.providers:
        raise DiscoveryAdapterError("provider is not included in the approved query")

    arguments: dict[str, object] = {
        "query": query.query,
        "max_results": query.max_results_per_provider,
    }
    has_year_filter = query.year_from is not None or query.year_to is not None

    if provider == "arxiv":
        if has_year_filter:
            raise DiscoveryAdapterError("arxiv year filtering is unsupported by pinned MCP")
        arguments.update(
            {
                "sort_by": "relevance" if query.sort == "relevance" else "submittedDate",
                "sort_order": "descending",
            }
        )
    elif provider == "pubmed":
        if has_year_filter:
            raise DiscoveryAdapterError("pubmed year filtering is unsupported by pinned MCP")
        arguments["sort"] = "relevance" if query.sort == "relevance" else "pub_date"
    elif provider == "crossref":
        filters: list[str] = []
        if query.year_from is not None:
            filters.append(f"from-pub-date:{query.year_from:04d}-01-01")
        if query.year_to is not None:
            filters.append(f"until-pub-date:{query.year_to:04d}-12-31")
        if filters:
            arguments["filter"] = ",".join(filters)
        arguments.update(
            {
                "sort": "relevance" if query.sort == "relevance" else "published",
                "order": "desc",
            }
        )
    else:
        # The upstream ``search_openalex`` tool accepts only query and max_results.
        # Its implicit order can only be used for the exact public scope below;
        # any non-default sort or year filter would silently broaden consent.
        if query.sort != "relevance" or has_year_filter:
            raise DiscoveryAdapterError(
                "openalex supports only relevance sort without year filters"
            )

    return BoundedMcpRequest(tool_name=_TOOL_BY_PROVIDER[provider], arguments=arguments)


class PaperSearchAdapter:
    """Persist-before-send bounded execution for one provider-specific search."""

    def execute(
        self,
        session: Session,
        *,
        workflow: WorkflowRecord,
        discovery_spec: DiscoverySpecRecord,
        job: JobRecord,
        query_id: str,
        provider: DiscoveryProvider,
        attempt: int,
        lease_token: str,
        broker: McpToolBroker,
    ) -> DiscoveryOperationObservation:
        if attempt < 1:
            raise DiscoveryAdapterError("discovery attempt must be positive")
        _validate_lease_binding(workflow, discovery_spec, job, attempt, lease_token)
        spec = DiscoverySpec.model_validate(discovery_spec.spec_json)
        if discovery_sha256(spec) != discovery_spec.spec_sha256:
            raise DiscoveryAdapterError("discovery specification hash does not match its payload")
        if discovery_spec.status != "approved":
            raise DiscoveryAdapterError("discovery specification is not approved")
        query = next((item for item in spec.queries if item.id == query_id), None)
        if query is None:
            raise DiscoveryAdapterError(
                "query is not included in the approved discovery specification"
            )
        if provider not in query.providers:
            raise DiscoveryAdapterError("provider is not included in the approved query")

        operation_key = discovery_operation_key(discovery_spec.id, query.id, provider)
        task, plan = _validate_task_binding(
            session,
            workflow=workflow,
            discovery_spec=discovery_spec,
            job=job,
            query_id=query.id,
            provider=provider,
            operation_key=operation_key,
        )
        existing = session.scalar(
            select(ToolInvocationRecord).where(
                ToolInvocationRecord.workflow_id == workflow.id,
                ToolInvocationRecord.operation_key == operation_key,
                ToolInvocationRecord.attempt == attempt,
            )
        )
        if existing is not None:
            return _existing_observation(session, existing)
        _assert_attempt_is_safe(session, workflow.id, operation_key, attempt)

        try:
            request = build_paper_search_request(query, provider)
        except DiscoveryAdapterError as error:
            invocation = _new_invocation(
                workflow=workflow,
                discovery_spec=discovery_spec,
                job=job,
                query_id=query.id,
                provider=provider,
                operation_key=operation_key,
                attempt=attempt,
                request={"preflight": "rejected"},
                tool_name=_TOOL_BY_PROVIDER[provider],
            )
            session.add(invocation)
            _mark_failure(invocation, "policy-rejected", str(error))
            existing_observation = _commit_new_invocation_or_existing(session, invocation)
            if existing_observation is not None:
                return existing_observation
            return _failure_observation(invocation, retry_classification="never-retry")

        invocation = _new_invocation(
            workflow=workflow,
            discovery_spec=discovery_spec,
            job=job,
            query_id=query.id,
            provider=provider,
            operation_key=operation_key,
            attempt=attempt,
            request={"toolName": request.tool_name, "arguments": request.arguments},
            tool_name=request.tool_name,
        )
        session.add(invocation)
        # A prepared identity proves that no external request is yet authorized.
        existing_observation = _commit_new_invocation_or_existing(session, invocation)
        if existing_observation is not None:
            return existing_observation
        _begin_locked_transition(session, invocation.id, expected_status="prepared")
        try:
            workflow, discovery_spec, job, task, plan = _reload_authority(
                session,
                workflow_id=workflow.id,
                discovery_spec_id=discovery_spec.id,
                job_id=job.id,
                task_id=task.id,
                plan_id=plan.id,
                reset_transaction=False,
            )
            _validate_lease_binding(workflow, discovery_spec, job, attempt, lease_token)
            _validate_loaded_task_binding(
                session=session,
                workflow=workflow,
                discovery_spec=discovery_spec,
                job=job,
                task=task,
                plan=plan,
                query_id=query.id,
                provider=provider,
                operation_key=operation_key,
            )
            _validate_current_spec(discovery_spec)
            invocation = session.get_one(ToolInvocationRecord, invocation.id)
            invocation.status = "pending"
            # ``pending`` is the durable sent-authorized point.  Cancellation
            # after this commit cannot retroactively revoke a request that has
            # already been authorized for dispatch.
            session.commit()
        except DiscoveryAdapterError as error:
            if workflow.cancel_requested_at is not None:
                _mark_cancelled(invocation)
            else:
                _mark_failure(invocation, "lease-or-policy-lost", str(error))
            session.commit()
            return _terminal_observation(invocation)

        try:
            raw_response = broker.call_tool(
                tool_name=request.tool_name,
                arguments=request.arguments,
            )
        except BaseException as error:
            if not isinstance(error, Exception):
                raise
            broker_error: Exception | None = error
            raw_response = None
        else:
            broker_error = None

        # The call may outlive its lease or be cancelled while in flight.  End
        # the pre-send read transaction and re-read every authority record before
        # any candidate or success state is persisted.
        _begin_locked_transition(session, invocation.id, expected_status="pending")
        try:
            workflow, discovery_spec, job, task, plan = _reload_authority(
                session,
                workflow_id=workflow.id,
                discovery_spec_id=discovery_spec.id,
                job_id=job.id,
                task_id=task.id,
                plan_id=plan.id,
                reset_transaction=False,
            )
            _validate_lease_binding(workflow, discovery_spec, job, attempt, lease_token)
            _validate_loaded_task_binding(
                session=session,
                workflow=workflow,
                discovery_spec=discovery_spec,
                job=job,
                task=task,
                plan=plan,
                query_id=query.id,
                provider=provider,
                operation_key=operation_key,
            )
            _validate_current_spec(discovery_spec)
        except DiscoveryAdapterError as error:
            if workflow.cancel_requested_at is not None:
                _mark_cancelled(invocation)
            else:
                _mark_failure(invocation, "lease-or-policy-lost", str(error))
            session.commit()
            return _terminal_observation(invocation)

        if isinstance(broker_error, KnownMcpToolFailure):
            _mark_failure(invocation, broker_error.code, str(broker_error))
            session.commit()
            return _failure_observation(
                invocation,
                retry_classification=(
                    "safe-to-retry" if broker_error.safe_to_retry else "never-retry"
                ),
            )
        if broker_error is not None:
            # A transport interruption provides no proof that the remote search did
            # not happen.  Preserve it as unknown and never auto-replay it.
            _mark_failure(
                invocation,
                "outcome-unknown",
                "The paper-search connector outcome is unknown.",
                unknown=True,
            )
            session.commit()
            return _unknown_observation(invocation)

        try:
            candidates, raw_hashes, output_sha256, candidate_set_sha256 = _normalize_response(
                raw_response,
                provider=provider,
                max_results=query.max_results_per_provider,
                query_text=query.query,
            )
        except DiscoveryAdapterError as error:
            _mark_failure(invocation, "malformed-output", str(error))
            session.commit()
            return _failure_observation(invocation, retry_classification="never-retry")

        novel_count, duplicate_count = persist_discovery_candidates(
            session,
            invocation=invocation,
            project_id=workflow.project_id,
            candidates=candidates,
            raw_hashes=raw_hashes,
        )
        invocation.output_sha256 = output_sha256
        invocation.returned_count = len(candidates)
        invocation.novel_candidate_count = novel_count
        invocation.duplicate_count = duplicate_count
        invocation.candidate_set_sha256 = candidate_set_sha256
        invocation.status = "succeeded"
        invocation.finished_at = utc_now()
        # Persist a complete, independently verifiable terminal anchor in the
        # same transaction as the invocation and its candidate occurrences.
        # Recovery may then settle this lease without replaying the provider.
        session.flush()
        task.outputs = discovery_terminal_task_outputs(
            session,
            invocation_id=invocation.id,
        )
        session.commit()
        return DiscoveryOperationObservation(
            invocation_id=invocation.id,
            query_id=invocation.query_id,
            provider=provider,
            status="succeeded",
            returned_count=len(candidates),
            novel_candidate_count=novel_count,
            duplicate_count=duplicate_count,
            candidate_set_sha256=candidate_set_sha256,
            error_code=None,
            retry_classification="never-retry",
        )


def _validate_lease_binding(
    workflow: WorkflowRecord,
    discovery_spec: DiscoverySpecRecord,
    job: JobRecord,
    attempt: int,
    lease_token: str,
) -> None:
    if discovery_spec.workflow_id != workflow.id:
        raise DiscoveryAdapterError("discovery specification does not belong to workflow")
    if job.workflow_id != workflow.id:
        raise DiscoveryAdapterError("job does not belong to workflow")
    if job.kind != "execute-task":
        raise DiscoveryAdapterError("discovery search requires a materialized execute-task job")
    if job.status != "leased" or not lease_token or job.lease_token != lease_token:
        raise DiscoveryAdapterError("discovery job lease is not current")
    if job.lease_expires_at is None or _as_utc(job.lease_expires_at) <= utc_now():
        raise DiscoveryAdapterError("discovery job lease has expired")
    if job.attempt != attempt:
        raise DiscoveryAdapterError("discovery invocation attempt does not match job attempt")
    if workflow.cancel_requested_at is not None:
        raise DiscoveryAdapterError("workflow cancellation was requested")


def _validate_task_binding(
    session: Session,
    *,
    workflow: WorkflowRecord,
    discovery_spec: DiscoverySpecRecord,
    job: JobRecord,
    query_id: str,
    provider: DiscoveryProvider,
    operation_key: str,
) -> tuple[TaskRecord, PlanRecord]:
    if job.task_id is None:
        raise DiscoveryAdapterError("discovery job must reference a materialized task")
    task = session.get(TaskRecord, job.task_id)
    if task is None or task.plan_id is None:
        raise DiscoveryAdapterError("discovery task or approved plan is missing")
    plan = session.get(PlanRecord, task.plan_id)
    if plan is None:
        raise DiscoveryAdapterError("discovery task approved plan is missing")
    _validate_loaded_task_binding(
        session=session,
        workflow=workflow,
        discovery_spec=discovery_spec,
        job=job,
        task=task,
        plan=plan,
        query_id=query_id,
        provider=provider,
        operation_key=operation_key,
    )
    return task, plan


def _validate_loaded_task_binding(
    *,
    session: Session,
    workflow: WorkflowRecord,
    discovery_spec: DiscoverySpecRecord,
    job: JobRecord,
    task: TaskRecord,
    plan: PlanRecord,
    query_id: str,
    provider: DiscoveryProvider,
    operation_key: str,
    require_running: bool = True,
) -> None:
    try:
        assert_plan_approval_integrity(session, workflow, plan)
    except WorkflowConflict as error:
        raise DiscoveryAdapterError(
            "discovery task plan has no matching immutable approval record"
        ) from error
    spec = DiscoverySpec.model_validate(discovery_spec.spec_json)
    operations = discovery_operations(discovery_spec, spec)
    expected_order_index = next(
        (
            index
            for index, operation in enumerate(operations, start=1)
            if operation.query.id == query_id and operation.provider == provider
        ),
        None,
    )
    if expected_order_index is None:
        raise DiscoveryAdapterError("query-provider operation is not in the approved plan")
    expected_input = discovery_task_input(discovery_spec, query_id, provider)
    expected_job_hash = _canonical_sha256(expected_input)
    expected_step_key = discovery_step_key(query_id, provider)
    expected_objective = f"Search {provider} for approved query {query_id}."
    expected_task_hash = _canonical_sha256(
        {
            "inputs": expected_input,
            "objective": expected_objective,
            "stepKey": expected_step_key,
            "stepType": "paper-discovery",
        }
    )
    expected_outputs = ["discovery-observation"]
    expected_criteria = ["persist-structured-discovery-observation"]
    expected_permissions = ["remote-paper-search"]
    expected_plan_spec = discovery_plan_spec(discovery_spec, spec)
    if (
        task.id != job.task_id
        or task.project_id != workflow.project_id
        or task.workflow_id != workflow.id
        or task.plan_id != plan.id
        or task.step_key != expected_step_key
        or task.order_index != expected_order_index
        or task.objective != expected_objective
        or task.task_type != "paper-discovery"
        or task.inputs != expected_input
        or task.expected_outputs != expected_outputs
        or task.acceptance_criteria != expected_criteria
        or task.permissions != expected_permissions
        or task.risk_level != "medium"
        or task.timeout_seconds != 120
        or task.input_sha256 != expected_task_hash
        or job.workflow_id != workflow.id
        or job.kind != "execute-task"
        or job.attempt < 1
    ):
        raise DiscoveryAdapterError("job is not bound to the current discovery task")
    if require_running and (task.status != "running" or task.outputs != {}):
        raise DiscoveryAdapterError("job is not bound to the current discovery task")
    if (
        plan.workflow_id != workflow.id
        or plan.status != "approved"
        or plan.spec_json != expected_plan_spec
        or plan.spec_sha256 != _canonical_sha256(expected_plan_spec)
    ):
        raise DiscoveryAdapterError("discovery task plan is not currently approved")
    if job.operation_key != operation_key or job.input_sha256 != expected_job_hash:
        raise DiscoveryAdapterError("job is not bound to the approved discovery operation")


def validate_terminal_discovery_invocation(
    session: Session,
    *,
    workflow: WorkflowRecord,
    discovery_spec: DiscoverySpecRecord,
    invocation: ToolInvocationRecord,
    expected_plan: PlanRecord | None = None,
    expected_task: TaskRecord | None = None,
    allow_unsettled: bool = False,
) -> None:
    """Validate every durable fact consumed from a terminal Discovery invocation."""

    if invocation.status not in {
        "succeeded",
        "failed",
        "outcome-unknown",
        "cancelled",
    }:
        raise DiscoveryAdapterError("discovery invocation is not terminal")
    if (
        invocation.project_id != workflow.project_id
        or invocation.workflow_id != workflow.id
        or invocation.discovery_spec_id != discovery_spec.id
        or discovery_spec.workflow_id != workflow.id
    ):
        raise DiscoveryAdapterError("terminal discovery invocation ownership is invalid")
    _validate_current_spec(discovery_spec)
    if invocation.provider not in _TOOL_BY_PROVIDER:
        raise DiscoveryAdapterError("terminal discovery invocation provider is invalid")
    provider = invocation.provider
    operation_key = discovery_operation_key(
        discovery_spec.id,
        invocation.query_id,
        provider,
    )
    if (
        invocation.operation_key != operation_key
        or invocation.tool_name != _TOOL_BY_PROVIDER[provider]
        or invocation.connector_name != PAPER_SEARCH_CONNECTOR_NAME
        or invocation.connector_version != PAPER_SEARCH_CONNECTOR_VERSION
        or invocation.attempt < 1
    ):
        raise DiscoveryAdapterError("terminal discovery invocation identity is invalid")

    job = session.get(JobRecord, invocation.job_id)
    if job is None or job.task_id is None:
        raise DiscoveryAdapterError("terminal discovery invocation job is missing")
    task = session.get(TaskRecord, job.task_id)
    if task is None or task.plan_id is None:
        raise DiscoveryAdapterError("terminal discovery invocation task is missing")
    plan = session.get(PlanRecord, task.plan_id)
    if plan is None:
        raise DiscoveryAdapterError("terminal discovery invocation plan is missing")
    if (
        expected_plan is not None and plan.id != expected_plan.id
    ) or (
        expected_task is not None and task.id != expected_task.id
    ):
        raise DiscoveryAdapterError("terminal discovery invocation authority is stale")
    _validate_loaded_task_binding(
        session=session,
        workflow=workflow,
        discovery_spec=discovery_spec,
        job=job,
        task=task,
        plan=plan,
        query_id=invocation.query_id,
        provider=provider,
        operation_key=operation_key,
        require_running=False,
    )
    if job.attempt != invocation.attempt:
        raise DiscoveryAdapterError("terminal invocation attempt does not match its job")

    authorization_hash = invocation.request_json.get(
        "authorizationLeaseTokenSha256"
    )
    if not isinstance(authorization_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        authorization_hash,
    ):
        raise DiscoveryAdapterError("terminal invocation authorization hash is invalid")
    request_without_authorization = dict(invocation.request_json)
    request_without_authorization.pop("authorizationLeaseTokenSha256", None)
    try:
        request = build_paper_search_request(
            next(
                query
                for query in DiscoverySpec.model_validate(
                    discovery_spec.spec_json
                ).queries
                if query.id == invocation.query_id
            ),
            provider,
        )
    except (DiscoveryAdapterError, StopIteration):
        expected_request: dict[str, object] = {"preflight": "rejected"}
        if invocation.status == "succeeded":
            raise DiscoveryAdapterError(
                "successful invocation cannot originate from rejected preflight"
            ) from None
    else:
        expected_request = {
            "toolName": request.tool_name,
            "arguments": request.arguments,
        }
    if request_without_authorization != expected_request:
        raise DiscoveryAdapterError("terminal invocation request is not canonical")
    request_sha256 = _canonical_sha256(invocation.request_json)
    if (
        invocation.request_payload_sha256 != request_sha256
        or invocation.request_idempotency_key
        != _canonical_sha256(
            {
                "workflowId": workflow.id,
                "operationKey": operation_key,
                "attempt": invocation.attempt,
                "requestSha256": request_sha256,
            }
        )
    ):
        raise DiscoveryAdapterError("terminal invocation request identity is invalid")

    occurrences = list(
        session.scalars(
            select(CandidateOccurrenceRecord)
            .where(
                CandidateOccurrenceRecord.project_id == workflow.project_id,
                CandidateOccurrenceRecord.invocation_id == invocation.id,
            )
            .order_by(CandidateOccurrenceRecord.rank)
        )
    )
    if invocation.status == "succeeded":
        _validate_successful_discovery_invocation(
            session,
            invocation=invocation,
            job=job,
        task=task,
        occurrences=occurrences,
        allow_unsettled=allow_unsettled,
        )
    else:
        if occurrences:
            raise DiscoveryAdapterError(
                "non-successful invocation cannot own candidate occurrences"
            )
        if (
            invocation.output_sha256 is not None
            or invocation.candidate_set_sha256 is not None
            or invocation.returned_count != 0
            or invocation.novel_candidate_count != 0
            or invocation.duplicate_count != 0
            or invocation.finished_at is None
            or (
                invocation.status in {"failed", "outcome-unknown"}
                and invocation.error_code is None
            )
            or (
                invocation.status == "cancelled"
                and invocation.error_code is not None
            )
        ):
            raise DiscoveryAdapterError("terminal failure counters are invalid")
        if allow_unsettled and job.status == "leased":
            if task.status != "running" or task.outputs != {}:
                raise DiscoveryAdapterError(
                    "terminal failure active state is invalid"
                )
        elif job.status == "failed":
            if task.status not in {"failed", "blocked", "completed"}:
                raise DiscoveryAdapterError(
                    "terminal failure task state is invalid"
                )
        else:
            raise DiscoveryAdapterError("terminal failure job state is invalid")


def validate_recoverable_discovery_invocation(
    session: Session,
    *,
    workflow: WorkflowRecord,
    task: TaskRecord,
    job: JobRecord,
    invocation: ToolInvocationRecord,
    lease_token: str,
) -> DiscoverySpecRecord:
    """Validate a prepared/pending invocation before recovery may mutate it."""

    if invocation.status not in {"prepared", "pending"}:
        raise DiscoveryAdapterError("discovery invocation is not recoverable")
    discovery_spec = session.get(DiscoverySpecRecord, invocation.discovery_spec_id)
    if (
        discovery_spec is None
        or invocation.project_id != workflow.project_id
        or invocation.workflow_id != workflow.id
        or invocation.job_id != job.id
        or invocation.attempt != job.attempt
        or job.status != "leased"
        or job.lease_token != lease_token
        or task.id != job.task_id
    ):
        raise DiscoveryAdapterError("recoverable discovery invocation ownership is invalid")
    _validate_current_spec(discovery_spec)
    if invocation.provider not in _TOOL_BY_PROVIDER:
        raise DiscoveryAdapterError("recoverable discovery invocation provider is invalid")
    provider = invocation.provider
    operation_key = discovery_operation_key(discovery_spec.id, invocation.query_id, provider)
    if (
        invocation.operation_key != operation_key
        or invocation.tool_name != _TOOL_BY_PROVIDER[provider]
        or invocation.connector_name != PAPER_SEARCH_CONNECTOR_NAME
        or invocation.connector_version != PAPER_SEARCH_CONNECTOR_VERSION
    ):
        raise DiscoveryAdapterError("recoverable discovery invocation identity is invalid")
    plan = session.get(PlanRecord, task.plan_id) if task.plan_id is not None else None
    if plan is None:
        raise DiscoveryAdapterError("recoverable discovery invocation plan is missing")
    _validate_loaded_task_binding(
        session=session,
        workflow=workflow,
        discovery_spec=discovery_spec,
        job=job,
        task=task,
        plan=plan,
        query_id=invocation.query_id,
        provider=provider,
        operation_key=operation_key,
    )
    _validate_invocation_request(discovery_spec, invocation, operation_key, provider)
    if (
        invocation.output_sha256 is not None
        or invocation.returned_count is not None
        or invocation.novel_candidate_count is not None
        or invocation.duplicate_count is not None
        or invocation.candidate_set_sha256 is not None
        or invocation.error_code is not None
        or invocation.error_message is not None
        or invocation.finished_at is not None
        or session.scalar(
            select(CandidateOccurrenceRecord.candidate_id).where(
                CandidateOccurrenceRecord.project_id == workflow.project_id,
                CandidateOccurrenceRecord.invocation_id == invocation.id,
            )
        )
        is not None
    ):
        raise DiscoveryAdapterError("recoverable discovery invocation state is invalid")
    return discovery_spec


def _validate_invocation_request(
    discovery_spec: DiscoverySpecRecord,
    invocation: ToolInvocationRecord,
    operation_key: str,
    provider: DiscoveryProvider,
) -> None:
    authorization_hash = invocation.request_json.get("authorizationLeaseTokenSha256")
    if not isinstance(authorization_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", authorization_hash):
        raise DiscoveryAdapterError("terminal invocation authorization hash is invalid")
    request_without_authorization = dict(invocation.request_json)
    request_without_authorization.pop("authorizationLeaseTokenSha256", None)
    try:
        request = build_paper_search_request(
            next(
                query
                for query in DiscoverySpec.model_validate(discovery_spec.spec_json).queries
                if query.id == invocation.query_id
            ),
            provider,
        )
    except (DiscoveryAdapterError, StopIteration):
        expected_request: dict[str, object] = {"preflight": "rejected"}
        if invocation.status == "succeeded":
            raise DiscoveryAdapterError(
                "successful invocation cannot originate from rejected preflight"
            ) from None
    else:
        expected_request = {"toolName": request.tool_name, "arguments": request.arguments}
    if request_without_authorization != expected_request:
        raise DiscoveryAdapterError("terminal invocation request is not canonical")
    request_sha256 = _canonical_sha256(invocation.request_json)
    if (
        invocation.request_payload_sha256 != request_sha256
        or invocation.request_idempotency_key
        != _canonical_sha256(
            {
                "workflowId": invocation.workflow_id,
                "operationKey": operation_key,
                "attempt": invocation.attempt,
                "requestSha256": request_sha256,
            }
        )
    ):
        raise DiscoveryAdapterError("terminal invocation request identity is invalid")


def discovery_terminal_task_outputs(
    session: Session,
    *,
    invocation_id: str,
) -> dict[str, object]:
    """Build the independent task-output anchor for a successful invocation."""

    invocation = session.get(ToolInvocationRecord, invocation_id)
    if invocation is None or invocation.status != "succeeded":
        raise DiscoveryAdapterError(
            "terminal result anchor requires a successful invocation"
        )
    occurrences = _terminal_occurrences(session, invocation)
    projection = _build_terminal_result_projection(
        session,
        invocation=invocation,
        occurrences=occurrences,
    )
    projection_json = projection.model_dump(mode="json", by_alias=True)
    return {
        "invocationId": invocation.id,
        "returnedCount": projection.returned_count,
        "novelCandidateCount": projection.novel_candidate_count,
        "duplicateCount": projection.duplicate_count,
        "candidateSetSha256": projection.candidate_set_sha256,
        "terminalResult": projection_json,
        "terminalResultSha256": discovery_sha256(projection),
    }


def _terminal_occurrences(
    session: Session,
    invocation: ToolInvocationRecord,
) -> list[CandidateOccurrenceRecord]:
    return list(
        session.scalars(
            select(CandidateOccurrenceRecord)
            .where(
                CandidateOccurrenceRecord.project_id == invocation.project_id,
                CandidateOccurrenceRecord.invocation_id == invocation.id,
            )
            .order_by(
                CandidateOccurrenceRecord.rank,
                CandidateOccurrenceRecord.candidate_id,
            )
        )
    )


def _build_terminal_result_projection(
    session: Session,
    *,
    invocation: ToolInvocationRecord,
    occurrences: list[CandidateOccurrenceRecord],
) -> DiscoveryTerminalResultProjection:
    returned = invocation.returned_count
    novel = invocation.novel_candidate_count
    duplicates = invocation.duplicate_count
    if any(
        not isinstance(value, int) or value < 0
        for value in (returned, novel, duplicates)
    ):
        raise DiscoveryAdapterError("successful invocation terminal fields are invalid")
    returned_count = cast(int, returned)
    novel_count = cast(int, novel)
    duplicate_count = cast(int, duplicates)
    if (
        returned_count != novel_count + duplicate_count
        or invocation.output_sha256 is None
        or invocation.candidate_set_sha256 is None
        or not re.fullmatch(r"[0-9a-f]{64}", invocation.output_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", invocation.candidate_set_sha256)
        or invocation.error_code is not None
        or invocation.error_message is not None
        or invocation.finished_at is None
    ):
        raise DiscoveryAdapterError("successful invocation terminal fields are invalid")
    if len(occurrences) != returned_count:
        raise DiscoveryAdapterError("candidate occurrence counts are inconsistent")

    candidate_hashes: list[str] = []
    occurrence_refs: list[DiscoveryTerminalOccurrenceRef] = []
    for occurrence in occurrences:
        candidate = session.get(DiscoveryCandidateRecord, occurrence.candidate_id)
        if (
            occurrence.project_id != invocation.project_id
            or occurrence.rank < 1
            or occurrence.rank > returned_count
            or not re.fullmatch(r"[0-9a-f]{64}", occurrence.raw_item_sha256)
            or candidate is None
            or candidate.project_id != invocation.project_id
            or candidate.metadata_json.get("trustClassification")
            != "untrusted-metadata"
        ):
            raise DiscoveryAdapterError("candidate occurrence ownership is invalid")
        try:
            parsed_candidate = DiscoveryCandidate.model_validate(
                candidate.metadata_json.get("candidate"),
                strict=True,
            )
        except ValidationError as error:
            raise DiscoveryAdapterError(
                "candidate occurrence metadata is invalid"
            ) from error
        if (
            discovery_candidate_sha256(parsed_candidate)
            != candidate.candidate_sha256
            or normalized_discovery_candidate_identity(parsed_candidate)
            != candidate.normalized_identity
        ):
            raise DiscoveryAdapterError("candidate occurrence hash is invalid")
        candidate_hashes.append(candidate.candidate_sha256)
        occurrence_refs.append(
            DiscoveryTerminalOccurrenceRef(
                rank=occurrence.rank,
                candidate_id=candidate.id,
                candidate_sha256=candidate.candidate_sha256,
                raw_item_sha256=occurrence.raw_item_sha256,
            )
        )
    if invocation.candidate_set_sha256 != (
        _canonical_sha256({"candidateHashes": sorted(candidate_hashes)})
    ):
        raise DiscoveryAdapterError("candidate occurrence set hash is invalid")
    try:
        return DiscoveryTerminalResultProjection(
            schema_version=DISCOVERY_TERMINAL_RESULT_SCHEMA_VERSION,
            invocation_id=invocation.id,
            job_id=invocation.job_id,
            operation_key=invocation.operation_key,
            attempt=invocation.attempt,
            output_sha256=invocation.output_sha256,
            returned_count=returned_count,
            novel_candidate_count=novel_count,
            duplicate_count=duplicate_count,
            occurrence_count=len(occurrence_refs),
            occurrences=occurrence_refs,
            candidate_set_sha256=invocation.candidate_set_sha256,
        )
    except ValidationError as error:
        raise DiscoveryAdapterError(
            "successful invocation terminal projection is invalid"
        ) from error


def _validate_successful_discovery_invocation(
    session: Session,
    *,
    invocation: ToolInvocationRecord,
    job: JobRecord,
    task: TaskRecord,
    occurrences: list[CandidateOccurrenceRecord],
    allow_unsettled: bool = False,
) -> None:
    projection = _build_terminal_result_projection(
        session,
        invocation=invocation,
        occurrences=occurrences,
    )
    projection_json = projection.model_dump(mode="json", by_alias=True)
    expected_outputs: dict[str, object] = {
        "invocationId": invocation.id,
        "returnedCount": projection.returned_count,
        "novelCandidateCount": projection.novel_candidate_count,
        "duplicateCount": projection.duplicate_count,
        "candidateSetSha256": projection.candidate_set_sha256,
        "terminalResult": projection_json,
        "terminalResultSha256": discovery_sha256(projection),
    }
    if allow_unsettled:
        if (
            job.status != "leased"
            or task.status != "running"
            or task.outputs != expected_outputs
        ):
            raise DiscoveryAdapterError("successful invocation recovery state is invalid")
        return
    stored_projection_json = task.outputs.get("terminalResult")
    stored_projection_sha256 = task.outputs.get("terminalResultSha256")
    try:
        stored_projection = DiscoveryTerminalResultProjection.model_validate(
            stored_projection_json,
            strict=True,
        )
    except ValidationError as error:
        raise DiscoveryAdapterError(
            "successful invocation terminal result anchor is missing or invalid"
        ) from error
    if (
        not isinstance(stored_projection_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", stored_projection_sha256)
        or discovery_sha256(stored_projection) != stored_projection_sha256
        or stored_projection != projection
    ):
        raise DiscoveryAdapterError(
            "successful invocation terminal result anchor is invalid"
        )
    if (
        task.status != "completed"
        or job.status != "succeeded"
        or task.outputs != expected_outputs
    ):
        raise DiscoveryAdapterError("successful invocation task outputs are invalid")


@dataclass(frozen=True, slots=True)
class DiscoveryOperation:
    query: DiscoveryQuery
    provider: DiscoveryProvider


def discovery_step_key(query_id: str, provider: DiscoveryProvider) -> str:
    """Return an Agent-loop compatible task identity for one approved operation."""

    return f"paper-discovery-{query_id}-{provider}"


def discovery_task_input(
    discovery_spec: DiscoverySpecRecord,
    query_id: str,
    provider: DiscoveryProvider,
) -> dict[str, object]:
    spec = DiscoverySpec.model_validate(discovery_spec.spec_json)
    query = next((item for item in spec.queries if item.id == query_id), None)
    if query is None or provider not in query.providers:
        raise DiscoveryAdapterError(
            "query-provider operation is not in the discovery specification"
        )
    return {
        "schemaVersion": "1",
        "discoverySpecId": discovery_spec.id,
        "discoverySpecRevision": discovery_spec.revision,
        "discoverySpecSha256": discovery_spec.spec_sha256,
        "queryId": query_id,
        "query": query.query,
        "provider": provider,
        "yearFrom": query.year_from,
        "yearTo": query.year_to,
        "sort": query.sort,
        "maxResultsPerProvider": query.max_results_per_provider,
        "derivedMaximumResults": query.max_results_per_provider,
        "stopPolicy": spec.stop_policy.model_dump(mode="json", by_alias=True),
        "downloadOpenAccessPdfs": spec.download_open_access_pdfs,
        "maxPdfDownloads": spec.max_pdf_downloads,
    }


def discovery_operations(
    discovery_spec: DiscoverySpecRecord,
    spec: DiscoverySpec,
) -> tuple[DiscoveryOperation, ...]:
    """Freeze the user-approved query/provider order before any request is sent."""

    if discovery_spec.status not in {"pending-approval", "approved"}:
        raise DiscoveryAdapterError("discovery specification is not current")
    return tuple(
        DiscoveryOperation(query=query, provider=provider)
        for query in spec.queries
        for provider in query.providers
    )


def discovery_plan_spec(
    discovery_spec: DiscoverySpecRecord,
    spec: DiscoverySpec,
) -> dict[str, object]:
    """Build the exact immutable multi-task plan bound to an approved spec."""

    steps: list[dict[str, object]] = []
    for order_index, operation in enumerate(discovery_operations(discovery_spec, spec), start=1):
        query_id = operation.query.id
        provider = operation.provider
        steps.append(
            {
                "key": discovery_step_key(query_id, provider),
                "orderIndex": order_index,
                "objective": f"Search {provider} for approved query {query_id}.",
                "taskType": "paper-discovery",
                "inputs": discovery_task_input(discovery_spec, query_id, provider),
                "expectedOutputs": ["discovery-observation"],
                "acceptanceCriteria": ["persist-structured-discovery-observation"],
                "permissions": ["remote-paper-search"],
                "riskLevel": "medium",
                "timeoutSeconds": 120,
            }
        )
    return {
        "schemaVersion": "1",
        "planType": "paper-discovery",
        "goal": spec.question,
        "discoverySpecId": discovery_spec.id,
        "discoverySpecRevision": discovery_spec.revision,
        "discoverySpecSha256": discovery_spec.spec_sha256,
        "steps": steps,
    }


def _reload_authority(
    session: Session,
    *,
    workflow_id: str,
    discovery_spec_id: str,
    job_id: str,
    task_id: str,
    plan_id: str,
    reset_transaction: bool = True,
) -> tuple[WorkflowRecord, DiscoverySpecRecord, JobRecord, TaskRecord, PlanRecord]:
    if reset_transaction:
        session.rollback()
    session.expire_all()
    records = (
        session.get(WorkflowRecord, workflow_id),
        session.get(DiscoverySpecRecord, discovery_spec_id),
        session.get(JobRecord, job_id),
        session.get(TaskRecord, task_id),
        session.get(PlanRecord, plan_id),
    )
    if any(record is None for record in records):
        raise DiscoveryAdapterError("discovery authority record disappeared")
    return cast(
        tuple[WorkflowRecord, DiscoverySpecRecord, JobRecord, TaskRecord, PlanRecord],
        records,
    )


def _validate_current_spec(discovery_spec: DiscoverySpecRecord) -> None:
    if discovery_spec.status != "approved":
        raise DiscoveryAdapterError("discovery specification approval is no longer current")
    try:
        current = DiscoverySpec.model_validate(discovery_spec.spec_json)
    except ValidationError as error:
        raise DiscoveryAdapterError("discovery specification payload is invalid") from error
    if discovery_sha256(current) != discovery_spec.spec_sha256:
        raise DiscoveryAdapterError("discovery specification hash changed")


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _begin_locked_transition(
    session: Session,
    invocation_id: str,
    *,
    expected_status: Literal["prepared", "pending"],
) -> None:
    """Acquire SQLite's writer lock before re-reading every authority row."""

    session.rollback()
    session.expire_all()
    result = session.connection().execute(
        update(ToolInvocationRecord)
        .where(
            ToolInvocationRecord.id == invocation_id,
            ToolInvocationRecord.status == expected_status,
        )
        .values(status=expected_status)
    )
    if result.rowcount != 1:
        raise DiscoveryAdapterError("discovery invocation transition is no longer current")
    session.expire_all()


def discovery_operation_key(
    spec_id: str,
    query_id: str,
    provider: DiscoveryProvider,
) -> str:
    """Return the canonical durable identity for one approved discovery operation."""

    return f"discovery:{spec_id}:{query_id}:{provider}"


def _assert_attempt_is_safe(
    session: Session, workflow_id: str, operation_key: str, attempt: int
) -> None:
    previous = list(
        session.scalars(
            select(ToolInvocationRecord)
            .where(
                ToolInvocationRecord.workflow_id == workflow_id,
                ToolInvocationRecord.operation_key == operation_key,
            )
            .order_by(ToolInvocationRecord.attempt.desc())
        )
    )
    if not previous:
        if attempt != 1:
            raise DiscoveryAdapterError("first discovery attempt must be one")
        return
    latest = previous[0]
    if latest.status in {"prepared", "pending"}:
        _recover_stale_pending(session, latest)
        if latest.status == "outcome-unknown":
            raise DiscoveryOutcomeUnknown("a prior discovery operation has an unknown outcome")
    if latest.status == "outcome-unknown":
        raise DiscoveryOutcomeUnknown("a prior discovery operation has an unknown outcome")
    if attempt <= latest.attempt:
        return
    if attempt != latest.attempt + 1:
        raise DiscoveryAdapterError("discovery retry attempts must be contiguous")
    if latest.status != "failed" or latest.error_code not in _SAFE_RETRY_CODES:
        raise DiscoveryAdapterError("prior discovery failure is not safe to retry")


def _new_invocation(
    *,
    workflow: WorkflowRecord,
    discovery_spec: DiscoverySpecRecord,
    job: JobRecord,
    query_id: str,
    provider: DiscoveryProvider,
    operation_key: str,
    attempt: int,
    request: Mapping[str, object],
    tool_name: str,
) -> ToolInvocationRecord:
    lease_token = job.lease_token
    if lease_token is None:
        raise DiscoveryAdapterError("discovery invocation requires a current lease token")
    durable_request = {
        **dict(request),
        "authorizationLeaseTokenSha256": hashlib.sha256(lease_token.encode()).hexdigest(),
    }
    request_hash = _canonical_sha256(durable_request)
    return ToolInvocationRecord(
        id=str(uuid.uuid4()),
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        discovery_spec_id=discovery_spec.id,
        job_id=job.id,
        schema_version="1",
        tool_name=tool_name,
        connector_name=PAPER_SEARCH_CONNECTOR_NAME,
        connector_version=PAPER_SEARCH_CONNECTOR_VERSION,
        query_id=query_id,
        provider=provider,
        operation_key=operation_key,
        attempt=attempt,
        request_idempotency_key=_canonical_sha256(
            {
                "workflowId": workflow.id,
                "operationKey": operation_key,
                "attempt": attempt,
                "requestSha256": request_hash,
            }
        ),
        request_payload_sha256=request_hash,
        request_json=durable_request,
        status="prepared",
    )


def _commit_new_invocation_or_existing(
    session: Session, invocation: ToolInvocationRecord
) -> DiscoveryOperationObservation | None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(ToolInvocationRecord).where(
                ToolInvocationRecord.workflow_id == invocation.workflow_id,
                ToolInvocationRecord.operation_key == invocation.operation_key,
                ToolInvocationRecord.attempt == invocation.attempt,
            )
        )
        if existing is None:
            raise
        return _existing_observation(session, existing)
    return None


def _normalize_response(
    value: object,
    *,
    provider: DiscoveryProvider,
    max_results: int,
    query_text: str | None = None,
) -> tuple[list[DiscoveryCandidate], list[str], str, str]:
    response_bytes = _canonical_json(value)
    if len(response_bytes) > MAX_RESPONSE_BYTES:
        raise DiscoveryAdapterError("paper search response exceeds bounded size")
    if not isinstance(value, list):
        raise DiscoveryAdapterError("paper search response must be a list")
    raw_items = cast(list[object], value)
    if len(raw_items) > max_results:
        raise DiscoveryAdapterError("paper search response exceeds approved result budget")

    candidates: list[DiscoveryCandidate] = []
    raw_hashes: list[str] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            raise DiscoveryAdapterError("paper search response contains a non-object item")
        raw = cast(Mapping[str, object], item)
        raw_bytes = _canonical_json(dict(raw))
        if len(raw_bytes) > MAX_RAW_ITEM_BYTES:
            raise DiscoveryAdapterError("paper search response item exceeds bounded size")
        candidates.append(_candidate_from_untrusted_item(raw, provider))
        raw_hashes.append(hashlib.sha256(raw_bytes).hexdigest())
    candidate_hashes = [discovery_candidate_sha256(item) for item in candidates]
    if len(set(candidate_hashes)) != len(candidate_hashes):
        raise DiscoveryAdapterError(
            "paper search response contains duplicate candidates that cannot form a terminal anchor"
        )

    normalized_candidates: list[DiscoveryCandidate] = []
    normalized_raw_hashes: list[str] = []
    seen_works: set[tuple[str, str]] = set()
    for candidate, raw_hash in zip(candidates, raw_hashes, strict=True):
        fingerprint = _candidate_work_fingerprint(candidate)
        if fingerprint in seen_works:
            continue
        seen_works.add(fingerprint)
        normalized_candidates.append(candidate)
        normalized_raw_hashes.append(raw_hash)

    if query_text:
        ranked = sorted(
            enumerate(
                zip(normalized_candidates, normalized_raw_hashes, strict=True)
            ),
            key=lambda item: (
                -_candidate_relevance_score(item[1][0], query_text),
                item[0],
            ),
        )
        normalized_candidates = [item[1][0] for item in ranked]
        normalized_raw_hashes = [item[1][1] for item in ranked]

    candidate_set_sha256 = _canonical_sha256(
        {
            "candidateHashes": sorted(
                discovery_candidate_sha256(item) for item in normalized_candidates
            )
        }
    )
    return (
        normalized_candidates,
        normalized_raw_hashes,
        hashlib.sha256(response_bytes).hexdigest(),
        candidate_set_sha256,
    )


def _candidate_work_fingerprint(candidate: DiscoveryCandidate) -> tuple[str, str]:
    title = re.sub(r"[^\w]+", " ", candidate.title.casefold()).strip()
    first_author = (
        re.sub(r"[^\w]+", " ", candidate.authors[0].casefold()).strip()
        if candidate.authors
        else ""
    )
    return title, first_author


_GENERIC_RELEVANCE_TERMS = frozenset(
    {
        "analysis",
        "benchmark",
        "evaluation",
        "method",
        "research",
        "study",
    }
)
_RELEVANCE_TERM_ALIASES: dict[str, str] = {
    "benchmarks": "benchmark",
    "chatgpt": "llm",
    "evaluate": "evaluation",
    "evaluated": "evaluation",
    "evaluates": "evaluation",
    "evaluating": "evaluation",
    "hallucinations": "hallucination",
    "llms": "llm",
    "methods": "method",
}


def _candidate_relevance_score(
    candidate: DiscoveryCandidate,
    query_text: str,
) -> int:
    query_terms = _relevance_terms(query_text)
    if not query_terms:
        return 0
    title_terms = set(_relevance_terms(candidate.title))
    abstract_terms = set(_relevance_terms(candidate.abstract or ""))
    focal_terms = query_terms - _GENERIC_RELEVANCE_TERMS
    score = 0
    covered_focal_terms = 0

    for term in query_terms:
        is_focal = term in focal_terms
        if term in title_terms:
            score += 8 if is_focal else 3
            if is_focal:
                covered_focal_terms += 1
        elif term in abstract_terms:
            score += 3 if is_focal else 1
            if is_focal:
                covered_focal_terms += 1

    if focal_terms and covered_focal_terms == len(focal_terms):
        score += 4
    return score


def _relevance_terms(value: str) -> set[str]:
    normalized = re.sub(
        r"\blarge\s+language\s+models?\b",
        " llm ",
        value.casefold(),
    )
    return {
        cast(str, _RELEVANCE_TERM_ALIASES.get(term, term))
        for term in re.findall(r"[\w]+", normalized)
        if term
    }


def _candidate_from_untrusted_item(
    raw: Mapping[str, object], provider: DiscoveryProvider
) -> DiscoveryCandidate:
    try:
        source = _required_text(raw.get("source"), "source").casefold()
        if source != provider:
            raise DiscoveryAdapterError("paper search item source does not match invoked provider")
        provider_id = _required_text(raw.get("paper_id"), "paper_id")
        title = _required_text(raw.get("title"), "title")
        doi = _normalized_doi(_optional_text(raw.get("doi")))
        arxiv_id = _normalized_arxiv_id(provider_id) if provider == "arxiv" else None
        pmid = _normalized_pmid(provider_id) if provider == "pubmed" else None
        if provider == "crossref":
            provider_id = _normalized_doi(provider_id) or provider_id
            doi = doi or _normalized_doi(provider_id)
        elif arxiv_id is not None:
            provider_id = arxiv_id
        elif pmid is not None:
            provider_id = pmid
        return DiscoveryCandidate(
            provider=provider,
            provider_id=provider_id,
            title=title,
            authors=_authors(raw.get("authors")),
            abstract=_optional_text(raw.get("abstract")),
            publication_date=_optional_text(raw.get("published_date")),
            doi=doi,
            arxiv_id=arxiv_id,
            pmid=pmid,
            landing_url=_https_url(raw.get("url"), "url", provider=provider),
            # This is only an untrusted provider-reported URL.  A later explicit
            # download approval must validate it again before any file operation.
            open_access_pdf_url=_https_url(raw.get("pdf_url"), "pdf_url", provider=provider),
        )
    except ValidationError as error:
        raise DiscoveryAdapterError("paper search item violates bounded metadata schema") from error


def _authors(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        authors = [item.strip() for item in value.split(";") if item.strip()]
        return list(dict.fromkeys(authors))
    if isinstance(value, list):
        raw_authors = cast(list[object], value)
        if not all(isinstance(item, str) for item in raw_authors):
            raise DiscoveryAdapterError("paper search authors must be a string or list of strings")
        authors = cast(list[str], raw_authors)
        return list(dict.fromkeys(item.strip() for item in authors))
    raise DiscoveryAdapterError("paper search authors must be a string or list of strings")


def _required_text(value: object, field: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise DiscoveryAdapterError(f"paper search item is missing {field}")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DiscoveryAdapterError("paper search metadata fields must be strings")
    stripped = value.strip()
    return stripped or None


def _https_url(value: object, field: str, *, provider: DiscoveryProvider) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    parsed = urlparse(text)
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise DiscoveryAdapterError(f"paper search {field} URL is invalid") from error
    if parsed.scheme == "http":
        if (
            hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
            and parsed.username is None
            and parsed.password is None
            and port in {None, 80}
        ):
            netloc = hostname
            return parsed._replace(scheme="https", netloc=netloc).geturl()
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DiscoveryAdapterError(f"paper search {field} must be an absolute HTTPS URL")
    return text


def _normalized_doi(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    if not re.fullmatch(r"10\.\d{4,9}/\S+", normalized):
        raise DiscoveryAdapterError("paper search DOI is invalid")
    return normalized


def _normalized_arxiv_id(value: str) -> str:
    normalized = value.strip().casefold()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    normalized = re.sub(r"v\d+$", "", normalized)
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})", normalized):
        raise DiscoveryAdapterError("paper search arXiv identifier is invalid")
    return normalized


def _normalized_pmid(value: str) -> str:
    normalized = value.strip().removeprefix("PMID:").removeprefix("pmid:").strip()
    if not re.fullmatch(r"\d{1,12}", normalized):
        raise DiscoveryAdapterError("paper search PMID is invalid")
    return normalized


def persist_discovery_candidates(
    session: Session,
    *,
    invocation: ToolInvocationRecord,
    project_id: str,
    candidates: list[DiscoveryCandidate],
    raw_hashes: list[str],
) -> tuple[int, int]:
    novel_count = 0
    duplicate_count = 0
    seen_in_invocation: set[tuple[str, str]] = set()
    for rank, (candidate, raw_hash) in enumerate(zip(candidates, raw_hashes, strict=True), 1):
        candidate_hash = discovery_candidate_sha256(candidate)
        identity = normalized_discovery_candidate_identity(candidate)
        identity_key = (identity, candidate_hash)
        if identity_key in seen_in_invocation:
            duplicate_count += 1
            continue
        seen_in_invocation.add(identity_key)
        candidate_record = session.scalar(
            select(DiscoveryCandidateRecord).where(
                DiscoveryCandidateRecord.project_id == project_id,
                DiscoveryCandidateRecord.normalized_identity == identity,
                DiscoveryCandidateRecord.candidate_sha256 == candidate_hash,
            )
        )
        if candidate_record is None:
            candidate_record = DiscoveryCandidateRecord(
                id=str(uuid.uuid4()),
                project_id=project_id,
                schema_version="1",
                provider=candidate.provider,
                provider_id=candidate.provider_id,
                normalized_identity=identity,
                metadata_json={
                    "candidate": candidate.model_dump(mode="json", by_alias=True),
                    "trustClassification": "untrusted-metadata",
                },
                candidate_sha256=candidate_hash,
            )
            session.add(candidate_record)
            session.flush()
            novel_count += 1
        else:
            candidate_record.last_seen_at = utc_now()
            duplicate_count += 1
        session.add(
            CandidateOccurrenceRecord(
                project_id=project_id,
                invocation_id=invocation.id,
                candidate_id=candidate_record.id,
                rank=rank,
                raw_item_sha256=raw_hash,
            )
        )
    return novel_count, duplicate_count


def normalized_discovery_candidate_identity(candidate: DiscoveryCandidate) -> str:
    if candidate.doi:
        return f"doi:{candidate.doi.strip().lower().removeprefix('https://doi.org/').removeprefix('doi:')}"
    if candidate.arxiv_id:
        return f"arxiv:{candidate.arxiv_id.strip().lower().removeprefix('arxiv:')}"
    if candidate.pmid:
        return f"pmid:{candidate.pmid.strip().lower().removeprefix('pmid:')}"
    return f"provider:{candidate.provider}:{candidate.provider_id.strip().casefold()}"


def _mark_failure(
    invocation: ToolInvocationRecord,
    code: str,
    message: str,
    *,
    unknown: bool = False,
) -> None:
    invocation.status = "outcome-unknown" if unknown else "failed"
    invocation.error_code = _bounded_error_code(code)
    invocation.error_message = _safe_message(message)
    invocation.returned_count = 0
    invocation.novel_candidate_count = 0
    invocation.duplicate_count = 0
    invocation.candidate_set_sha256 = None
    invocation.finished_at = utc_now()


def _mark_cancelled(invocation: ToolInvocationRecord) -> None:
    invocation.status = "cancelled"
    invocation.returned_count = 0
    invocation.novel_candidate_count = 0
    invocation.duplicate_count = 0
    invocation.candidate_set_sha256 = None
    invocation.finished_at = utc_now()


def _failure_observation(
    invocation: ToolInvocationRecord,
    *,
    retry_classification: Literal["safe-to-retry", "never-retry"],
) -> DiscoveryOperationObservation:
    return DiscoveryOperationObservation(
        invocation_id=invocation.id,
        query_id=invocation.query_id,
        provider=invocation.provider,  # type: ignore[arg-type]
        status="failed",
        returned_count=0,
        novel_candidate_count=0,
        duplicate_count=0,
        candidate_set_sha256=None,
        error_code=invocation.error_code,
        retry_classification=retry_classification,
    )


def _unknown_observation(invocation: ToolInvocationRecord) -> DiscoveryOperationObservation:
    return DiscoveryOperationObservation(
        invocation_id=invocation.id,
        query_id=invocation.query_id,
        provider=invocation.provider,  # type: ignore[arg-type]
        status="outcome-unknown",
        returned_count=0,
        novel_candidate_count=0,
        duplicate_count=0,
        candidate_set_sha256=None,
        error_code=invocation.error_code,
        retry_classification="manual-review",
    )


def _cancelled_observation(invocation: ToolInvocationRecord) -> DiscoveryOperationObservation:
    return DiscoveryOperationObservation(
        invocation_id=invocation.id,
        query_id=invocation.query_id,
        provider=invocation.provider,  # type: ignore[arg-type]
        status="cancelled",
        returned_count=0,
        novel_candidate_count=0,
        duplicate_count=0,
        candidate_set_sha256=None,
        error_code=None,
        retry_classification="never-retry",
    )


def _terminal_observation(invocation: ToolInvocationRecord) -> DiscoveryOperationObservation:
    if invocation.status == "cancelled":
        return _cancelled_observation(invocation)
    if invocation.status == "outcome-unknown":
        return _unknown_observation(invocation)
    return _failure_observation(invocation, retry_classification="never-retry")


def _existing_observation(
    session: Session, invocation: ToolInvocationRecord
) -> DiscoveryOperationObservation:
    if invocation.status in {"outcome-unknown", "prepared", "pending"}:
        if invocation.status in {"prepared", "pending"}:
            _recover_stale_pending(session, invocation)
        if invocation.status == "outcome-unknown":
            raise DiscoveryOutcomeUnknown("the existing discovery operation outcome is unknown")
        if invocation.status in {"prepared", "pending"}:
            raise DiscoveryOperationInProgress("the discovery operation is still in progress")
    workflow = session.get(WorkflowRecord, invocation.workflow_id)
    discovery_spec = session.get(
        DiscoverySpecRecord,
        invocation.discovery_spec_id,
    )
    if workflow is None or discovery_spec is None:
        raise DiscoveryAdapterError("terminal discovery invocation authority is missing")
    validate_terminal_discovery_invocation(
        session,
        workflow=workflow,
        discovery_spec=discovery_spec,
        invocation=invocation,
    )
    if invocation.status == "succeeded":
        return DiscoveryOperationObservation(
            invocation_id=invocation.id,
            query_id=invocation.query_id,
            provider=invocation.provider,  # type: ignore[arg-type]
            status="existing",
            returned_count=int(invocation.returned_count or 0),
            novel_candidate_count=int(invocation.novel_candidate_count or 0),
            duplicate_count=int(invocation.duplicate_count or 0),
            candidate_set_sha256=invocation.candidate_set_sha256,
            error_code=None,
            retry_classification="never-retry",
        )
    if invocation.status == "cancelled":
        return _cancelled_observation(invocation)
    retry = "safe-to-retry" if invocation.error_code in _SAFE_RETRY_CODES else "never-retry"
    return _failure_observation(invocation, retry_classification=retry)


def _recover_stale_pending(session: Session, invocation: ToolInvocationRecord) -> None:
    job = session.get(JobRecord, invocation.job_id)
    authorization_hash = invocation.request_json.get("authorizationLeaseTokenSha256")
    current_lease_hash = (
        hashlib.sha256(job.lease_token.encode()).hexdigest()
        if job is not None and job.lease_token is not None
        else None
    )
    if (
        job is not None
        and job.status == "leased"
        and job.lease_expires_at is not None
        and _as_utc(job.lease_expires_at) > utc_now()
        and authorization_hash == current_lease_hash
    ):
        return
    was_prepared = invocation.status == "prepared"
    target_status = "failed" if was_prepared else "outcome-unknown"
    error_code = "prepared-not-sent" if was_prepared else "outcome-unknown"
    now = utc_now()
    result = session.connection().execute(
        update(ToolInvocationRecord)
        .where(
            ToolInvocationRecord.id == invocation.id,
            ToolInvocationRecord.status == invocation.status,
        )
        .values(
            status=target_status,
            error_code=error_code,
            error_message="The paper-search connector did not return a usable result.",
            returned_count=0,
            novel_candidate_count=0,
            duplicate_count=0,
            candidate_set_sha256=None,
            finished_at=now,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        session.refresh(invocation)
        return
    session.commit()
    session.refresh(invocation)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise DiscoveryAdapterError("paper search response is not canonical JSON") from error


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _bounded_error_code(value: str) -> str:
    normalized = "".join(
        character if character.isascii() and (character.isalnum() or character == "-") else "-"
        for character in value.strip().lower()
    ).strip("-")
    return (normalized or "connector-failure")[:100]


def _safe_message(value: object) -> str:
    # Provider failures and hostile metadata can include request fragments,
    # credentials, or prompt-shaped text.  Durable control records keep a
    # normalized code and deliberately do not retain arbitrary external prose.
    del value
    return "The paper-search connector did not return a usable result."


__all__ = [
    "BoundedMcpRequest",
    "DiscoveryAdapterError",
    "DiscoveryOperationInProgress",
    "DiscoveryOperationObservation",
    "DiscoveryOperation",
    "DiscoveryOutcomeUnknown",
    "KnownMcpToolFailure",
    "McpToolBroker",
    "PAPER_SEARCH_CONNECTOR_NAME",
    "PAPER_SEARCH_CONNECTOR_VERSION",
    "PAPER_SEARCH_ALLOWED_TOOLS",
    "PaperSearchAdapter",
    "build_paper_search_request",
    "discovery_operation_key",
    "discovery_operations",
    "discovery_plan_spec",
    "discovery_step_key",
    "discovery_task_input",
    "discovery_terminal_task_outputs",
    "validate_recoverable_discovery_invocation",
    "validate_terminal_discovery_invocation",
]
