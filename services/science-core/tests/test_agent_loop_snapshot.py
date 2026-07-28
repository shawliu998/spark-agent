from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, null, select
from sqlalchemy.orm import Session, sessionmaker

from open_science_core.models import (
    AgentDecisionRecord,
    Base,
    EventRecord,
    JobRecord,
    ProjectRecord,
    StepObservationRecord,
    WorkflowRecord,
)
from open_science_core.workflow._service.events import append_workflow_events
from open_science_core.workflow._service.integrity import WorkflowConflict
from open_science_core.workflow.agent_loop.policy import (
    MAX_AGENT_STEPS,
    MAX_ANALYSIS_SPEC_REVISIONS,
    MAX_CLARIFICATION_ROUNDS,
    MAX_INVALID_MODEL_DECISIONS,
    MAX_MODEL_DECISIONS,
    MAX_PLAN_REVISIONS,
    MAX_STEP_RETRIES,
)
from open_science_core.workflow.agent_loop.schemas import (
    AgentDecision,
    ObservationFact,
    StepObservation,
    agent_decision_sha256,
    step_observation_sha256,
)
from open_science_core.workflow.agent_service import agent_run_snapshot
from open_science_core.workflow.research_memory import get_or_create_context_snapshot
from open_science_core.workflow.schemas import AgentDecisionEventData


@pytest.fixture
def snapshot_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(
            ProjectRecord(
                id="project-1",
                title="Agent loop snapshot",
                description="",
                project_path="/tmp/agent-loop-snapshot",
                execution_mode="safe",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _workflow(session: Session, workflow_id: str) -> WorkflowRecord:
    workflow = WorkflowRecord(
        id=workflow_id,
        project_id="project-1",
        create_idempotency_key=f"create-{workflow_id}",
        create_payload_sha256="a" * 64,
        creation_mode="autonomous",
        selected_source_ids=[],
        current_intent_decision_id=None,
        workflow_type=None,
        dataset_source_id=None,
        dataset_content_hash=None,
        goal="Inspect the bounded agent loop snapshot.",
        generation_mode="local-deterministic",
        status="routing",
        row_version=1,
        event_sequence=0,
    )
    session.add(workflow)
    session.flush()
    return workflow


def _source_job(session: Session, workflow: WorkflowRecord, job_id: str) -> JobRecord:
    job = JobRecord(
        id=job_id,
        workflow_id=workflow.id,
        task_id=None,
        kind="route-intent",
        operation_key=f"workflow:{workflow.id}:route",
        attempt=1,
        input_sha256="b" * 64,
        handler_version="agent-router-v1",
        status="succeeded",
    )
    session.add(job)
    session.flush()
    return job


def test_old_workflow_snapshot_has_empty_agent_loop_fields(
    snapshot_session: Session,
) -> None:
    workflow = _workflow(snapshot_session, "workflow-old")
    _source_job(snapshot_session, workflow, "job-old")
    snapshot_session.commit()

    snapshot = agent_run_snapshot(snapshot_session, workflow)

    assert snapshot.latest_observation is None
    assert snapshot.pending_decision is None
    assert snapshot.decision_history == []
    limits = snapshot.agent_loop_limits
    assert limits.agent_steps.model_dump() == {
        "count": 0,
        "limit": MAX_AGENT_STEPS,
        "reached": False,
    }
    assert limits.plan_revisions.limit == MAX_PLAN_REVISIONS
    assert limits.analysis_spec_revisions.limit == MAX_ANALYSIS_SPEC_REVISIONS
    assert limits.step_retries.limit == MAX_STEP_RETRIES
    assert limits.clarification_rounds.limit == MAX_CLARIFICATION_ROUNDS
    assert limits.model_decisions.limit == MAX_MODEL_DECISIONS
    assert limits.invalid_model_decisions.limit == MAX_INVALID_MODEL_DECISIONS


def test_snapshot_reads_latest_observation_pending_decision_and_history(
    snapshot_session: Session,
) -> None:
    workflow = _workflow(snapshot_session, "workflow-loop")
    job = _source_job(snapshot_session, workflow, "job-loop")
    observation = StepObservation(
        schema_version="1",
        workflow_id=workflow.id,
        plan_id=None,
        task_id=None,
        source_job_id=job.id,
        run_id=None,
        review_id=None,
        observation_type="pre-plan",
        step_key="select-analysis-method",
        attempt=1,
        status="blocked",
        facts=[
            ObservationFact(
                code="unsupported-capability",
                statement="The requested capability is outside the bounded methods.",
                value="survival-analysis",
                source_type="workflow",
                source_id=workflow.id,
            )
        ],
        warnings=[],
        unresolved_questions=[],
        artifact_ids=[],
        failure_category="unsupported",
        recommended_actions=["stop"],
    )
    observation_record = StepObservationRecord(
        id="observation-1",
        workflow_id=workflow.id,
        plan_id=None,
        task_id=None,
        source_job_id=job.id,
        run_id=None,
        review_id=None,
        schema_version="1",
        observation_type="pre-plan",
        step_key="select-analysis-method",
        attempt=1,
        status="blocked",
        facts_json=[item.model_dump(mode="json", by_alias=True) for item in observation.facts],
        warnings_json=[],
        unresolved_questions_json=[],
        artifact_ids_json=[],
        failure_category="unsupported",
        recommended_actions_json=["stop"],
        input_sha256="c" * 64,
        output_sha256=step_observation_sha256(observation),
        generator="deterministic-observer-v1",
        prompt_version=None,
        model=None,
        model_invocation_id=None,
    )
    snapshot_session.add(observation_record)
    decision = AgentDecision(
        schema_version="1",
        action="stop",
        reason_code="unsupported-capability",
        reason="No safe supported analysis action is available.",
        clarification_requests=[],
        requires_user_confirmation=False,
    )
    snapshot_session.add(
        AgentDecisionRecord(
            id="decision-1",
            workflow_id=workflow.id,
            observation_id=observation_record.id,
            schema_version="1",
            decision_revision=1,
            expected_workflow_revision=workflow.row_version,
            action="stop",
            reason_code="unsupported-capability",
            reason="No safe supported analysis action is available.",
            target_step_key=None,
            proposed_analysis_spec_json=null(),  # type: ignore[arg-type]
            proposed_analysis_spec_sha256=None,
            analysis_spec_diff_json=null(),  # type: ignore[arg-type]
            clarification_requests_json=[],
            requires_user_confirmation=False,
            generator="deterministic-action-policy-v1",
            prompt_version=None,
            model=None,
            model_invocation_id=None,
            input_sha256="d" * 64,
            output_sha256=agent_decision_sha256(decision),
            status="proposed",
            applied_at=None,
        )
    )
    snapshot_session.commit()

    snapshot = agent_run_snapshot(snapshot_session, workflow)

    assert snapshot.latest_observation is not None
    assert snapshot.latest_observation.id == observation_record.id
    assert snapshot.latest_observation.output_sha256 == observation_record.output_sha256
    assert snapshot.pending_decision is not None
    assert snapshot.pending_decision.id == "decision-1"
    assert snapshot.pending_decision.action == "stop"
    # A pre-provenance decision remains readable but must not imply memory use.
    assert snapshot.pending_decision.research_context_snapshot_id is None
    assert snapshot.pending_decision.research_context_snapshot_sha256 is None
    assert [item.id for item in snapshot.decision_history] == ["decision-1"]
    assert snapshot.agent_loop_limits.agent_steps.count == 1
    assert snapshot.agent_loop_limits.model_decisions.count == 0

    context_snapshot = get_or_create_context_snapshot(
        snapshot_session,
        workflow,
        plan_id=observation_record.plan_id,
        observation_id=observation_record.id,
    )
    proposed_event = AgentDecisionEventData(
        observation_id=observation_record.id,
        decision_id="decision-1",
        action="stop",
        expected_workflow_revision=workflow.row_version,
        reason_code="unsupported-capability",
        research_context_snapshot_id=context_snapshot.id,
        research_context_snapshot_sha256=context_snapshot.context_sha256,
    )
    append_workflow_events(
        snapshot_session,
        workflow,
        [("agent.decision-proposed", proposed_event, None, None)],
    )
    snapshot_session.flush()
    bound_snapshot = agent_run_snapshot(snapshot_session, workflow)
    assert bound_snapshot.pending_decision is not None
    assert (
        bound_snapshot.pending_decision.research_context_snapshot_id
        == context_snapshot.id
    )
    assert (
        bound_snapshot.pending_decision.research_context_snapshot_sha256
        == context_snapshot.context_sha256
    )
    assert (
        bound_snapshot.decision_history[0].research_context_snapshot_id
        == context_snapshot.id
    )

    event = snapshot_session.scalar(select(EventRecord))
    assert event is not None
    event.payload["observationId"] = "different-observation"
    with pytest.raises(WorkflowConflict, match="does not match"):
        agent_run_snapshot(snapshot_session, workflow)
    event.payload["observationId"] = observation_record.id
    append_workflow_events(
        snapshot_session,
        workflow,
        [("agent.decision-proposed", proposed_event, None, None)],
    )
    with pytest.raises(WorkflowConflict, match="unique proposed-event"):
        agent_run_snapshot(snapshot_session, workflow)


def test_snapshot_rejects_observation_hash_drift(snapshot_session: Session) -> None:
    workflow = _workflow(snapshot_session, "workflow-drift")
    job = _source_job(snapshot_session, workflow, "job-drift")
    snapshot_session.add(
        StepObservationRecord(
            id="observation-drift",
            workflow_id=workflow.id,
            plan_id=None,
            task_id=None,
            source_job_id=job.id,
            run_id=None,
            review_id=None,
            schema_version="1",
            observation_type="pre-plan",
            step_key="select-analysis-method",
            attempt=1,
            status="blocked",
            facts_json=[
                {
                    "code": "unsupported-capability",
                    "statement": "The capability is unsupported.",
                    "value": "survival-analysis",
                    "sourceType": "workflow",
                    "sourceId": workflow.id,
                }
            ],
            warnings_json=[],
            unresolved_questions_json=[],
            artifact_ids_json=[],
            failure_category="unsupported",
            recommended_actions_json=["stop"],
            input_sha256="e" * 64,
            output_sha256="f" * 64,
            generator="deterministic-observer-v1",
            prompt_version=None,
            model=None,
            model_invocation_id=None,
        )
    )
    snapshot_session.commit()

    with pytest.raises(WorkflowConflict, match="immutable hash"):
        agent_run_snapshot(snapshot_session, workflow)
