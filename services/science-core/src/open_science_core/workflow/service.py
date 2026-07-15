from __future__ import annotations

from sqlalchemy.orm import Session

from ..model_gateway import model_gateway
from ..models import ProjectRecord, WorkflowRecord
from ._service.events import (
    append_workflow_events,
    transition_task,
    transition_workflow,
    workflow_events,
)
from ._service.integrity import (
    DATASET_PLAN_APPROVAL_REASON,
    LEGACY_HANDLER_VERSIONS,
    LOCAL_PLAN_APPROVAL_REASON,
    MAX_JOB_ATTEMPTS,
    PLAN_HANDLER_VERSION,
    REMOTE_PASSAGE_APPROVAL_REASON,
    REVIEW_HANDLER_VERSION,
    TASK_HANDLER_VERSION,
    TASK_PERMISSIONS_BY_TYPE,
    WorkflowConflict,
    assert_approved_plan_for_workflow,
    assert_plan_approval_integrity,
    assert_plan_for_workflow,
    assert_plan_integrity,
    assert_task_input_integrity,
    assert_task_matches_approved_plan,
    canonical_json_bytes,
    content_sha256,
    expected_plan_approval_semantics,
    plan_approval_hash,
    task_input_hash,
    task_materialization_hash,
    workflow_create_hash,
)
from ._service.integrity import (
    model_payload as model_payload,
)
from ._service.jobs import (
    analysis_execution_operation_key,
    current_job_input_hash,
    enqueue_job,
    handler_version_for,
    job_input_compatibility,
    job_input_hash_for_handler_version,
    latest_active_job,
    retry_delay_seconds,
)
from ._service.jobs import (
    job_input_payload as job_input_payload,
)
from ._service.jobs import (
    plan_job_envelope as plan_job_envelope,
)
from ._service.lifecycle import (
    accept_review_warnings,
    approve_analysis_execution,
    approve_plan,
    decide_analysis_execution,
    materialize_plan_tasks,
    request_cancel,
    resume_workflow,
    retry_workflow,
)
from ._service.lifecycle import (
    start_workflow as _start_workflow,
)
from ._service.snapshots import (
    allowed_actions as allowed_actions,
)
from ._service.snapshots import (
    assert_result_sources_current as assert_result_sources_current,
)
from ._service.snapshots import (
    build_workflow_result,
    list_workflows,
    workflow_result_hash,
    workflow_snapshot,
)
from ._service.snapshots import (
    result_source_descriptors as result_source_descriptors,
)
from ._service.snapshots import (
    reviewed_result_snapshot as reviewed_result_snapshot,
)
from ._service.snapshots import (
    source_page_manifest_hash as source_page_manifest_hash,
)
from ._service.snapshots import (
    task_output_summary as task_output_summary,
)
from ._service.snapshots import (
    validated_review_result as validated_review_result,
)
from .schemas import ResearchWorkflowCreateIn

__all__ = [
    "DATASET_PLAN_APPROVAL_REASON",
    "LEGACY_HANDLER_VERSIONS",
    "LOCAL_PLAN_APPROVAL_REASON",
    "MAX_JOB_ATTEMPTS",
    "PLAN_HANDLER_VERSION",
    "REMOTE_PASSAGE_APPROVAL_REASON",
    "REVIEW_HANDLER_VERSION",
    "TASK_HANDLER_VERSION",
    "TASK_PERMISSIONS_BY_TYPE",
    "WorkflowConflict",
    "accept_review_warnings",
    "append_workflow_events",
    "analysis_execution_operation_key",
    "approve_analysis_execution",
    "approve_plan",
    "decide_analysis_execution",
    "assert_approved_plan_for_workflow",
    "assert_plan_approval_integrity",
    "assert_plan_for_workflow",
    "assert_plan_integrity",
    "assert_task_input_integrity",
    "assert_task_matches_approved_plan",
    "build_workflow_result",
    "canonical_json_bytes",
    "content_sha256",
    "current_job_input_hash",
    "enqueue_job",
    "expected_plan_approval_semantics",
    "handler_version_for",
    "job_input_compatibility",
    "job_input_hash_for_handler_version",
    "latest_active_job",
    "list_workflows",
    "materialize_plan_tasks",
    "model_gateway",
    "plan_approval_hash",
    "request_cancel",
    "resume_workflow",
    "retry_delay_seconds",
    "retry_workflow",
    "start_workflow",
    "task_input_hash",
    "task_materialization_hash",
    "transition_task",
    "transition_workflow",
    "workflow_create_hash",
    "workflow_events",
    "workflow_result_hash",
    "workflow_snapshot",
]


def start_workflow(
    session: Session,
    project: ProjectRecord,
    payload: ResearchWorkflowCreateIn,
    idempotency_key: str,
) -> WorkflowRecord:
    return _start_workflow(
        session,
        project,
        payload,
        idempotency_key,
        gateway=model_gateway,
    )
