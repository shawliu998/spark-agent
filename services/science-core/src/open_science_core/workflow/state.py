from __future__ import annotations

from dataclasses import dataclass

WORKFLOW_TRANSITIONS: dict[str, frozenset[str]] = {
    "routing": frozenset(
        {"waiting-clarification", "planning", "unsupported", "failed", "cancelled"}
    ),
    "waiting-clarification": frozenset({"routing", "unsupported", "cancelled"}),
    "planning": frozenset(
        {"routing", "waiting-plan-approval", "blocked", "failed", "cancelled"}
    ),
    "waiting-plan-approval": frozenset({"routing", "running", "blocked", "cancelled"}),
    "running": frozenset({"reviewing", "blocked", "failed", "cancelled"}),
    "reviewing": frozenset({"completed", "blocked", "failed", "cancelled"}),
    "blocked": frozenset({"planning", "running", "cancelled"}),
    "failed": frozenset({"routing", "planning", "running", "reviewing", "cancelled"}),
    "completed": frozenset(),
    "unsupported": frozenset(),
    "cancelled": frozenset(),
}

TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"queued", "waiting-approval", "cancelled"}),
    "queued": frozenset({"running", "failed", "blocked", "cancelled"}),
    "running": frozenset({"completed", "failed", "blocked", "cancelled"}),
    "waiting-approval": frozenset({"queued", "blocked", "cancelled"}),
    "failed": frozenset({"queued", "waiting-approval", "blocked", "cancelled"}),
    "blocked": frozenset({"queued", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


def workflow_transition_allowed(current: str, target: str) -> bool:
    return target in WORKFLOW_TRANSITIONS.get(current, frozenset())


def task_transition_allowed(current: str, target: str) -> bool:
    return target in TASK_TRANSITIONS.get(current, frozenset())


@dataclass(frozen=True, slots=True)
class WorkflowBlockedError(RuntimeError):
    code: str
    user_message: str

    def __str__(self) -> str:
        return self.user_message


@dataclass(frozen=True, slots=True)
class WorkflowFailure(RuntimeError):
    code: str
    user_message: str
    retryable: bool = False
    outcome_unknown: bool = False

    def __str__(self) -> str:
        return self.user_message
