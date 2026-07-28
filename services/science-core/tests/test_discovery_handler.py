from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Generator, Mapping
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import open_science_core.workflow.agent_loop.coordinator as coordinator_module
from open_science_core.models import (
    AgentDecisionRecord,
    ApprovalRecord,
    Base,
    CandidateOccurrenceRecord,
    DiscoveryCandidateRecord,
    DiscoverySpecRecord,
    EventRecord,
    InteractionRequestRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    StepObservationRecord,
    TaskRecord,
    ToolInvocationRecord,
    WorkflowRecord,
    utc_now,
)
from open_science_core.workflow._handlers.lifecycle import mark_leased_job_started
from open_science_core.workflow._service.events import transition_task
from open_science_core.workflow._service.integrity import (
    content_sha256,
    plan_approval_hash,
)
from open_science_core.workflow._service.jobs import enqueue_job
from open_science_core.workflow._service.lifecycle import materialize_plan_tasks
from open_science_core.workflow.agent_loop.coordinator import AgentLoopCoordinator
from open_science_core.workflow.agent_loop.decision import next_action_input_sha256
from open_science_core.workflow.agent_loop.schemas import ObservationFact, StepObservation
from open_science_core.workflow.discovery_adapter import (
    DiscoveryAdapterError,
    PaperSearchAdapter,
    discovery_operation_key,
    discovery_plan_spec,
)
from open_science_core.workflow.discovery_handler import (
    execute_leased_discovery_job,
    materialize_discovery_plan,
)
from open_science_core.workflow.discovery_schemas import (
    DISCOVERY_PLAN_APPROVAL_REASON,
    DISCOVERY_PLAN_APPROVAL_SCHEMA_VERSION,
    DiscoveryCandidate,
    DiscoverySpec,
    discovery_approval_resources,
    discovery_candidate_sha256,
    discovery_sha256,
)
from open_science_core.workflow.state import WorkflowFailure
from open_science_core.workflow.worker import WorkflowWorker


@pytest.fixture
def session(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{tmp_path / 'discovery-handler.sqlite3'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as value:
        value.add(ProjectRecord(id="project-1", title="Discovery", description="", project_path=str(tmp_path), execution_mode="safe"))
        workflow = WorkflowRecord(
            id="workflow-1", project_id="project-1", create_idempotency_key="create-1",
            create_payload_sha256="a" * 64, creation_mode="autonomous", selected_source_ids=[],
            current_intent_decision_id=None, workflow_type="literature-synthesis", dataset_source_id=None,
            dataset_content_hash=None, goal="Which methods evaluate hallucinations?",
            generation_mode="local-deterministic", status="running", row_version=1, event_sequence=0,
        )
        value.add(workflow)
        value.flush()
        spec = DiscoverySpec.model_validate(
            {
                "schemaVersion": "1", "question": "Which methods evaluate hallucinations?",
                "queries": [
                    {"id": "query-primary", "query": "language model hallucination evaluation", "providers": ["pubmed"], "maxResultsPerProvider": 2},
                    {"id": "query-secondary", "query": "language model hallucination benchmark", "providers": ["crossref"], "maxResultsPerProvider": 2},
                ],
                "stopPolicy": {"minUniqueCandidates": 1, "maxAttempts": 2, "maxConsecutiveNoNovelty": 2},
                "downloadOpenAccessPdfs": False, "maxPdfDownloads": 0,
            }
        )
        discovery_record = DiscoverySpecRecord(id="spec-1", workflow_id=workflow.id, revision=1, previous_spec_id=None, schema_version="1", spec_json=spec.model_dump(mode="json", by_alias=True), spec_sha256=discovery_sha256(spec), status="approved", approved_at=utc_now())
        value.add(discovery_record)
        value.flush()
        plan_json = discovery_plan_spec(discovery_record, spec)
        plan = PlanRecord(
            id="plan-1",
            workflow_id=workflow.id,
            version=1,
            spec_json=plan_json,
            spec_sha256=content_sha256(plan_json),
            status="approved",
            generator="paper-discovery-v1",
            prompt_version="paper-discovery-v1",
            approved_at=utc_now(),
        )
        value.add(plan)
        value.flush()
        resources = discovery_approval_resources(
            project_id=workflow.project_id,
            spec_id=discovery_record.id,
            revision=discovery_record.revision,
            spec_sha256=discovery_record.spec_sha256,
            spec=spec,
        )
        value.add(
            ApprovalRecord(
                id="approval-1",
                task_id=None,
                workflow_id=workflow.id,
                plan_id=plan.id,
                subject_type="plan",
                subject_id=plan.id,
                payload_schema_version=DISCOVERY_PLAN_APPROVAL_SCHEMA_VERSION,
                row_version=2,
                intent_hash=plan_approval_hash(
                    plan,
                    resources,
                    schema_version=DISCOVERY_PLAN_APPROVAL_SCHEMA_VERSION,
                    workflow_goal=workflow.goal,
                    risk_level="medium",
                    reason=DISCOVERY_PLAN_APPROVAL_REASON,
                    subject_id=plan.id,
                    task_id=None,
                ),
                requested_action="approve-research-plan",
                risk_level="medium",
                reason=DISCOVERY_PLAN_APPROVAL_REASON,
                affected_resources=resources,
                user_decision="approved",
                decided_at=utc_now(),
            )
        )
        tasks = materialize_plan_tasks(value, workflow, plan)
        transition_task(value, tasks[0], "queued")
        enqueue_job(
            value,
            workflow,
            kind="execute-task",
            task=tasks[0],
            operation_key=discovery_operation_key(
                discovery_record.id,
                "query-primary",
                "pubmed",
            ),
        )
        value.commit()
        yield value
    engine.dispose()


def _workflow(session: Session) -> WorkflowRecord:
    return session.get_one(WorkflowRecord, "workflow-1")


def _spec(session: Session) -> DiscoverySpecRecord:
    return session.get_one(DiscoverySpecRecord, "spec-1")


def _discovery_jobs(session: Session, task_id: str) -> list[JobRecord]:
    return list(
        session.scalars(
            select(JobRecord)
            .where(
                JobRecord.workflow_id == "workflow-1",
                JobRecord.kind == "execute-task",
                JobRecord.task_id == task_id,
            )
            .order_by(JobRecord.attempt)
        )
    )


def _assert_single_queued_observe_step(session: Session) -> None:
    assert len(
        list(
            session.scalars(
                select(JobRecord).where(
                    JobRecord.workflow_id == "workflow-1",
                    JobRecord.kind == "observe-step",
                    JobRecord.status == "queued",
                )
            )
        )
    ) == 1


class _Broker:
    def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]) -> object:
        assert tool_name == "search_pubmed"
        assert arguments["query"] == "language model hallucination evaluation"
        return [
            {
                "paper_id": "12345678",
                "title": "A bounded result",
                "authors": "A. Researcher",
                "source": "pubmed",
            }
        ]


class _EmptyBroker:
    def __init__(self) -> None:
        self.call_count = 0

    def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]) -> object:
        self.call_count += 1
        return []


class _CrossrefBroker:
    def __init__(self) -> None:
        self.call_count = 0

    def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]) -> object:
        self.call_count += 1
        assert tool_name == "search_crossref"
        return [
            {
                "paper_id": "10.1000/selector-integrity",
                "title": "Selector integrity fixture",
                "authors": "A. Researcher",
                "source": "crossref",
            }
        ]


class _CountingPubmedBroker(_Broker):
    def __init__(self) -> None:
        self.call_count = 0

    def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]) -> object:
        self.call_count += 1
        return super().call_tool(tool_name=tool_name, arguments=arguments)


def _crash_window_invocation(
    job: JobRecord,
    *,
    status: str,
    suffix: str = "",
) -> ToolInvocationRecord:
    request = {
        "toolName": "search_pubmed",
        "arguments": {
            "query": "language model hallucination evaluation",
            "max_results": 2,
            "sort": "relevance",
        },
        "authorizationLeaseTokenSha256": "d" * 64,
    }
    request_sha256 = hashlib.sha256(
        json.dumps(request, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    idempotency = hashlib.sha256(
        json.dumps(
            {
                "workflowId": "workflow-1",
                "operationKey": job.operation_key,
                "attempt": job.attempt,
                "requestSha256": request_sha256,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return ToolInvocationRecord(
        id=f"invocation-{status}{suffix}",
        project_id="project-1",
        workflow_id="workflow-1",
        discovery_spec_id="spec-1",
        job_id=job.id,
        schema_version="1",
        tool_name="search_pubmed",
        connector_name="paper-search-mcp",
        connector_version="0.1.4+spark.3",
        query_id="query-primary",
        provider="pubmed",
        operation_key=job.operation_key,
        attempt=job.attempt,
        request_idempotency_key=idempotency,
        request_payload_sha256=request_sha256,
        request_json=request,
        status=status,
    )


def _successful_discovery_observation(
    *,
    plan: PlanRecord,
    task: TaskRecord,
    job: JobRecord,
    novel_count: int,
) -> StepObservation:
    return StepObservation(
        schema_version="1",
        workflow_id="workflow-1",
        plan_id=plan.id,
        task_id=task.id,
        source_job_id=job.id,
        observation_type="step-output",
        step_key=cast(str, task.step_key),
        attempt=job.attempt,
        status="succeeded",
        facts=[
            ObservationFact(
                code="discovery-result",
                statement="The durable Discovery result is available.",
                value={"novelCandidateCount": novel_count},
                source_type="workflow",
                source_id=task.id,
            )
        ],
        failure_category="none",
        recommended_actions=["continue"],
    )


def _replace_with_adaptive_plan(session: Session) -> tuple[PlanRecord, list[TaskRecord]]:
    workflow = _workflow(session)
    record = _spec(session)
    plan = session.get_one(PlanRecord, "plan-1")
    approval = session.get_one(ApprovalRecord, "approval-1")
    spec = DiscoverySpec.model_validate(
        {
            "schemaVersion": "1",
            "question": workflow.goal,
            "queries": [
                {
                    "id": "query-primary",
                    "query": "language model hallucination evaluation",
                    "providers": ["crossref", "pubmed"],
                    "maxResultsPerProvider": 2,
                },
                {
                    "id": "query-secondary",
                    "query": "language model hallucination benchmark",
                    "providers": ["crossref"],
                    "maxResultsPerProvider": 2,
                },
            ],
            "stopPolicy": {
                "minUniqueCandidates": 20,
                "maxAttempts": 3,
                "maxConsecutiveNoNovelty": 2,
            },
            "downloadOpenAccessPdfs": False,
            "maxPdfDownloads": 0,
        }
    )
    record.spec_json = spec.model_dump(mode="json", by_alias=True)
    record.spec_sha256 = discovery_sha256(spec)
    plan_json = discovery_plan_spec(record, spec)
    plan.spec_json = plan_json
    plan.spec_sha256 = content_sha256(plan_json)
    resources = discovery_approval_resources(
        project_id=workflow.project_id,
        spec_id=record.id,
        revision=record.revision,
        spec_sha256=record.spec_sha256,
        spec=spec,
    )
    approval.affected_resources = resources
    approval.intent_hash = plan_approval_hash(
        plan,
        resources,
        schema_version=DISCOVERY_PLAN_APPROVAL_SCHEMA_VERSION,
        workflow_goal=workflow.goal,
        risk_level="medium",
        reason=DISCOVERY_PLAN_APPROVAL_REASON,
        subject_id=plan.id,
        task_id=None,
    )

    tasks = list(
        session.scalars(
            select(TaskRecord)
            .where(TaskRecord.plan_id == plan.id)
            .order_by(TaskRecord.order_index)
        )
    )
    steps = cast(list[dict[str, object]], plan_json["steps"])
    while len(tasks) < len(steps):
        task = TaskRecord(
            id=f"task-adaptive-{len(tasks) + 1}",
            project_id=workflow.project_id,
            workflow_id=workflow.id,
            plan_id=plan.id,
            objective="pending",
            task_type="paper-discovery",
        )
        session.add(task)
        tasks.append(task)
    for index, task in enumerate(tasks, start=1):
        task.step_key = f"adaptive-transition-{index}"
        task.order_index = 100 + index
    session.flush()
    for index, (task, step) in enumerate(zip(tasks, steps, strict=True), start=1):
        inputs = cast(dict[str, object], step["inputs"])
        task.step_key = cast(str, step["key"])
        task.order_index = index
        task.objective = cast(str, step["objective"])
        task.task_type = "paper-discovery"
        task.inputs = inputs
        task.input_sha256 = content_sha256(
            {
                "inputs": inputs,
                "objective": task.objective,
                "stepKey": task.step_key,
                "stepType": task.task_type,
            }
        )
        task.expected_outputs = ["discovery-observation"]
        task.outputs = {}
        task.acceptance_criteria = ["persist-structured-discovery-observation"]
        task.permissions = ["remote-paper-search"]
        task.risk_level = "medium"
        task.timeout_seconds = 120
        task.status = "queued" if index == 1 else "pending"
    initial_job = session.scalar(
        select(JobRecord).where(
            JobRecord.workflow_id == workflow.id,
            JobRecord.kind == "execute-task",
        )
    )
    assert initial_job is not None
    initial_job.task_id = tasks[0].id
    initial_job.operation_key = discovery_operation_key(
        record.id,
        "query-primary",
        "crossref",
    )
    initial_job.input_sha256 = content_sha256(tasks[0].inputs)
    session.commit()
    return plan, tasks


def test_materializes_canonical_multi_task_plan_and_only_queues_first(session: Session) -> None:
    plan = materialize_discovery_plan(session, _workflow(session), _spec(session))

    tasks = list(session.scalars(select(TaskRecord).where(TaskRecord.plan_id == plan.id).order_by(TaskRecord.order_index)))
    jobs = list(session.scalars(select(JobRecord).where(JobRecord.workflow_id == "workflow-1")))
    assert [task.step_key for task in tasks] == [
        "paper-discovery-query-primary-pubmed",
        "paper-discovery-query-secondary-crossref",
    ]
    assert [task.status for task in tasks] == ["queued", "pending"]
    assert len(jobs) == 1
    assert jobs[0].task_id == tasks[0].id
    assert jobs[0].input_sha256 == content_sha256(tasks[0].inputs)


def test_exact_plan_membership_rejects_tampering_before_send(session: Session) -> None:
    plan = materialize_discovery_plan(session, _workflow(session), _spec(session))
    job = session.scalar(select(JobRecord).where(JobRecord.workflow_id == "workflow-1"))
    assert job is not None
    job.status = "leased"
    job.lease_token = "lease-1"
    job.lease_expires_at = utc_now() + timedelta(minutes=1)
    task = session.get_one(TaskRecord, job.task_id)
    task.status = "running"
    tampered = dict(plan.spec_json)
    tampered["steps"] = [*plan.spec_json["steps"]]
    tampered["steps"][1] = {**tampered["steps"][1], "permissions": []}
    plan.spec_json = tampered
    session.commit()

    with pytest.raises(DiscoveryAdapterError, match="plan"):
        PaperSearchAdapter().execute(
            session, workflow=_workflow(session), discovery_spec=_spec(session), job=job,
            query_id="query-primary", provider="pubmed", attempt=1, lease_token="lease-1",
            broker=_Broker(),
        )
    assert session.scalar(select(ToolInvocationRecord)) is None


def test_handler_persists_success_then_blocks_for_pdf_import(session: Session) -> None:
    materialize_discovery_plan(session, _workflow(session), _spec(session))
    job = session.scalar(select(JobRecord).where(JobRecord.workflow_id == "workflow-1"))
    assert job is not None
    job.status = "leased"
    job.lease_token = "lease-1"
    job.lease_expires_at = utc_now() + timedelta(minutes=1)
    session.commit()
    mark_leased_job_started(session, job.id, "lease-1")

    result = execute_leased_discovery_job(session, job.id, "lease-1", broker=_Broker())

    task = session.get_one(TaskRecord, job.task_id)
    invocation = session.scalar(select(ToolInvocationRecord).where(ToolInvocationRecord.job_id == job.id))
    workflow = _workflow(session)
    assert result.status == "succeeded"
    assert invocation is not None and invocation.status == "succeeded"
    assert task.status == "completed"
    assert workflow.status == "blocked"
    assert workflow.blocking_code == "discovery-candidate-target-reached"
    assert session.scalar(
        select(InteractionRequestRecord).where(
            InteractionRequestRecord.workflow_id == workflow.id
        )
    ) is None


def test_continue_queues_next_discovery_task_with_canonical_adapter_key(session: Session) -> None:
    plan = materialize_discovery_plan(session, _workflow(session), _spec(session))
    tasks = list(
        session.scalars(
            select(TaskRecord).where(TaskRecord.plan_id == plan.id).order_by(TaskRecord.order_index)
        )
    )
    first, second = tasks
    first_job = session.scalar(select(JobRecord).where(JobRecord.task_id == first.id))
    assert first_job is not None
    first_job.status = "leased"
    first_job.lease_token = "lease-1"
    first_job.lease_expires_at = utc_now() + timedelta(minutes=1)
    session.commit()
    mark_leased_job_started(session, first_job.id, "lease-1")
    execute_leased_discovery_job(
        session,
        first_job.id,
        "lease-1",
        broker=_EmptyBroker(),
    )
    observation = _successful_discovery_observation(
        plan=plan,
        task=first,
        job=first_job,
        novel_count=0,
    )
    context = coordinator_module._loop_context(  # pyright: ignore[reportPrivateUsage]
        session,
        _workflow(session),
        observation,
        None,
    )
    AgentLoopCoordinator(cast(Any, None))._apply_continue(  # pyright: ignore[reportPrivateUsage]
        session,
        _workflow(session),
        cast(Any, SimpleNamespace(target_step_key=second.step_key)),
        observation,
        context.discovery_selection,
    )
    job = session.scalar(select(JobRecord).where(JobRecord.task_id == second.id))
    assert job is not None
    assert job.operation_key == "discovery:spec-1:query-secondary:crossref"
    job.status = "leased"
    job.lease_token = "lease-2"
    job.lease_expires_at = utc_now() + timedelta(minutes=1)
    second.status = "running"
    session.commit()

    result = PaperSearchAdapter().execute(
        session,
        workflow=_workflow(session),
        discovery_spec=_spec(session),
        job=job,
        query_id="query-secondary",
        provider="crossref",
        attempt=1,
        lease_token="lease-2",
        broker=_EmptyBroker(),
    )

    assert result.status == "succeeded"


def test_discovery_selection_uses_coverage_recovers_and_stops_without_expansion(
    session: Session,
) -> None:
    plan, tasks = _replace_with_adaptive_plan(session)
    first, skipped_same_query, selected_other_query = tasks
    first_job = session.scalar(
        select(JobRecord).where(
            JobRecord.workflow_id == "workflow-1",
            JobRecord.kind == "execute-task",
            JobRecord.task_id == first.id,
        )
    )
    assert first_job is not None
    first_job.status = "leased"
    first_job.lease_token = "adaptive-lease-1"
    first_job.lease_expires_at = utc_now() + timedelta(minutes=1)
    session.commit()
    mark_leased_job_started(session, first_job.id, "adaptive-lease-1")
    execute_leased_discovery_job(
        session,
        first_job.id,
        "adaptive-lease-1",
        broker=_EmptyBroker(),
    )
    first_observation = _successful_discovery_observation(
        plan=plan,
        task=first,
        job=first_job,
        novel_count=0,
    )
    unrelated_spec = DiscoverySpecRecord(
        id="spec-unrelated",
        workflow_id="workflow-1",
        revision=2,
        previous_spec_id="spec-1",
        schema_version="1",
        spec_json=_spec(session).spec_json,
        spec_sha256=_spec(session).spec_sha256,
        status="rejected",
    )
    unrelated_job = JobRecord(
        id="job-unrelated-spec",
        workflow_id="workflow-1",
        task_id=None,
        kind="execute-task",
        operation_key="discovery:spec-unrelated:query-primary:crossref",
        attempt=1,
        input_sha256="b" * 64,
        handler_version="test",
        status="succeeded",
        finished_at=utc_now(),
    )
    session.add_all([unrelated_spec, unrelated_job])
    session.flush()
    session.add(
        ToolInvocationRecord(
            id="invocation-unrelated-spec",
            project_id="project-1",
            workflow_id="workflow-1",
            discovery_spec_id=unrelated_spec.id,
            job_id=unrelated_job.id,
            schema_version="1",
            tool_name="search_crossref",
            connector_name="paper-search-mcp",
            connector_version="0.1.4+spark.3",
            query_id="query-primary",
            provider="crossref",
            operation_key=unrelated_job.operation_key,
            attempt=1,
            request_idempotency_key="unrelated-spec-invocation",
            request_payload_sha256="c" * 64,
            request_json={"query": "unrelated"},
            output_sha256="d" * 64,
            returned_count=999,
            novel_candidate_count=999,
            duplicate_count=0,
            candidate_set_sha256="e" * 64,
            status="succeeded",
            finished_at=utc_now(),
        )
    )
    session.flush()

    first_context = coordinator_module._loop_context(  # pyright: ignore[reportPrivateUsage]
        session,
        _workflow(session),
        first_observation,
        None,
    )
    first_input_sha256 = next_action_input_sha256(
        goal=_workflow(session).goal,
        observation=first_observation,
        context=first_context,
        current_analysis_spec=None,
        model=None,
    )
    assert first_context.next_step_key == selected_other_query.step_key
    assert first_context.next_step_key != skipped_same_query.step_key
    projection = first_context.discovery_selection
    assert projection is not None
    assert projection.policy_version == "discovery-next-operation-v1"
    assert projection.selected_step_key == selected_other_query.step_key
    assert projection.selected_operation_key.endswith("query-secondary:crossref")
    assert projection.reason_code == "query-coverage-gap"
    assert (
        projection.postcondition
        == "queue-selected-pending-approved-operation-only"
    )
    assert [item.rank for item in projection.eligible_operations] == [1, 2]
    assert all(
        item.operation_key.startswith("discovery:spec-1:")
        for item in projection.eligible_operations
    )
    alternate_input_sha256 = next_action_input_sha256(
        goal=_workflow(session).goal,
        observation=first_observation,
        context=replace(
            first_context,
            next_step_key=cast(str, skipped_same_query.step_key),
            discovery_selection=None,
        ),
        current_analysis_spec=None,
        model=None,
    )
    assert alternate_input_sha256 != first_input_sha256

    session.commit()
    restart_factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    with restart_factory() as restarted_session:
        restarted_plan = restarted_session.get_one(PlanRecord, plan.id)
        restarted_first = restarted_session.get_one(TaskRecord, first.id)
        restarted_job = restarted_session.get_one(JobRecord, first_job.id)
        restarted_observation = _successful_discovery_observation(
            plan=restarted_plan,
            task=restarted_first,
            job=restarted_job,
            novel_count=0,
        )
        restarted_context = coordinator_module._loop_context(  # pyright: ignore[reportPrivateUsage]
            restarted_session,
            restarted_session.get_one(WorkflowRecord, "workflow-1"),
            restarted_observation,
            None,
        )
        restarted_input_sha256 = next_action_input_sha256(
            goal=restarted_session.get_one(WorkflowRecord, "workflow-1").goal,
            observation=restarted_observation,
            context=restarted_context,
            current_analysis_spec=None,
            model=None,
        )
    assert restarted_context.next_step_key == first_context.next_step_key
    assert restarted_input_sha256 == first_input_sha256
    assert restarted_context.discovery_selection == projection

    session.expire_all()
    resumed_plan = session.get_one(PlanRecord, plan.id)
    resumed_first = session.get_one(TaskRecord, first.id)
    resumed_job = session.get_one(JobRecord, first_job.id)
    resumed_observation = _successful_discovery_observation(
        plan=resumed_plan,
        task=resumed_first,
        job=resumed_job,
        novel_count=0,
    )
    fake_decision = cast(
        Any,
        SimpleNamespace(
            id="decision-selection-1",
            action="continue",
            target_step_key=selected_other_query.step_key,
            expected_workflow_revision=_workflow(session).row_version,
            reason_code="deterministic-policy",
        ),
    )
    proposed_event = coordinator_module._decision_event(  # pyright: ignore[reportPrivateUsage]
        fake_decision,
        cast(
            Any,
            SimpleNamespace(
                id="observation-selection-1",
                task_id=first.id,
                facts_json=[],
            ),
        ),
        discovery_selection=projection,
    )
    event = EventRecord(
        id="event-selection-1",
        project_id="project-1",
        workflow_id="workflow-1",
        task_id=first.id,
        job_id=None,
        sequence=None,
        event_type="agent.decision-proposed",
        payload=proposed_event.model_dump(mode="json", by_alias=True),
    )
    session.add(event)
    session.flush()
    assert (
        coordinator_module._persisted_discovery_selection(  # pyright: ignore[reportPrivateUsage]
            session,
            _workflow(session),
            fake_decision,
        )
        == projection
    )
    tampered_payload = dict(event.payload)
    tampered_payload["discoverySelectionSha256"] = "0" * 64
    event.payload = tampered_payload
    session.flush()
    with pytest.raises(
        WorkflowFailure,
        match="failed integrity checks",
    ):
        coordinator_module._persisted_discovery_selection(  # pyright: ignore[reportPrivateUsage]
            session,
            _workflow(session),
            fake_decision,
        )
    event.payload = proposed_event.model_dump(mode="json", by_alias=True)
    session.flush()
    newer_pending_plan = PlanRecord(
        id="plan-newer-pending",
        workflow_id="workflow-1",
        version=2,
        spec_json=resumed_plan.spec_json,
        spec_sha256=resumed_plan.spec_sha256,
        status="pending-approval",
        generator="paper-discovery-v1",
        prompt_version="paper-discovery-v1",
    )
    newer_pending_task = TaskRecord(
        id="task-newer-pending",
        project_id="project-1",
        workflow_id="workflow-1",
        plan_id=newer_pending_plan.id,
        step_key=selected_other_query.step_key,
        order_index=selected_other_query.order_index,
        objective=selected_other_query.objective,
        task_type=selected_other_query.task_type,
        inputs=selected_other_query.inputs,
        input_sha256=selected_other_query.input_sha256,
        expected_outputs=selected_other_query.expected_outputs,
        outputs={},
        acceptance_criteria=selected_other_query.acceptance_criteria,
        permissions=selected_other_query.permissions,
        risk_level=selected_other_query.risk_level,
        status="pending",
        timeout_seconds=selected_other_query.timeout_seconds,
    )
    session.add_all([newer_pending_plan, newer_pending_task])
    session.flush()
    with pytest.raises(
        WorkflowFailure,
        match="current eligible operation",
    ):
        AgentLoopCoordinator(cast(Any, None))._apply_continue(  # pyright: ignore[reportPrivateUsage]
            session,
            _workflow(session),
            cast(
                Any,
                SimpleNamespace(target_step_key=skipped_same_query.step_key),
            ),
            resumed_observation,
            restarted_context.discovery_selection,
        )
    assert (
        session.scalar(
            select(JobRecord).where(JobRecord.task_id == skipped_same_query.id)
        )
        is None
    )

    AgentLoopCoordinator(cast(Any, None))._apply_continue(  # pyright: ignore[reportPrivateUsage]
        session,
        _workflow(session),
        cast(
            Any,
            SimpleNamespace(target_step_key=selected_other_query.step_key),
        ),
        resumed_observation,
        restarted_context.discovery_selection,
    )
    second_job = session.scalar(
        select(JobRecord).where(JobRecord.task_id == selected_other_query.id)
    )
    assert second_job is not None
    assert (
        session.scalar(
            select(JobRecord).where(JobRecord.task_id == newer_pending_task.id)
        )
        is None
    )
    second_job.status = "leased"
    second_job.lease_token = "adaptive-lease-2"
    second_job.lease_expires_at = utc_now() + timedelta(minutes=1)
    selected_other_query.status = "running"
    session.commit()
    result = execute_leased_discovery_job(
        session,
        second_job.id,
        "adaptive-lease-2",
        broker=_EmptyBroker(),
    )
    assert result.status == "succeeded"
    second_observation = _successful_discovery_observation(
        plan=plan,
        task=selected_other_query,
        job=second_job,
        novel_count=0,
    )
    stopped = coordinator_module._loop_context(  # pyright: ignore[reportPrivateUsage]
        session,
        _workflow(session),
        second_observation,
        None,
    )
    assert stopped.next_step_key is None
    assert skipped_same_query.status == "pending"
    assert (
        session.scalar(
            select(JobRecord).where(JobRecord.task_id == skipped_same_query.id)
        )
        is None
    )
    invocations = list(
        session.scalars(
            select(ToolInvocationRecord).where(
                ToolInvocationRecord.workflow_id == "workflow-1",
                ToolInvocationRecord.discovery_spec_id == "spec-1",
            )
        )
    )
    assert len(invocations) == 2
    assert {item.tool_name for item in invocations} == {"search_crossref"}
    assert all("download" not in item.tool_name.lower() for item in invocations)
    assert all("scihub" not in item.tool_name.lower() for item in invocations)


@pytest.mark.parametrize(
    "tamper",
    [
        "wrong-job",
        "wrong-tool-connector",
        "request-hash-drift",
        "counts-inconsistent",
        "occurrence-drift",
        "output-sha-drift",
        "existing-duplicate-occurrence-deleted",
        "missing-terminal-anchor",
    ],
)
def test_discovery_selector_rejects_tampered_terminal_invocation_without_next_call(
    session: Session,
    tamper: str,
) -> None:
    plan, tasks = _replace_with_adaptive_plan(session)
    first = tasks[0]
    first_job = session.scalar(
        select(JobRecord).where(
            JobRecord.workflow_id == "workflow-1",
            JobRecord.kind == "execute-task",
            JobRecord.task_id == first.id,
        )
    )
    assert first_job is not None
    first_job.status = "leased"
    first_job.lease_token = "tamper-lease-1"
    first_job.lease_expires_at = utc_now() + timedelta(minutes=1)
    if tamper == "existing-duplicate-occurrence-deleted":
        existing_candidate = DiscoveryCandidate(
            provider="crossref",
            provider_id="10.1000/selector-integrity",
            title="Selector integrity fixture",
            authors=["A. Researcher"],
            doi="10.1000/selector-integrity",
        )
        session.add(
            DiscoveryCandidateRecord(
                id="existing-selector-candidate",
                project_id="project-1",
                schema_version="1",
                provider=existing_candidate.provider,
                provider_id=existing_candidate.provider_id,
                normalized_identity="doi:10.1000/selector-integrity",
                metadata_json={
                    "candidate": existing_candidate.model_dump(
                        mode="json",
                        by_alias=True,
                    ),
                    "trustClassification": "untrusted-metadata",
                },
                candidate_sha256=discovery_candidate_sha256(existing_candidate),
            )
        )
    session.commit()
    mark_leased_job_started(session, first_job.id, "tamper-lease-1")
    broker: _EmptyBroker | _CrossrefBroker = (
        _CrossrefBroker()
        if tamper
        in {"occurrence-drift", "existing-duplicate-occurrence-deleted"}
        else _EmptyBroker()
    )
    execute_leased_discovery_job(
        session,
        first_job.id,
        "tamper-lease-1",
        broker=broker,
    )
    invocation = session.scalar(
        select(ToolInvocationRecord).where(
            ToolInvocationRecord.discovery_spec_id == "spec-1"
        )
    )
    assert invocation is not None
    anchored_task = session.get_one(TaskRecord, first.id)
    assert anchored_task.outputs["terminalResultSha256"]
    assert anchored_task.outputs["terminalResult"]
    if tamper == "wrong-job":
        rogue_job = JobRecord(
            id="rogue-terminal-job",
            workflow_id="workflow-1",
            task_id=tasks[1].id,
            kind="execute-task",
            operation_key="rogue:terminal-job",
            attempt=1,
            input_sha256="f" * 64,
            handler_version="test",
            status="succeeded",
            finished_at=utc_now(),
        )
        session.add(rogue_job)
        session.flush()
        invocation.job_id = rogue_job.id
    elif tamper == "wrong-tool-connector":
        invocation.tool_name = "search_pubmed"
        invocation.connector_name = "rogue-paper-search"
    elif tamper == "request-hash-drift":
        invocation.request_payload_sha256 = "f" * 64
    elif tamper == "counts-inconsistent":
        invocation.returned_count = 0
        invocation.novel_candidate_count = 5
        invocation.duplicate_count = 7
    elif tamper == "occurrence-drift":
        occurrence = session.scalar(
            select(CandidateOccurrenceRecord).where(
                CandidateOccurrenceRecord.invocation_id == invocation.id
            )
        )
        assert occurrence is not None
        occurrence.rank = 2
    elif tamper == "output-sha-drift":
        invocation.output_sha256 = "f" * 64
    elif tamper == "existing-duplicate-occurrence-deleted":
        assert invocation.returned_count == 1
        assert invocation.novel_candidate_count == 0
        assert invocation.duplicate_count == 1
        occurrence = session.scalar(
            select(CandidateOccurrenceRecord).where(
                CandidateOccurrenceRecord.invocation_id == invocation.id
            )
        )
        assert occurrence is not None
        session.delete(occurrence)
    else:
        anchored_task.outputs = {
            "invocationId": invocation.id,
            "returnedCount": invocation.returned_count,
            "novelCandidateCount": invocation.novel_candidate_count,
            "duplicateCount": invocation.duplicate_count,
            "candidateSetSha256": invocation.candidate_set_sha256,
        }
    session.commit()
    execute_jobs_before = list(
        session.scalars(
            select(JobRecord).where(
                JobRecord.workflow_id == "workflow-1",
                JobRecord.kind == "execute-task",
            )
        )
    )
    observation = _successful_discovery_observation(
        plan=plan,
        task=first,
        job=first_job,
        novel_count=int(invocation.novel_candidate_count or 0),
    )

    with pytest.raises(WorkflowFailure) as error:
        coordinator_module._loop_context(  # pyright: ignore[reportPrivateUsage]
            session,
            _workflow(session),
            observation,
            None,
        )

    assert error.value.code == "discovery-selection-invocation-invalid"
    execute_jobs_after = list(
        session.scalars(
            select(JobRecord).where(
                JobRecord.workflow_id == "workflow-1",
                JobRecord.kind == "execute-task",
            )
        )
    )
    assert [job.id for job in execute_jobs_after] == [
        job.id for job in execute_jobs_before
    ]
    assert broker.call_count == 1
    assert all(task.status != "queued" for task in tasks[1:])
    assert session.scalar(select(AgentDecisionRecord.id)) is None


def test_worker_recovery_marks_pending_discovery_outcome_unknown_without_replay(
    session: Session,
) -> None:
    job = session.scalar(select(JobRecord).where(JobRecord.workflow_id == "workflow-1"))
    assert job is not None
    task = session.get_one(TaskRecord, cast(str, job.task_id))
    job.status = "leased"
    job.lease_token = "crash-pending"
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    task.status = "running"
    session.add(_crash_window_invocation(job, status="pending"))
    session.commit()

    broker = _CountingPubmedBroker()
    restarted = WorkflowWorker(
        sessionmaker(bind=session.bind, expire_on_commit=False),
        discovery_broker_factory=lambda: broker,
    )
    restarted.recover()

    session.expire_all()
    invocation = session.get_one(ToolInvocationRecord, "invocation-pending")
    workflow = _workflow(session)
    recovered_job = session.get_one(JobRecord, job.id)
    recovered_task = session.get_one(TaskRecord, task.id)
    assert broker.call_count == 0
    assert invocation.status == "outcome-unknown"
    assert recovered_job.status == "failed"
    assert recovered_job.error_code == "discovery-outcome-unknown"
    assert recovered_task.status == "blocked"
    assert workflow.status == "blocked"
    assert workflow.last_error_code == "discovery-outcome-unknown"
    assert workflow.last_error_message == (
        "A paper-search request may have been sent; review the durable result before retrying."
    )
    assert len(
        list(
            session.scalars(
                select(JobRecord).where(JobRecord.workflow_id == workflow.id)
            )
        )
    ) == 2
    assert session.scalar(select(func.count(CandidateOccurrenceRecord.candidate_id))) == 0
    assert session.scalar(
        select(JobRecord.id).where(
            JobRecord.workflow_id == workflow.id,
            JobRecord.kind == "observe-step",
            JobRecord.status == "queued",
        )
    ) is not None
    assert asyncio.run(restarted.run_once())
    session.expire_all()
    assert session.scalar(
        select(StepObservationRecord.id).where(
            StepObservationRecord.source_job_id == recovered_job.id
        )
    ) is not None


def test_worker_recovery_retries_prepared_discovery_once_with_fresh_attempt(
    session: Session,
) -> None:
    job = session.scalar(select(JobRecord).where(JobRecord.workflow_id == "workflow-1"))
    assert job is not None
    task = session.get_one(TaskRecord, cast(str, job.task_id))
    job.status = "leased"
    job.lease_token = "crash-prepared"
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    task.status = "running"
    session.add(_crash_window_invocation(job, status="prepared"))
    session.commit()

    broker = _CountingPubmedBroker()
    restarted = WorkflowWorker(
        sessionmaker(bind=session.bind, expire_on_commit=False),
        discovery_broker_factory=lambda: broker,
    )
    restarted.recover()
    session.expire_all()
    invocation = session.get_one(ToolInvocationRecord, "invocation-prepared")
    retries = _discovery_jobs(session, task.id)
    assert invocation.status == "failed"
    assert invocation.error_code == "prepared-not-sent"
    assert broker.call_count == 0
    assert [(item.attempt, item.status) for item in retries] == [
        (1, "failed"),
        (2, "queued"),
    ]

    retries[1].available_at = utc_now()
    session.commit()
    assert asyncio.run(restarted.run_once())
    session.expire_all()
    assert broker.call_count == 1
    assert session.get_one(JobRecord, retries[1].id).status == "succeeded"
    assert session.scalar(select(func.count(CandidateOccurrenceRecord.candidate_id))) == 1
    restarted.recover()
    session.expire_all()
    assert session.scalar(select(func.count(CandidateOccurrenceRecord.candidate_id))) == 1
    assert [(item.attempt, item.status) for item in _discovery_jobs(session, task.id)] == [
        (1, "failed"),
        (2, "succeeded"),
    ]
    _assert_single_queued_observe_step(session)


def test_worker_recovery_settles_succeeded_discovery_without_broker_or_duplicates(
    session: Session,
) -> None:
    job = session.scalar(select(JobRecord).where(JobRecord.workflow_id == "workflow-1"))
    assert job is not None
    task = session.get_one(TaskRecord, cast(str, job.task_id))
    job.status = "leased"
    job.lease_token = "crash-succeeded"
    job.lease_expires_at = utc_now() + timedelta(minutes=1)
    task.status = "running"
    session.commit()
    sent_broker = _CountingPubmedBroker()
    observation = PaperSearchAdapter().execute(
        session,
        workflow=_workflow(session),
        discovery_spec=_spec(session),
        job=job,
        query_id="query-primary",
        provider="pubmed",
        attempt=job.attempt,
        lease_token="crash-succeeded",
        broker=sent_broker,
    )
    assert observation.status == "succeeded"
    provisional_task = session.get_one(TaskRecord, task.id)
    assert provisional_task.status == "running"
    assert provisional_task.outputs["invocationId"] == observation.invocation_id
    assert provisional_task.outputs["terminalResultSha256"]
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    session.commit()

    recovery_broker = _CountingPubmedBroker()
    restarted = WorkflowWorker(
        sessionmaker(bind=session.bind, expire_on_commit=False),
        discovery_broker_factory=lambda: recovery_broker,
    )
    restarted.recover()

    session.expire_all()
    recovered_job = session.get_one(JobRecord, job.id)
    recovered_task = session.get_one(TaskRecord, task.id)
    invocation = session.get_one(ToolInvocationRecord, observation.invocation_id)
    assert sent_broker.call_count == 1
    assert recovery_broker.call_count == 0
    assert recovered_job.status == "succeeded"
    assert recovered_task.status == "completed"
    assert recovered_task.outputs["invocationId"] == invocation.id
    assert recovered_task.outputs["terminalResultSha256"]
    assert session.scalar(select(func.count(CandidateOccurrenceRecord.candidate_id))) == 1
    assert session.scalar(select(func.count(DiscoveryCandidateRecord.id))) == 1
    assert session.scalar(
        select(JobRecord.id).where(
            JobRecord.workflow_id == "workflow-1",
            JobRecord.kind == "observe-step",
            JobRecord.status == "queued",
        )
    ) is not None


@pytest.mark.parametrize("tamper", ["output-sha", "candidate-set", "occurrence"])
def test_worker_recovery_refuses_tampered_provisional_success_anchor(
    session: Session,
    tamper: str,
) -> None:
    job = session.scalar(select(JobRecord).where(JobRecord.workflow_id == "workflow-1"))
    assert job is not None
    task = session.get_one(TaskRecord, cast(str, job.task_id))
    job.status = "leased"
    job.lease_token = "tampered-success"
    job.lease_expires_at = utc_now() + timedelta(minutes=1)
    task.status = "running"
    session.commit()
    result = PaperSearchAdapter().execute(
        session,
        workflow=_workflow(session),
        discovery_spec=_spec(session),
        job=job,
        query_id="query-primary",
        provider="pubmed",
        attempt=job.attempt,
        lease_token="tampered-success",
        broker=_CountingPubmedBroker(),
    )
    invocation = session.get_one(ToolInvocationRecord, result.invocation_id)
    if tamper == "output-sha":
        invocation.output_sha256 = "f" * 64
    elif tamper == "candidate-set":
        invocation.candidate_set_sha256 = "f" * 64
    else:
        occurrence = session.scalar(
            select(CandidateOccurrenceRecord).where(
                CandidateOccurrenceRecord.invocation_id == invocation.id
            )
        )
        assert occurrence is not None
        session.delete(occurrence)
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    session.commit()

    broker = _CountingPubmedBroker()
    WorkflowWorker(
        sessionmaker(bind=session.bind, expire_on_commit=False),
        discovery_broker_factory=lambda: broker,
    ).recover()
    session.expire_all()
    assert broker.call_count == 0
    assert [(item.attempt, item.status) for item in _discovery_jobs(session, task.id)] == [
        (1, "failed"),
    ]
    assert session.get_one(JobRecord, job.id).status == "failed"
    _assert_single_queued_observe_step(session)


def test_worker_recovery_does_not_create_third_attempt_after_second_prepared_crash(
    session: Session,
) -> None:
    first = session.scalar(select(JobRecord).where(JobRecord.workflow_id == "workflow-1"))
    assert first is not None
    task = session.get_one(TaskRecord, cast(str, first.task_id))
    first.status = "leased"
    first.lease_token = "prepared-first"
    first.lease_expires_at = utc_now() - timedelta(seconds=1)
    task.status = "running"
    session.add(_crash_window_invocation(first, status="prepared", suffix="-first"))
    session.commit()
    worker = WorkflowWorker(sessionmaker(bind=session.bind, expire_on_commit=False))
    worker.recover()
    session.expire_all()
    second = session.scalar(
        select(JobRecord).where(
            JobRecord.workflow_id == "workflow-1",
            JobRecord.kind == "execute-task",
            JobRecord.task_id == task.id,
            JobRecord.attempt == 2,
        )
    )
    assert second is not None
    task = session.get_one(TaskRecord, cast(str, second.task_id))
    second.status = "leased"
    second.lease_token = "prepared-second"
    second.lease_expires_at = utc_now() - timedelta(seconds=1)
    task.status = "running"
    session.add(_crash_window_invocation(second, status="prepared", suffix="-second"))
    session.commit()

    worker.recover()
    session.expire_all()
    assert [item.attempt for item in _discovery_jobs(session, task.id)] == [1, 2]
    invocation = session.get_one(ToolInvocationRecord, "invocation-prepared-second")
    assert invocation.error_code == "discovery-retry-exhausted"
    assert session.get_one(JobRecord, second.id).status == "failed"
    assert session.get_one(TaskRecord, task.id).status == "failed"
    _assert_single_queued_observe_step(session)


def test_worker_recovery_settles_existing_discovery_failure_without_retry(
    session: Session,
) -> None:
    job = session.scalar(select(JobRecord).where(JobRecord.workflow_id == "workflow-1"))
    assert job is not None
    task = session.get_one(TaskRecord, cast(str, job.task_id))
    job.status = "leased"
    job.lease_token = "known-failure"
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    task.status = "running"
    invocation = _crash_window_invocation(job, status="failed")
    invocation.error_code = "provider-disabled"
    invocation.error_message = "The paper-search connector did not return a usable result."
    invocation.returned_count = 0
    invocation.novel_candidate_count = 0
    invocation.duplicate_count = 0
    invocation.finished_at = utc_now()
    session.add(invocation)
    session.commit()

    broker = _CountingPubmedBroker()
    WorkflowWorker(
        sessionmaker(bind=session.bind, expire_on_commit=False),
        discovery_broker_factory=lambda: broker,
    ).recover()
    session.expire_all()
    assert broker.call_count == 0
    assert session.get_one(JobRecord, job.id).status == "failed"
    assert session.get_one(JobRecord, job.id).error_code == "provider-disabled"
    assert session.get_one(TaskRecord, task.id).status == "failed"
    assert len(list(session.scalars(select(JobRecord).where(JobRecord.workflow_id == "workflow-1")))) == 2


def test_worker_recovery_settles_existing_discovery_cancellation_without_retry(
    session: Session,
) -> None:
    job = session.scalar(select(JobRecord).where(JobRecord.workflow_id == "workflow-1"))
    assert job is not None
    task = session.get_one(TaskRecord, cast(str, job.task_id))
    job.status = "leased"
    job.lease_token = "known-cancellation"
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    task.status = "running"
    invocation = _crash_window_invocation(job, status="cancelled")
    invocation.returned_count = 0
    invocation.novel_candidate_count = 0
    invocation.duplicate_count = 0
    invocation.finished_at = utc_now()
    session.add(invocation)
    session.commit()

    WorkflowWorker(sessionmaker(bind=session.bind, expire_on_commit=False)).recover()
    session.expire_all()
    assert session.get_one(JobRecord, job.id).status == "cancelled"
    assert session.get_one(TaskRecord, task.id).status == "cancelled"
    assert len(list(session.scalars(select(JobRecord).where(JobRecord.workflow_id == "workflow-1")))) == 1
