from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker

import open_science_core._analysis_service.execution as execution_module
import open_science_core._analysis_service.filesystem as filesystem_module
import open_science_core._analysis_service.intents as intents_module
import open_science_core.analysis as analysis_module
from open_science_core.analysis import (
    RuntimeExecutionResult,
    RuntimeServiceError,
    canonical_analysis_payload,
)
from open_science_core.analysis_service import (
    AnalysisServiceError,
    analysis_code_diff,
    canonical_workflow_analysis_payload,
    create_standalone_analysis_intent,
    create_workflow_analysis_intent,
    decide_standalone_analysis_intent,
    decide_workflow_analysis_intent,
    execute_standalone_analysis_intent,
    execute_workflow_analysis_intent,
    list_project_analysis_runs,
    recover_interrupted_analysis_state,
    resolve_analysis_intent_for_run,
    validate_workflow_analysis_intent,
)
from open_science_core.config import settings
from open_science_core.db import Base
from open_science_core.fixed_analysis_policy import fixed_analysis_source
from open_science_core.models import (
    AnalysisIntentRecord,
    ApprovalRecord,
    ArtifactRecord,
    PlanRecord,
    ProjectRecord,
    RunRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from open_science_core.schemas import AnalysisIntentCreate
from runtime_attestation import write_attested_runtime_result

WORKFLOW_BASELINE_CODE = fixed_analysis_source("baseline", selected_column_index=1)
WORKFLOW_REPAIR_ONE_CODE = fixed_analysis_source("repair-1")
WORKFLOW_REPAIR_TWO_CODE = fixed_analysis_source("repair-2")


@dataclass(frozen=True, slots=True)
class ServiceEnvironment:
    engine: Engine
    session_factory: sessionmaker[Session]
    root: Path
    exchange: Path
    dataset_path: Path
    dataset_hash: str


@pytest.fixture
def service_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[ServiceEnvironment, None, None]:
    database_path = tmp_path / "service.sqlite3"
    engine = create_engine(f"sqlite:///{database_path}")

    def _configure_sqlite(dbapi_connection: DBAPIConnection, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=1000")
        cursor.close()

    event.listen(engine, "connect", _configure_sqlite)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    root = tmp_path / "project"
    (root / "data" / "raw").mkdir(parents=True)
    (root / "runs").mkdir()
    dataset_path = root / "data" / "raw" / "dataset.csv"
    dataset_path.write_bytes(b"group,value\nA,1\nB,2\n")
    dataset_path.chmod(0o444)
    dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    exchange = tmp_path / "exchange"
    replacement_settings = replace(
        settings,
        data_dir=tmp_path / "core",
        database_path=database_path,
        runtime_exchange_dir=exchange,
        runtime_socket_path=tmp_path / "runtime.sock",
        execution_timeout_seconds=5,
    )
    monkeypatch.setattr(analysis_module, "settings", replacement_settings)
    monkeypatch.setattr(execution_module, "settings", replacement_settings)
    monkeypatch.setattr(filesystem_module, "settings", replacement_settings)
    monkeypatch.setattr(intents_module, "settings", replacement_settings)
    environment = ServiceEnvironment(
        engine=engine,
        session_factory=session_factory,
        root=root,
        exchange=exchange,
        dataset_path=dataset_path,
        dataset_hash=dataset_hash,
    )
    _seed_project_and_dataset(environment)
    yield environment
    engine.dispose()


def _seed_project_and_dataset(environment: ServiceEnvironment) -> None:
    with environment.session_factory.begin() as session:
        session.add(
            ProjectRecord(
                id="project-1",
                title="Project",
                description="",
                project_path=str(environment.root),
                execution_mode="safe",
            )
        )
        session.add(
            SourceRecord(
                id="dataset-1",
                project_id="project-1",
                title="Dataset",
                source_kind="dataset",
                authors=[],
                local_path=str(environment.dataset_path),
                ingestion_status="ready",
                content_hash=environment.dataset_hash,
            )
        )


def _standalone_payload() -> AnalysisIntentCreate:
    return AnalysisIntentCreate(
        dataset_source_id="dataset-1",
        objective="Summarize the values.",
        code="print(df['value'].mean())",
    )


def create_approved_standalone(environment: ServiceEnvironment) -> str:
    with environment.session_factory() as session:
        intent = create_standalone_analysis_intent(
            session,
            "project-1",
            _standalone_payload(),
        )
        intent_id = intent.id
        session.commit()
    with environment.session_factory() as session:
        decide_standalone_analysis_intent(session, intent_id, "approved")
        session.commit()
    return intent_id


def _seed_workflow(environment: ServiceEnvironment) -> tuple[str, list[str]]:
    expected_outputs = [
        "executed-notebook",
        "summary-table",
        "figures",
        "analysis-log",
        "environment-manifest",
    ]
    with environment.session_factory.begin() as session:
        session.add(
            WorkflowRecord(
                id="workflow-1",
                project_id="project-1",
                create_idempotency_key="workflow-key",
                create_payload_sha256="1" * 64,
                workflow_type="dataset-analysis",
                dataset_source_id="dataset-1",
                dataset_content_hash=environment.dataset_hash,
                goal="Analyze the dataset.",
                generation_mode="local-deterministic",
                status="running",
                row_version=7,
            )
        )
        session.flush()
        session.add(
            PlanRecord(
                id="plan-1",
                workflow_id="workflow-1",
                version=1,
                spec_json={"schemaVersion": "1"},
                spec_sha256="2" * 64,
                status="approved",
                generator="dataset-template-v1",
                approved_at=utc_now(),
            )
        )
        session.flush()
        session.add(
            TaskRecord(
                id="task-workflow-1",
                project_id="project-1",
                workflow_id="workflow-1",
                plan_id="plan-1",
                step_key="execute-analysis",
                order_index=2,
                objective="Execute only the approved analysis intent.",
                task_type="python-data-analysis",
                inputs={
                    "datasetSourceId": "dataset-1",
                    "datasetContentHash": environment.dataset_hash,
                    "expectedOutputs": expected_outputs,
                    "timeoutSeconds": 5,
                },
                expected_outputs=expected_outputs,
                acceptance_criteria=["Run only approved code."],
                permissions=["dataset:read", "python:execute"],
                risk_level="high",
                status="waiting-approval",
                timeout_seconds=5,
            )
        )
    return "task-workflow-1", expected_outputs


def runtime_result(
    run_dir: Path,
    exchange_root: Path,
    request: Mapping[str, object],
    *,
    evidence_request: Mapping[str, object] | None = None,
    mutate_files: Callable[[dict[str, bytes]], None] | None = None,
    started_at: str = "2026-07-15T00:00:00+00:00",
    finished_at: str = "2026-07-15T00:00:01+00:00",
    duration_seconds: str = "1.000",
) -> RuntimeExecutionResult:
    return write_attested_runtime_result(
        run_dir,
        exchange_root,
        request,
        stdout="2.0\n",
        evidence_request=evidence_request,
        mutate_files=mutate_files,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
    )


def _add_completed_run_records(
    session: Session,
    intent: AnalysisIntentRecord,
    run_id: str,
) -> RunRecord:
    artifact_specs = (
        ("input.ipynb", "notebook-input", b"input-notebook"),
        ("executed.ipynb", "notebook-executed", b"notebook"),
        ("environment.json", "environment", b"environment"),
        ("stdout.txt", "stdout", b"stdout"),
        ("stderr.txt", "stderr", b"stderr"),
        ("execution.log", "log", b"log"),
    )
    paths = [f"runs/{run_id}/{name}" for name, _artifact_type, _content in artifact_specs]
    environment_hash = hashlib.sha256(b"environment").hexdigest()
    run = RunRecord(
        id=run_id,
        task_id=intent.task_id,
        analysis_intent_id=intent.id,
        environment_hash=environment_hash,
        input_artifacts=[intent.dataset_source_id],
        output_artifacts=paths,
        logs_path=f"runs/{run_id}/execution.log",
        status="completed",
        finished_at=utc_now(),
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            ArtifactRecord(
                id=f"artifact-{run_id}-{index}",
                run_id=run_id,
                artifact_type=artifact_type,
                path=f"runs/{run_id}/{name}",
                mime_type="application/octet-stream",
                content_hash=hashlib.sha256(content).hexdigest(),
                parent_artifacts=[intent.dataset_source_id],
                metadata_json={
                    "sizeBytes": len(content),
                    "payloadSha256": intent.payload_sha256,
                },
            )
            for index, (name, artifact_type, content) in enumerate(artifact_specs)
        ]
    )
    return run


def test_v1_canonical_payload_golden_bytes_remain_unchanged() -> None:
    encoded, digest = canonical_analysis_payload(
        "dataset-1",
        "Mean Δ",
        'print("héllo")',
    )

    assert encoded == (
        b'{"code":"print(\\"h\xc3\xa9llo\\")","datasetSourceId":"dataset-1",'
        b'"objective":"Mean \xce\x94"}'
    )
    assert digest == "e228d2409657f3c2a5ae37a40fe5a1b2325228397ecb68dc19d664071c308360"


def test_v2_canonical_payload_is_golden_and_every_field_is_hash_bound() -> None:
    arguments: dict[str, Any] = {
        "project_id": "project-1",
        "workflow_id": "workflow-1",
        "plan_id": "plan-1",
        "task_id": "task-1",
        "analysis_intent_id": "intent-1",
        "plan_step_id": "execute-analysis",
        "dataset_source_id": "dataset-1",
        "dataset_content_hash": "a" * 64,
        "objective": "Analyze Δ",
        "expected_outputs": [
            "executed-notebook",
            "analysis-log",
            "environment-manifest",
        ],
        "timeout_seconds": 600,
        "code": 'print("héllo")',
        "code_diff": None,
        "error_summary": None,
        "previous_intent_id": None,
        "repair_attempt": 0,
        "expected_workflow_revision": 7,
    }
    encoded, digest = canonical_workflow_analysis_payload(**arguments)

    assert encoded.decode("utf-8") == (
        '{"action":"execute-python-data-analysis","analysisIntentId":"intent-1",'
        '"code":"print(\\"héllo\\")","codeDiff":null,'
        '"datasetContentHash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"datasetSourceId":"dataset-1","errorSummary":null,'
        '"expectedOutputs":["executed-notebook","analysis-log","environment-manifest"],'
        '"expectedWorkflowRevision":7,"objective":"Analyze Δ","planId":"plan-1",'
        '"planStepId":"execute-analysis","previousIntentId":null,'
        '"projectId":"project-1","repairAttempt":0,"riskLevel":"high",'
        '"schemaVersion":"analysis-intent-v2","taskId":"task-1",'
        '"timeoutSeconds":600,"workflowId":"workflow-1"}'
    )
    assert digest == "c00bb9c1f1ed3870c295c4a27c1fcafe375fc7d4cc7b57768b0ca680cd59ebcc"

    mutations = {
        "project_id": "project-2",
        "workflow_id": "workflow-2",
        "plan_id": "plan-2",
        "task_id": "task-2",
        "analysis_intent_id": "intent-2",
        "plan_step_id": "other-step",
        "dataset_source_id": "dataset-2",
        "dataset_content_hash": "b" * 64,
        "objective": "Changed",
        "expected_outputs": ["executed-notebook"],
        "timeout_seconds": 601,
        "code": "print(2)",
        "code_diff": "diff",
        "error_summary": {"code": "failed"},
        "previous_intent_id": "intent-0",
        "repair_attempt": 1,
        "expected_workflow_revision": 8,
    }
    for field, value in mutations.items():
        changed = copy.deepcopy(arguments)
        changed[field] = value
        assert canonical_workflow_analysis_payload(**changed)[1] != digest, field


def test_v3_canonical_payload_binds_the_fixed_execution_policy() -> None:
    arguments: dict[str, Any] = {
        "project_id": "project-1",
        "workflow_id": "workflow-1",
        "plan_id": "plan-1",
        "task_id": "task-1",
        "analysis_intent_id": "intent-1",
        "plan_step_id": "execute-analysis",
        "dataset_source_id": "dataset-1",
        "dataset_content_hash": "a" * 64,
        "objective": "Analyze",
        "expected_outputs": ["executed-notebook", "analysis-log"],
        "timeout_seconds": 600,
        "code": "print(1)",
        "code_diff": None,
        "error_summary": None,
        "previous_intent_id": None,
        "repair_attempt": 0,
        "expected_workflow_revision": 7,
        "schema_version": "analysis-intent-v3",
        "policy_profile_id": "dataset-analysis-fixed-v1",
        "policy_template": "baseline",
    }
    encoded, digest = canonical_workflow_analysis_payload(**arguments)

    decoded = encoded.decode("utf-8")
    assert '"policyProfileId":"dataset-analysis-fixed-v1"' in decoded
    assert '"policyTemplate":"baseline"' in decoded
    assert '"schemaVersion":"analysis-intent-v3"' in decoded

    for field, value in (
        ("policy_profile_id", "general-analysis-v1"),
        ("policy_template", "repair-1"),
    ):
        changed = copy.deepcopy(arguments)
        changed[field] = value
        assert canonical_workflow_analysis_payload(**changed)[1] != digest

    missing_policy = copy.deepcopy(arguments)
    missing_policy["policy_template"] = None
    with pytest.raises(ValueError, match="requires an execution policy binding"):
        canonical_workflow_analysis_payload(**missing_policy)

    historical_v2 = copy.deepcopy(arguments)
    historical_v2["schema_version"] = "analysis-intent-v2"
    with pytest.raises(ValueError, match="does not bind an execution policy"):
        canonical_workflow_analysis_payload(**historical_v2)


def test_v4_canonical_payload_binds_exact_compiled_provenance() -> None:
    code = "import pandas as pd\nprint(pd.__version__)"
    arguments: dict[str, Any] = {
        "project_id": "project-1",
        "workflow_id": "workflow-1",
        "plan_id": "plan-1",
        "task_id": "task-1",
        "analysis_intent_id": "intent-1",
        "plan_step_id": "execute-analysis",
        "dataset_source_id": "dataset-1",
        "dataset_content_hash": "a" * 64,
        "objective": "Analyze",
        "expected_outputs": ["executed-notebook", "analysis-log"],
        "timeout_seconds": 600,
        "code": code,
        "code_diff": None,
        "error_summary": None,
        "previous_intent_id": None,
        "repair_attempt": 0,
        "expected_workflow_revision": 7,
        "schema_version": "analysis-intent-v4",
        "policy_profile_id": "dataset-analysis-spec-v1",
        "policy_template": "analysis-spec-compiler-v1",
        "analysis_spec_id": "spec-1",
        "analysis_spec_sha256": "b" * 64,
        "dataset_profile_sha256": "c" * 64,
        "compiler_version": "analysis-spec-compiler-v1",
        "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "runtime_policy_id": "dataset-analysis-spec-v1",
    }

    encoded, digest = canonical_workflow_analysis_payload(**arguments)
    decoded = json.loads(encoded)

    assert decoded["schemaVersion"] == "analysis-intent-v4"
    assert decoded["analysisSpecId"] == "spec-1"
    assert decoded["analysisSpecSha256"] == "b" * 64
    assert decoded["datasetProfileSha256"] == "c" * 64
    assert decoded["compilerVersion"] == "analysis-spec-compiler-v1"
    assert decoded["codeSha256"] == arguments["code_sha256"]
    assert decoded["runtimePolicyId"] == "dataset-analysis-spec-v1"

    for field in (
        "analysis_spec_id",
        "analysis_spec_sha256",
        "dataset_profile_sha256",
        "compiler_version",
        "code_sha256",
        "runtime_policy_id",
    ):
        changed = copy.deepcopy(arguments)
        changed[field] = None
        with pytest.raises(ValueError, match="compiled provenance is invalid"):
            canonical_workflow_analysis_payload(**changed)

    changed_code = copy.deepcopy(arguments)
    changed_code["code"] = code + "\nprint('tampered')"
    with pytest.raises(ValueError, match="compiled provenance is invalid"):
        canonical_workflow_analysis_payload(**changed_code)

    changed_spec = copy.deepcopy(arguments)
    changed_spec["analysis_spec_sha256"] = "d" * 64
    assert canonical_workflow_analysis_payload(**changed_spec)[1] != digest


def test_create_and_decide_do_not_commit_callers_session(
    service_environment: ServiceEnvironment,
) -> None:
    with service_environment.session_factory() as writer:
        intent = create_standalone_analysis_intent(
            writer,
            "project-1",
            _standalone_payload(),
        )
        intent_id = intent.id
        with service_environment.session_factory() as observer:
            assert observer.get(AnalysisIntentRecord, intent_id) is None
        writer.commit()

    with service_environment.session_factory() as writer:
        decide_standalone_analysis_intent(writer, intent_id, "approved")
        with service_environment.session_factory() as observer:
            persisted = observer.get(AnalysisIntentRecord, intent_id)
            assert persisted is not None
            assert persisted.status == "waiting-approval"
        writer.commit()

    with service_environment.session_factory() as observer:
        persisted = observer.get(AnalysisIntentRecord, intent_id)
        approval = observer.scalar(
            select(ApprovalRecord).where(ApprovalRecord.subject_id == intent_id)
        )
        assert persisted is not None and persisted.status == "approved"
        assert approval is not None and approval.user_decision == "approved"


def test_standalone_decision_rejects_tampered_exact_approval_hash(
    service_environment: ServiceEnvironment,
) -> None:
    with service_environment.session_factory() as session:
        intent = create_standalone_analysis_intent(
            session,
            "project-1",
            _standalone_payload(),
        )
        intent_id = intent.id
        session.commit()
    with service_environment.session_factory.begin() as session:
        approval = session.scalar(
            select(ApprovalRecord).where(ApprovalRecord.subject_id == intent_id)
        )
        assert approval is not None
        approval.intent_hash = "f" * 64
    with service_environment.session_factory() as session:
        with pytest.raises(AnalysisServiceError) as conflict:
            decide_standalone_analysis_intent(session, intent_id, "approved")
        assert conflict.value.status_code == 409
        assert conflict.value.code == "analysis-approval-binding-invalid"
        session.rollback()


def test_standalone_decision_recomputes_v1_payload_before_approval(
    service_environment: ServiceEnvironment,
) -> None:
    with service_environment.session_factory() as session:
        intent = create_standalone_analysis_intent(
            session,
            "project-1",
            _standalone_payload(),
        )
        intent_id = intent.id
        session.commit()

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        intent.code = "print('tampered after approval request')"
        with pytest.raises(AnalysisServiceError) as conflict:
            decide_standalone_analysis_intent(session, intent_id, "approved")
        assert conflict.value.code == "analysis-approval-binding-invalid"
        session.rollback()

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        assert intent.status == "waiting-approval"
        assert intent.decision is None


def test_historical_v2_workflow_approval_fails_closed_with_stable_code(
    service_environment: ServiceEnvironment,
) -> None:
    task_id, outputs = _seed_workflow(service_environment)
    with service_environment.session_factory() as session:
        create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=WORKFLOW_BASELINE_CODE,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            intent_id="historical-v2-intent",
        )
        session.commit()
    with service_environment.session_factory() as session:
        decide_workflow_analysis_intent(
            session,
            "historical-v2-intent",
            "approved",
            expected_workflow_id="workflow-1",
            expected_workflow_revision=7,
        )
        session.commit()
    with service_environment.session_factory.begin() as session:
        approval = session.scalar(
            select(ApprovalRecord).where(
                ApprovalRecord.subject_id == "historical-v2-intent"
            )
        )
        assert approval is not None
        approval.payload_schema_version = "analysis-intent-v2"

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, "historical-v2-intent")
        assert intent is not None
        with pytest.raises(AnalysisServiceError) as conflict:
            validate_workflow_analysis_intent(
                session,
                intent,
                expected_workflow_id="workflow-1",
                expected_workflow_revision=7,
                require_approval=True,
                require_current_revision=True,
            )
        assert conflict.value.code == "analysis-policy-binding-upgrade-required"


def test_workflow_intent_binds_exact_rows_approval_and_real_repair_diff(
    service_environment: ServiceEnvironment,
) -> None:
    task_id, outputs = _seed_workflow(service_environment)
    with service_environment.session_factory() as session:
        create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=WORKFLOW_BASELINE_CODE,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            intent_id="intent-initial",
        )
        session.commit()
    with service_environment.session_factory() as session:
        decide_workflow_analysis_intent(
            session,
            "intent-initial",
            "approved",
            expected_workflow_id="workflow-1",
            expected_workflow_revision=7,
        )
        intent = session.get(AnalysisIntentRecord, "intent-initial")
        assert intent is not None
        intent.status = "failed"
        intent.error_summary = {
            "schemaVersion": "1",
            "category": "runtime",
            "code": "analysis-runtime-failed",
            "userMessage": "The approved analysis code failed.",
            "stderrExcerpt": None,
            "retryable": True,
        }
        session.add(
            RunRecord(
                id="run-failed-initial",
                task_id=task_id,
                analysis_intent_id=intent.id,
                input_artifacts=["dataset-1"],
                output_artifacts=[],
                status="failed",
                finished_at=utc_now(),
            )
        )
        session.commit()

    proposed_code = WORKFLOW_REPAIR_ONE_CODE
    with service_environment.session_factory() as session:
        previous = session.get(AnalysisIntentRecord, "intent-initial")
        assert previous is not None and previous.error_summary is not None
        previous.code = "print(999)  # tampered after approval"
        with pytest.raises(AnalysisServiceError) as conflict:
            create_workflow_analysis_intent(
                session,
                expected_workflow_id="workflow-1",
                task_id=task_id,
                code=proposed_code,
                expected_outputs=outputs,
                expected_workflow_revision=7,
                previous_intent_id=previous.id,
                error_summary=previous.error_summary,
                code_diff=analysis_code_diff(previous.code, proposed_code),
                intent_id="intent-repair-from-tampered-predecessor",
            )
        assert conflict.value.code == "analysis-lineage-invalid"
        session.rollback()

    with service_environment.session_factory() as session:
        previous = session.get(AnalysisIntentRecord, "intent-initial")
        assert previous is not None and previous.error_summary is not None
        repair = create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=proposed_code,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            previous_intent_id=previous.id,
            error_summary=previous.error_summary,
            code_diff=analysis_code_diff(previous.code, proposed_code),
            intent_id="intent-repair",
        )
        assert repair.intent.repair_attempt == 1
        assert repair.approval.subject_id == repair.intent.id
        session.commit()

    with service_environment.session_factory() as session:
        repair = session.get(AnalysisIntentRecord, "intent-repair")
        approval = session.scalar(
            select(ApprovalRecord).where(ApprovalRecord.subject_id == "intent-repair")
        )
        assert repair is not None and approval is not None
        approval.plan_id = None
        with pytest.raises(AnalysisServiceError, match="exactly match"):
            validate_workflow_analysis_intent(
                session,
                repair,
                expected_workflow_id="workflow-1",
                expected_workflow_revision=7,
                require_approval=True,
                require_current_revision=True,
            )
        session.rollback()

    with service_environment.session_factory() as session:
        repair = session.get(AnalysisIntentRecord, "intent-repair")
        assert repair is not None
        repair.code_diff = "not the canonical old-to-new diff"
        with pytest.raises(AnalysisServiceError, match="lineage"):
            validate_workflow_analysis_intent(
                session,
                repair,
                expected_workflow_id="workflow-1",
                expected_workflow_revision=7,
                require_approval=True,
                require_current_revision=True,
            )

    with service_environment.session_factory() as session:
        decide_workflow_analysis_intent(
            session,
            "intent-repair",
            "approved",
            expected_workflow_id="workflow-1",
            expected_workflow_revision=7,
        )
        repair = session.get(AnalysisIntentRecord, "intent-repair")
        assert repair is not None
        repair.status = "failed"
        repair.error_summary = {
            "schemaVersion": "1",
            "category": "timeout",
            "code": "runtime-timeout",
            "userMessage": "The repair exceeded its approved timeout.",
            "stderrExcerpt": None,
            "retryable": True,
        }
        session.add(
            RunRecord(
                id="run-failed-repair",
                task_id=task_id,
                analysis_intent_id=repair.id,
                input_artifacts=["dataset-1"],
                output_artifacts=[],
                status="failed",
                finished_at=utc_now(),
            )
        )
        session.flush()
        # Revalidation reconstructs the immutable approval-time summary from
        # the predecessor even though this intent now has its own outcome.
        validate_workflow_analysis_intent(
            session,
            repair,
            expected_workflow_id="workflow-1",
            expected_workflow_revision=7,
            require_approval=True,
            require_current_revision=True,
        )
        prior_payload_hash = repair.payload_sha256
        session.commit()

    final_code = WORKFLOW_REPAIR_TWO_CODE
    with service_environment.session_factory() as session:
        initial = session.get(AnalysisIntentRecord, "intent-initial")
        repair = session.get(AnalysisIntentRecord, "intent-repair")
        assert initial is not None
        assert repair is not None and repair.error_summary is not None
        initial.code = "print(12345)  # tampered grandparent"
        with pytest.raises(AnalysisServiceError) as conflict:
            create_workflow_analysis_intent(
                session,
                expected_workflow_id="workflow-1",
                task_id=task_id,
                code=final_code,
                expected_outputs=outputs,
                expected_workflow_revision=7,
                previous_intent_id=repair.id,
                error_summary=repair.error_summary,
                code_diff=analysis_code_diff(repair.code, final_code),
                intent_id="intent-final-from-tampered-grandparent",
            )
        assert conflict.value.code == "analysis-lineage-invalid"
        session.rollback()

    with service_environment.session_factory() as session:
        repair = session.get(AnalysisIntentRecord, "intent-repair")
        assert repair is not None and repair.error_summary is not None
        final = create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=final_code,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            previous_intent_id=repair.id,
            error_summary=repair.error_summary,
            code_diff=analysis_code_diff(repair.code, final_code),
            intent_id="intent-final-repair",
        )
        assert final.intent.repair_attempt == 2
        assert final.intent.payload_sha256 != prior_payload_hash
        assert final.approval.subject_id == "intent-final-repair"

        with pytest.raises(AnalysisServiceError) as exhausted:
            create_workflow_analysis_intent(
                session,
                expected_workflow_id="workflow-1",
                task_id=task_id,
                code=WORKFLOW_BASELINE_CODE,
                expected_outputs=outputs,
                expected_workflow_revision=7,
                previous_intent_id=final.intent.id,
                error_summary=repair.error_summary,
                code_diff=analysis_code_diff(final.intent.code, WORKFLOW_BASELINE_CODE),
                intent_id="intent-repair-limit-exceeded",
            )
        assert exhausted.value.status_code == 409
        assert exhausted.value.code == "analysis-lineage-invalid"


def test_workflow_intent_rejects_non_template_code_before_approval(
    service_environment: ServiceEnvironment,
) -> None:
    task_id, outputs = _seed_workflow(service_environment)

    with service_environment.session_factory() as session:
        with pytest.raises(AnalysisServiceError) as rejected:
            create_workflow_analysis_intent(
                session,
                expected_workflow_id="workflow-1",
                task_id=task_id,
                code=WORKFLOW_BASELINE_CODE + "\nprint('unapproved statement')",
                expected_outputs=outputs,
                expected_workflow_revision=7,
            )
        assert rejected.value.status_code == 422
        assert rejected.value.code == "analysis-code-invalid"
        session.rollback()

    with service_environment.session_factory() as session:
        assert session.scalar(select(AnalysisIntentRecord.id)) is None
        assert (
            session.scalar(
                select(ApprovalRecord.id).where(ApprovalRecord.subject_type == "analysis-intent")
            )
            is None
        )


def test_run_lineage_never_falls_back_when_exact_link_is_tampered(
    service_environment: ServiceEnvironment,
) -> None:
    first_id = create_approved_standalone(service_environment)
    second_id = create_approved_standalone(service_environment)
    with service_environment.session_factory() as session:
        first = session.get(AnalysisIntentRecord, first_id)
        second = session.get(AnalysisIntentRecord, second_id)
        assert first is not None and second is not None
        run = RunRecord(
            id="run-tampered",
            task_id=second.task_id,
            analysis_intent_id=first.id,
            input_artifacts=["dataset-1"],
            output_artifacts=[],
            status="running",
        )
        session.add(run)
        session.flush()
        with pytest.raises(AnalysisServiceError, match="bindings"):
            resolve_analysis_intent_for_run(session, run)


def test_unique_legacy_null_run_link_lists_but_ambiguous_fallback_fails(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        task = session.get(TaskRecord, intent.task_id)
        assert task is not None
        intent.status = "completed"
        task.status = "completed"
        session.add(
            RunRecord(
                id="run-legacy-null-link",
                task_id=intent.task_id,
                analysis_intent_id=None,
                input_artifacts=[intent.dataset_source_id],
                output_artifacts=[],
                status="completed",
                finished_at=utc_now(),
            )
        )

    with service_environment.session_factory() as session:
        [run] = list_project_analysis_runs(session, "project-1")
        assert run.id == "run-legacy-null-link"
        assert run.intent_id == intent_id

    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        session.add(
            AnalysisIntentRecord(
                id="legacy-ambiguous-intent",
                task_id=intent.task_id,
                project_id=intent.project_id,
                dataset_source_id=intent.dataset_source_id,
                objective=intent.objective,
                code=intent.code,
                payload_sha256=intent.payload_sha256,
                status="failed",
                decision="approved",
            )
        )

    with service_environment.session_factory() as session:
        with pytest.raises(AnalysisServiceError) as conflict:
            list_project_analysis_runs(session, "project-1")
        assert conflict.value.code == "analysis-run-lineage-ambiguous"


def test_exact_completed_run_list_rejects_missing_terminal_evidence(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        task = session.get(TaskRecord, intent.task_id) if intent is not None else None
        assert intent is not None and task is not None
        intent.status = "completed"
        task.status = "completed"
        session.add(
            RunRecord(
                id="run-completed-without-evidence",
                task_id=task.id,
                analysis_intent_id=intent.id,
                input_artifacts=[intent.dataset_source_id],
                output_artifacts=[],
                status="completed",
                finished_at=utc_now(),
            )
        )

    with service_environment.session_factory() as session:
        with pytest.raises(AnalysisServiceError) as conflict:
            list_project_analysis_runs(session, "project-1")
        assert conflict.value.code == "analysis-run-terminal-invalid"


def test_exact_failed_run_list_rejects_spoofed_terminal_evidence(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        task = session.get(TaskRecord, intent.task_id) if intent is not None else None
        assert intent is not None and task is not None
        intent.status = "failed"
        task.status = "failed"
        spoofed_path = "data/raw/dataset.csv"
        session.add(
            RunRecord(
                id="run-failed-spoofed-evidence",
                task_id=task.id,
                analysis_intent_id=intent.id,
                input_artifacts=[intent.dataset_source_id],
                output_artifacts=[spoofed_path],
                logs_path=spoofed_path,
                status="failed",
                finished_at=utc_now(),
            )
        )
        session.flush()
        session.add(
            ArtifactRecord(
                id="artifact-failed-spoofed-evidence",
                run_id="run-failed-spoofed-evidence",
                artifact_type="log",
                path=spoofed_path,
                mime_type="text/plain",
                content_hash="a" * 64,
                parent_artifacts=[intent.dataset_source_id],
                metadata_json={
                    "sizeBytes": 1,
                    "payloadSha256": intent.payload_sha256,
                },
            )
        )

    with service_environment.session_factory() as session:
        with pytest.raises(AnalysisServiceError) as conflict:
            list_project_analysis_runs(session, "project-1")
        assert conflict.value.code == "analysis-run-terminal-invalid"


def test_run_output_rejects_terminal_status_drift(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        task = session.get(TaskRecord, intent.task_id)
        assert task is not None
        intent.status = "failed"
        task.status = "failed"
        session.add(
            RunRecord(
                id="run-terminal-status-drift",
                task_id=task.id,
                analysis_intent_id=intent.id,
                input_artifacts=[intent.dataset_source_id],
                output_artifacts=[],
                status="completed",
                finished_at=utc_now(),
            )
        )

    with service_environment.session_factory() as session:
        with pytest.raises(AnalysisServiceError) as conflict:
            list_project_analysis_runs(session, "project-1")
        assert conflict.value.code == "analysis-run-status-invalid"


def test_exact_run_lineage_rejects_project_and_workflow_row_drift(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        session.add(
            ProjectRecord(
                id="project-2",
                title="Other project",
                description="",
                project_path=str(service_environment.root / "other"),
                execution_mode="safe",
            )
        )
        session.flush()
        intent.project_id = "project-2"
        run = RunRecord(
            id="run-project-drift",
            task_id=intent.task_id,
            analysis_intent_id=intent.id,
            input_artifacts=["dataset-1"],
            output_artifacts=[],
            status="running",
        )
        session.add(run)
        session.flush()
        with pytest.raises(AnalysisServiceError, match="project"):
            resolve_analysis_intent_for_run(session, run)
        session.rollback()

    task_id, outputs = _seed_workflow(service_environment)
    with service_environment.session_factory() as session:
        bundle = create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=WORKFLOW_BASELINE_CODE,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            intent_id="intent-workflow-drift",
        )
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.workflow_id = None
        run = RunRecord(
            id="run-workflow-drift",
            task_id=task_id,
            analysis_intent_id=bundle.intent.id,
            input_artifacts=["dataset-1"],
            output_artifacts=[],
            status="running",
        )
        session.add(run)
        session.flush()
        with pytest.raises(AnalysisServiceError, match="workflow"):
            resolve_analysis_intent_for_run(session, run)


@pytest.mark.asyncio
async def test_execution_holds_capacity_slot_but_no_database_transaction_during_runtime(
    service_environment: ServiceEnvironment,
) -> None:
    first_id = create_approved_standalone(service_environment)
    second_id = create_approved_standalone(service_environment)
    started = asyncio.Event()
    release = asyncio.Event()
    call_count = 0

    async def blocked_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        nonlocal call_count
        call_count += 1
        with service_environment.session_factory() as probe:
            assert not probe.in_transaction()
            assert probe.get(AnalysisIntentRecord, first_id) is not None
        started.set()
        await release.wait()
        assert kwargs["timeout_seconds"] == 5
        assert kwargs["policy_profile_id"] == "approved-python-container-v1"
        assert kwargs["policy_template"] is None
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    first_execution = asyncio.create_task(
        execute_standalone_analysis_intent(
            first_id,
            session_factory=service_environment.session_factory,
            runtime_executor=blocked_runtime,
        )
    )
    await started.wait()
    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_standalone_analysis_intent(
            second_id,
            session_factory=service_environment.session_factory,
            runtime_executor=blocked_runtime,
        )
    assert conflict.value.status_code == 409
    assert call_count == 1
    release.set()
    result = await first_execution
    assert result.status == "completed"
    assert call_count == 1

    async def reusable_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    second_result = await execute_standalone_analysis_intent(
        second_id,
        session_factory=service_environment.session_factory,
        runtime_executor=reusable_runtime,
    )
    assert second_result.status == "completed"


@pytest.mark.asyncio
async def test_execution_rejects_symlink_dataset_without_runtime_or_path_leak(
    service_environment: ServiceEnvironment,
    tmp_path: Path,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    original = service_environment.dataset_path
    external = tmp_path / "external.csv"
    external.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(external)
    called = False

    async def forbidden_runtime(**_kwargs: Any) -> RuntimeExecutionResult:
        nonlocal called
        called = True
        raise AssertionError("runtime must not receive a symlinked dataset")

    with pytest.raises(AnalysisServiceError) as failure:
        await execute_standalone_analysis_intent(
            intent_id,
            session_factory=service_environment.session_factory,
            runtime_executor=forbidden_runtime,
        )
    assert failure.value.status_code == 409
    assert failure.value.code == "dataset-path-symlink"
    assert str(tmp_path) not in failure.value.detail
    assert not called
    with service_environment.session_factory() as session:
        run = session.scalar(select(RunRecord).where(RunRecord.analysis_intent_id == intent_id))
        assert run is None


@pytest.mark.asyncio
async def test_runtime_failure_releases_capacity_and_redacts_raw_runtime_detail(
    service_environment: ServiceEnvironment,
) -> None:
    first_id = create_approved_standalone(service_environment)
    second_id = create_approved_standalone(service_environment)

    async def failing_runtime(**_kwargs: Any) -> RuntimeExecutionResult:
        raise RuntimeServiceError(
            "science-runtime transport failed at /private/socket: raw-secret-body"
        )

    with pytest.raises(AnalysisServiceError) as failure:
        await execute_standalone_analysis_intent(
            first_id,
            session_factory=service_environment.session_factory,
            runtime_executor=failing_runtime,
        )
    assert failure.value.code == "runtime-unavailable"
    assert "/private/socket" not in failure.value.detail
    assert "raw-secret-body" not in failure.value.detail
    with service_environment.session_factory() as session:
        failed_run = session.scalar(
            select(RunRecord).where(RunRecord.analysis_intent_id == first_id)
        )
        assert failed_run is not None and failed_run.logs_path is not None
        safe_log = service_environment.root / failed_run.logs_path
        assert safe_log.read_text(encoding="utf-8") == (
            "AnalysisExecutionError: runtime-unavailable\n"
        )

    async def successful_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    result = await execute_standalone_analysis_intent(
        second_id,
        session_factory=service_environment.session_factory,
        runtime_executor=successful_runtime,
    )
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_cancelled_execution_releases_capacity_and_converges_run(
    service_environment: ServiceEnvironment,
) -> None:
    cancelled_id = create_approved_standalone(service_environment)
    reusable_id = create_approved_standalone(service_environment)
    started = asyncio.Event()

    async def blocked_runtime(**_kwargs: Any) -> RuntimeExecutionResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    execution = asyncio.create_task(
        execute_standalone_analysis_intent(
            cancelled_id,
            session_factory=service_environment.session_factory,
            runtime_executor=blocked_runtime,
        )
    )
    await started.wait()
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    with service_environment.session_factory() as session:
        cancelled_run = session.scalar(
            select(RunRecord).where(RunRecord.analysis_intent_id == cancelled_id)
        )
        assert cancelled_run is not None and cancelled_run.status == "failed"

    async def successful_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    result = await execute_standalone_analysis_intent(
        reusable_id,
        session_factory=service_environment.session_factory,
        runtime_executor=successful_runtime,
    )
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_workflow_cancellation_cannot_be_overwritten_by_runtime_completion(
    service_environment: ServiceEnvironment,
) -> None:
    task_id, outputs = _seed_workflow(service_environment)
    with service_environment.session_factory() as session:
        bundle = create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=WORKFLOW_BASELINE_CODE,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            intent_id="intent-cancelled",
        )
        session.commit()
    with service_environment.session_factory() as session:
        decide_workflow_analysis_intent(
            session,
            bundle.intent.id,
            "approved",
            expected_workflow_id="workflow-1",
            expected_workflow_revision=7,
        )
        session.commit()

    called_from_legacy_endpoint = False

    async def forbidden_legacy_runtime(**_kwargs: Any) -> RuntimeExecutionResult:
        nonlocal called_from_legacy_endpoint
        called_from_legacy_endpoint = True
        raise AssertionError("workflow intent must not reach the legacy executor")

    with pytest.raises(AnalysisServiceError) as legacy_conflict:
        await execute_standalone_analysis_intent(
            "intent-cancelled",
            session_factory=service_environment.session_factory,
            runtime_executor=forbidden_legacy_runtime,
        )
    assert legacy_conflict.value.status_code == 409
    assert legacy_conflict.value.code == "workflow-analysis-endpoint-required"
    assert not called_from_legacy_endpoint

    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        assert kwargs["policy_profile_id"] == "dataset-analysis-fixed-v1"
        assert kwargs["policy_template"] == "baseline"
        started.set()
        await release.wait()
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    execution = asyncio.create_task(
        execute_workflow_analysis_intent(
            "intent-cancelled",
            session_factory=service_environment.session_factory,
            expected_workflow_id="workflow-1",
            approval_workflow_revision=7,
            runtime_executor=blocked_runtime,
        )
    )
    await started.wait()
    with service_environment.session_factory.begin() as session:
        workflow = session.get(WorkflowRecord, "workflow-1")
        task = session.get(TaskRecord, task_id)
        assert workflow is not None and task is not None
        workflow.status = "cancelled"
        workflow.cancel_requested_at = utc_now()
        task.status = "cancelled"
    release.set()
    with pytest.raises(AnalysisServiceError) as conflict:
        await execution
    assert conflict.value.code == "workflow-execution-superseded"
    with service_environment.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        intent = session.get(AnalysisIntentRecord, "intent-cancelled")
        run = session.scalar(
            select(RunRecord).where(RunRecord.analysis_intent_id == "intent-cancelled")
        )
        assert task is not None and task.status == "cancelled"
        assert intent is not None and intent.status == "failed"
        assert intent.error_summary is not None
        assert intent.error_summary["code"] == "workflow-execution-superseded"
        assert run is not None and run.status == "failed"


@pytest.mark.asyncio
async def test_workflow_revision_supersede_converges_running_task_to_failed(
    service_environment: ServiceEnvironment,
) -> None:
    task_id, outputs = _seed_workflow(service_environment)
    with service_environment.session_factory() as session:
        bundle = create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=WORKFLOW_BASELINE_CODE,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            intent_id="intent-revision-superseded",
        )
        session.commit()
    with service_environment.session_factory() as session:
        decide_workflow_analysis_intent(
            session,
            bundle.intent.id,
            "approved",
            expected_workflow_id="workflow-1",
            expected_workflow_revision=7,
        )
        session.commit()

    async def superseding_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        with service_environment.session_factory.begin() as session:
            workflow = session.get(WorkflowRecord, "workflow-1")
            task = session.get(TaskRecord, task_id)
            assert workflow is not None and task is not None
            assert task.status == "running"
            workflow.row_version = 8
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    with pytest.raises(AnalysisServiceError) as conflict:
        await execute_workflow_analysis_intent(
            bundle.intent.id,
            session_factory=service_environment.session_factory,
            expected_workflow_id="workflow-1",
            approval_workflow_revision=7,
            runtime_executor=superseding_runtime,
        )
    assert conflict.value.code == "workflow-execution-superseded"
    with service_environment.session_factory() as session:
        task = session.get(TaskRecord, task_id)
        intent = session.get(AnalysisIntentRecord, bundle.intent.id)
        run = session.scalar(
            select(RunRecord).where(RunRecord.analysis_intent_id == bundle.intent.id)
        )
        assert task is not None and task.status == "failed"
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"


def test_runtime_executor_timeout_argument_defaults_preserve_call_compatibility() -> None:
    signature_defaults = analysis_module.execute_in_runtime.__kwdefaults__
    assert signature_defaults is not None
    assert signature_defaults["timeout_seconds"] is None


@pytest.mark.asyncio
async def test_run_output_never_reads_symlink_or_hash_tampered_artifact(
    service_environment: ServiceEnvironment,
    tmp_path: Path,
) -> None:
    intent_id = create_approved_standalone(service_environment)

    async def successful_runtime(**kwargs: Any) -> RuntimeExecutionResult:
        return runtime_result(kwargs["run_dir"], service_environment.exchange, kwargs)

    result = await execute_standalone_analysis_intent(
        intent_id,
        session_factory=service_environment.session_factory,
        runtime_executor=successful_runtime,
    )
    stdout_artifact = next(
        artifact for artifact in result.artifacts if artifact.path.endswith("stdout.txt")
    )
    stdout_path = service_environment.root / stdout_artifact.path
    stdout_path.unlink()
    external = tmp_path / "external-output.txt"
    external.write_text("must-not-be-returned", encoding="utf-8")
    stdout_path.symlink_to(external)
    with service_environment.session_factory() as session:
        [redacted] = list_project_analysis_runs(session, "project-1")
        assert redacted.stdout == ""

    stdout_path.unlink()
    stdout_path.write_text("hash-tampered", encoding="utf-8")
    with service_environment.session_factory() as session:
        [redacted] = list_project_analysis_runs(session, "project-1")
        assert redacted.stdout == ""


def test_recovery_handles_running_orphan_and_terminal_exact_run(
    service_environment: ServiceEnvironment,
) -> None:
    running_id = create_approved_standalone(service_environment)
    orphan_id = create_approved_standalone(service_environment)
    terminal_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        running = session.get(AnalysisIntentRecord, running_id)
        orphan = session.get(AnalysisIntentRecord, orphan_id)
        terminal = session.get(AnalysisIntentRecord, terminal_id)
        assert running is not None and orphan is not None and terminal is not None
        running.status = "executing"
        orphan.status = "executing"
        terminal.status = "executing"
        running_task = session.get(TaskRecord, running.task_id)
        orphan_task = session.get(TaskRecord, orphan.task_id)
        terminal_task = session.get(TaskRecord, terminal.task_id)
        assert running_task is not None and orphan_task is not None and terminal_task is not None
        running_task.status = "running"
        orphan_task.status = "running"
        terminal_task.status = "running"
        terminal_paths = [
            "runs/run-recovery-terminal/input.ipynb",
            "runs/run-recovery-terminal/executed.ipynb",
            "runs/run-recovery-terminal/environment.json",
            "runs/run-recovery-terminal/stdout.txt",
            "runs/run-recovery-terminal/stderr.txt",
            "runs/run-recovery-terminal/execution.log",
        ]
        terminal_environment_hash = "e" * 64
        session.add_all(
            [
                RunRecord(
                    id="run-recovery-running",
                    task_id=running.task_id,
                    analysis_intent_id=running.id,
                    input_artifacts=["dataset-1"],
                    output_artifacts=[],
                    status="running",
                ),
                RunRecord(
                    id="run-recovery-terminal",
                    task_id=terminal.task_id,
                    analysis_intent_id=terminal.id,
                    environment_hash=terminal_environment_hash,
                    input_artifacts=["dataset-1"],
                    output_artifacts=terminal_paths,
                    logs_path=terminal_paths[-1],
                    status="completed",
                    finished_at=utc_now(),
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                ArtifactRecord(
                    id=f"artifact-recovery-terminal-{index}",
                    run_id="run-recovery-terminal",
                    artifact_type=artifact_type,
                    path=path,
                    mime_type="application/octet-stream",
                    content_hash=(
                        terminal_environment_hash
                        if artifact_type == "environment"
                        else f"{index + 1:x}" * 64
                    ),
                    parent_artifacts=["dataset-1"],
                    metadata_json={
                        "sizeBytes": 0,
                        "payloadSha256": terminal.payload_sha256,
                    },
                )
                for index, (artifact_type, path) in enumerate(
                        zip(
                            (
                                "notebook-input",
                                "notebook-executed",
                            "environment",
                            "stdout",
                            "stderr",
                            "log",
                        ),
                        terminal_paths,
                        strict=True,
                    )
                )
            ]
        )

    with service_environment.session_factory() as session:
        recover_interrupted_analysis_state(session)
        session.commit()

    with service_environment.session_factory() as session:
        running = session.get(AnalysisIntentRecord, running_id)
        orphan = session.get(AnalysisIntentRecord, orphan_id)
        terminal = session.get(AnalysisIntentRecord, terminal_id)
        running_run = session.get(RunRecord, "run-recovery-running")
        assert running is not None and running.status == "failed"
        assert orphan is not None and orphan.status == "failed"
        assert terminal is not None and terminal.status == "completed"
        assert running_run is not None and running_run.status == "failed"


def test_recovery_removes_artifacts_copied_before_database_commit(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    run_id = "run-uncommitted-artifacts"
    run_dir = service_environment.root / "runs" / run_id
    run_dir.mkdir()
    (run_dir / "input.csv").write_bytes(service_environment.dataset_path.read_bytes())
    (run_dir / "executed.ipynb").write_text("uncommitted", encoding="utf-8")
    nested = run_dir / "figures"
    nested.mkdir()
    (nested / "figure.png").write_bytes(b"uncommitted-figure")
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        task = session.get(TaskRecord, intent.task_id)
        assert task is not None
        intent.status = "executing"
        task.status = "running"
        session.add(
            RunRecord(
                id=run_id,
                task_id=task.id,
                analysis_intent_id=intent.id,
                input_artifacts=[intent.dataset_source_id],
                output_artifacts=[],
                status="running",
            )
        )

    with service_environment.session_factory() as session:
        recover_interrupted_analysis_state(session)
        session.commit()

    assert run_dir.is_dir()
    assert list(run_dir.iterdir()) == []
    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.get(RunRecord, run_id)
        task = session.get(TaskRecord, intent.task_id if intent is not None else "missing")
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"
        assert run.output_artifacts == []
        assert (
            session.scalar(select(ArtifactRecord.id).where(ArtifactRecord.run_id == run_id)) is None
        )
        assert task is not None and task.status == "failed"


def test_recovery_rejects_incomplete_completed_exact_run(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        task = session.get(TaskRecord, intent.task_id)
        assert task is not None
        intent.status = "executing"
        task.status = "running"
        session.add(
            RunRecord(
                id="run-incomplete-completed",
                task_id=task.id,
                analysis_intent_id=intent.id,
                environment_hash="e" * 64,
                input_artifacts=[intent.dataset_source_id],
                output_artifacts=[],
                status="completed",
                finished_at=None,
            )
        )

    with service_environment.session_factory() as session:
        recover_interrupted_analysis_state(session)
        session.commit()

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.get(RunRecord, "run-incomplete-completed")
        task = session.get(TaskRecord, intent.task_id if intent is not None else "missing")
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"
        assert run.finished_at is not None
        assert task is not None and task.status == "failed"


def test_recovery_preserves_cancelled_workflow_task(
    service_environment: ServiceEnvironment,
) -> None:
    task_id, outputs = _seed_workflow(service_environment)
    with service_environment.session_factory() as session:
        bundle = create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=WORKFLOW_BASELINE_CODE,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            intent_id="intent-recovery-cancelled",
        )
        session.commit()
    with service_environment.session_factory() as session:
        decide_workflow_analysis_intent(
            session,
            bundle.intent.id,
            "approved",
            expected_workflow_id="workflow-1",
            expected_workflow_revision=7,
        )
        session.commit()
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, bundle.intent.id)
        task = session.get(TaskRecord, task_id)
        assert intent is not None and task is not None
        intent.status = "executing"
        task.status = "cancelled"
        session.add(
            RunRecord(
                id="run-recovery-cancelled",
                task_id=task.id,
                analysis_intent_id=intent.id,
                input_artifacts=[intent.dataset_source_id],
                output_artifacts=[],
                status="running",
            )
        )

    with service_environment.session_factory() as session:
        recover_interrupted_analysis_state(session)
        session.commit()

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, bundle.intent.id)
        task = session.get(TaskRecord, task_id)
        run = session.get(RunRecord, "run-recovery-cancelled")
        assert intent is not None and intent.status == "failed"
        assert task is not None and task.status == "cancelled"
        assert run is not None and run.status == "failed"


@pytest.mark.parametrize("task_cancelled", [True, False])
def test_recovery_never_revives_completed_run_after_workflow_cancellation(
    service_environment: ServiceEnvironment,
    task_cancelled: bool,
) -> None:
    task_id, outputs = _seed_workflow(service_environment)
    with service_environment.session_factory() as session:
        bundle = create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=WORKFLOW_BASELINE_CODE,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            intent_id="intent-terminal-cancelled",
        )
        session.commit()
    with service_environment.session_factory() as session:
        decide_workflow_analysis_intent(
            session,
            bundle.intent.id,
            "approved",
            expected_workflow_id="workflow-1",
            expected_workflow_revision=7,
        )
        session.commit()
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, bundle.intent.id)
        task = session.get(TaskRecord, task_id)
        workflow = session.get(WorkflowRecord, "workflow-1")
        assert intent is not None and task is not None and workflow is not None
        intent.status = "executing"
        task.status = "cancelled" if task_cancelled else "running"
        if not task_cancelled:
            workflow.cancel_requested_at = utc_now()
        _add_completed_run_records(session, intent, "run-terminal-cancelled")

    with service_environment.session_factory() as session:
        recover_interrupted_analysis_state(session)
        session.commit()

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, bundle.intent.id)
        task = session.get(TaskRecord, task_id)
        run = session.get(RunRecord, "run-terminal-cancelled")
        assert intent is not None and intent.status == "failed"
        assert task is not None
        assert task.status == ("cancelled" if task_cancelled else "failed")
        assert run is not None and run.status == "failed"


def test_recovery_never_revives_completed_run_from_stale_workflow_revision(
    service_environment: ServiceEnvironment,
) -> None:
    task_id, outputs = _seed_workflow(service_environment)
    with service_environment.session_factory() as session:
        bundle = create_workflow_analysis_intent(
            session,
            expected_workflow_id="workflow-1",
            task_id=task_id,
            code=WORKFLOW_BASELINE_CODE,
            expected_outputs=outputs,
            expected_workflow_revision=7,
            intent_id="intent-terminal-stale-revision",
        )
        session.commit()
    with service_environment.session_factory() as session:
        decide_workflow_analysis_intent(
            session,
            bundle.intent.id,
            "approved",
            expected_workflow_id="workflow-1",
            expected_workflow_revision=7,
        )
        session.commit()
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, bundle.intent.id)
        task = session.get(TaskRecord, task_id)
        workflow = session.get(WorkflowRecord, "workflow-1")
        assert intent is not None and task is not None and workflow is not None
        intent.status = "executing"
        task.status = "running"
        workflow.row_version = 8
        _add_completed_run_records(session, intent, "run-terminal-stale-revision")

    with service_environment.session_factory() as session:
        recover_interrupted_analysis_state(session)
        session.commit()

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, bundle.intent.id)
        task = session.get(TaskRecord, task_id)
        run = session.get(RunRecord, "run-terminal-stale-revision")
        assert intent is not None and intent.status == "failed"
        assert task is not None and task.status == "failed"
        assert run is not None and run.status == "failed"


def test_recovery_fails_closed_on_terminal_exact_run_with_wrong_task(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    unrelated_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        unrelated = session.get(AnalysisIntentRecord, unrelated_id)
        assert intent is not None and unrelated is not None
        intent.status = "executing"
        canonical_task = session.get(TaskRecord, intent.task_id)
        unrelated_task = session.get(TaskRecord, unrelated.task_id)
        assert canonical_task is not None and unrelated_task is not None
        canonical_task.status = "running"
        session.add(
            RunRecord(
                id="run-terminal-wrong-task",
                task_id=unrelated.task_id,
                analysis_intent_id=intent.id,
                input_artifacts=["dataset-1"],
                output_artifacts=[],
                status="completed",
                finished_at=utc_now(),
            )
        )

    with service_environment.session_factory() as session:
        recover_interrupted_analysis_state(session)
        session.commit()

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        canonical_task = session.get(
            TaskRecord,
            intent.task_id if intent is not None else "missing",
        )
        unrelated = session.get(AnalysisIntentRecord, unrelated_id)
        unrelated_task = session.get(
            TaskRecord,
            unrelated.task_id if unrelated is not None else "missing",
        )
        run = session.get(RunRecord, "run-terminal-wrong-task")
        assert intent is not None and intent.status == "failed"
        assert canonical_task is not None and canonical_task.status == "failed"
        assert unrelated_task is not None and unrelated_task.status == "waiting-execution"
        assert run is not None and run.status == "failed"


def test_terminal_run_read_and_recovery_reject_approved_payload_tamper(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        task = session.get(TaskRecord, intent.task_id)
        assert task is not None
        intent.status = "completed"
        intent.code = "print('tampered after execution approval')"
        task.status = "completed"
        session.add(
            RunRecord(
                id="run-terminal-payload-tamper",
                task_id=task.id,
                analysis_intent_id=intent.id,
                input_artifacts=[intent.dataset_source_id],
                output_artifacts=[],
                status="completed",
                finished_at=utc_now(),
            )
        )

    with service_environment.session_factory() as session:
        with pytest.raises(AnalysisServiceError) as conflict:
            list_project_analysis_runs(session, "project-1")
        assert conflict.value.code == "analysis-approval-binding-invalid"

    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        task = session.get(TaskRecord, intent.task_id)
        assert task is not None
        intent.status = "executing"
        task.status = "running"

    with service_environment.session_factory() as session:
        recover_interrupted_analysis_state(session)
        session.commit()

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.get(RunRecord, "run-terminal-payload-tamper")
        task = session.get(TaskRecord, intent.task_id if intent is not None else "missing")
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"
        assert task is not None and task.status == "failed"


def test_terminal_run_read_and_recovery_require_approved_decision(
    service_environment: ServiceEnvironment,
) -> None:
    intent_id = create_approved_standalone(service_environment)
    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        task = session.get(TaskRecord, intent.task_id)
        approval = session.scalar(
            select(ApprovalRecord).where(ApprovalRecord.subject_id == intent.id)
        )
        assert task is not None and approval is not None
        intent.status = "completed"
        intent.decision = "rejected"
        approval.user_decision = "rejected"
        task.status = "completed"
        session.add(
            RunRecord(
                id="run-terminal-rejected-decision",
                task_id=task.id,
                analysis_intent_id=intent.id,
                input_artifacts=[intent.dataset_source_id],
                output_artifacts=[],
                status="completed",
                finished_at=utc_now(),
            )
        )

    with service_environment.session_factory() as session:
        with pytest.raises(AnalysisServiceError) as conflict:
            list_project_analysis_runs(session, "project-1")
        assert conflict.value.code == "analysis-approval-required"

    with service_environment.session_factory.begin() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        assert intent is not None
        task = session.get(TaskRecord, intent.task_id)
        assert task is not None
        intent.status = "executing"
        task.status = "running"

    with service_environment.session_factory() as session:
        recover_interrupted_analysis_state(session)
        session.commit()

    with service_environment.session_factory() as session:
        intent = session.get(AnalysisIntentRecord, intent_id)
        run = session.get(RunRecord, "run-terminal-rejected-decision")
        task = session.get(TaskRecord, intent.task_id if intent is not None else "missing")
        assert intent is not None and intent.status == "failed"
        assert run is not None and run.status == "failed"
        assert task is not None and task.status == "failed"
