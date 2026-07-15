from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...analysis import sha256_file
from ...models import (
    PlanRecord,
    ProjectRecord,
    SourcePageRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
)
from ..schemas import FrozenSourceDescriptor, InspectSourcesInput, PlanSpec
from ..service import content_sha256
from ..state import WorkflowBlockedError, WorkflowFailure
from .text import string_list


def source_page_manifest_hash(
    session: Session,
    source_id: str,
) -> tuple[str, int] | None:
    pages = list(
        session.scalars(
            select(SourcePageRecord)
            .where(SourcePageRecord.source_id == source_id)
            .order_by(SourcePageRecord.page_index)
        )
    )
    if not pages:
        return None
    manifest = [
        {
            "height": page.height,
            "pageIndex": page.page_index,
            "pageLabel": page.page_label,
            "text": page.text,
            "width": page.width,
            "words": page.words,
        }
        for page in pages
    ]
    return content_sha256(manifest), len(pages)


def source_file_matches(
    project_root: Path,
    source: SourceRecord,
    expected_content_hash: str,
) -> bool:
    raw_path = Path(source.local_path)
    if raw_path.is_symlink():
        return False
    try:
        path = raw_path.resolve(strict=True)
        path.relative_to(project_root)
        return (
            path.is_file()
            and source.content_hash == expected_content_hash
            and sha256_file(path) == expected_content_hash
        )
    except (OSError, ValueError):
        return False


def ready_source_descriptors(
    session: Session,
    workflow: WorkflowRecord,
) -> list[FrozenSourceDescriptor]:
    project = session.get(ProjectRecord, workflow.project_id)
    if project is None:
        raise WorkflowFailure("project-missing", "The workflow project is missing.")
    project_root = Path(project.project_path).resolve()
    sources = list(
        session.scalars(
            select(SourceRecord)
            .where(
                SourceRecord.project_id == workflow.project_id,
                SourceRecord.source_kind == "pdf",
                SourceRecord.ingestion_status == "ready",
            )
            .order_by(SourceRecord.created_at, SourceRecord.id)
        )
    )
    descriptors: list[FrozenSourceDescriptor] = []
    for source in sources:
        page_manifest = source_page_manifest_hash(session, source.id)
        if (
            page_manifest is None
            or source.page_count not in {None, page_manifest[1]}
            or not source_file_matches(project_root, source, source.content_hash)
        ):
            continue
        try:
            descriptor = FrozenSourceDescriptor(
                source_id=source.id,
                title=source.title,
                content_hash=source.content_hash,
                page_manifest_hash=page_manifest[0],
            )
        except ValidationError:
            continue
        descriptors.append(descriptor)
    return descriptors


def validate_source_descriptors(
    session: Session,
    workflow: WorkflowRecord,
    descriptors: list[FrozenSourceDescriptor],
) -> list[SourceRecord]:
    project = session.get(ProjectRecord, workflow.project_id)
    if project is None:
        raise WorkflowFailure("project-missing", "The workflow project is missing.")
    if not descriptors or len({item.source_id for item in descriptors}) != len(descriptors):
        raise WorkflowFailure(
            "source-reproducibility-failed",
            "The workflow has no valid immutable source descriptor set.",
        )
    project_root = Path(project.project_path).resolve()
    validated: list[SourceRecord] = []
    for descriptor in descriptors:
        source = session.get(SourceRecord, descriptor.source_id)
        page_manifest = (
            source_page_manifest_hash(session, descriptor.source_id)
            if source is not None
            else None
        )
        valid = bool(
            source is not None
            and source.project_id == workflow.project_id
            and source.source_kind == "pdf"
            and source.ingestion_status == "ready"
            and source.title == descriptor.title
            and source.content_hash == descriptor.content_hash
            and page_manifest is not None
            and page_manifest[0] == descriptor.page_manifest_hash
            and source.page_count in {None, page_manifest[1]}
            and source_file_matches(
                project_root,
                source,
                descriptor.content_hash,
            )
        )
        if not valid or source is None:
            raise WorkflowFailure(
                "source-reproducibility-failed",
                "A selected source no longer matches its approved file and parsed-page "
                "fingerprints.",
            )
        validated.append(source)
    return validated


def parse_source_descriptors(value: Any) -> list[FrozenSourceDescriptor]:
    if not isinstance(value, list):
        raise WorkflowFailure(
            "source-reproducibility-failed",
            "The source inspection step did not preserve immutable source descriptors.",
        )
    try:
        descriptors = [
            FrozenSourceDescriptor.model_validate(item)
            for item in cast(list[Any], value)
        ]
    except ValidationError:
        raise WorkflowFailure(
            "source-reproducibility-failed",
            "The source inspection descriptor set is invalid.",
        ) from None
    if not descriptors:
        raise WorkflowFailure(
            "source-reproducibility-failed",
            "The source inspection descriptor set is empty.",
        )
    return descriptors


def validated_source_descriptors_for_task(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
    *,
    inspect_task: TaskRecord | None = None,
    allow_legacy_upgrade: bool = False,
) -> list[FrozenSourceDescriptor]:
    if inspect_task is None:
        inspect_task = session.scalar(
            select(TaskRecord).where(
                TaskRecord.plan_id == task.plan_id,
                TaskRecord.order_index == 0,
            )
        )
    if inspect_task is None or inspect_task.status != "completed":
        raise WorkflowFailure(
            "source-reproducibility-failed",
            "The immutable source inspection result is unavailable.",
        )
    raw_descriptors = inspect_task.outputs.get("sourceDescriptors")
    if raw_descriptors is None and allow_legacy_upgrade:
        descriptors = upgrade_legacy_inspect_descriptors(
            session,
            workflow,
            inspect_task,
        )
    else:
        descriptors = parse_source_descriptors(raw_descriptors)
    if workflow.generation_mode == "remote-model-assisted":
        plan = session.get(PlanRecord, task.plan_id) if task.plan_id is not None else None
        if plan is None:
            raise WorkflowFailure(
                "remote-plan-approval-missing",
                "The approved remote-assisted plan could not be verified.",
            )
        try:
            spec = PlanSpec.model_validate(plan.spec_json)
            inspect_inputs = cast(InspectSourcesInput, spec.steps[0].inputs)
            plan_descriptors = inspect_inputs.frozen_sources
        except (AttributeError, ValidationError):
            raise WorkflowFailure(
                "remote-source-approval-missing",
                "The approved plan has no valid immutable source descriptor set.",
            ) from None
        if plan_descriptors is None or [
            item.model_dump(mode="json", by_alias=True) for item in descriptors
        ] != [
            item.model_dump(mode="json", by_alias=True) for item in plan_descriptors
        ]:
            raise WorkflowFailure(
                "remote-source-approval-mismatch",
                "The inspected source descriptors differ from the approved remote plan.",
            )
    validate_source_descriptors(session, workflow, descriptors)
    return descriptors


def upgrade_legacy_inspect_descriptors(
    session: Session,
    workflow: WorkflowRecord,
    inspect_task: TaskRecord,
) -> list[FrozenSourceDescriptor]:
    if workflow.generation_mode != "local-deterministic":
        raise WorkflowFailure(
            "legacy-source-provenance-unavailable",
            "Legacy source materialization may only be upgraded for a local workflow.",
        )
    source_ids = string_list(inspect_task.outputs.get("sourceIds"))
    raw_source_hashes: object = inspect_task.outputs.get("sourceContentHashes")
    if (
        not source_ids
        or not isinstance(raw_source_hashes, dict)
    ):
        raise WorkflowFailure(
            "legacy-source-provenance-unavailable",
            "The legacy inspection result has no complete source content-hash set.",
        )
    source_hashes = cast(dict[str, Any], raw_source_hashes)
    if set(source_hashes) != set(source_ids) or any(
        not isinstance(source_hashes[source_id], str) for source_id in source_ids
    ):
        raise WorkflowFailure(
            "legacy-source-provenance-unavailable",
            "The legacy inspection result has no complete source content-hash set.",
        )
    current = {
        descriptor.source_id: descriptor
        for descriptor in ready_source_descriptors(session, workflow)
    }
    if any(
        source_id not in current
        or current[source_id].content_hash != source_hashes[source_id]
        for source_id in source_ids
    ):
        raise WorkflowFailure(
            "legacy-source-provenance-unavailable",
            "A legacy inspected source no longer matches its recorded file hash.",
        )
    descriptors = [current[source_id] for source_id in source_ids]
    inspect_task.outputs = {
        **inspect_task.outputs,
        "sourceDescriptors": [
            descriptor.model_dump(mode="json", by_alias=True)
            for descriptor in descriptors
        ],
        "sourcePageManifestHashes": {
            descriptor.source_id: descriptor.page_manifest_hash
            for descriptor in descriptors
        },
    }
    return descriptors


def inspect_sources(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
) -> dict[str, Any]:
    payload = InspectSourcesInput.model_validate(task.inputs)
    if payload.frozen_sources is not None:
        descriptors = payload.frozen_sources
        validate_source_descriptors(session, workflow, descriptors)
    else:
        if workflow.generation_mode == "remote-model-assisted":
            raise WorkflowFailure(
                "remote-source-approval-missing",
                "The approved remote plan does not freeze source content fingerprints.",
            )
        descriptors = ready_source_descriptors(session, workflow)
        if payload.source_ids is not None:
            descriptor_by_id = {item.source_id: item for item in descriptors}
            descriptors = [
                descriptor_by_id[source_id]
                for source_id in payload.source_ids
                if source_id in descriptor_by_id
            ]
        if not descriptors:
            if payload.source_ids is not None:
                raise WorkflowBlockedError(
                    "no-approved-ready-pdf",
                    "None of the locally allowlisted PDF sources are still valid.",
                )
            raise WorkflowBlockedError(
                "no-ready-pdf",
                "Import and finish parsing at least one valid PDF before continuing.",
            )
    descriptor_payloads = [
        descriptor.model_dump(mode="json", by_alias=True)
        for descriptor in descriptors
    ]
    return {
        "sourceIds": [source.source_id for source in descriptors],
        "sourceContentHashes": {
            source.source_id: source.content_hash for source in descriptors
        },
        "sourcePageManifestHashes": {
            source.source_id: source.page_manifest_hash for source in descriptors
        },
        "sourceDescriptors": descriptor_payloads,
    }
