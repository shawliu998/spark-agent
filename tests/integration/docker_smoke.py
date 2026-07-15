#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from fixtures import DATASET_CSV, build_pdf


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_REVIEW_TERMINALS = {"completed", "blocked"}


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


# Never allow host proxy settings or redirects to receive a loopback Bearer
# credential or route local smoke traffic away from the isolated Compose project.
DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    NoRedirectHandler(),
)


class SmokeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def canonical_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


class ApiClient:
    def __init__(self, base_url: str, token: str) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        require(parsed.scheme == "http", "Smoke API URL must use HTTP on loopback")
        require(
            parsed.hostname == "127.0.0.1" and parsed.port is not None,
            "Smoke API URL must be a dynamically published IPv4 loopback port",
        )
        require(bool(token), "Smoke Bearer token is missing")
        self.base_url = base_url.rstrip("/")
        self._authorization = f"Bearer {token}"

    def json_request(
        self,
        method: str,
        path: str,
        *,
        payload: Any | None = None,
        headers: dict[str, str] | None = None,
        expected_status: tuple[int, ...] = (200,),
        timeout: float = 30.0,
    ) -> Any:
        body = None
        request_headers = {
            "Accept": "application/json",
            "Authorization": self._authorization,
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        status, response_body = self._request(
            method,
            path,
            body=body,
            headers=request_headers,
            timeout=timeout,
        )
        require(
            status in expected_status,
            f"{method} {path} returned HTTP {status}: {_safe_body(response_body)}",
        )
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as error:
            raise SmokeFailure(f"{method} {path} did not return valid JSON") from error

    def upload(
        self,
        path: str,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        expected_status: tuple[int, ...] = (200,),
    ) -> Any:
        boundary = f"spark-agent-smoke-{uuid.uuid4().hex}"
        body = b"".join(
            (
                f"--{boundary}\r\n".encode("ascii"),
                (
                    "Content-Disposition: form-data; name=\"file\"; "
                    f"filename=\"{filename}\"\r\n"
                ).encode("ascii"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                content,
                f"\r\n--{boundary}--\r\n".encode("ascii"),
            )
        )
        status, response_body = self._request(
            "POST",
            path,
            body=body,
            headers={
                "Accept": "application/json",
                "Authorization": self._authorization,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            timeout=60.0,
        )
        require(
            status in expected_status,
            f"POST {path} returned HTTP {status}: {_safe_body(response_body)}",
        )
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as error:
            raise SmokeFailure(f"POST {path} did not return valid JSON") from error

    def bytes_request(self, path: str, *, timeout: float = 30.0) -> bytes:
        status, response_body = self._request(
            "GET",
            path,
            headers={"Authorization": self._authorization},
            timeout=timeout,
        )
        require(status == 200, f"GET {path} returned HTTP {status}")
        return response_body

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float,
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            with DIRECT_OPENER.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            raise SmokeFailure(f"{method} {path} could not reach science-core") from error


def _safe_body(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")[:1_000]


def wait_ready(base_url: str, timeout_seconds: float) -> None:
    parsed = urllib.parse.urlsplit(base_url)
    require(
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.port is not None,
        "Health URL must be a dynamically published IPv4 loopback port",
    )
    deadline = time.monotonic() + timeout_seconds
    last_health: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            with DIRECT_OPENER.open(
                f"{base_url.rstrip('/')}/health",
                timeout=2,
            ) as response:
                decoded_health = json.load(response)
            last_health = decoded_health if isinstance(decoded_health, dict) else {}
            if (
                last_health.get("status") == "ok"
                and last_health.get("database") == "ok"
                and last_health.get("runtime") == "ready"
                and last_health.get("modelGateway") == "unconfigured"
            ):
                print("science-core and science-runtime are ready (model gateway unconfigured).")
                return
        except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        time.sleep(1.0)
    raise SmokeFailure(
        "Science services did not become ready with an unconfigured model gateway; "
        f"last health keys: {sorted(last_health) if last_health else []}"
    )


def assert_bearer_required(base_url: str) -> None:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/v1/projects", method="GET")
    try:
        with DIRECT_OPENER.open(request, timeout=5) as response:
            status = response.status
            response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        error.read()
    except (TimeoutError, urllib.error.URLError, OSError) as error:
        raise SmokeFailure("Unauthenticated request could not reach science-core") from error
    require(status == 401, f"Unauthenticated project request returned HTTP {status}, not 401")


def wait_for_workflow(
    client: ApiClient,
    workflow_id: str,
    target_statuses: set[str],
    *,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = "unknown"
    encoded_id = urllib.parse.quote(workflow_id, safe="")
    while time.monotonic() < deadline:
        snapshot = client.json_request("GET", f"/v1/workflows/{encoded_id}")
        last_status = snapshot.get("workflow", {}).get("status", "unknown")
        if last_status in target_statuses:
            return snapshot
        if last_status in {"failed", "cancelled"}:
            raise SmokeFailure(
                f"Workflow reached unexpected terminal state {last_status}"
            )
        if last_status == "blocked" and "blocked" not in target_statuses:
            reason = snapshot.get("workflow", {}).get("blockingReason")
            raise SmokeFailure(f"Workflow blocked before the expected state: {reason}")
        time.sleep(0.5)
    raise SmokeFailure(
        f"Workflow did not reach {sorted(target_statuses)}; last state was {last_status}"
    )


def wait_for_workflow_condition(
    client: ApiClient,
    workflow_id: str,
    description: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    encoded_id = urllib.parse.quote(workflow_id, safe="")
    last_snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_snapshot = client.json_request("GET", f"/v1/workflows/{encoded_id}")
        if predicate(last_snapshot):
            return last_snapshot
        workflow = last_snapshot.get("workflow", {})
        status = workflow.get("status", "unknown")
        if status in {"failed", "cancelled", "blocked"}:
            reason = workflow.get("blockingReason")
            raise SmokeFailure(
                f"Workflow reached {status} before {description}: {reason}"
            )
        time.sleep(0.5)
    last_workflow = last_snapshot.get("workflow", {})
    raise SmokeFailure(
        f"Workflow did not reach {description}; last state was "
        f"{last_workflow.get('status', 'unknown')} at revision "
        f"{last_workflow.get('revision', 'unknown')}"
    )


def workflow_events(client: ApiClient, workflow_id: str) -> dict[str, Any]:
    encoded_id = urllib.parse.quote(workflow_id, safe="")
    response = client.json_request(
        "GET",
        f"/v1/workflows/{encoded_id}/events?after=0&limit=500",
    )
    events = response.get("events")
    require(isinstance(events, list) and events, "Workflow event stream is empty")
    sequences = [event.get("sequence") for event in events]
    require(
        sequences == list(range(1, len(events) + 1)),
        "Workflow event sequence is not contiguous from one",
    )
    require(response.get("hasMore") is False, "Workflow smoke event page unexpectedly truncated")
    require(response.get("nextAfter") == sequences[-1], "Workflow event cursor is inconsistent")
    return response


def validate_review_terminal(snapshot: dict[str, Any]) -> None:
    workflow = snapshot.get("workflow", {})
    status = workflow.get("status")
    review = snapshot.get("latestReview")
    require(isinstance(review, dict), "Workflow terminal state has no deterministic review")
    if status == "completed":
        require(review.get("verdict") == "passed", "Completed workflow review did not pass")
        require(snapshot.get("result") is not None, "Completed workflow has no frozen result")
        return
    require(status == "blocked", f"Unexpected reviewer terminal state {status}")
    require(
        review.get("verdict") == "revision-required",
        "Blocked reviewer terminal does not require a revision",
    )
    blocking_reason = workflow.get("blockingReason") or {}
    require(
        blocking_reason.get("code") == "review-required",
        "Blocked reviewer terminal has an unexpected reason",
    )


def validate_and_download_artifacts(
    client: ApiClient,
    run: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    artifacts = run.get("artifacts")
    require(isinstance(artifacts, list) and artifacts, "Analysis run produced no artifacts")
    output_paths = run.get("outputArtifacts")
    require(
        isinstance(output_paths, list)
        and set(output_paths) == {artifact.get("path") for artifact in artifacts},
        "Run output artifact paths do not match persisted artifact records",
    )
    verified: dict[str, dict[str, Any]] = {}
    environment_artifact_hash: str | None = None
    downloaded: dict[str, bytes] = {}
    names: set[str] = set()
    for artifact in artifacts:
        artifact_id = artifact.get("id")
        content_hash = artifact.get("contentHash")
        size_bytes = artifact.get("sizeBytes")
        path = artifact.get("path")
        artifact_type = artifact.get("artifactType")
        require(isinstance(artifact_id, str) and artifact_id, "Artifact ID is missing")
        require(
            isinstance(content_hash, str) and SHA256_RE.fullmatch(content_hash) is not None,
            f"Artifact {artifact_id} has an invalid content hash",
        )
        require(isinstance(size_bytes, int) and size_bytes >= 0, "Artifact size is invalid")
        require(isinstance(path, str) and path, "Artifact path is missing")
        require(
            isinstance(artifact_type, str) and artifact_type,
            f"Artifact {artifact_id} has no type",
        )
        name = PurePosixPath(path).name
        require(name not in names, f"Analysis produced duplicate artifact name {name}")
        names.add(name)
        content = client.bytes_request(
            f"/v1/artifacts/{urllib.parse.quote(artifact_id, safe='')}/file"
        )
        require(len(content) == size_bytes, f"Artifact {name} size changed after persistence")
        require(
            hashlib.sha256(content).hexdigest() == content_hash,
            f"Artifact {name} content hash changed after persistence",
        )
        downloaded[name] = content
        if name == "environment.json":
            environment_artifact_hash = content_hash
        verified[artifact_id] = {
            "artifactType": artifact_type,
            "contentHash": content_hash,
            "path": path,
            "sizeBytes": size_bytes,
        }
    required_artifacts = {
        "environment.json": "environment",
        "executed.ipynb": "notebook-executed",
        "execution.log": "log",
        "figure.png": "figure",
        "stderr.txt": "stderr",
        "stdout.txt": "stdout",
        "summary.csv": "dataset",
    }
    for required_name, required_type in required_artifacts.items():
        require(
            required_name in downloaded,
            f"Analysis did not persist required {required_name}",
        )
        matching_records = [
            record
            for record in verified.values()
            if PurePosixPath(record["path"]).name == required_name
        ]
        require(
            len(matching_records) == 1
            and matching_records[0]["artifactType"] == required_type,
            f"Artifact {required_name} does not have type {required_type}",
        )
    require(
        environment_artifact_hash is not None,
        "Analysis did not persist the runtime environment manifest",
    )
    require(
        run.get("environmentHash") == environment_artifact_hash,
        "Run environmentHash does not match the downloaded environment.json bytes",
    )

    try:
        table_rows = list(
            csv.reader(io.StringIO(downloaded["summary.csv"].decode("utf-8")))
        )
    except (UnicodeDecodeError, csv.Error) as error:
        raise SmokeFailure("Generated summary.csv is not valid UTF-8 CSV") from error
    require(
        len(table_rows) >= 3
        and any(row and row[0] == "group" for row in table_rows)
        and any(row and row[0] == "value" for row in table_rows),
        "Generated summary.csv does not describe both fixture columns",
    )
    require(
        downloaded["figure.png"].startswith(b"\x89PNG\r\n\x1a\n"),
        "Generated figure.png is not a PNG",
    )
    try:
        notebook = json.loads(downloaded["executed.ipynb"])
        environment = json.loads(downloaded["environment.json"])
    except json.JSONDecodeError as error:
        raise SmokeFailure("Runtime notebook or environment manifest is invalid JSON") from error
    require(
        isinstance(notebook, dict) and isinstance(notebook.get("cells"), list),
        "Executed notebook has no cell array",
    )
    require(isinstance(environment, dict), "Environment manifest is not an object")
    return verified


def exercise(client: ApiClient, state_path: Path) -> None:
    assert_bearer_required(client.base_url)
    project = client.json_request(
        "POST",
        "/v1/projects",
        payload={
            "title": "Docker integration smoke",
            "description": "Ephemeral local-only integration test",
            "researchDomain": "brain-computer interfaces",
        },
    )
    project_id = project.get("id")
    require(isinstance(project_id, str) and project_id, "Project creation returned no ID")
    encoded_project_id = urllib.parse.quote(project_id, safe="")

    pdf = build_pdf()
    source = client.upload(
        f"/v1/projects/{encoded_project_id}/sources",
        filename="brain-computer-interface-evidence.pdf",
        content_type="application/pdf",
        content=pdf,
    )
    require(source.get("sourceKind") == "pdf", "PDF upload returned the wrong source kind")
    require(source.get("ingestionStatus") == "ready", "PDF did not finish local parsing")
    require(source.get("pageCount") == 1, "PDF fixture did not parse as one page")
    require(
        source.get("contentHash") == hashlib.sha256(pdf).hexdigest(),
        "PDF source hash differs from uploaded fixture",
    )

    workflow = client.json_request(
        "POST",
        f"/v1/projects/{encoded_project_id}/workflows",
        payload={
            "workflowType": "literature-synthesis",
            "goal": "How do brain computer interfaces improve communication?",
            "generationMode": "local-deterministic",
            "remoteDataApproved": False,
        },
        headers={"Idempotency-Key": f"docker-smoke-{uuid.uuid4().hex}"},
        expected_status=(202,),
    )
    workflow_id = workflow.get("workflow", {}).get("id")
    require(isinstance(workflow_id, str) and workflow_id, "Workflow creation returned no ID")
    planned = wait_for_workflow(client, workflow_id, {"waiting-plan-approval"})
    plan = planned.get("plan") or {}
    approvals = planned.get("pendingApprovals") or []
    require(len(approvals) == 1, "Workflow did not produce exactly one plan approval")
    approval = approvals[0]
    approved = client.json_request(
        "POST",
        f"/v1/workflows/{urllib.parse.quote(workflow_id, safe='')}/approve-plan",
        payload={
            "approvalId": approval.get("id"),
            "planId": plan.get("id"),
            "planVersion": plan.get("version"),
            "planSha256": plan.get("planSha256"),
            "expectedWorkflowRevision": planned.get("workflow", {}).get("revision"),
        },
    )
    require(
        approved.get("workflow", {}).get("status") == "running",
        "Approved literature workflow did not enter running state",
    )
    terminal = wait_for_workflow(client, workflow_id, EXPECTED_REVIEW_TERMINALS)
    validate_review_terminal(terminal)

    events_response = workflow_events(client, workflow_id)
    events = events_response["events"]
    event_types = {event.get("type") for event in events}
    required_event_types = {
        "workflow.created",
        "plan.generated",
        "approval.requested",
        "plan.approved",
        "review.completed",
    }
    require(
        required_event_types.issubset(event_types),
        f"Workflow event stream is missing {sorted(required_event_types - event_types)}",
    )
    literature_event_hashes = {
        event["id"]: canonical_sha256(event)
        for event in events
        if isinstance(event.get("id"), str)
    }
    require(
        len(literature_event_hashes) == len(events),
        "Literature workflow event IDs are missing or duplicated",
    )

    dataset = client.upload(
        f"/v1/projects/{encoded_project_id}/datasets",
        filename="analysis-input.csv",
        content_type="text/csv",
        content=DATASET_CSV,
    )
    dataset_id = dataset.get("id")
    require(isinstance(dataset_id, str) and dataset_id, "Dataset upload returned no ID")
    require(dataset.get("sourceKind") == "dataset", "CSV upload returned wrong source kind")
    require(dataset.get("ingestionStatus") == "ready", "CSV dataset is not ready")
    require(
        dataset.get("contentHash") == hashlib.sha256(DATASET_CSV).hexdigest(),
        "Dataset source hash differs from uploaded fixture",
    )

    dataset_hash = hashlib.sha256(DATASET_CSV).hexdigest()
    dataset_workflow = client.json_request(
        "POST",
        f"/v1/projects/{encoded_project_id}/workflows",
        payload={
            "workflowType": "dataset-analysis",
            "datasetSourceId": dataset_id,
            "goal": "Describe the uploaded dataset with a reproducible local baseline.",
            "generationMode": "local-deterministic",
            "remoteDataApproved": False,
        },
        headers={"Idempotency-Key": f"docker-dataset-smoke-{uuid.uuid4().hex}"},
        expected_status=(202,),
    )
    dataset_workflow_id = dataset_workflow.get("workflow", {}).get("id")
    require(
        isinstance(dataset_workflow_id, str) and dataset_workflow_id,
        "Dataset workflow creation returned no ID",
    )
    created_state = dataset_workflow.get("workflow", {})
    require(
        created_state.get("workflowType") == "dataset-analysis"
        and created_state.get("datasetSourceId") == dataset_id
        and created_state.get("datasetContentHash") == dataset_hash,
        "Dataset workflow did not freeze the uploaded CSV identity",
    )

    dataset_planned = wait_for_workflow(
        client,
        dataset_workflow_id,
        {"waiting-plan-approval"},
    )
    dataset_plan = dataset_planned.get("plan") or {}
    dataset_plan_spec = dataset_plan.get("spec") or {}
    dataset_plan_steps = dataset_plan_spec.get("steps") or []
    require(
        dataset_plan_spec.get("workflowType") == "dataset-analysis"
        and dataset_plan_spec.get("datasetSourceId") == dataset_id
        and dataset_plan_spec.get("datasetContentHash") == dataset_hash
        and [step.get("key") for step in dataset_plan_steps]
        == [
            "inspect-dataset",
            "prepare-analysis",
            "execute-analysis",
            "collect-artifacts",
        ],
        "Dataset workflow did not materialize the fixed typed analysis plan",
    )
    dataset_plan_approvals = dataset_planned.get("pendingApprovals") or []
    require(
        len(dataset_plan_approvals) == 1,
        "Dataset workflow did not produce exactly one plan approval",
    )
    dataset_plan_approval = dataset_plan_approvals[0]
    require(
        dataset_plan_approval.get("kind") == "plan"
        and dataset_plan_approval.get("workflowType") == "dataset-analysis"
        and dataset_plan_approval.get("planId") == dataset_plan.get("id")
        and dataset_plan_approval.get("planVersion") == dataset_plan.get("version")
        and dataset_plan_approval.get("planSha256") == dataset_plan.get("planSha256")
        and dataset_plan_approval.get("datasetSourceId") == dataset_id
        and dataset_plan_approval.get("datasetContentHash") == dataset_hash
        and dataset_plan_approval.get("expectedWorkflowRevision")
        == dataset_planned.get("workflow", {}).get("revision"),
        "Dataset plan approval is not bound to the exact plan and CSV",
    )
    dataset_approved = client.json_request(
        "POST",
        f"/v1/workflows/{urllib.parse.quote(dataset_workflow_id, safe='')}/approve-plan",
        payload={
            "approvalId": dataset_plan_approval.get("id"),
            "planId": dataset_plan.get("id"),
            "planVersion": dataset_plan.get("version"),
            "planSha256": dataset_plan.get("planSha256"),
            "expectedWorkflowRevision": dataset_planned.get("workflow", {}).get(
                "revision"
            ),
        },
    )
    require(
        dataset_approved.get("workflow", {}).get("status") == "running",
        "Approved dataset workflow did not enter running state",
    )

    waiting_execution = wait_for_workflow_condition(
        client,
        dataset_workflow_id,
        "the exact analysis execution approval barrier",
        lambda snapshot: (
            (snapshot.get("analysisIntent") or {}).get("status")
            == "waiting-approval"
            and len(snapshot.get("pendingApprovals") or []) == 1
            and snapshot["pendingApprovals"][0].get("kind") == "analysis-execution"
        ),
    )
    profile = waiting_execution.get("datasetProfile") or {}
    require(
        profile.get("datasetSourceId") == dataset_id
        and profile.get("contentHash") == dataset_hash
        and profile.get("rowCount") == 3
        and profile.get("columnCount") == 2,
        "Dataset inspection profile does not match the uploaded CSV",
    )
    intent = waiting_execution.get("analysisIntent") or {}
    intent_id = intent.get("id")
    require(isinstance(intent_id, str) and intent_id, "Workflow analysis intent has no ID")
    execution_approval = waiting_execution["pendingApprovals"][0]
    expected_outputs = {
        "analysis-log",
        "environment-manifest",
        "executed-notebook",
        "figures",
        "summary-table",
    }
    require(
        execution_approval.get("subjectId") == intent_id
        and execution_approval.get("analysisIntentId") == intent_id
        and execution_approval.get("payloadSha256") == intent.get("payloadSha256")
        and execution_approval.get("datasetSourceId") == dataset_id
        and execution_approval.get("datasetContentHash") == dataset_hash
        and execution_approval.get("code") == intent.get("code")
        and execution_approval.get("timeoutSeconds") == intent.get("timeoutSeconds")
        and set(execution_approval.get("expectedOutputs") or []) == expected_outputs
        and execution_approval.get("expectedWorkflowRevision")
        == waiting_execution.get("workflow", {}).get("revision"),
        "Execution approval is not bound to the exact immutable analysis intent",
    )
    require(
        waiting_execution.get("allowedActions")
        == ["approve-analysis", "reject-analysis", "cancel"],
        "Dataset workflow exposed unexpected actions at execution approval",
    )
    analysis_code = intent.get("code")
    require(
        isinstance(analysis_code, str)
        and "DATASET_PATH" in analysis_code
        and "RUN_DIR / 'summary.csv'" in analysis_code
        and "RUN_DIR / 'figure.png'" in analysis_code,
        "Generated analysis code does not declare the approved dataset outputs",
    )

    decided = client.json_request(
        "POST",
        f"/v1/workflows/{urllib.parse.quote(dataset_workflow_id, safe='')}"
        f"/analysis-intents/{urllib.parse.quote(intent_id, safe='')}/decision",
        payload={
            "approvalId": execution_approval.get("id"),
            "decision": "approved",
            "payloadSha256": intent.get("payloadSha256"),
            "expectedWorkflowRevision": waiting_execution.get("workflow", {}).get(
                "revision"
            ),
        },
        timeout=60.0,
    )
    require(
        decided.get("analysisIntent", {}).get("status") == "approved"
        and decided.get("pendingApprovals") == [],
        "Workflow-scoped analysis approval was not atomically queued",
    )

    reviewing = wait_for_workflow_condition(
        client,
        dataset_workflow_id,
        "the deterministic passed-with-warnings review",
        lambda snapshot: (
            snapshot.get("workflow", {}).get("status") == "reviewing"
            and (snapshot.get("analysisRun") or {}).get("status") == "completed"
            and (snapshot.get("latestReview") or {}).get("verdict")
            == "passed-with-warnings"
        ),
        timeout_seconds=300.0,
    )
    run = reviewing.get("analysisRun") or {}
    require(run.get("status") == "completed", f"Analysis failed: {run.get('error')}")
    require(run.get("intentId") == intent_id, "Analysis run points to a different intent")
    require(
        run.get("payloadSha256") == intent.get("payloadSha256")
        and run.get("datasetSourceId") == dataset_id
        and run.get("code") == analysis_code,
        "Analysis run differs from the exact approved workflow intent",
    )
    require(
        isinstance(run.get("environmentHash"), str)
        and SHA256_RE.fullmatch(run["environmentHash"]) is not None,
        "Analysis run environment hash is invalid",
    )
    artifacts = validate_and_download_artifacts(client, run)
    review = reviewing.get("latestReview") or {}
    review_result = review.get("result") or {}
    require(
        review_result.get("runId") == run.get("id")
        and review_result.get("analysisIntentId") == intent_id
        and review_result.get("inputDatasetContentHash") == dataset_hash
        and any(
            warning.get("code") == "descriptive-baseline-method-scope"
            for warning in review_result.get("methodWarnings") or []
        ),
        "Deterministic review is not bound to the completed run and method warning",
    )
    require(
        reviewing.get("allowedActions") == ["accept-review-warnings", "cancel"],
        "Warning-bearing review did not require explicit acceptance",
    )
    acceptance_revision = reviewing.get("workflow", {}).get("revision")
    accepted = client.json_request(
        "POST",
        f"/v1/workflows/{urllib.parse.quote(dataset_workflow_id, safe='')}"
        "/accept-review-warnings",
        payload={
            "reviewId": review.get("id"),
            "reviewInputSha256": review.get("inputSha256"),
            "expectedWorkflowRevision": acceptance_revision,
            "decision": "accepted",
        },
    )
    acceptance = accepted.get("reviewWarningAcceptance") or {}
    require(
        accepted.get("workflow", {}).get("status") == "completed"
        and accepted.get("allowedActions") == []
        and acceptance.get("reviewId") == review.get("id")
        and acceptance.get("reviewInputSha256") == review.get("inputSha256")
        and acceptance.get("expectedWorkflowRevision") == acceptance_revision
        and acceptance.get("decision") == "accepted",
        "Exact review warning acceptance did not complete the dataset workflow",
    )
    require(
        accepted.get("analysisRun") == run,
        "Analysis run changed while review warnings were accepted",
    )

    dataset_events_response = workflow_events(client, dataset_workflow_id)
    require(
        accepted.get("eventCursor") == dataset_events_response.get("nextAfter"),
        "Completed dataset snapshot event cursor is inconsistent",
    )
    dataset_events = dataset_events_response["events"]
    dataset_event_types = [event.get("type") for event in dataset_events]
    required_dataset_event_types = {
        "analysis.approval-requested",
        "analysis.approved",
        "analysis.intent-created",
        "analysis.review-warnings-accepted",
        "analysis.run-completed",
        "analysis.run-started",
        "approval.requested",
        "artifact.created",
        "plan.approved",
        "plan.generated",
        "review.completed",
        "workflow.created",
    }
    require(
        required_dataset_event_types.issubset(set(dataset_event_types)),
        "Dataset workflow event stream is missing "
        f"{sorted(required_dataset_event_types - set(dataset_event_types))}",
    )
    ordered_dataset_events = [
        "analysis.approved",
        "analysis.run-started",
        "analysis.run-completed",
        "review.completed",
        "analysis.review-warnings-accepted",
    ]
    require(
        [dataset_event_types.index(item) for item in ordered_dataset_events]
        == sorted(dataset_event_types.index(item) for item in ordered_dataset_events),
        "Dataset approval, execution, review, and acceptance events are out of order",
    )
    require(
        dataset_event_types.count("artifact.created") == len(artifacts),
        "Workflow artifact events do not match the persisted run artifacts",
    )
    artifact_event_bindings = {
        event.get("data", {}).get("artifactId"): event.get("data", {}).get(
            "contentHash"
        )
        for event in dataset_events
        if event.get("type") == "artifact.created"
    }
    require(
        artifact_event_bindings
        == {
            artifact_id: record["contentHash"]
            for artifact_id, record in artifacts.items()
        },
        "Workflow artifact events do not bind the persisted artifact hashes",
    )
    dataset_event_hashes = {
        event["id"]: canonical_sha256(event)
        for event in dataset_events
        if isinstance(event.get("id"), str)
    }
    require(
        len(dataset_event_hashes) == len(dataset_events),
        "Dataset workflow event IDs are missing or duplicated",
    )
    state = {
        "artifactRecords": artifacts,
        "datasetEventCursor": dataset_events_response["nextAfter"],
        "datasetEventHashes": dataset_event_hashes,
        "datasetSnapshotSha256": canonical_sha256(accepted),
        "datasetWorkflowId": dataset_workflow_id,
        "datasetWorkflowRevision": accepted["workflow"]["revision"],
        "literatureEventCursor": events_response["nextAfter"],
        "literatureEventHashes": literature_event_hashes,
        "literatureSnapshotSha256": canonical_sha256(terminal),
        "literatureWorkflowId": workflow_id,
        "literatureWorkflowRevision": terminal["workflow"]["revision"],
        "literatureWorkflowStatus": terminal["workflow"]["status"],
        "intentId": intent_id,
        "projectId": project_id,
        "runEnvironmentHash": run["environmentHash"],
        "runId": run["id"],
        "runPayloadSha256": run["payloadSha256"],
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("x", encoding="utf-8") as state_file:
        json.dump(state, state_file, sort_keys=True)
        state_file.write("\n")
    state_path.chmod(0o600)
    print(
        "Initial smoke completed: "
        f"literature={state['literatureWorkflowStatus']}, "
        f"dataset=completed, run=completed, artifacts={len(artifacts)}."
    )


def verify_restart(client: ApiClient, state_path: Path) -> None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeFailure("Could not read the pre-restart smoke state") from error

    project_id = state["projectId"]
    literature_workflow_id = state["literatureWorkflowId"]
    dataset_workflow_id = state["datasetWorkflowId"]
    projects = client.json_request("GET", "/v1/projects")
    require(
        any(project.get("id") == project_id for project in projects),
        "Project disappeared after science-core restart",
    )
    literature_snapshot = client.json_request(
        "GET",
        f"/v1/workflows/{urllib.parse.quote(literature_workflow_id, safe='')}",
    )
    require(
        literature_snapshot.get("workflow", {}).get("id") == literature_workflow_id,
        "Literature workflow ID changed after restart",
    )
    require(
        literature_snapshot.get("workflow", {}).get("status")
        == state["literatureWorkflowStatus"],
        "Literature workflow terminal status changed after restart",
    )
    require(
        literature_snapshot.get("workflow", {}).get("revision")
        == state["literatureWorkflowRevision"],
        "Literature workflow revision changed after restart",
    )
    require(
        canonical_sha256(literature_snapshot) == state["literatureSnapshotSha256"],
        "Literature workflow snapshot changed after restart",
    )
    validate_review_terminal(literature_snapshot)

    literature_events = workflow_events(client, literature_workflow_id)
    require(
        literature_events.get("nextAfter") == state["literatureEventCursor"],
        "Literature workflow event cursor changed after restart",
    )
    literature_hashes = {
        event["id"]: canonical_sha256(event)
        for event in literature_events["events"]
        if isinstance(event.get("id"), str)
    }
    require(
        literature_hashes == state["literatureEventHashes"],
        "Literature workflow events changed after restart",
    )

    dataset_snapshot = client.json_request(
        "GET",
        f"/v1/workflows/{urllib.parse.quote(dataset_workflow_id, safe='')}",
    )
    require(
        dataset_snapshot.get("workflow", {}).get("id") == dataset_workflow_id
        and dataset_snapshot.get("workflow", {}).get("status") == "completed"
        and dataset_snapshot.get("workflow", {}).get("revision")
        == state["datasetWorkflowRevision"],
        "Completed dataset workflow identity or revision changed after restart",
    )
    require(
        canonical_sha256(dataset_snapshot) == state["datasetSnapshotSha256"],
        "Dataset workflow snapshot changed after restart",
    )
    require(
        dataset_snapshot.get("analysisRun", {}).get("id") == state["runId"]
        and dataset_snapshot.get("analysisIntent", {}).get("id") == state["intentId"]
        and dataset_snapshot.get("latestReview", {}).get("verdict")
        == "passed-with-warnings"
        and dataset_snapshot.get("reviewWarningAcceptance", {}).get("decision")
        == "accepted",
        "Dataset run, intent, review, or warning acceptance changed after restart",
    )

    dataset_events = workflow_events(client, dataset_workflow_id)
    require(
        dataset_events.get("nextAfter") == state["datasetEventCursor"],
        "Dataset workflow event cursor changed after restart",
    )
    dataset_hashes = {
        event["id"]: canonical_sha256(event)
        for event in dataset_events["events"]
        if isinstance(event.get("id"), str)
    }
    require(
        dataset_hashes == state["datasetEventHashes"],
        "Dataset workflow events changed after restart",
    )

    runs = client.json_request(
        "GET",
        f"/v1/projects/{urllib.parse.quote(project_id, safe='')}/analysis-runs",
    )
    run = next((candidate for candidate in runs if candidate.get("id") == state["runId"]), None)
    require(isinstance(run, dict), "Analysis run disappeared after science-core restart")
    require(run.get("status") == "completed", "Analysis run is no longer completed")
    require(run.get("intentId") == state["intentId"], "Analysis run intent changed")
    require(
        run.get("payloadSha256") == state["runPayloadSha256"],
        "Analysis run payload hash changed after restart",
    )
    require(
        run.get("environmentHash") == state["runEnvironmentHash"],
        "Analysis environment hash changed after restart",
    )
    persisted_artifacts = validate_and_download_artifacts(client, run)
    require(
        persisted_artifacts == state["artifactRecords"],
        "Analysis artifact records changed after science-core restart",
    )
    print(
        "Restart persistence verified: both workflows, the dataset run, events, and "
        "artifact hashes are intact."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Spark Agent Docker integration smoke driver")
    parser.add_argument("command", choices=("wait-ready", "exercise", "verify-restart"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--timeout", type=float, default=150.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "wait-ready":
            wait_ready(args.base_url, args.timeout)
            return 0
        require(args.state_file is not None, "--state-file is required for this command")
        token = os.environ.pop("SPARK_AGENT_SMOKE_TOKEN", "")
        client = ApiClient(args.base_url, token)
        if args.command == "exercise":
            exercise(client, args.state_file)
        else:
            verify_restart(client, args.state_file)
        return 0
    except SmokeFailure as error:
        print(f"Docker integration smoke failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
