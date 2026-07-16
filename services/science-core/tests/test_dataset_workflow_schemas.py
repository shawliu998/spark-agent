from __future__ import annotations

import unittest
from copy import deepcopy
from typing import cast

from pydantic import ValidationError

from open_science_core.workflow.schemas import (
    AnalysisExecutionPendingApprovalOut,
    AnalysisIntentCreatedEventData,
    CollectArtifactsStepInput,
    DatasetAnalysisPlanSpec,
    DatasetAnalysisReviewResult,
    DatasetInspectionStepInput,
    DatasetPlanPendingApprovalOut,
    DatasetProfile,
    DatasetWorkflowCreateIn,
    ExecuteAnalysisStepInput,
    PendingApprovalOut,
    PlanSnapshotOut,
    PrepareAnalysisStepInput,
    RemoteDataApprovalEventData,
    ResearchWorkflowSnapshot,
    ReviewSnapshotOut,
    WorkflowAnalysisIntentOut,
    WorkflowAnalysisRunOut,
    WorkflowCreateIn,
    WorkflowEventOut,
)

DATASET_HASH = "a" * 64


def _valid_dataset_profile() -> dict[str, object]:
    return {
        "schemaVersion": "1",
        "datasetSourceId": "dataset-source-1",
        "filename": "experiment.csv",
        "contentHash": DATASET_HASH,
        "fileSizeBytes": 128,
        "encoding": "utf-8",
        "delimiter": ",",
        "rowCount": 2,
        "columnCount": 2,
        "columns": [
            {
                "index": 0,
                "name": "group",
                "inferredType": "categorical",
                "missingCount": 0,
                "uniqueCount": 2,
                "numericRange": None,
                "lowCardinality": {
                    "values": ["control", "treated"],
                    "truncated": False,
                },
                "potentialDate": False,
                "potentialId": False,
                "mixedType": False,
            },
            {
                "index": 1,
                "name": "outcome",
                "inferredType": "number",
                "missingCount": 0,
                "uniqueCount": 2,
                "numericRange": {"minimum": 1.0, "maximum": 2.0},
                "lowCardinality": None,
                "potentialDate": False,
                "potentialId": False,
                "mixedType": False,
            },
        ],
        "sampling": {
            "method": "head-and-reservoir-v1",
            "rowsRead": 2,
            "rowsProfiled": 2,
            "maxSampleRows": 500,
            "seed": 0,
        },
        "warnings": [],
    }


def _valid_dataset_plan() -> dict[str, object]:
    expected_outputs = [
        "executed-notebook",
        "summary-table",
        "figures",
        "analysis-log",
        "environment-manifest",
    ]
    return {
        "schemaVersion": "1",
        "workflowType": "dataset-analysis",
        "goal": "Quantify the relationship between treatment and recovery.",
        "datasetSourceId": "dataset-source-1",
        "datasetContentHash": DATASET_HASH,
        "assumptions": ["The source file contains a header row."],
        "questionsForUser": [],
        "steps": [
            {
                "key": "inspect-dataset",
                "type": "dataset-inspection",
                "objective": "Inspect schema, missingness, and a bounded sample.",
                "dependencies": [],
                "inputs": {
                    "datasetSourceId": "dataset-source-1",
                    "datasetContentHash": DATASET_HASH,
                    "samplingMethod": "head-and-reservoir-v1",
                    "maxSampleRows": 500,
                },
                "expectedArtifacts": ["dataset-profile"],
                "acceptanceCriteria": ["A bounded dataset profile is persisted."],
                "riskLevel": "low",
            },
            {
                "key": "prepare-analysis",
                "type": "prepare-analysis",
                "objective": "Prepare content-bound Python analysis for approval.",
                "dependencies": ["inspect-dataset"],
                "inputs": {
                    "datasetSourceId": "dataset-source-1",
                    "datasetContentHash": DATASET_HASH,
                    "profileStepKey": "inspect-dataset",
                },
                "expectedArtifacts": ["analysis-intent"],
                "acceptanceCriteria": ["The intent binds code to the dataset hash."],
                "riskLevel": "medium",
            },
            {
                "key": "execute-analysis",
                "type": "python-data-analysis",
                "objective": "Execute only the approved analysis intent.",
                "dependencies": ["prepare-analysis"],
                "inputs": {
                    "datasetSourceId": "dataset-source-1",
                    "datasetContentHash": DATASET_HASH,
                    "preparationStepKey": "prepare-analysis",
                    "expectedOutputs": expected_outputs,
                    "timeoutSeconds": 600,
                },
                "expectedArtifacts": [
                    "executed-notebook",
                    "summary-table",
                    "figure",
                    "analysis-log",
                    "environment-manifest",
                ],
                "acceptanceCriteria": ["Execution completes inside the approved sandbox."],
                "riskLevel": "high",
            },
            {
                "key": "collect-artifacts",
                "type": "collect-artifacts",
                "objective": "Collect and hash every declared analysis artifact.",
                "dependencies": ["execute-analysis"],
                "inputs": {
                    "executionStepKey": "execute-analysis",
                    "expectedOutputs": expected_outputs,
                },
                "expectedArtifacts": [
                    "executed-notebook",
                    "summary-table",
                    "figure",
                    "analysis-log",
                    "environment-manifest",
                ],
                "acceptanceCriteria": ["Every collected artifact has a verified hash."],
                "riskLevel": "low",
            },
        ],
    }


def _review_payload(verdict: str = "passed") -> dict[str, object]:
    return {
        "schemaVersion": "1",
        "verdict": verdict,
        "checks": [
            {
                "code": "dataset-hash-matches",
                "status": "passed",
                "message": "The run used the approved dataset bytes.",
                "artifactId": None,
            }
        ],
        "artifactIssues": [],
        "numericIssues": [],
        "methodWarnings": [],
        "requiredRevisions": [],
        "runId": "run-1",
        "analysisIntentId": "intent-1",
        "inputDatasetContentHash": DATASET_HASH,
    }


def _valid_dataset_snapshot() -> dict[str, object]:
    return {
        "workflow": {
            "id": "workflow-1",
            "projectId": "project-1",
            "workflowType": "dataset-analysis",
            "datasetSourceId": "dataset-source-1",
            "datasetContentHash": DATASET_HASH,
            "goal": "Quantify the relationship between treatment and recovery.",
            "generationMode": "local-deterministic",
            "status": "running",
            "revision": 3,
            "planVersion": None,
            "currentStepId": None,
            "retryCount": 0,
            "blockingReason": None,
            "cancelRequestedAt": None,
            "createdAt": "2026-07-15T00:00:00Z",
            "updatedAt": "2026-07-15T00:00:00Z",
            "completedAt": None,
        },
        "plan": None,
        "pendingApprovals": [],
        "result": None,
        "latestReview": None,
        "datasetProfile": None,
        "analysisIntent": None,
        "analysisRun": None,
        "reviewWarningAcceptance": None,
        "allowedActions": ["cancel"],
        "eventCursor": 2,
    }


def _valid_dataset_plan_snapshot() -> dict[str, object]:
    spec = _valid_dataset_plan()
    declared_steps = spec["steps"]
    assert isinstance(declared_steps, list)
    typed_declared_steps = cast(list[dict[str, object]], declared_steps)
    materialized: list[dict[str, object]] = []
    for order_index, (task_id, declared) in enumerate(
        zip(
            ("task-inspect", "task-prepare", "task-execute", "task-collect"),
            typed_declared_steps,
            strict=True,
        )
    ):
        assert isinstance(declared, dict)
        materialized.append(
            {
                "id": task_id,
                "key": declared["key"],
                "orderIndex": order_index,
                "type": declared["type"],
                "objective": declared["objective"],
                "status": "completed" if order_index < 3 else "pending",
                "retryCount": 0,
                "startedAt": None,
                "completedAt": None,
                "outputSummary": None,
            }
        )
    return {
        "id": "plan-1",
        "workflowId": "workflow-1",
        "version": 1,
        "status": "approved",
        "planSha256": "c" * 64,
        "generator": "dataset-template-v1",
        "model": None,
        "promptVersion": None,
        "spec": spec,
        "steps": materialized,
        "createdAt": "2026-07-15T00:00:00Z",
        "approvedAt": "2026-07-15T00:00:01Z",
    }


def _valid_workflow_intent() -> dict[str, object]:
    return {
        "id": "intent-1",
        "taskId": "task-execute",
        "projectId": "project-1",
        "datasetSourceId": "dataset-source-1",
        "datasetContentHash": DATASET_HASH,
        "objective": "Execute only the approved analysis intent.",
        "code": "print('approved')",
        "payloadSha256": "b" * 64,
        "riskLevel": "high",
        "affectedResources": ["dataset:dataset-source-1"],
        "status": "completed",
        "decision": "approved",
        "workflowId": "workflow-1",
        "planStepId": "execute-analysis",
        "previousIntentId": None,
        "expectedOutputs": [
            "executed-notebook",
            "summary-table",
            "figures",
            "analysis-log",
            "environment-manifest",
        ],
        "timeoutSeconds": 600,
        "repairAttempt": 0,
        "errorSummary": None,
        "codeDiff": None,
        "createdAt": "2026-07-15T00:00:00Z",
        "updatedAt": "2026-07-15T00:00:00Z",
    }


def _valid_workflow_run() -> dict[str, object]:
    artifacts = [
        {
            "id": f"artifact-{index}",
            "artifactType": artifact_type,
            "path": f"runs/run-1/{filename}",
            "mimeType": mime_type,
            "contentHash": ("c" if artifact_type == "environment" else "d") * 64,
            "sizeBytes": 1,
            "createdAt": "2026-07-15T00:01:00Z",
        }
        for index, (artifact_type, filename, mime_type) in enumerate(
            (
                ("notebook-executed", "executed.ipynb", "application/x-ipynb+json"),
                ("environment", "environment.json", "application/json"),
                ("stdout", "stdout.txt", "text/plain"),
                ("stderr", "stderr.txt", "text/plain"),
                ("log", "execution.log", "text/plain"),
                ("dataset", "summary.csv", "text/csv"),
                ("figure", "figure.png", "image/png"),
            ),
            start=1,
        )
    ]
    return {
        "id": "run-1",
        "intentId": "intent-1",
        "taskId": "task-execute",
        "projectId": "project-1",
        "datasetSourceId": "dataset-source-1",
        "objective": "Execute only the approved analysis intent.",
        "code": "print('approved')",
        "payloadSha256": "b" * 64,
        "status": "completed",
        "environmentHash": "c" * 64,
        "inputArtifacts": ["dataset-source-1"],
        "outputArtifacts": [artifact["path"] for artifact in artifacts],
        "stdout": "",
        "stderr": "",
        "log": "",
        "logs": "",
        "error": None,
        "artifacts": artifacts,
        "createdAt": "2026-07-15T00:00:00Z",
        "finishedAt": "2026-07-15T00:01:00Z",
    }


class DatasetWorkflowSchemaTest(unittest.TestCase):
    def test_dataset_profile_is_bounded_structured_and_content_bound(self) -> None:
        profile = DatasetProfile.model_validate(_valid_dataset_profile())
        self.assertEqual(profile.content_hash, DATASET_HASH)
        assert profile.columns[1].numeric_range is not None
        self.assertEqual(profile.columns[1].numeric_range.maximum, 2.0)

        invalid = profile.model_dump(mode="json", by_alias=True)
        invalid["filename"] = "../experiment.csv"
        with self.assertRaises(ValidationError):
            DatasetProfile.model_validate(invalid)

    def test_fixed_four_step_plan_matches_the_cross_language_contract(self) -> None:
        plan = DatasetAnalysisPlanSpec.model_validate(_valid_dataset_plan())

        self.assertEqual(
            [(step.key, step.type, step.dependencies, step.risk_level) for step in plan.steps],
            [
                ("inspect-dataset", "dataset-inspection", (), "low"),
                ("prepare-analysis", "prepare-analysis", ("inspect-dataset",), "medium"),
                (
                    "execute-analysis",
                    "python-data-analysis",
                    ("prepare-analysis",),
                    "high",
                ),
                (
                    "collect-artifacts",
                    "collect-artifacts",
                    ("execute-analysis",),
                    "low",
                ),
            ],
        )
        self.assertIsInstance(plan.steps[0].inputs, DatasetInspectionStepInput)
        self.assertIsInstance(plan.steps[1].inputs, PrepareAnalysisStepInput)
        self.assertIsInstance(plan.steps[2].inputs, ExecuteAnalysisStepInput)
        self.assertIsInstance(plan.steps[3].inputs, CollectArtifactsStepInput)
        dumped = plan.model_dump(mode="json", by_alias=True)
        self.assertEqual(dumped["workflowType"], "dataset-analysis")
        self.assertEqual(dumped["datasetContentHash"], DATASET_HASH)
        self.assertEqual(len(dumped["steps"]), 4)

        materialized = PlanSnapshotOut.model_validate(_valid_dataset_plan_snapshot())
        self.assertEqual(
            [step.id for step in materialized.steps],
            ["task-inspect", "task-prepare", "task-execute", "task-collect"],
        )

        missing_tasks = _valid_dataset_plan_snapshot()
        missing_tasks["steps"] = []
        with self.assertRaises(ValidationError):
            PlanSnapshotOut.model_validate(missing_tasks)

    def test_plan_rejects_sequence_dependency_risk_and_binding_drift(self) -> None:
        mutations: list[tuple[str, object]] = []

        wrong_order = _valid_dataset_plan()
        steps = wrong_order["steps"]
        assert isinstance(steps, list)
        steps[0], steps[1] = steps[1], steps[0]
        mutations.append(("order", wrong_order))

        wrong_type = _valid_dataset_plan()
        steps = wrong_type["steps"]
        assert isinstance(steps, list)
        assert isinstance(steps[2], dict)
        steps[2]["type"] = "collect-artifacts"
        mutations.append(("type", wrong_type))

        wrong_dependency = _valid_dataset_plan()
        steps = wrong_dependency["steps"]
        assert isinstance(steps, list)
        assert isinstance(steps[3], dict)
        steps[3]["dependencies"] = ["prepare-analysis"]
        mutations.append(("dependency", wrong_dependency))

        wrong_risk = _valid_dataset_plan()
        steps = wrong_risk["steps"]
        assert isinstance(steps, list)
        assert isinstance(steps[1], dict)
        steps[1]["riskLevel"] = "low"
        mutations.append(("risk", wrong_risk))

        identity_drift = _valid_dataset_plan()
        steps = identity_drift["steps"]
        assert isinstance(steps, list)
        assert isinstance(steps[2], dict)
        inputs = cast(dict[str, object], steps[2]["inputs"])
        assert isinstance(inputs, dict)
        inputs["datasetContentHash"] = "b" * 64
        mutations.append(("identity", identity_drift))

        output_drift = _valid_dataset_plan()
        steps = output_drift["steps"]
        assert isinstance(steps, list)
        assert isinstance(steps[3], dict)
        inputs = cast(dict[str, object], steps[3]["inputs"])
        assert isinstance(inputs, dict)
        inputs["expectedOutputs"] = ["analysis-log"]
        mutations.append(("outputs", output_drift))

        empty_artifacts = _valid_dataset_plan()
        steps = empty_artifacts["steps"]
        assert isinstance(steps, list)
        assert isinstance(steps[0], dict)
        steps[0]["expectedArtifacts"] = []
        mutations.append(("artifacts", empty_artifacts))

        empty_criteria = _valid_dataset_plan()
        steps = empty_criteria["steps"]
        assert isinstance(steps, list)
        assert isinstance(steps[0], dict)
        steps[0]["acceptanceCriteria"] = []
        mutations.append(("criteria", empty_criteria))

        missing_audit_outputs = _valid_dataset_plan()
        steps = missing_audit_outputs["steps"]
        assert isinstance(steps, list)
        for index in (2, 3):
            assert isinstance(steps[index], dict)
            inputs = cast(dict[str, object], steps[index]["inputs"])
            assert isinstance(inputs, dict)
            inputs["expectedOutputs"] = ["summary-table"]
            steps[index]["expectedArtifacts"] = ["summary-table"]
        mutations.append(("mandatory-audit-outputs", missing_audit_outputs))

        reordered_artifacts = _valid_dataset_plan()
        steps = reordered_artifacts["steps"]
        assert isinstance(steps, list)
        reordered = [
            "figure",
            "executed-notebook",
            "summary-table",
            "analysis-log",
            "environment-manifest",
        ]
        for index in (2, 3):
            assert isinstance(steps[index], dict)
            steps[index]["expectedArtifacts"] = reordered
        mutations.append(("artifact-order", reordered_artifacts))

        extra_field = _valid_dataset_plan()
        steps = extra_field["steps"]
        assert isinstance(steps, list)
        assert isinstance(steps[0], dict)
        steps[0]["unregisteredHandler"] = True
        mutations.append(("extra", extra_field))

        for label, payload in mutations:
            with self.subTest(label=label), self.assertRaises(ValidationError):
                DatasetAnalysisPlanSpec.model_validate(payload)

    def test_workflow_create_requires_dataset_identity_only_for_dataset_workflows(self) -> None:
        literature = WorkflowCreateIn.model_validate({"goal": "Review local literature"})
        self.assertEqual(literature.workflow_type, "literature-synthesis")
        dataset = DatasetWorkflowCreateIn.model_validate(
            {
                "goal": "Analyze this local dataset",
                "workflowType": "dataset-analysis",
                "datasetSourceId": "dataset-source-1",
            }
        )
        self.assertEqual(dataset.dataset_source_id, "dataset-source-1")
        self.assertEqual(
            dataset.model_dump(mode="json", by_alias=True)["datasetSourceId"],
            "dataset-source-1",
        )

        with self.assertRaises(ValidationError):
            DatasetWorkflowCreateIn.model_validate(
                {"goal": "Analyze this local dataset", "workflowType": "dataset-analysis"}
            )
        with self.assertRaises(ValidationError):
            WorkflowCreateIn.model_validate(
                {
                    "goal": "Review local literature",
                    "workflowType": "literature-synthesis",
                    "datasetSourceId": "dataset-source-1",
                }
            )

    def test_deterministic_analysis_review_accepts_supported_verdicts(self) -> None:
        passed = DatasetAnalysisReviewResult.model_validate(_review_payload())
        self.assertEqual(passed.verdict, "passed")

        warning_payload = _review_payload("passed-with-warnings")
        warning_payload["checks"] = [
            {
                "code": "small-sample",
                "status": "warning",
                "message": "The sample is too small for a stable interval.",
                "artifactId": "summary-1",
            }
        ]
        warning_payload["methodWarnings"] = [
            {
                "code": "small-sample",
                "message": "Interpret the interval cautiously.",
                "artifactId": "summary-1",
            }
        ]
        warning = DatasetAnalysisReviewResult.model_validate(warning_payload)
        self.assertEqual(warning.verdict, "passed-with-warnings")

        revision_payload = _review_payload("revision-required")
        revision_payload["checks"] = [
            {
                "code": "missing-environment",
                "status": "failed",
                "message": "The environment manifest is missing.",
                "artifactId": None,
            }
        ]
        revision_payload["artifactIssues"] = [
            {
                "code": "missing-environment",
                "message": "Collect an environment manifest.",
                "artifactId": None,
            }
        ]
        revision_payload["requiredRevisions"] = ["Collect the environment manifest."]
        revision = DatasetAnalysisReviewResult.model_validate(revision_payload)
        self.assertEqual(revision.verdict, "revision-required")

        snapshot = ReviewSnapshotOut.model_validate(
            {
                "id": "review-1",
                "reviewType": "deterministic-analysis-v1",
                "verdict": "passed-with-warnings",
                "inputSha256": "c" * 64,
                "result": warning_payload,
                "createdAt": "2026-07-15T00:00:00Z",
            }
        )
        self.assertIsInstance(snapshot.result, DatasetAnalysisReviewResult)

        mismatched_snapshot = snapshot.model_dump(mode="json", by_alias=True)
        mismatched_snapshot["verdict"] = "failed"
        with self.assertRaises(ValidationError):
            ReviewSnapshotOut.model_validate(mismatched_snapshot)

        wrong_review_type = snapshot.model_dump(mode="json", by_alias=True)
        wrong_review_type["reviewType"] = "deterministic-claims-v1"
        with self.assertRaises(ValidationError):
            ReviewSnapshotOut.model_validate(wrong_review_type)

        claims_with_dataset_verdict: dict[str, object] = {
            "id": "review-claims-1",
            "reviewType": "deterministic-claims-v1",
            "verdict": "passed-with-warnings",
            "inputSha256": "d" * 64,
            "result": {
                "schemaVersion": "1",
                "verdict": "passed-with-warnings",
                "checks": [],
                "claimResults": [],
                "requiredRevisions": [],
                "resultSnapshotSha256": None,
                "resultSnapshot": None,
            },
            "createdAt": "2026-07-15T00:00:00Z",
        }
        with self.assertRaises(ValidationError):
            ReviewSnapshotOut.model_validate(claims_with_dataset_verdict)

    def test_remote_dataset_disclosure_requires_the_exact_category_pair(self) -> None:
        event = RemoteDataApprovalEventData.model_validate(
            {
                "provider": "openai-compatible",
                "endpointHost": "models.example.test",
                "endpointIdentity": f"sha256:{'d' * 64}",
                "model": "example-model",
                "dataCategories": ["user-goal", "dataset-profile"],
            }
        )
        self.assertEqual(event.data_categories, ["user-goal", "dataset-profile"])

        for categories in (
            ["dataset-profile"],
            ["user-goal", "user-goal"],
            ["dataset-profile", "user-goal"],
        ):
            with self.subTest(categories=categories), self.assertRaises(ValidationError):
                RemoteDataApprovalEventData.model_validate(
                    {
                        "provider": "openai-compatible",
                        "endpointHost": "models.example.test",
                        "endpointIdentity": f"sha256:{'d' * 64}",
                        "model": "example-model",
                        "dataCategories": categories,
                    }
                )

    def test_dataset_analysis_event_payloads_are_strict_and_content_bound(self) -> None:
        event = WorkflowEventOut.model_validate(
            {
                "id": "event-1",
                "sequence": 7,
                "type": "analysis.intent-created",
                "taskId": "task-1",
                "jobId": "job-1",
                "data": {
                    "analysisIntentId": "intent-1",
                    "taskId": "task-1",
                    "jobId": "job-1",
                    "planStepId": "execute-analysis",
                    "datasetSourceId": "dataset-source-1",
                    "datasetContentHash": DATASET_HASH,
                    "payloadSha256": "b" * 64,
                    "repairAttempt": 0,
                },
                "createdAt": "2026-07-15T00:00:00Z",
            }
        )
        self.assertIsInstance(event.data, AnalysisIntentCreatedEventData)
        assert isinstance(event.data, AnalysisIntentCreatedEventData)
        self.assertEqual(event.data.analysis_intent_id, "intent-1")

        malformed = event.model_dump(mode="json", by_alias=True)
        assert isinstance(malformed["data"], dict)
        malformed["data"]["payloadSha256"] = "not-a-hash"
        with self.assertRaises(ValidationError):
            WorkflowEventOut.model_validate(malformed)

        mismatched = event.model_dump(mode="json", by_alias=True)
        mismatched["type"] = "analysis.run-completed"
        with self.assertRaises(ValidationError):
            WorkflowEventOut.model_validate(mismatched)

        mismatched_task = event.model_dump(mode="json", by_alias=True)
        mismatched_task["taskId"] = "task-other"
        with self.assertRaises(ValidationError):
            WorkflowEventOut.model_validate(mismatched_task)

        completed_run = {
            "id": "event-run-1",
            "sequence": 8,
            "type": "analysis.run-completed",
            "taskId": "task-1",
            "jobId": "job-1",
            "data": {
                "analysisIntentId": "intent-1",
                "runId": "run-1",
                "taskId": "task-1",
                "jobId": "job-1",
                "payloadSha256": "b" * 64,
                "environmentHash": "c" * 64,
                "artifactCount": 5,
                "errorCode": None,
            },
            "createdAt": "2026-07-15T00:00:00Z",
        }
        WorkflowEventOut.model_validate(completed_run)
        incomplete_run = deepcopy(completed_run)
        assert isinstance(incomplete_run["data"], dict)
        incomplete_run["data"]["artifactCount"] = 4
        with self.assertRaises(ValidationError):
            WorkflowEventOut.model_validate(incomplete_run)

        progress = {
            "id": "event-progress-1",
            "sequence": 9,
            "type": "analysis.run-progress",
            "taskId": "task-1",
            "jobId": "job-1",
            "data": {
                "analysisIntentId": "intent-1",
                "runId": "run-1",
                "taskId": "task-1",
                "jobId": "job-1",
                "stage": "executing-runtime",
                "elapsedSeconds": 0.25,
            },
            "createdAt": "2026-07-15T00:00:01Z",
        }
        WorkflowEventOut.model_validate(progress)
        fabricated_percentage = deepcopy(progress)
        assert isinstance(fabricated_percentage["data"], dict)
        fabricated_percentage["data"]["percent"] = 50
        with self.assertRaises(ValidationError):
            WorkflowEventOut.model_validate(fabricated_percentage)
        negative_elapsed = deepcopy(progress)
        assert isinstance(negative_elapsed["data"], dict)
        negative_elapsed["data"]["elapsedSeconds"] = -1
        with self.assertRaises(ValidationError):
            WorkflowEventOut.model_validate(negative_elapsed)

    def test_interaction_events_are_discriminated_by_lifecycle_stage(self) -> None:
        requested = {
            "id": "event-interaction-requested",
            "sequence": 1,
            "type": "interaction.requested",
            "taskId": None,
            "jobId": None,
            "data": {
                "interactionId": "interaction-1",
                "requestType": "single-choice",
                "required": True,
                "responseId": None,
                "responseRevision": None,
                "expectedWorkflowRevision": 2,
            },
            "createdAt": "2026-07-16T00:00:00Z",
        }
        WorkflowEventOut.model_validate(requested)

        requested_with_response = deepcopy(requested)
        assert isinstance(requested_with_response["data"], dict)
        requested_with_response["data"]["responseId"] = "response-1"
        requested_with_response["data"]["responseRevision"] = 1
        with self.assertRaises(ValidationError):
            WorkflowEventOut.model_validate(requested_with_response)

        answered_without_response = deepcopy(requested)
        answered_without_response["type"] = "interaction.answered"
        with self.assertRaises(ValidationError):
            WorkflowEventOut.model_validate(answered_without_response)

        answered = deepcopy(requested_with_response)
        answered["type"] = "interaction.answered"
        WorkflowEventOut.model_validate(answered)

    def test_dataset_approvals_cannot_fall_back_to_the_literature_envelope(self) -> None:
        common = {
            "id": "approval-1",
            "workflowId": "workflow-1",
            "planId": "plan-1",
            "status": "waiting",
            "payloadSha256": "b" * 64,
            "reason": "Continue only within this immutable dataset workflow.",
            "affectedResources": ["dataset:dataset-source-1"],
            "createdAt": "2026-07-15T00:00:00Z",
            "decidedAt": None,
        }
        plan_payload = {
            **common,
            "taskId": None,
            "kind": "plan",
            "subjectType": "plan",
            "subjectId": "plan-1",
            "action": "approve-plan",
            "riskLevel": "medium",
            "workflowType": "dataset-analysis",
            "approvalSchemaVersion": "workflow-plan-approval-v3",
            "planVersion": 1,
            "planSha256": "c" * 64,
            "expectedWorkflowRevision": 2,
            "datasetSourceId": "dataset-source-1",
            "datasetContentHash": DATASET_HASH,
        }
        plan_approval = DatasetPlanPendingApprovalOut.model_validate(plan_payload)
        self.assertEqual(plan_approval.approval_schema_version, "workflow-plan-approval-v3")
        with self.assertRaises(ValidationError):
            PendingApprovalOut.model_validate(plan_payload)

        analysis_payload = {
            **common,
            "taskId": "task-1",
            "kind": "analysis-execution",
            "subjectType": "analysis-intent",
            "subjectId": "intent-1",
            "action": "execute-python-data-analysis",
            "riskLevel": "high",
            "approvalSchemaVersion": "analysis-intent-v3",
            "expectedWorkflowRevision": 4,
            "analysisIntentId": "intent-1",
            "planStepId": "execute-analysis",
            "datasetSourceId": "dataset-source-1",
            "datasetContentHash": DATASET_HASH,
            "expectedOutputs": [
                "executed-notebook",
                "analysis-log",
                "environment-manifest",
            ],
            "timeoutSeconds": 600,
            "code": "print('approved')",
            "codeDiff": None,
        }
        analysis_approval = AnalysisExecutionPendingApprovalOut.model_validate(analysis_payload)
        self.assertEqual(analysis_approval.analysis_intent_id, "intent-1")

        duplicated_output = deepcopy(analysis_payload)
        duplicated_output["expectedOutputs"] = [
            "executed-notebook",
            "analysis-log",
            "analysis-log",
            "environment-manifest",
        ]
        with self.assertRaises(ValidationError):
            AnalysisExecutionPendingApprovalOut.model_validate(duplicated_output)

    def test_dataset_snapshot_rejects_identity_and_run_contract_drift(self) -> None:
        snapshot_payload = _valid_dataset_snapshot()
        snapshot = ResearchWorkflowSnapshot.model_validate(snapshot_payload)
        self.assertEqual(snapshot.workflow.workflow_type, "dataset-analysis")

        false_completion = _valid_dataset_snapshot()
        assert isinstance(false_completion["workflow"], dict)
        false_completion["workflow"]["status"] = "completed"
        false_completion["workflow"]["completedAt"] = "2026-07-15T00:01:00Z"
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(false_completion)

        stranded_plan = _valid_dataset_snapshot()
        assert isinstance(stranded_plan["workflow"], dict)
        stranded_plan["workflow"]["planVersion"] = 1
        stranded_plan["plan"] = _valid_dataset_plan_snapshot()
        assert isinstance(stranded_plan["plan"], dict)
        stranded_plan["plan"]["status"] = "pending-approval"
        stranded_plan["plan"]["approvedAt"] = None
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(stranded_plan)

        profile_drift = _valid_dataset_snapshot()
        profile_drift["datasetProfile"] = {
            **_valid_dataset_profile(),
            "contentHash": "d" * 64,
        }
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(profile_drift)

        bound_run = _valid_dataset_snapshot()
        assert isinstance(bound_run["workflow"], dict)
        bound_run["workflow"]["planVersion"] = 1
        bound_run["plan"] = _valid_dataset_plan_snapshot()
        bound_run["analysisIntent"] = _valid_workflow_intent()
        bound_run["analysisRun"] = _valid_workflow_run()
        parsed = ResearchWorkflowSnapshot.model_validate(bound_run)
        self.assertIsInstance(parsed.analysis_intent, WorkflowAnalysisIntentOut)
        self.assertIsInstance(parsed.analysis_run, WorkflowAnalysisRunOut)

        completed = deepcopy(bound_run)
        assert isinstance(completed["workflow"], dict)
        assert isinstance(completed["plan"], dict)
        completed["workflow"]["status"] = "completed"
        completed["workflow"]["completedAt"] = "2026-07-15T00:02:00Z"
        materialized_steps = cast(
            list[dict[str, object]],
            completed["plan"]["steps"],
        )
        assert isinstance(materialized_steps, list)
        for step in materialized_steps:
            assert isinstance(step, dict)
            step["status"] = "completed"
            step["completedAt"] = "2026-07-15T00:01:00Z"
        completed["datasetProfile"] = _valid_dataset_profile()
        completed["latestReview"] = {
            "id": "review-1",
            "reviewType": "deterministic-analysis-v1",
            "verdict": "passed",
            "inputSha256": "f" * 64,
            "result": _review_payload(),
            "createdAt": "2026-07-15T00:01:30Z",
        }
        completed["allowedActions"] = []
        ResearchWorkflowSnapshot.model_validate(completed)

        stranded_intent = deepcopy(bound_run)
        stranded_intent["analysisRun"] = None
        assert isinstance(stranded_intent["analysisIntent"], dict)
        stranded_intent["analysisIntent"]["status"] = "waiting-approval"
        stranded_intent["analysisIntent"]["decision"] = None
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(stranded_intent)

        invented_status = deepcopy(bound_run)
        assert isinstance(invented_status["analysisRun"], dict)
        invented_status["analysisRun"]["status"] = "invented"
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(invented_status)

        missing_artifacts = deepcopy(bound_run)
        assert isinstance(missing_artifacts["analysisRun"], dict)
        missing_artifacts["analysisRun"]["artifacts"] = []
        missing_artifacts["analysisRun"]["outputArtifacts"] = []
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(missing_artifacts)

        environment_drift = deepcopy(bound_run)
        assert isinstance(environment_drift["analysisRun"], dict)
        environment_drift["analysisRun"]["environmentHash"] = "e" * 64
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(environment_drift)

        unapproved_run = deepcopy(bound_run)
        assert isinstance(unapproved_run["analysisIntent"], dict)
        unapproved_run["analysisIntent"]["status"] = "waiting-approval"
        unapproved_run["analysisIntent"]["decision"] = None
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(unapproved_run)

        dropped_plan_outputs = deepcopy(bound_run)
        assert isinstance(dropped_plan_outputs["analysisIntent"], dict)
        dropped_plan_outputs["analysisIntent"]["expectedOutputs"] = [
            "executed-notebook",
            "analysis-log",
            "environment-manifest",
        ]
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(dropped_plan_outputs)

        extra_input = deepcopy(bound_run)
        assert isinstance(extra_input["analysisRun"], dict)
        extra_input["analysisRun"]["inputArtifacts"] = [
            "dataset-source-1",
            "unapproved-source",
        ]
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(extra_input)

        mismatched_task = deepcopy(bound_run)
        assert isinstance(mismatched_task["analysisRun"], dict)
        mismatched_task["analysisRun"]["taskId"] = "task-other"
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(mismatched_task)

        rogue_task = deepcopy(bound_run)
        assert isinstance(rogue_task["analysisIntent"], dict)
        assert isinstance(rogue_task["analysisRun"], dict)
        rogue_task["analysisIntent"]["taskId"] = "rogue-task"
        rogue_task["analysisRun"]["taskId"] = "rogue-task"
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(rogue_task)

        unknown_field = _valid_dataset_snapshot()
        unknown_field["untrustedDatasetState"] = {"rawRows": [["secret"]]}
        with self.assertRaises(ValidationError):
            ResearchWorkflowSnapshot.model_validate(unknown_field)

    def test_workflow_intent_repair_lineage_is_complete(self) -> None:
        WorkflowAnalysisIntentOut.model_validate(_valid_workflow_intent())

        repair = _valid_workflow_intent()
        repair.update(
            {
                "id": "intent-2",
                "status": "waiting-approval",
                "decision": None,
                "previousIntentId": "intent-1",
                "repairAttempt": 1,
                "errorSummary": {
                    "schemaVersion": "1",
                    "category": "runtime",
                    "code": "analysis-runtime-failed",
                    "userMessage": "The approved analysis did not complete.",
                    "stderrExcerpt": "safe excerpt",
                    "retryable": True,
                },
                "codeDiff": "-print('old')\n+print('repaired')",
            }
        )
        WorkflowAnalysisIntentOut.model_validate(repair)

        missing_lineage = deepcopy(repair)
        missing_lineage["codeDiff"] = None
        with self.assertRaises(ValidationError):
            WorkflowAnalysisIntentOut.model_validate(missing_lineage)

    def test_deterministic_analysis_review_rejects_inconsistent_verdicts(self) -> None:
        passed_with_warning = _review_payload("passed")
        passed_with_warning["methodWarnings"] = [
            {
                "code": "warning",
                "message": "A warning cannot be hidden by a passed verdict.",
                "artifactId": None,
            }
        ]

        warning_without_warning = _review_payload("passed-with-warnings")

        revision_without_action = _review_payload("revision-required")
        revision_without_action["checks"] = [
            {
                "code": "failed-check",
                "status": "failed",
                "message": "This failed check requires a revision.",
                "artifactId": None,
            }
        ]

        unknown_field = deepcopy(_review_payload())
        unknown_field["unverifiedNarrative"] = "not allowed"

        for payload in (
            passed_with_warning,
            warning_without_warning,
            revision_without_action,
            unknown_field,
        ):
            with self.subTest(verdict=payload["verdict"]), self.assertRaises(ValidationError):
                DatasetAnalysisReviewResult.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
