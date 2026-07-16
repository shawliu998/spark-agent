from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis import canonical_analysis_payload, validate_python_code
from ..fixed_analysis_policy import (
    COMPILED_ANALYSIS_POLICY_ID,
    COMPILED_ANALYSIS_TEMPLATE,
    FIXED_ANALYSIS_POLICY_ID,
    AnalysisPolicyId,
    AnalysisPolicyTemplate,
    FixedAnalysisPolicyError,
    fixed_analysis_template_for_repair_attempt,
)
from ..models import (
    AnalysisIntentRecord,
    AnalysisSpecRecord,
    ApprovalRecord,
    ArtifactRecord,
    PlanRecord,
    ProjectRecord,
    RunRecord,
    SourceRecord,
    StructuredAnalysisResultRecord,
    TaskRecord,
    WorkflowRecord,
)
from ..workflow.schemas import AnalysisErrorSummaryOut
from .contracts import analysis_code_diff, canonical_workflow_analysis_payload
from .errors import (
    ANALYSIS_ACTION,
    ANALYSIS_APPROVAL_REASON,
    ANALYSIS_RISK_LEVEL,
    ANALYSIS_V1_SCHEMA,
    ANALYSIS_V2_SCHEMA,
    ANALYSIS_V3_SCHEMA,
    ANALYSIS_V4_SCHEMA,
    WORKFLOW_ANALYSIS_APPROVAL_REASON,
    AnalysisServiceError,
)

_MAX_HISTORICAL_WORKFLOW_REVISION = 10_000
_RUNTIME_REQUIRED_ARTIFACTS = {
    "input.ipynb": "notebook-input",
    "executed.ipynb": "notebook-executed",
    "environment.json": "environment",
    "stdout.txt": "stdout",
    "stderr.txt": "stderr",
    "execution.log": "log",
}
_RUNTIME_RESERVED_NAMES = set(_RUNTIME_REQUIRED_ARTIFACTS)


def validated_execution_records(
    session: Session,
    intent_id: str,
    *,
    expected_workflow_id: str | None,
    approval_workflow_revision: int | None,
) -> tuple[
    AnalysisIntentRecord,
    ProjectRecord,
    SourceRecord,
    TaskRecord,
    ApprovalRecord,
    str,
]:
    intent = intent_or_error(session, intent_id)
    if expected_workflow_id is None:
        if intent.workflow_id is not None:
            raise AnalysisServiceError(
                409,
                "Workflow analysis execution requires the internal workflow service",
                code="workflow-analysis-endpoint-required",
            )
    else:
        if approval_workflow_revision is None:
            raise AnalysisServiceError(
                409,
                "The approved workflow revision is required",
                code="analysis-approval-binding-invalid",
            )
        validate_workflow_analysis_intent(
            session,
            intent,
            expected_workflow_id=expected_workflow_id,
            expected_workflow_revision=approval_workflow_revision,
            require_approval=True,
            require_current_revision=False,
        )

    project = project_or_error(session, intent.project_id)
    dataset = session.get(SourceRecord, intent.dataset_source_id)
    task = session.get(TaskRecord, intent.task_id)
    approval = approval_for_intent(session, intent)
    if dataset is None or task is None or approval is None:
        raise AnalysisServiceError(
            409,
            "Analysis execution records are incomplete",
            code="analysis-records-incomplete",
        )
    assert_intent_binding(session, intent, task, dataset, project)
    if intent.workflow_id is None:
        if task.status != "waiting-execution":
            raise AnalysisServiceError(
                409,
                "Standalone analysis task is not waiting for execution",
                code="analysis-status-conflict",
            )
    else:
        workflow = session.get(WorkflowRecord, intent.workflow_id)
        if (
            workflow is None
            or workflow.status != "running"
            or workflow.cancel_requested_at is not None
            or task.status in {"completed", "failed", "cancelled"}
        ):
            raise AnalysisServiceError(
                409,
                "Workflow is not in an executable analysis state",
                code="analysis-status-conflict",
            )
    assert_approval_record(session, intent, approval)
    current_hash = recompute_approval_hash(
        session,
        intent,
        expected_workflow_revision=approval_workflow_revision,
    )
    if current_hash != intent.payload_sha256 or approval.intent_hash != current_hash:
        raise AnalysisServiceError(
            409,
            "Approved payload hash no longer matches the intent",
            code="analysis-payload-mismatch",
        )
    if intent.decision != "approved" or approval.user_decision != "approved":
        raise AnalysisServiceError(
            409,
            "Analysis intent must be explicitly approved",
            code="analysis-approval-required",
        )
    if intent.status != "approved":
        raise AnalysisServiceError(
            409,
            f"Analysis intent cannot execute from {intent.status}",
            code="analysis-status-conflict",
        )
    return intent, project, dataset, task, approval, current_hash


def validate_workflow_analysis_intent(
    session: Session,
    intent: AnalysisIntentRecord,
    *,
    expected_workflow_id: str,
    expected_workflow_revision: int,
    require_approval: bool,
    require_current_revision: bool,
) -> None:
    if intent.workflow_id != expected_workflow_id:
        raise AnalysisServiceError(
            409,
            "Analysis intent does not belong to the expected workflow",
            code="analysis-workflow-mismatch",
        )
    workflow = session.get(WorkflowRecord, expected_workflow_id)
    task = session.get(TaskRecord, intent.task_id)
    project = session.get(ProjectRecord, intent.project_id)
    dataset = session.get(SourceRecord, intent.dataset_source_id)
    if workflow is None or task is None or project is None or dataset is None:
        raise AnalysisServiceError(
            409,
            "Workflow analysis provenance records are incomplete",
            code="analysis-records-incomplete",
        )
    if require_current_revision and workflow.row_version != expected_workflow_revision:
        raise AnalysisServiceError(
            409,
            "The workflow revision does not match the approved analysis payload",
            code="workflow-revision-conflict",
        )
    assert_intent_binding(session, intent, task, dataset, project)
    expected_hash = recompute_approval_hash(
        session,
        intent,
        expected_workflow_revision=expected_workflow_revision,
    )
    if expected_hash != intent.payload_sha256:
        raise AnalysisServiceError(
            409,
            "Workflow analysis payload no longer matches its immutable hash",
            code="analysis-payload-mismatch",
        )
    if require_approval:
        approval = approval_for_intent(session, intent)
        if approval is None:
            raise AnalysisServiceError(
                409,
                "Workflow analysis approval record is missing",
                code="analysis-audit-missing",
            )
        assert_approval_record(session, intent, approval)
        if approval.intent_hash != expected_hash:
            raise AnalysisServiceError(
                409,
                "Workflow analysis approval payload does not match the intent",
                code="analysis-approval-binding-invalid",
            )


def assert_intent_binding(
    session: Session,
    intent: AnalysisIntentRecord,
    task: TaskRecord,
    dataset: SourceRecord,
    project: ProjectRecord,
    *,
    run: RunRecord | None = None,
    allow_legacy_null_run_link: bool = False,
) -> None:
    if (
        task.id != intent.task_id
        or task.project_id != intent.project_id
        or project.id != intent.project_id
        or dataset.id != intent.dataset_source_id
        or dataset.project_id != intent.project_id
        or task.task_type != "python-data-analysis"
    ):
        raise AnalysisServiceError(
            409,
            "Analysis task, project, and dataset bindings do not match",
            code="analysis-binding-invalid",
        )
    assert_ready_dataset(dataset, detail="Selected dataset is not ready")
    if intent.workflow_id is None:
        if task.workflow_id is not None:
            raise AnalysisServiceError(
                409,
                "Standalone analysis intent cannot use a workflow task",
                code="analysis-binding-invalid",
            )
    else:
        workflow = session.get(WorkflowRecord, intent.workflow_id)
        plan = session.get(PlanRecord, task.plan_id) if task.plan_id is not None else None
        if workflow is None or plan is None:
            raise AnalysisServiceError(
                409,
                "Analysis workflow or approved plan record is missing",
                code="analysis-records-incomplete",
            )
        assert_workflow_task_dataset_binding(workflow, task, dataset)
        historical_superseded = (
            plan.status == "superseded" and intent.status in {"completed", "failed"}
        )
        if plan.workflow_id != workflow.id or (
            plan.status != "approved" and not historical_superseded
        ):
            raise AnalysisServiceError(
                409,
                "Analysis task is not bound to the current approved plan",
                code="analysis-binding-invalid",
            )
        if (
            intent.plan_step_id != "execute-analysis"
            or intent.dataset_content_hash != dataset.content_hash
            or intent.objective != task.objective
            or intent.expected_outputs != task.expected_outputs
            or intent.timeout_seconds != task.timeout_seconds
            or intent.risk_level != ANALYSIS_RISK_LEVEL
        ):
            raise AnalysisServiceError(
                409,
                "Workflow analysis intent fields do not match the approved task",
                code="analysis-binding-invalid",
            )
        previous = (
            session.get(AnalysisIntentRecord, intent.previous_intent_id)
            if intent.previous_intent_id is not None
            else None
        )
        assert_repair_lineage(session, intent, previous)
        if intent.analysis_spec_id is not None:
            analysis_spec = session.get(AnalysisSpecRecord, intent.analysis_spec_id)
            plan_spec_id = plan.spec_json.get("analysisSpecId")
            plan_spec_sha256 = plan.spec_json.get("analysisSpecSha256")
            if (
                analysis_spec is None
                or analysis_spec.workflow_id != workflow.id
                or analysis_spec.status
                not in ({"approved", "superseded"} if historical_superseded else {"approved"})
                or plan_spec_id != analysis_spec.id
                or plan_spec_sha256 != analysis_spec.spec_sha256
                or intent.spec_sha256 != analysis_spec.spec_sha256
                or intent.dataset_profile_sha256
                != analysis_spec.dataset_profile_sha256
                or analysis_spec.dataset_source_id != dataset.id
                or analysis_spec.dataset_content_hash != dataset.content_hash
                or intent.compiler_version != COMPILED_ANALYSIS_TEMPLATE
                or intent.runtime_policy_id != COMPILED_ANALYSIS_POLICY_ID
                or intent.code_sha256
                != hashlib.sha256(intent.code.encode("utf-8")).hexdigest()
            ):
                raise AnalysisServiceError(
                    409,
                    "Compiled analysis no longer matches its approved AnalysisSpec",
                    code="analysis-spec-binding-invalid",
                )
    run_link_matches = run is not None and (
        run.analysis_intent_id == intent.id
        or (
            allow_legacy_null_run_link
            and run.analysis_intent_id is None
            and intent.workflow_id is None
        )
    )
    if run is not None and (
        not run_link_matches
        or run.task_id != intent.task_id
        or run.input_artifacts != [intent.dataset_source_id]
    ):
        raise AnalysisServiceError(
            409,
            "Analysis run provenance does not exactly match its intent",
            code="analysis-run-lineage-invalid",
        )


def assert_workflow_task_dataset_binding(
    workflow: WorkflowRecord,
    task: TaskRecord,
    dataset: SourceRecord | None,
) -> None:
    if workflow.workflow_type != "dataset-analysis" or dataset is None:
        raise AnalysisServiceError(
            409,
            "Workflow is not bound to a ready dataset",
            code="analysis-binding-invalid",
        )
    assert_ready_dataset(dataset)
    if (
        workflow.dataset_source_id != dataset.id
        or workflow.dataset_content_hash != dataset.content_hash
        or workflow.project_id != dataset.project_id
        or task.workflow_id != workflow.id
        or task.project_id != workflow.project_id
        or task.step_key != "execute-analysis"
        or task.task_type != "python-data-analysis"
        or task.risk_level != ANALYSIS_RISK_LEVEL
    ):
        raise AnalysisServiceError(
            409,
            "Workflow, analysis task, and dataset identity do not match",
            code="analysis-binding-invalid",
        )
    inputs = task.inputs
    if (
        inputs.get("datasetSourceId") != dataset.id
        or inputs.get("datasetContentHash") != dataset.content_hash
        or inputs.get("timeoutSeconds") != task.timeout_seconds
    ):
        raise AnalysisServiceError(
            409,
            "Analysis task inputs do not match the workflow dataset",
            code="analysis-binding-invalid",
        )


def assert_workflow_execution_inputs(task: TaskRecord, outputs: list[str]) -> None:
    raw_outputs = task.inputs.get("expectedOutputs")
    if (
        not outputs
        or len(outputs) != len(set(outputs))
        or task.expected_outputs != outputs
        or raw_outputs != outputs
    ):
        raise AnalysisServiceError(
            409,
            "Analysis outputs do not match the approved execution step",
            code="analysis-binding-invalid",
        )


def assert_repair_lineage(
    session: Session,
    intent: AnalysisIntentRecord,
    previous: AnalysisIntentRecord | None,
) -> None:
    attempt = intent.repair_attempt
    if attempt == 0:
        if (
            intent.previous_intent_id is not None
            or intent.code_diff is not None
            or (intent.status != "failed" and intent.error_summary is not None)
            or (intent.status == "failed" and intent.error_summary is None)
        ):
            raise AnalysisServiceError(
                409,
                "Initial analysis intent contains repair-only fields",
                code="analysis-lineage-invalid",
            )
        if intent.error_summary is not None:
            try:
                AnalysisErrorSummaryOut.model_validate(intent.error_summary)
            except ValueError as error:
                raise AnalysisServiceError(
                    409,
                    "Analysis failure summary is invalid",
                    code="analysis-lineage-invalid",
                ) from error
        return
    previous_runs = (
        list(session.scalars(select(RunRecord).where(RunRecord.analysis_intent_id == previous.id)))
        if previous is not None
        else []
    )
    previous_approval = approval_for_intent(session, previous) if previous is not None else None
    sibling_ids: set[str] = (
        set(
            session.scalars(
                select(AnalysisIntentRecord.id).where(
                    AnalysisIntentRecord.previous_intent_id == previous.id
                )
            )
        )
        if previous is not None
        else set()
    )
    expected_diff = analysis_code_diff(previous.code, intent.code) if previous is not None else None
    if (
        attempt not in {1, 2}
        or previous is None
        or intent.previous_intent_id != previous.id
        or previous.workflow_id != intent.workflow_id
        or previous.task_id != intent.task_id
        or previous.project_id != intent.project_id
        or previous.dataset_source_id != intent.dataset_source_id
        or previous.dataset_content_hash != intent.dataset_content_hash
        or previous.repair_attempt is None
        or attempt != previous.repair_attempt + 1
        or previous.status != "failed"
        or previous.decision != "approved"
        or previous_approval is None
        or previous_approval.user_decision != "approved"
        or len(previous_runs) != 1
        or previous_runs[0].status != "failed"
        or previous_runs[0].task_id != intent.task_id
        or previous_runs[0].input_artifacts != [intent.dataset_source_id]
        or previous_runs[0].finished_at is None
        or sibling_ids - {intent.id}
        or intent.error_summary is None
        or (
            intent.status in {"waiting-approval", "approved", "executing", "completed"}
            and intent.error_summary != previous.error_summary
        )
        or intent.code_diff is None
        or intent.code_diff != expected_diff
        or not expected_diff
    ):
        raise AnalysisServiceError(
            409,
            "Analysis repair lineage is invalid",
            code="analysis-lineage-invalid",
        )
    assert previous is not None and previous_approval is not None
    _assert_historical_workflow_intent(session, previous, previous_approval)
    try:
        AnalysisErrorSummaryOut.model_validate(intent.error_summary)
    except ValueError as error:
        raise AnalysisServiceError(
            409,
            "Analysis repair error summary is invalid",
            code="analysis-lineage-invalid",
        ) from error


def _assert_historical_workflow_intent(
    session: Session,
    intent: AnalysisIntentRecord,
    approval: ApprovalRecord,
) -> int:
    """Revalidate an approved predecessor without trusting its mutable row fields."""

    task = session.get(TaskRecord, intent.task_id)
    dataset = session.get(SourceRecord, intent.dataset_source_id)
    project = session.get(ProjectRecord, intent.project_id)
    if task is None or dataset is None or project is None:
        raise AnalysisServiceError(
            409,
            "Analysis repair predecessor records are incomplete",
            code="analysis-lineage-invalid",
        )
    assert_intent_binding(session, intent, task, dataset, project)
    assert_approval_record(session, intent, approval)
    if intent.workflow_id is None:
        raise AnalysisServiceError(
            409,
            "Analysis repair predecessor is not workflow-bound",
            code="analysis-lineage-invalid",
        )
    workflow = session.get(WorkflowRecord, intent.workflow_id)
    if (
        workflow is None
        or workflow.row_version < 1
        or workflow.row_version > _MAX_HISTORICAL_WORKFLOW_REVISION
    ):
        raise AnalysisServiceError(
            409,
            "Analysis repair predecessor workflow revision is invalid",
            code="analysis-lineage-invalid",
        )

    matching_revisions: list[int] = []
    for candidate_revision in range(1, workflow.row_version + 1):
        candidate_hash = recompute_approval_hash(
            session,
            intent,
            expected_workflow_revision=candidate_revision,
        )
        if candidate_hash == intent.payload_sha256 == approval.intent_hash:
            matching_revisions.append(candidate_revision)
    if len(matching_revisions) != 1:
        raise AnalysisServiceError(
            409,
            "Analysis repair predecessor no longer matches its approved payload",
            code="analysis-lineage-invalid",
        )
    return matching_revisions[0]


def assert_persisted_intent_approval(
    session: Session,
    intent: AnalysisIntentRecord,
) -> int | None:
    approval = approval_for_intent(session, intent)
    if approval is None:
        raise AnalysisServiceError(
            409,
            "Analysis approval audit record is missing",
            code="analysis-audit-missing",
        )
    if intent.decision != "approved" or approval.user_decision != "approved":
        raise AnalysisServiceError(
            409,
            "Analysis run does not have an approved immutable intent",
            code="analysis-approval-required",
        )
    if intent.workflow_id is None:
        assert_approval_record(session, intent, approval)
        return None
    return _assert_historical_workflow_intent(session, intent, approval)


def assert_completed_run_binding(
    session: Session,
    run: RunRecord,
    intent: AnalysisIntentRecord,
) -> list[ArtifactRecord]:
    artifacts = _artifacts_for_run(session, run)
    valid = (
        run.status == "completed"
        and run.finished_at is not None
        and _common_artifact_binding_is_valid(run, intent, artifacts)
        and _full_runtime_evidence_is_valid(run, artifacts)
    )
    if valid and intent.analysis_spec_id is not None:
        structured_results = list(
            session.scalars(
                select(StructuredAnalysisResultRecord).where(
                    StructuredAnalysisResultRecord.run_id == run.id
                )
            )
        )
        result_artifacts = [
            artifact
            for artifact in artifacts
            if Path(artifact.path).name == "results.json"
        ]
        valid = (
            len(structured_results) == 1
            and len(result_artifacts) == 1
            and structured_results[0].analysis_spec_id == intent.analysis_spec_id
            and structured_results[0].analysis_intent_id == intent.id
            and structured_results[0].run_id == run.id
            and result_artifacts[0].metadata_json.get("structuredResultSha256")
            == structured_results[0].result_sha256
        )
    if not valid:
        raise AnalysisServiceError(
            409,
            "Completed analysis run evidence is incomplete or inconsistent",
            code="analysis-run-terminal-invalid",
        )
    return artifacts


def assert_failed_run_binding(
    session: Session,
    run: RunRecord,
    intent: AnalysisIntentRecord,
) -> list[ArtifactRecord]:
    artifacts = _artifacts_for_run(session, run)
    valid = (
        run.status == "failed"
        and run.finished_at is not None
        and _common_artifact_binding_is_valid(run, intent, artifacts)
    )
    run_prefix = Path("runs") / run.id
    if valid and not artifacts:
        valid = run.environment_hash is None and run.logs_path is None
    elif valid and len(artifacts) == 1:
        artifact = artifacts[0]
        core_log_path = (run_prefix / "core-execution-error.log").as_posix()
        valid = (
            run.environment_hash is None
            and run.logs_path == core_log_path
            and artifact.path == core_log_path
            and artifact.artifact_type == "log"
        )
    elif valid:
        valid = _full_runtime_evidence_is_valid(run, artifacts)
    if not valid:
        raise AnalysisServiceError(
            409,
            "Failed analysis run evidence is incomplete or inconsistent",
            code="analysis-run-terminal-invalid",
        )
    return artifacts


def _artifacts_for_run(session: Session, run: RunRecord) -> list[ArtifactRecord]:
    return list(
        session.scalars(
            select(ArtifactRecord)
            .where(ArtifactRecord.run_id == run.id)
            .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
        )
    )


def _common_artifact_binding_is_valid(
    run: RunRecord,
    intent: AnalysisIntentRecord,
    artifacts: list[ArtifactRecord],
) -> bool:
    output_artifacts = _validated_string_list(run.output_artifacts)
    if output_artifacts is None:
        return False
    paths = [artifact.path for artifact in artifacts]
    run_prefix = Path("runs") / run.id
    return not (
        len(paths) != len(set(paths))
        or len(output_artifacts) != len(set(output_artifacts))
        or set(paths) != set(output_artifacts)
        or any(
            not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or run_prefix not in Path(path).parents
            or (
                Path(path).name in _RUNTIME_RESERVED_NAMES
                and Path(path).parent != run_prefix
            )
            for path in paths
        )
        or any(
            len(artifact.content_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in artifact.content_hash
            )
            or artifact.parent_artifacts != [intent.dataset_source_id]
            or not isinstance(artifact.metadata_json.get("sizeBytes"), int)
            or isinstance(artifact.metadata_json.get("sizeBytes"), bool)
            or artifact.metadata_json["sizeBytes"] < 0
            or artifact.metadata_json.get("payloadSha256") != intent.payload_sha256
            for artifact in artifacts
        )
    )


def _validated_string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items = cast(list[object], value)
    if any(not isinstance(item, str) for item in items):
        return None
    return cast(list[str], items)


def _full_runtime_evidence_is_valid(
    run: RunRecord,
    artifacts: list[ArtifactRecord],
) -> bool:
    if (
        not isinstance(run.environment_hash, str)
        or len(run.environment_hash) != 64
        or any(character not in "0123456789abcdef" for character in run.environment_hash)
    ):
        return False
    run_prefix = Path("runs") / run.id
    by_path = {artifact.path: artifact for artifact in artifacts}
    required_paths = {
        (run_prefix / name).as_posix(): artifact_type
        for name, artifact_type in _RUNTIME_REQUIRED_ARTIFACTS.items()
    }
    environment_artifacts = [
        artifact for artifact in artifacts if artifact.artifact_type == "environment"
    ]
    return not any(
        path not in by_path or by_path[path].artifact_type != artifact_type
        for path, artifact_type in required_paths.items()
    ) and (
        len(environment_artifacts) == 1
        and environment_artifacts[0].content_hash == run.environment_hash
        and run.logs_path == (run_prefix / "execution.log").as_posix()
    )


def assert_approval_record(
    session: Session,
    intent: AnalysisIntentRecord,
    approval: ApprovalRecord,
) -> None:
    if (
        intent.workflow_id is not None
        and approval.payload_schema_version == ANALYSIS_V2_SCHEMA
    ):
        raise AnalysisServiceError(
            409,
            "The approved workflow analysis predates execution policy binding",
            code="analysis-policy-binding-upgrade-required",
        )
    expected_schema = (
        ANALYSIS_V4_SCHEMA
        if intent.workflow_id is not None and intent.analysis_spec_id is not None
        else ANALYSIS_V3_SCHEMA
        if intent.workflow_id is not None
        else ANALYSIS_V1_SCHEMA
    )
    task = session.get(TaskRecord, intent.task_id)
    if task is None:
        raise AnalysisServiceError(
            409,
            "Analysis approval task is missing",
            code="analysis-approval-binding-invalid",
        )
    expected_plan_id = task.plan_id if intent.workflow_id is not None else None
    expected_reason = (
        WORKFLOW_ANALYSIS_APPROVAL_REASON
        if intent.workflow_id is not None
        else ANALYSIS_APPROVAL_REASON
    )
    if intent.workflow_id is None:
        expected_resources = [intent.dataset_source_id, "runs/<run-id>"]
    else:
        expected_resources = [
            f"source:{intent.dataset_source_id}:sha256:{intent.dataset_content_hash}",
        ]
        if intent.analysis_spec_id is not None:
            expected_resources.extend(
                [
                    f"analysis-spec:{intent.analysis_spec_id}:sha256:{intent.spec_sha256}",
                    f"analysis-code:sha256:{intent.code_sha256}",
                    f"runtime-policy:{intent.runtime_policy_id}",
                ]
            )
        expected_resources.append("runs/<run-id>")
    expected_intent_hash = intent.payload_sha256
    if intent.workflow_id is None:
        _canonical, expected_intent_hash = canonical_analysis_payload(
            intent.dataset_source_id,
            intent.objective,
            intent.code,
        )
    if (
        approval.task_id != intent.task_id
        or approval.workflow_id != intent.workflow_id
        or approval.plan_id != expected_plan_id
        or approval.subject_type != "analysis-intent"
        or approval.subject_id != intent.id
        or approval.payload_schema_version != expected_schema
        or approval.requested_action != ANALYSIS_ACTION
        or approval.risk_level != ANALYSIS_RISK_LEVEL
        or approval.reason != expected_reason
        or approval.affected_resources != expected_resources
        or intent.payload_sha256 != expected_intent_hash
        or approval.intent_hash != expected_intent_hash
        or approval.user_decision != intent.decision
        or approval.row_version != 1
        or (approval.user_decision is None) != (approval.decided_at is None)
        or (
            approval.user_decision is not None
            and approval.user_decision not in {"approved", "rejected"}
        )
    ):
        raise AnalysisServiceError(
            409,
            "Stored approval does not exactly match the analysis intent",
            code="analysis-approval-binding-invalid",
        )


def approval_for_intent(
    session: Session,
    intent: AnalysisIntentRecord,
) -> ApprovalRecord | None:
    exact = list(
        session.scalars(
            select(ApprovalRecord).where(
                ApprovalRecord.task_id == intent.task_id,
                ApprovalRecord.subject_type == "analysis-intent",
                ApprovalRecord.subject_id == intent.id,
                ApprovalRecord.requested_action == ANALYSIS_ACTION,
            )
        )
    )
    if len(exact) > 1:
        raise AnalysisServiceError(
            409,
            "Multiple approvals match the analysis intent",
            code="analysis-approval-ambiguous",
        )
    if exact:
        return exact[0]
    if intent.workflow_id is not None:
        return None
    legacy = list(
        session.scalars(
            select(ApprovalRecord).where(
                ApprovalRecord.task_id == intent.task_id,
                ApprovalRecord.subject_type.is_(None),
                ApprovalRecord.subject_id.is_(None),
                ApprovalRecord.requested_action == ANALYSIS_ACTION,
            )
        )
    )
    if len(legacy) > 1:
        raise AnalysisServiceError(
            409,
            "Legacy analysis approval is ambiguous",
            code="analysis-approval-ambiguous",
        )
    if legacy:
        # Normalize the one unambiguous pre-subject row before any new decision.
        approval = legacy[0]
        approval.subject_type = "analysis-intent"
        approval.subject_id = intent.id
        approval.payload_schema_version = ANALYSIS_V1_SCHEMA
        return approval
    return None


def recompute_approval_hash(
    session: Session,
    intent: AnalysisIntentRecord,
    *,
    expected_workflow_revision: int | None,
) -> str:
    if intent.workflow_id is None:
        _canonical, digest = canonical_analysis_payload(
            intent.dataset_source_id,
            intent.objective,
            intent.code,
        )
        return digest
    if (
        expected_workflow_revision is None
        or intent.plan_step_id is None
        or intent.dataset_content_hash is None
        or intent.expected_outputs is None
        or intent.timeout_seconds is None
        or intent.repair_attempt is None
    ):
        raise AnalysisServiceError(
            409,
            "Workflow analysis approval fields are incomplete",
            code="analysis-approval-binding-invalid",
        )
    task = session.get(TaskRecord, intent.task_id)
    if task is None or task.plan_id is None:
        raise AnalysisServiceError(
            409,
            "Workflow analysis plan binding is missing",
            code="analysis-approval-binding-invalid",
        )
    approval_error_summary: dict[str, Any] | None = None
    if intent.repair_attempt > 0:
        previous = session.get(AnalysisIntentRecord, intent.previous_intent_id)
        if previous is None or previous.error_summary is None:
            raise AnalysisServiceError(
                409,
                "Workflow analysis repair context is missing",
                code="analysis-approval-binding-invalid",
            )
        approval_error_summary = previous.error_summary
    approval = approval_for_intent(session, intent)
    if approval is None or approval.payload_schema_version not in {
        ANALYSIS_V3_SCHEMA,
        ANALYSIS_V4_SCHEMA,
    }:
        raise AnalysisServiceError(
            409,
            "Workflow analysis approval has no execution policy binding",
            code="analysis-policy-binding-upgrade-required",
        )
    if approval.payload_schema_version == ANALYSIS_V4_SCHEMA:
        compiled_fields = (
            intent.analysis_spec_id,
            intent.spec_sha256,
            intent.dataset_profile_sha256,
            intent.compiler_version,
            intent.code_sha256,
            intent.runtime_policy_id,
        )
        if (
            any(value is None for value in compiled_fields)
            or intent.runtime_policy_id != COMPILED_ANALYSIS_POLICY_ID
            or intent.compiler_version != COMPILED_ANALYSIS_TEMPLATE
        ):
            raise AnalysisServiceError(
                409,
                "Workflow analysis compiled provenance is invalid",
                code="analysis-approval-binding-invalid",
            )
        schema_version = ANALYSIS_V4_SCHEMA
        policy_profile_id = COMPILED_ANALYSIS_POLICY_ID
        policy_template: AnalysisPolicyTemplate = COMPILED_ANALYSIS_TEMPLATE
    else:
        try:
            policy_template = fixed_analysis_template_for_repair_attempt(
                intent.repair_attempt
            )
        except FixedAnalysisPolicyError as error:
            raise AnalysisServiceError(
                409,
                "Workflow analysis policy binding is invalid",
                code="analysis-approval-binding-invalid",
            ) from error
        schema_version = ANALYSIS_V3_SCHEMA
        policy_profile_id = FIXED_ANALYSIS_POLICY_ID
    _canonical, digest = canonical_workflow_analysis_payload(
        project_id=intent.project_id,
        workflow_id=intent.workflow_id,
        plan_id=task.plan_id,
        task_id=intent.task_id,
        analysis_intent_id=intent.id,
        plan_step_id=intent.plan_step_id,
        dataset_source_id=intent.dataset_source_id,
        dataset_content_hash=intent.dataset_content_hash,
        objective=intent.objective,
        expected_outputs=intent.expected_outputs,
        timeout_seconds=intent.timeout_seconds,
        code=intent.code,
        code_diff=intent.code_diff,
        error_summary=approval_error_summary,
        previous_intent_id=intent.previous_intent_id,
        repair_attempt=intent.repair_attempt,
        expected_workflow_revision=expected_workflow_revision,
        schema_version=schema_version,
        policy_profile_id=policy_profile_id,
        policy_template=policy_template,
        analysis_spec_id=intent.analysis_spec_id,
        analysis_spec_sha256=intent.spec_sha256,
        dataset_profile_sha256=intent.dataset_profile_sha256,
        compiler_version=intent.compiler_version,
        code_sha256=intent.code_sha256,
        runtime_policy_id=cast(AnalysisPolicyId | None, intent.runtime_policy_id),
    )
    return digest


def validate_code(
    code: str,
    *,
    policy_profile_id: AnalysisPolicyId | None = None,
    policy_template: AnalysisPolicyTemplate | None = None,
    approved_code_sha256: str | None = None,
) -> None:
    try:
        if policy_profile_id is None:
            validate_python_code(code)
        else:
            validate_python_code(
                code,
                policy_profile_id=policy_profile_id,
                policy_template=policy_template,
                approved_code_sha256=approved_code_sha256,
            )
    except ValueError as error:
        raise AnalysisServiceError(422, str(error), code="analysis-code-invalid") from error


def assert_ready_dataset(
    dataset: SourceRecord,
    *,
    detail: str = "Analysis requires a ready CSV dataset source",
) -> None:
    if dataset.source_kind != "dataset" or dataset.ingestion_status != "ready":
        raise AnalysisServiceError(
            409,
            detail,
            code="dataset-not-ready",
        )


def project_or_error(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise AnalysisServiceError(404, "Project not found", code="project-not-found")
    return project


def intent_or_error(session: Session, intent_id: str) -> AnalysisIntentRecord:
    intent = session.get(AnalysisIntentRecord, intent_id)
    if intent is None:
        raise AnalysisServiceError(
            404,
            "Analysis intent not found",
            code="analysis-intent-not-found",
        )
    return intent
