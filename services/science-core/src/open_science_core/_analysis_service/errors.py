from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ANALYSIS_ACTION = "execute-python-data-analysis"
ANALYSIS_RISK_LEVEL = "high"
ANALYSIS_V1_SCHEMA = "analysis-intent-v1"
ANALYSIS_V2_SCHEMA = "analysis-intent-v2"
ANALYSIS_V3_SCHEMA = "analysis-intent-v3"
ANALYSIS_APPROVAL_REASON = "Execute the displayed Python code against the selected CSV dataset"
WORKFLOW_ANALYSIS_APPROVAL_REASON = (
    "Execute only the displayed immutable analysis intent in the restricted runtime."
)


class AnalysisServiceError(RuntimeError):
    """A stable error that an API or workflow adapter can expose safely."""

    def __init__(self, status_code: int, detail: str, *, code: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


@dataclass(frozen=True, slots=True)
class SafeExecutionError:
    code: str
    user_message: str


def safe_execution_error(error: Exception) -> SafeExecutionError:
    message = str(error).lower()
    if "exceeded" in message or "timeout" in message:
        return SafeExecutionError(
            "runtime-timeout",
            "The restricted analysis runtime exceeded its execution limit.",
        )
    if "cancelled" in message:
        return SafeExecutionError(
            "analysis-execution-cancelled",
            "Analysis execution was cancelled before completion.",
        )
    if "transport" in message or "socket" in message or "connect" in message:
        return SafeExecutionError(
            "runtime-unavailable",
            "The restricted analysis runtime is unavailable.",
        )
    if isinstance(error, OSError):
        return SafeExecutionError(
            "analysis-filesystem-error",
            "Analysis input or output storage is unavailable.",
        )
    if "rejected execution" in message:
        return SafeExecutionError(
            "runtime-rejected",
            "The restricted runtime rejected the analysis request.",
        )
    if "invalid response" in message:
        return SafeExecutionError(
            "runtime-invalid-response",
            "The restricted runtime returned an invalid response.",
        )
    return SafeExecutionError(
        "analysis-integrity-error",
        "Analysis execution stopped because an integrity check failed.",
    )


def execution_http_error(error: Exception) -> AnalysisServiceError:
    safe = safe_execution_error(error)
    return AnalysisServiceError(502, safe.user_message, code=safe.code)


def safe_error_summary(error: SafeExecutionError) -> dict[str, Any]:
    category = "unknown"
    if error.code == "runtime-timeout":
        category = "timeout"
    elif error.code in {"runtime-unavailable", "runtime-rejected", "runtime-invalid-response"}:
        category = "runtime"
    elif error.code in {"analysis-filesystem-error", "analysis-integrity-error"}:
        category = "input-integrity"
    return {
        "schemaVersion": "1",
        "category": category,
        "code": error.code,
        "userMessage": error.user_message,
        "stderrExcerpt": None,
        "retryable": error.code in {"runtime-timeout", "runtime-unavailable"},
    }


def runtime_result_error_summary(*, superseded: bool) -> dict[str, Any]:
    if superseded:
        return {
            "schemaVersion": "1",
            "category": "runtime",
            "code": "workflow-execution-superseded",
            "userMessage": "Workflow changed or was cancelled before analysis completion.",
            "stderrExcerpt": None,
            "retryable": False,
        }
    return {
        "schemaVersion": "1",
        "category": "runtime",
        "code": "analysis-runtime-failed",
        "userMessage": "The approved analysis code failed in the restricted runtime.",
        "stderrExcerpt": None,
        "retryable": True,
    }


def recovery_error_summary() -> dict[str, Any]:
    return {
        "schemaVersion": "1",
        "category": "runtime",
        "code": "analysis-interrupted",
        "userMessage": "Analysis execution was interrupted before completion.",
        "stderrExcerpt": None,
        "retryable": True,
    }
