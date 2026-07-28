from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import ProjectRecord


def project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def active_project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = project_or_404(session, project_id)
    if project.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "project-archived",
                "userMessage": "Restore this project before starting a research task.",
                "retryable": False,
            },
        )
    return project
