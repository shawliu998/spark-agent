"""Exact approval, project-local installation, activation, and rollback."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    ProjectRecord,
    SkillActivationRecord,
    SkillCandidateRecord,
    WorkflowRecord,
    utc_now,
)
from ._service.integrity import content_sha256
from .skill_candidate_schemas import (
    ApproveSkillActivationIn,
    RollbackSkillActivationIn,
    SkillActivationPreviewOut,
)
from .skill_candidates import (
    PERMISSION,
    SKILL_NAME,
    assert_skill_candidate_integrity,
    remember_verified_evidence_capability,
)
from .state import WorkflowFailure

TARGET_RELATIVE_PATH = ".opencode/skills/remember-verified-evidence/SKILL.md"
_TARGET_PARTS = (".opencode", "skills", SKILL_NAME)
_MAX_SKILL_BYTES = 64 * 1024
_ACTIVE_STATUSES = ("installing", "active", "rollback-pending")


@dataclass(frozen=True, slots=True)
class _TargetState:
    directory_present: bool
    present: bool
    content: bytes | None
    sha256: str | None


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _begin_immediate(session: Session) -> None:
    if session.in_transaction():
        session.rollback()
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _project_root(project: ProjectRecord) -> Path:
    expected = settings.data_dir / "projects" / project.id
    if not expected.is_absolute() or project.project_path != str(expected) or not expected.exists():
        raise WorkflowFailure(
            "skill-project-path-invalid",
            "The Core-owned research project directory is unavailable.",
        )
    metadata = expected.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or expected.resolve() != expected
    ):
        raise WorkflowFailure(
            "skill-project-path-invalid",
            "The Core-owned research project directory is invalid.",
        )
    return expected


def _safe_directory(path: Path, *, allow_missing: bool) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return False
        raise WorkflowFailure(
            "skill-target-invalid",
            "The project-local Skill directory is unavailable.",
        ) from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise WorkflowFailure(
            "skill-target-invalid",
            "The project-local Skill path is not a regular directory.",
        )
    return True


def _target_directory(project_root: Path) -> tuple[Path, bool]:
    current = project_root
    for part in _TARGET_PARTS[:-1]:
        current = current / part
        if not _safe_directory(current, allow_missing=True):
            return project_root.joinpath(*_TARGET_PARTS), False
    target_dir = current / _TARGET_PARTS[-1]
    return target_dir, _safe_directory(target_dir, allow_missing=True)


def _inspect_target(project_root: Path) -> _TargetState:
    target_dir, directory_present = _target_directory(project_root)
    if not directory_present:
        return _TargetState(False, False, None, None)
    directory = os.open(
        target_dir,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if any(name != "SKILL.md" for name in os.listdir(directory)):
            raise WorkflowFailure(
                "skill-target-contains-unknown-files",
                "The project-local Skill directory contains unmanaged files.",
            )
        try:
            metadata = os.stat(
                "SKILL.md",
                dir_fd=directory,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > _MAX_SKILL_BYTES
            ):
                raise WorkflowFailure(
                    "skill-target-invalid",
                    "The project-local Skill file is not a safe regular file.",
                )
            descriptor = os.open(
                "SKILL.md",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory,
            )
        except FileNotFoundError:
            return _TargetState(True, False, None, None)
        except OSError:
            raise WorkflowFailure(
                "skill-target-invalid",
                "The project-local Skill file is not a safe regular file.",
            ) from None
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > _MAX_SKILL_BYTES
            ):
                raise WorkflowFailure(
                    "skill-target-invalid",
                    "The project-local Skill file is not a safe regular file.",
                )
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                content = source.read(_MAX_SKILL_BYTES + 1)
            if len(content) != metadata.st_size or len(content) > _MAX_SKILL_BYTES:
                raise WorkflowFailure(
                    "skill-target-drift",
                    "The project-local Skill file changed while it was inspected.",
                    retryable=True,
                )
            return _TargetState(True, True, content, _sha256_bytes(content))
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _candidate_hashes(
    session: Session,
    workflow: WorkflowRecord,
    candidate: SkillCandidateRecord,
) -> tuple[str, str]:
    assert_skill_candidate_integrity(session, workflow, candidate)
    raw_results = candidate.evaluation_json.get("results")
    results = cast(list[object], raw_results) if isinstance(raw_results, list) else None
    if (
        candidate.status != "awaiting-approval"
        or candidate.evaluation_json.get("passed") is not True
        or results is None
        or len(results) != 6
        or any(
            not isinstance(result, dict)
            or cast(dict[str, object], result).get("passed") is not True
            for result in results
        )
    ):
        raise WorkflowFailure(
            "skill-candidate-not-approvable",
            "The Skill candidate has not passed its complete replay evaluation.",
        )
    template = candidate.generated_skill_md.encode("utf-8")
    if len(template) > _MAX_SKILL_BYTES:
        raise WorkflowFailure(
            "skill-template-too-large",
            "The generated Skill template exceeds the installation limit.",
        )
    return _sha256_bytes(template), content_sha256(candidate.evaluation_json)


def _approval_material(
    *,
    project_id: str,
    workflow_id: str,
    candidate_id: str,
    candidate_content_hash: str,
    template_sha256: str,
    evaluation_sha256: str,
    prior_present: bool,
    prior_sha256: str | None,
    target_directory_present: bool,
) -> dict[str, object]:
    return {
        "action": "approve-project-skill-activation-v1",
        "schemaVersion": "1",
        "projectId": project_id,
        "workflowId": workflow_id,
        "candidateId": candidate_id,
        "candidateContentHash": candidate_content_hash,
        "templateSha256": template_sha256,
        "evaluationSha256": evaluation_sha256,
        "targetRelativePath": TARGET_RELATIVE_PATH,
        "priorPresent": prior_present,
        "priorSha256": prior_sha256,
        "targetDirectoryPresent": target_directory_present,
    }


def _approval_hash_for_record(activation: SkillActivationRecord) -> str:
    return content_sha256(
        _approval_material(
            project_id=activation.project_id,
            workflow_id=activation.workflow_id,
            candidate_id=activation.candidate_id,
            candidate_content_hash=activation.candidate_content_hash,
            template_sha256=activation.template_sha256,
            evaluation_sha256=activation.evaluation_sha256,
            prior_present=activation.prior_present,
            prior_sha256=activation.prior_sha256,
            target_directory_present=not activation.created_directory,
        )
    )


def _assert_activation_ledger(
    activation: SkillActivationRecord,
    candidate: SkillCandidateRecord,
    *,
    template_sha256: str,
    evaluation_sha256: str,
) -> None:
    if (
        activation.skill_name != SKILL_NAME
        or activation.target_relative_path != TARGET_RELATIVE_PATH
        or activation.candidate_content_hash != candidate.content_hash
        or activation.template_sha256 != template_sha256
        or activation.installed_sha256 != template_sha256
        or activation.evaluation_sha256 != evaluation_sha256
        or activation.approval_sha256 != _approval_hash_for_record(activation)
        or (
            activation.prior_present
            and (
                activation.prior_bytes is None
                or activation.prior_sha256 != _sha256_bytes(activation.prior_bytes)
            )
        )
        or (
            not activation.prior_present
            and (activation.prior_bytes is not None or activation.prior_sha256 is not None)
        )
    ):
        raise WorkflowFailure(
            "skill-activation-integrity-invalid",
            "The Skill activation ledger no longer matches its exact approval.",
        )


def activation_preview(
    session: Session,
    project: ProjectRecord,
    workflow: WorkflowRecord,
    candidate: SkillCandidateRecord,
) -> SkillActivationPreviewOut:
    template_sha256, evaluation_sha256 = _candidate_hashes(session, workflow, candidate)
    target = _inspect_target(_project_root(project))
    approval_sha256 = content_sha256(
        _approval_material(
            project_id=project.id,
            workflow_id=workflow.id,
            candidate_id=candidate.id,
            candidate_content_hash=candidate.content_hash,
            template_sha256=template_sha256,
            evaluation_sha256=evaluation_sha256,
            prior_present=target.present,
            prior_sha256=target.sha256,
            target_directory_present=target.directory_present,
        )
    )
    latest = session.scalar(
        select(SkillActivationRecord)
        .where(
            SkillActivationRecord.project_id == project.id,
            SkillActivationRecord.candidate_id == candidate.id,
        )
        .order_by(SkillActivationRecord.created_at.desc(), SkillActivationRecord.id)
        .limit(1)
    )
    return SkillActivationPreviewOut.model_validate(
        {
            "schemaVersion": "1",
            "projectId": project.id,
            "workflowId": workflow.id,
            "candidateId": candidate.id,
            "expectedStatus": "awaiting-approval",
            "targetRelativePath": TARGET_RELATIVE_PATH,
            "candidateContentHash": candidate.content_hash,
            "templateSha256": template_sha256,
            "evaluationSha256": evaluation_sha256,
            "approvalSha256": approval_sha256,
            "priorPresent": target.present,
            "priorSha256": target.sha256,
            "targetDirectoryPresent": target.directory_present,
            "latestActivation": latest,
        },
        strict=True,
    )


def _assert_approval_echo(
    preview: SkillActivationPreviewOut,
    payload: ApproveSkillActivationIn,
) -> None:
    if (
        payload.expected_status != preview.expected_status
        or payload.expected_candidate_content_hash != preview.candidate_content_hash
        or payload.expected_template_sha256 != preview.template_sha256
        or payload.expected_evaluation_sha256 != preview.evaluation_sha256
        or payload.expected_approval_sha256 != preview.approval_sha256
        or payload.expected_prior_present != preview.prior_present
        or payload.expected_prior_sha256 != preview.prior_sha256
        or payload.expected_target_directory_present != preview.target_directory_present
    ):
        raise WorkflowFailure(
            "skill-approval-stale",
            "The Skill approval preview changed before activation.",
            retryable=True,
        )


def _ensure_target_directory(project_root: Path) -> Path:
    current = project_root
    for part in _TARGET_PARTS:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            _safe_directory(current, allow_missing=False)
    return current


def _atomic_write(target: Path, content: bytes) -> None:
    temporary = f".SKILL.md.{uuid.uuid4().hex}.tmp"
    directory = os.open(
        target.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(
            temporary,
            "SKILL.md",
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def _matches_prior(
    target: _TargetState,
    activation: SkillActivationRecord,
) -> bool:
    return (
        target.present == activation.prior_present
        and target.sha256 == activation.prior_sha256
        and (not target.present or target.content == activation.prior_bytes)
    )


def _mark_status(
    session: Session,
    activation_id: str,
    *,
    expected: str,
    status: str,
) -> SkillActivationRecord:
    _begin_immediate(session)
    activation = session.get_one(SkillActivationRecord, activation_id)
    if activation.status == status:
        session.commit()
        return activation
    if activation.status != expected:
        session.rollback()
        raise WorkflowFailure(
            "skill-activation-state-conflict",
            "The Skill activation state changed.",
        )
    activation.status = status
    activation.updated_at = utc_now()
    if status == "active":
        activation.activated_at = utc_now()
    if status == "rolled-back":
        activation.rolled_back_at = utc_now()
    session.commit()
    session.refresh(activation)
    return activation


def _resume_install(
    session: Session,
    project: ProjectRecord,
    activation: SkillActivationRecord,
    template: bytes,
) -> SkillActivationRecord:
    state = _inspect_target(_project_root(project))
    if state.present and state.sha256 == activation.installed_sha256 and state.content == template:
        return _mark_status(
            session,
            activation.id,
            expected="installing",
            status="active",
        )
    if not _matches_prior(state, activation):
        if activation.status == "installing":
            _mark_status(
                session,
                activation.id,
                expected="installing",
                status="blocked",
            )
        raise WorkflowFailure(
            "skill-install-disk-drift",
            "The project-local Skill target changed during installation.",
        )
    target_dir = _ensure_target_directory(_project_root(project))
    _atomic_write(target_dir / "SKILL.md", template)
    installed = _inspect_target(_project_root(project))
    if installed.sha256 != activation.installed_sha256 or installed.content != template:
        raise WorkflowFailure(
            "skill-install-verification-failed",
            "The installed project-local Skill could not be verified.",
        )
    return _mark_status(
        session,
        activation.id,
        expected="installing",
        status="active",
    )


def approve_and_activate(
    session: Session,
    project: ProjectRecord,
    workflow: WorkflowRecord,
    candidate: SkillCandidateRecord,
    payload: ApproveSkillActivationIn,
    *,
    idempotency_key: str,
) -> SkillActivationRecord:
    recover_project_activations(session, project)
    request_sha256 = content_sha256(payload.model_dump(mode="json", by_alias=True))
    _begin_immediate(session)
    existing = session.scalar(
        select(SkillActivationRecord).where(
            SkillActivationRecord.project_id == project.id,
            SkillActivationRecord.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if (
            existing.request_sha256 != request_sha256
            or existing.candidate_id != candidate.id
            or existing.workflow_id != workflow.id
        ):
            session.rollback()
            raise WorkflowFailure(
                "skill-activation-idempotency-conflict",
                "The Skill activation idempotency key was reused with different input.",
            )
        session.commit()
        if existing.status == "active":
            assert_skill_activation_integrity(session, project, workflow, candidate, existing)
            return existing
        if existing.status != "installing":
            raise WorkflowFailure(
                "skill-activation-state-conflict",
                "The prior Skill activation request cannot be resumed.",
            )
        return _resume_install(
            session,
            project,
            existing,
            candidate.generated_skill_md.encode("utf-8"),
        )
    preview = activation_preview(session, project, workflow, candidate)
    _assert_approval_echo(preview, payload)
    active_like = session.scalar(
        select(SkillActivationRecord.id).where(
            SkillActivationRecord.project_id == project.id,
            SkillActivationRecord.target_relative_path == TARGET_RELATIVE_PATH,
            SkillActivationRecord.status.in_(_ACTIVE_STATUSES),
        )
    )
    if active_like is not None:
        session.rollback()
        raise WorkflowFailure(
            "skill-activation-already-active",
            "This project already has an active-like installation for the Skill.",
        )
    state = _inspect_target(_project_root(project))
    activation = SkillActivationRecord(
        id=str(uuid.uuid4()),
        project_id=project.id,
        workflow_id=workflow.id,
        candidate_id=candidate.id,
        schema_version="1",
        skill_name=SKILL_NAME,
        target_relative_path=TARGET_RELATIVE_PATH,
        candidate_content_hash=candidate.content_hash,
        template_sha256=preview.template_sha256,
        evaluation_sha256=preview.evaluation_sha256,
        approval_sha256=preview.approval_sha256,
        request_sha256=request_sha256,
        idempotency_key=idempotency_key,
        prior_present=state.present,
        prior_bytes=state.content,
        prior_sha256=state.sha256,
        installed_sha256=preview.template_sha256,
        created_directory=not state.directory_present,
        status="installing",
    )
    session.add(activation)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise WorkflowFailure(
            "skill-activation-concurrent-conflict",
            "A concurrent Skill activation already owns this project target.",
        ) from error
    session.refresh(activation)
    return _resume_install(
        session,
        project,
        activation,
        candidate.generated_skill_md.encode("utf-8"),
    )


def assert_skill_activation_integrity(
    session: Session,
    project: ProjectRecord,
    workflow: WorkflowRecord,
    candidate: SkillCandidateRecord,
    activation: SkillActivationRecord,
) -> None:
    template_sha256, evaluation_sha256 = _candidate_hashes(session, workflow, candidate)
    _assert_activation_ledger(
        activation,
        candidate,
        template_sha256=template_sha256,
        evaluation_sha256=evaluation_sha256,
    )
    state = _inspect_target(_project_root(project))
    if (
        activation.project_id != project.id
        or activation.workflow_id != workflow.id
        or activation.candidate_id != candidate.id
        or activation.schema_version != "1"
        or activation.status != "active"
        or not state.present
        or state.sha256 != activation.installed_sha256
        or state.content != candidate.generated_skill_md.encode("utf-8")
    ):
        raise WorkflowFailure(
            "skill-activation-integrity-invalid",
            "The active project-local Skill no longer matches its exact approval.",
        )


def rollback_activation(
    session: Session,
    project: ProjectRecord,
    workflow: WorkflowRecord,
    candidate: SkillCandidateRecord,
    activation: SkillActivationRecord,
    payload: RollbackSkillActivationIn,
    *,
    idempotency_key: str,
) -> SkillActivationRecord:
    recover_project_activations(session, project)
    request_sha256 = content_sha256(payload.model_dump(mode="json", by_alias=True))
    _begin_immediate(session)
    activation = session.get_one(SkillActivationRecord, activation.id)
    if activation.rollback_idempotency_key is not None:
        if (
            activation.rollback_idempotency_key != idempotency_key
            or activation.rollback_request_sha256 != request_sha256
        ):
            session.rollback()
            raise WorkflowFailure(
                "skill-rollback-idempotency-conflict",
                "The rollback idempotency key conflicts with the stored request.",
            )
        session.commit()
        if activation.status == "rolled-back":
            return activation
        if activation.status != "rollback-pending":
            raise WorkflowFailure(
                "skill-rollback-state-conflict",
                "The stored rollback cannot be resumed.",
            )
        return _resume_rollback(session, project, activation.id)
    assert_skill_activation_integrity(session, project, workflow, candidate, activation)
    current = _inspect_target(_project_root(project))
    if (
        payload.expected_status != "active"
        or payload.expected_activation_id != activation.id
        or payload.expected_approval_sha256 != activation.approval_sha256
        or payload.expected_installed_sha256 != activation.installed_sha256
        or payload.expected_current_target_sha256 != current.sha256
    ):
        session.rollback()
        raise WorkflowFailure(
            "skill-rollback-stale",
            "The Skill rollback request no longer matches the active installation.",
            retryable=True,
        )
    activation.rollback_idempotency_key = idempotency_key
    activation.rollback_request_sha256 = request_sha256
    activation.status = "rollback-pending"
    activation.updated_at = utc_now()
    session.commit()
    session.refresh(activation)
    return _resume_rollback(session, project, activation.id)


def _resume_rollback(
    session: Session,
    project: ProjectRecord,
    activation_id: str,
) -> SkillActivationRecord:
    project_id = project.id
    _begin_immediate(session)
    stored_project = session.get(ProjectRecord, project_id)
    activation = session.get(SkillActivationRecord, activation_id)
    if activation is None:
        session.rollback()
        raise WorkflowFailure(
            "skill-rollback-recovery-invalid",
            "The pending Skill rollback scope is no longer valid.",
        )
    workflow = session.scalar(
        select(WorkflowRecord).where(
            WorkflowRecord.id == activation.workflow_id,
            WorkflowRecord.project_id == project_id,
        )
    )
    candidate = session.scalar(
        select(SkillCandidateRecord).where(
            SkillCandidateRecord.id == activation.candidate_id,
            SkillCandidateRecord.project_id == project_id,
            SkillCandidateRecord.workflow_id == activation.workflow_id,
        )
    )
    try:
        if (
            stored_project is None
            or activation.project_id != project_id
            or workflow is None
            or candidate is None
            or activation.status != "rollback-pending"
        ):
            raise WorkflowFailure(
                "skill-rollback-recovery-invalid",
                "The pending Skill rollback scope is no longer valid.",
            )
        template_sha256, evaluation_sha256 = _candidate_hashes(
            session,
            workflow,
            candidate,
        )
        _assert_activation_ledger(
            activation,
            candidate,
            template_sha256=template_sha256,
            evaluation_sha256=evaluation_sha256,
        )
        root = _project_root(stored_project)
        rollback_material = {
            "expectedStatus": "active",
            "expectedActivationId": activation.id,
            "expectedApprovalSha256": activation.approval_sha256,
            "expectedInstalledSha256": activation.installed_sha256,
            "expectedCurrentTargetSha256": activation.installed_sha256,
        }
        if (
            not activation.rollback_idempotency_key
            or activation.rollback_request_sha256 is None
            or activation.rollback_request_sha256 != content_sha256(rollback_material)
        ):
            raise WorkflowFailure(
                "skill-rollback-recovery-invalid",
                "The pending Skill rollback request is incomplete or invalid.",
            )
    except WorkflowFailure:
        activation.status = "blocked"
        activation.updated_at = utc_now()
        session.commit()
        raise

    template = candidate.generated_skill_md.encode("utf-8")
    state = _inspect_target(root)
    if _matches_prior(state, activation):
        activation.status = "rolled-back"
        activation.rolled_back_at = utc_now()
        activation.updated_at = utc_now()
        session.commit()
        session.refresh(activation)
        return activation
    if (
        not state.present
        or state.sha256 != activation.installed_sha256
        or state.content != template
    ):
        activation.status = "blocked"
        activation.updated_at = utc_now()
        session.commit()
        raise WorkflowFailure(
            "skill-rollback-disk-drift",
            "The project-local Skill changed before rollback completed.",
        )
    target_dir, directory_present = _target_directory(root)
    if not directory_present:
        raise WorkflowFailure(
            "skill-rollback-disk-drift",
            "The project-local Skill directory disappeared during rollback.",
        )
    target = target_dir / "SKILL.md"
    if activation.prior_present:
        assert activation.prior_bytes is not None
        _atomic_write(target, activation.prior_bytes)
    else:
        directory = os.open(
            target_dir,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.unlink("SKILL.md", dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)
        if activation.created_directory:
            try:
                target_dir.rmdir()
            except OSError:
                pass
    restored = _inspect_target(root)
    if not _matches_prior(restored, activation):
        session.rollback()
        raise WorkflowFailure(
            "skill-rollback-verification-failed",
            "The prior project-local Skill state could not be restored.",
        )
    activation.status = "rolled-back"
    activation.rolled_back_at = utc_now()
    activation.updated_at = utc_now()
    session.commit()
    session.refresh(activation)
    return activation


def list_activations(
    session: Session,
    *,
    project_id: str,
    workflow_id: str | None = None,
) -> list[SkillActivationRecord]:
    statement = select(SkillActivationRecord).where(SkillActivationRecord.project_id == project_id)
    if workflow_id is not None:
        statement = statement.where(SkillActivationRecord.workflow_id == workflow_id)
    return list(
        session.scalars(
            statement.order_by(
                SkillActivationRecord.created_at.desc(),
                SkillActivationRecord.id,
            )
        )
    )


def recover_project_activations(
    session: Session,
    project: ProjectRecord,
) -> None:
    """Deterministically finish any committed filesystem intent for a project."""

    _begin_immediate(session)
    pending = list(
        session.scalars(
            select(SkillActivationRecord).where(
                SkillActivationRecord.project_id == project.id,
                SkillActivationRecord.status.in_(("installing", "rollback-pending")),
            )
        )
    )
    session.commit()
    for activation in pending:
        if activation.status == "rollback-pending":
            _resume_rollback(session, project, activation.id)
            continue
        workflow = session.scalar(
            select(WorkflowRecord).where(
                WorkflowRecord.id == activation.workflow_id,
                WorkflowRecord.project_id == project.id,
            )
        )
        candidate = session.scalar(
            select(SkillCandidateRecord).where(
                SkillCandidateRecord.id == activation.candidate_id,
                SkillCandidateRecord.project_id == project.id,
                SkillCandidateRecord.workflow_id == activation.workflow_id,
            )
        )
        if workflow is None or candidate is None:
            _mark_status(
                session,
                activation.id,
                expected="installing",
                status="blocked",
            )
            raise WorkflowFailure(
                "skill-install-recovery-invalid",
                "The pending Skill installation scope is no longer valid.",
            )
        try:
            template_sha256, evaluation_sha256 = _candidate_hashes(session, workflow, candidate)
            _assert_activation_ledger(
                activation,
                candidate,
                template_sha256=template_sha256,
                evaluation_sha256=evaluation_sha256,
            )
        except WorkflowFailure:
            session.rollback()
            _mark_status(
                session,
                activation.id,
                expected="installing",
                status="blocked",
            )
            raise
        session.rollback()
        _resume_install(
            session,
            project,
            activation,
            candidate.generated_skill_md.encode("utf-8"),
        )


def invoke_active_capability(
    session: Session,
    project: ProjectRecord,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    activations = list(
        session.scalars(
            select(SkillActivationRecord).where(
                SkillActivationRecord.project_id == project.id,
                SkillActivationRecord.status == "active",
                SkillActivationRecord.target_relative_path == TARGET_RELATIVE_PATH,
            )
        )
    )
    if len(activations) != 1:
        raise WorkflowFailure(
            "skill-capability-not-active",
            "This project has no active verified-evidence skill capability.",
        )
    activation = activations[0]
    workflow = session.scalar(
        select(WorkflowRecord).where(
            WorkflowRecord.id == activation.workflow_id,
            WorkflowRecord.project_id == project.id,
        )
    )
    candidate = session.scalar(
        select(SkillCandidateRecord).where(
            SkillCandidateRecord.id == activation.candidate_id,
            SkillCandidateRecord.project_id == project.id,
            SkillCandidateRecord.workflow_id == activation.workflow_id,
        )
    )
    if workflow is None or candidate is None:
        raise WorkflowFailure(
            "skill-capability-not-active",
            "The active Skill capability scope is invalid.",
        )
    assert_skill_activation_integrity(session, project, workflow, candidate, activation)
    return remember_verified_evidence_capability(
        session,
        execution_project_id=project.id,
        execution_workflow_id=workflow.id,
        arguments=arguments,
        granted_permissions=frozenset({PERMISSION}),
    )


__all__ = (
    "TARGET_RELATIVE_PATH",
    "activation_preview",
    "approve_and_activate",
    "assert_skill_activation_integrity",
    "invoke_active_capability",
    "list_activations",
    "recover_project_activations",
    "rollback_activation",
)
