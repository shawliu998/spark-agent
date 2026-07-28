from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

# Reuse the production-shaped local workflow harness rather than fabricate an
# approved plan or frozen inspect payload by hand.
import test_workflow as _workflow_tests
from open_science_core.models import (
    AnswerRecord,
    EvidenceSpanRecord,
    ExtractionCellEvidenceRecord,
    ExtractionCellRecord,
    ExtractionColumnRecord,
    PlanRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
)
from open_science_core.workflow._handlers.sources import ready_source_descriptors


class CoverageWorkflowCase(_workflow_tests.WorkflowApiTest):
    __test__ = False

    def add_ready_source(self, *, source_id: str) -> None:
        self._add_ready_source(source_id=source_id)

    def start(self, *, key: str) -> dict[str, Any]:
        return self._start(key=key)

    def start_remote(self, *, key: str) -> dict[str, Any]:
        return self._start_remote(key=key)

    def run_once(self) -> bool:
        return self._run_once()

    def plan(self, workflow_id: str) -> dict[str, Any]:
        return self._plan(workflow_id)

    def approve(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return self._approve(snapshot)

    def complete_local_workflow(self, key: str) -> str:
        return self._complete_local_workflow(key)


class TestEvidenceCoverage:
    def _case(self) -> CoverageWorkflowCase:
        case = CoverageWorkflowCase(methodName="runTest")
        case.setUp()
        return case

    def test_claim_coverage_is_not_generated_before_synthesis(self) -> None:
        case = self._case()
        try:
            case.add_ready_source(source_id="coverage-claim-not-generated")
            started = case.start(key="coverage-claim-not-generated-0001")
            planned = case.plan(started["workflow"]["id"])
            case.approve(planned)
            assert case.run_once()

            response = case.client.get(
                f"/v1/workflows/{started['workflow']['id']}/evidence-coverage"
            )
            assert response.status_code == 200, response.text
            assert response.json()["claimCoverage"] == {
                "state": "not-generated",
                "totalClaimCount": 0,
                "evidenceLinkedClaimCount": 0,
                "supportedClaimCount": 0,
                "unresolvedQuestionCount": 0,
            }
            assert response.json()["contradictionAssessment"] == "not-assessed"
        finally:
            case.tearDown()

    def test_claim_coverage_counts_unverified_result_without_strengthening_it(self) -> None:
        case = self._case()
        try:
            case.add_ready_source(source_id="coverage-claim-unverified")
            started = case.start(key="coverage-claim-unverified-0001")
            planned = case.plan(started["workflow"]["id"])
            case.approve(planned)
            for _ in range(3):
                assert case.run_once()
            with case.session_factory() as session:
                answer = session.scalar(
                    select(AnswerRecord).where(
                        AnswerRecord.workflow_id == started["workflow"]["id"]
                    )
                )
                assert answer is not None
                answer.unresolved_questions = [
                    "Which population should be prioritized?",
                    "Is follow-up duration comparable?",
                ]
                session.commit()

            response = case.client.get(
                f"/v1/workflows/{started['workflow']['id']}/evidence-coverage"
            )
            assert response.status_code == 200, response.text
            assert response.json()["claimCoverage"] == {
                "state": "not-verified",
                "totalClaimCount": 1,
                "evidenceLinkedClaimCount": 0,
                "supportedClaimCount": 0,
                "unresolvedQuestionCount": 2,
            }
            assert response.json()["contradictionAssessment"] == "not-assessed"
        finally:
            case.tearDown()

    def test_claim_coverage_counts_only_current_verified_frozen_relationships(self) -> None:
        case = self._case()
        try:
            workflow_id = case.complete_local_workflow(
                "coverage-claim-verified-frozen-0001"
            )
            response = case.client.get(
                f"/v1/workflows/{workflow_id}/evidence-coverage"
            )
            assert response.status_code == 200, response.text
            assert response.json()["claimCoverage"] == {
                "state": "verified-frozen",
                "totalClaimCount": 1,
                "evidenceLinkedClaimCount": 1,
                "supportedClaimCount": 1,
                "unresolvedQuestionCount": 1,
            }
            assert response.json()["contradictionAssessment"] == "not-assessed"
        finally:
            case.tearDown()

    def test_not_ready_then_complete_partial_unverified_and_missing_facets(self) -> None:
        case = self._case()
        try:
            case.add_ready_source(source_id="coverage-source-a")
            case.add_ready_source(source_id="coverage-source-b")
            started = case.start(key="coverage-not-ready-0001")
            workflow_id = started["workflow"]["id"]
            not_ready = case.client.get(
                f"/v1/workflows/{workflow_id}/evidence-coverage"
            )
            assert not_ready.status_code == 200
            assert not_ready.json()["state"] == "not-ready"
            assert not_ready.json()["planId"] is None

            planned = case.plan(workflow_id)
            case.approve(planned)
            for _ in range(4):
                assert case.run_once()

            with case.session_factory() as session:
                plan = session.scalar(
                    select(PlanRecord).where(
                        PlanRecord.workflow_id == workflow_id,
                        PlanRecord.status == "approved",
                    )
                )
                assert plan is not None
                approved_plan_id = plan.id
                approved_plan_version = plan.version
                approved_plan_sha256 = plan.spec_sha256
                inspect = session.scalar(
                    select(TaskRecord).where(
                        TaskRecord.plan_id == plan.id,
                        TaskRecord.step_key == "inspect-sources",
                    )
                )
                assert inspect is not None
                source_ids = [item["sourceId"] for item in inspect.outputs["sourceDescriptors"]]
                evidence_by_source = {
                    source_id: session.scalar(
                        select(EvidenceSpanRecord).where(EvidenceSpanRecord.source_id == source_id)
                    )
                    for source_id in source_ids
                }
                assert all(evidence_by_source.values())
                columns = [
                    ExtractionColumnRecord(
                        id="coverage-complete",
                        project_id="project-1",
                        name="Complete",
                        instructions=None,
                        order_index=0,
                        row_version=1,
                    ),
                    ExtractionColumnRecord(
                        id="coverage-partial",
                        project_id="project-1",
                        name="Partial",
                        instructions=None,
                        order_index=1,
                        row_version=1,
                    ),
                    ExtractionColumnRecord(
                        id="coverage-unverified",
                        project_id="project-1",
                        name="Unverified",
                        instructions=None,
                        order_index=2,
                        row_version=1,
                    ),
                    ExtractionColumnRecord(
                        id="coverage-missing",
                        project_id="project-1",
                        name="Missing",
                        instructions=None,
                        order_index=3,
                        row_version=1,
                    ),
                ]
                session.add_all(columns)
                session.flush()
                for source_id in source_ids:
                    evidence = evidence_by_source[source_id]
                    assert evidence is not None
                    cell_id = f"complete-{source_id}"
                    session.add(
                        ExtractionCellRecord(
                            id=cell_id,
                            project_id="project-1",
                            source_id=source_id,
                            column_id="coverage-complete",
                            value="Confirmed local extraction",
                            review_status="confirmed",
                            row_version=1,
                        )
                    )
                first_source = source_ids[0]
                first_evidence = evidence_by_source[first_source]
                assert first_evidence is not None
                partial_cell = "partial-cell"
                session.add(
                    ExtractionCellRecord(
                        id=partial_cell,
                        project_id="project-1",
                        source_id=first_source,
                        column_id="coverage-partial",
                        value="Confirmed partial extraction",
                        review_status="confirmed",
                        row_version=1,
                    )
                )
                unverified = EvidenceSpanRecord(
                    id=str(uuid.uuid4()),
                    source_id=first_source,
                    page_index=first_evidence.page_index,
                    page_label=first_evidence.page_label,
                    text=first_evidence.text,
                    bbox=first_evidence.bbox,
                    coordinate_space=first_evidence.coordinate_space,
                    quote_hash=first_evidence.quote_hash,
                    extraction_method=first_evidence.extraction_method,
                    confidence=first_evidence.confidence,
                    verified=False,
                )
                session.add(unverified)
                unverified_cell = "unverified-cell"
                session.add(
                    ExtractionCellRecord(
                        id=unverified_cell,
                        project_id="project-1",
                        source_id=first_source,
                        column_id="coverage-unverified",
                        value="Confirmed but unverified extraction",
                        review_status="confirmed",
                        row_version=1,
                    )
                )
                session.flush()
                for source_id in source_ids:
                    evidence = evidence_by_source[source_id]
                    assert evidence is not None
                    session.add(
                        ExtractionCellEvidenceRecord(
                            project_id="project-1",
                            cell_id=f"complete-{source_id}",
                            source_id=source_id,
                            evidence_id=evidence.id,
                        )
                    )
                session.add(
                    ExtractionCellEvidenceRecord(
                        project_id="project-1",
                        cell_id=partial_cell,
                        source_id=first_source,
                        evidence_id=first_evidence.id,
                    )
                )
                session.add(
                    ExtractionCellEvidenceRecord(
                        project_id="project-1",
                        cell_id=unverified_cell,
                        source_id=first_source,
                        evidence_id=unverified.id,
                    )
                )
                session.add(
                    ExtractionCellEvidenceRecord(
                        project_id="project-1",
                        cell_id=unverified_cell,
                        source_id=first_source,
                        evidence_id=first_evidence.id,
                    )
                )
                ignored_evidence = EvidenceSpanRecord(
                    id=str(uuid.uuid4()),
                    source_id=first_source,
                    page_index=first_evidence.page_index,
                    page_label=first_evidence.page_label,
                    text=first_evidence.text,
                    bbox=first_evidence.bbox,
                    coordinate_space=first_evidence.coordinate_space,
                    quote_hash="0" * 64,
                    extraction_method=first_evidence.extraction_method,
                    confidence=first_evidence.confidence,
                    verified=True,
                )
                session.add(ignored_evidence)
                session.add(
                    ExtractionCellRecord(
                        id="awaiting-cell",
                        project_id="project-1",
                        source_id=first_source,
                        column_id="coverage-missing",
                        value="Awaiting confirmation",
                        review_status="unreviewed",
                        row_version=1,
                    )
                )
                session.flush()
                session.add(
                    ExtractionCellEvidenceRecord(
                        project_id="project-1",
                        cell_id="awaiting-cell",
                        source_id=first_source,
                        evidence_id=ignored_evidence.id,
                    )
                )
                session.commit()

            response = case.client.get(f"/v1/workflows/{workflow_id}/evidence-coverage")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["state"] == "available"
            assert payload["planId"] == approved_plan_id
            assert payload["planVersion"] == approved_plan_version
            assert payload["planSha256"] == approved_plan_sha256
            assert payload["sourceBreadth"] == {
                "frozenSourceCount": 2,
                "sourcesWithCoveredEvidenceCount": 2,
                "sourcesWithoutCoveredEvidenceCount": 0,
                "verifiedReferencedSpanCount": 2,
            }
            facets = {item["columnId"]: item for item in payload["facets"]}
            assert facets["coverage-complete"]["state"] == "complete"
            assert facets["coverage-partial"]["state"] == "partial"
            assert facets["coverage-unverified"]["state"] == "unverified"
            assert facets["coverage-missing"]["state"] == "missing"
            assert facets["coverage-missing"]["awaitingConfirmationSourceCount"] == 1
            assert payload["claimCoverage"]["state"] == "verified-frozen"
            assert payload["claimCoverage"]["evidenceLinkedClaimCount"] >= 1
            assert payload["contradictionAssessment"] == "not-assessed"
            repeated = case.client.get(
                f"/v1/workflows/{workflow_id}/evidence-coverage"
            )
            assert repeated.content == response.content
        finally:
            case.tearDown()

    def test_remote_inspect_descriptor_drift_from_approved_plan_fails_closed(self) -> None:
        case = self._case()
        try:
            case.add_ready_source(source_id="approved-source")
            gateway = _workflow_tests.FakeModelGateway()
            with (
                patch.object(_workflow_tests.workflow_service, "model_gateway", gateway),
                patch.object(_workflow_tests.workflow_handlers, "model_gateway", gateway),
            ):
                started = case.start_remote(key="coverage-remote-drift-0001")
                planned = case.plan(started["workflow"]["id"])
                case.approve(planned)
                case.add_ready_source(source_id="substitute-source")
                assert case.run_once()
            with case.session_factory() as session:
                workflow = session.get_one(
                    WorkflowRecord,
                    started["workflow"]["id"],
                )
                descriptors = ready_source_descriptors(session, workflow)
                substitute = next(
                    item for item in descriptors if item.source_id == "substitute-source"
                )
                inspect = session.scalar(
                    select(TaskRecord).where(
                        TaskRecord.workflow_id == workflow.id,
                        TaskRecord.step_key == "inspect-sources",
                    )
                )
                assert inspect is not None
                inspect.outputs = {
                    **inspect.outputs,
                    "sourceDescriptors": [
                        substitute.model_dump(mode="json", by_alias=True)
                    ],
                }
                session.commit()
            response = case.client.get(
                f"/v1/workflows/{started['workflow']['id']}/evidence-coverage"
            )
            assert response.status_code == 409, response.text
            assert (
                response.json()["detail"]["code"]
                == "evidence-coverage-frozen-source-invalid"
            )
        finally:
            case.tearDown()

    @pytest.mark.parametrize("tamper", ["quote-hash", "bbox", "file"])
    def test_referenced_verified_span_tamper_fails_closed(self, tamper: str) -> None:
        case = self._case()
        try:
            workflow_id = case.complete_local_workflow(
                f"coverage-tamper-{tamper}-0001"
            )
            with case.session_factory() as session:
                evidence = session.scalar(select(EvidenceSpanRecord))
                assert evidence is not None
                column = ExtractionColumnRecord(
                    id="coverage-tamper-column",
                    project_id="project-1",
                    name="Tamper",
                    instructions=None,
                    order_index=0,
                    row_version=1,
                )
                session.add(column)
                session.flush()
                session.add(
                    ExtractionCellRecord(
                        id="coverage-tamper-cell",
                        project_id="project-1",
                        source_id=evidence.source_id,
                        column_id=column.id,
                        value="Confirmed extraction",
                        review_status="confirmed",
                        row_version=1,
                    )
                )
                session.flush()
                session.add(
                    ExtractionCellEvidenceRecord(
                        project_id="project-1",
                        cell_id="coverage-tamper-cell",
                        source_id=evidence.source_id,
                        evidence_id=evidence.id,
                    )
                )
                if tamper == "quote-hash":
                    evidence.quote_hash = "0" * 64
                elif tamper == "bbox":
                    evidence.bbox = {
                        "x0": 0.0,
                        "y0": 0.0,
                        "x1": 0.1,
                        "y1": 0.1,
                    }
                else:
                    source = session.get_one(SourceRecord, evidence.source_id)
                    with open(source.local_path, "ab") as handle:
                        handle.write(b"-tampered")
                session.commit()
            response = case.client.get(f"/v1/workflows/{workflow_id}/evidence-coverage")
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] in {
                "evidence-coverage-frozen-source-invalid",
                "evidence-coverage-span-invalid",
                "workflow-result-integrity-failed",
                "workflow-source-drift",
            }
        finally:
            case.tearDown()
