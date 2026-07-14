from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectRecord(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    project_path: Mapped[str] = mapped_column(Text, unique=True)
    research_domain: Mapped[str | None] = mapped_column(String(200), nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(32), default="safe")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    sources: Mapped[list[SourceRecord]] = relationship(back_populates="project", cascade="all, delete-orphan")


class SourceRecord(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("project_id", "content_hash", name="uq_source_project_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    source_kind: Mapped[str] = mapped_column(String(32), default="pdf")
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    local_path: Mapped[str] = mapped_column(Text)
    publication_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ingestion_status: Mapped[str] = mapped_column(String(32), default="pending")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[ProjectRecord] = relationship(back_populates="sources")
    pages: Mapped[list[SourcePageRecord]] = relationship(
        back_populates="source", cascade="all, delete-orphan", order_by="SourcePageRecord.page_index"
    )


class SourcePageRecord(Base):
    __tablename__ = "source_pages"
    __table_args__ = (UniqueConstraint("source_id", "page_index", name="uq_source_page"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    page_index: Mapped[int] = mapped_column(Integer)
    page_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    width: Mapped[float] = mapped_column(Float)
    height: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    source: Mapped[SourceRecord] = relationship(back_populates="pages")


class AnswerRecord(Base):
    __tablename__ = "answers"
    __table_args__ = (
        Index(
            "uq_workflow_answer_task",
            "task_id",
            unique=True,
            sqlite_where=text("task_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    unresolved_questions: Mapped[list[str]] = mapped_column(JSON, default=list)
    generator: Mapped[str] = mapped_column(String(100), default="legacy-unknown")
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    claims: Mapped[list[ClaimRecord]] = relationship(
        back_populates="answer_record", cascade="all, delete-orphan"
    )


class ClaimRecord(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    answer_id: Mapped[str] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(32), default="answer")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    review_status: Mapped[str] = mapped_column(String(32), default="unreviewed")

    answer_record: Mapped[AnswerRecord] = relationship(back_populates="claims")
    links: Mapped[list[ClaimEvidenceRecord]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )


class EvidenceSpanRecord(Base):
    __tablename__ = "evidence_spans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    page_index: Mapped[int] = mapped_column(Integer)
    page_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    bbox: Mapped[dict[str, float] | None] = mapped_column(JSON, nullable=True)
    coordinate_space: Mapped[str] = mapped_column(
        String(64), default="normalized-rotated-top-left-v1"
    )
    quote_hash: Mapped[str] = mapped_column(String(64))
    extraction_method: Mapped[str] = mapped_column(String(100))
    confidence: Mapped[float] = mapped_column(Float)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    links: Mapped[list[ClaimEvidenceRecord]] = relationship(
        back_populates="evidence", cascade="all, delete-orphan"
    )


class ClaimEvidenceRecord(Base):
    __tablename__ = "claim_evidence"

    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_spans.id", ondelete="CASCADE"), primary_key=True
    )
    relationship_kind: Mapped[str] = mapped_column(String(32), default="supporting")

    claim: Mapped[ClaimRecord] = relationship(back_populates="links")
    evidence: Mapped[EvidenceSpanRecord] = relationship(back_populates="links")


class TaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("plan_id", "step_key", name="uq_task_plan_step_key"),
        UniqueConstraint("plan_id", "order_index", name="uq_task_plan_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_plans.id", ondelete="CASCADE"), nullable=True, index=True
    )
    step_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    order_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    objective: Mapped[str] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected_outputs: Mapped[list[str]] = mapped_column(JSON, default=list)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, default=list)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="draft-plan")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=600)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowRecord(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "create_idempotency_key", name="uq_workflow_project_create_key"
        ),
        CheckConstraint(
            "status IN ('planning','waiting-plan-approval','running','reviewing',"
            "'completed','blocked','failed','cancelled')",
            name="ck_workflow_status",
        ),
        CheckConstraint(
            "generation_mode IN ('local-deterministic','remote-model-assisted')",
            name="ck_workflow_generation_mode",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    create_idempotency_key: Mapped[str] = mapped_column(String(200))
    create_payload_sha256: Mapped[str] = mapped_column(String(64))
    workflow_type: Mapped[str] = mapped_column(String(64), default="literature-synthesis")
    goal: Mapped[str] = mapped_column(Text)
    generation_mode: Mapped[str] = mapped_column(
        String(32), default="local-deterministic"
    )
    status: Mapped[str] = mapped_column(String(32), default="planning", index=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    blocking_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    blocking_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlanRecord(Base):
    __tablename__ = "workflow_plans"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_plan_version"),
        CheckConstraint(
            "status IN ('pending-approval','approved','rejected','superseded')",
            name="ck_workflow_plan_status",
        ),
        Index(
            "uq_workflow_one_approved_plan",
            "workflow_id",
            unique=True,
            sqlite_where=text("status = 'approved'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    spec_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending-approval", index=True)
    generator: Mapped[str] = mapped_column(String(100), default="template-v1")
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobRecord(Base):
    __tablename__ = "workflow_jobs"
    __table_args__ = (
        UniqueConstraint("operation_key", "attempt", name="uq_workflow_job_attempt"),
        CheckConstraint(
            "kind IN ('generate-plan','execute-task','review-workflow')",
            name="ck_workflow_job_kind",
        ),
        CheckConstraint(
            "status IN ('queued','leased','succeeded','failed','cancelled')",
            name="ck_workflow_job_status",
        ),
        Index(
            "uq_workflow_job_active_operation",
            "operation_key",
            unique=True,
            sqlite_where=text("status IN ('queued','leased')"),
        ),
        Index("ix_workflow_job_claim", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    operation_key: Mapped[str] = mapped_column(String(300), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input_sha256: Mapped[str] = mapped_column(String(64))
    handler_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_idempotency_key: Mapped[str | None] = mapped_column(
        String(200), nullable=True, unique=True
    )
    previous_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_jobs.id", ondelete="SET NULL"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewRecord(Base):
    __tablename__ = "workflow_reviews"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id", "review_type", "input_sha256", name="uq_workflow_review_input"
        ),
        CheckConstraint(
            "verdict IN ('passed','revision-required','blocked','failed')",
            name="ck_workflow_review_verdict",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_plans.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    review_type: Mapped[str] = mapped_column(String(100))
    input_sha256: Mapped[str] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(32))
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisIntentRecord(Base):
    __tablename__ = "analysis_intents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    dataset_source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), index=True
    )
    objective: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), default="waiting-approval", index=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    environment_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_artifacts: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_artifacts: Mapped[list[str]] = mapped_column(JSON, default=list)
    logs_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    parent_artifacts: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ApprovalRecord(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint(
            "subject_type",
            "subject_id",
            "requested_action",
            "intent_hash",
            name="uq_approval_subject_payload",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_plans.id", ondelete="CASCADE"), nullable=True, index=True
    )
    subject_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload_schema_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    intent_hash: Mapped[str] = mapped_column(String(64), index=True)
    requested_action: Mapped[str] = mapped_column(String(200))
    risk_level: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    affected_resources: Mapped[list[str]] = mapped_column(JSON, default=list)
    user_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventRecord(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("workflow_id", "sequence", name="uq_workflow_event_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
