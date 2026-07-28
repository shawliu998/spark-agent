#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
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
                f"Literature workflow reached unexpected terminal state {last_status}"
            )
        if last_status == "blocked" and "blocked" not in target_statuses:
            reason = snapshot.get("workflow", {}).get("blockingReason")
            raise SmokeFailure(f"Literature workflow blocked before review: {reason}")
        time.sleep(0.5)
    raise SmokeFailure(
        f"Literature workflow did not reach {sorted(target_statuses)}; last state was {last_status}"
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
    summary_payload: dict[str, Any] | None = None
    names: set[str] = set()
    for artifact in artifacts:
        artifact_id = artifact.get("id")
        content_hash = artifact.get("contentHash")
        size_bytes = artifact.get("sizeBytes")
        path = artifact.get("path")
        require(isinstance(artifact_id, str) and artifact_id, "Artifact ID is missing")
        require(
            isinstance(content_hash, str) and SHA256_RE.fullmatch(content_hash) is not None,
            f"Artifact {artifact_id} has an invalid content hash",
        )
        require(isinstance(size_bytes, int) and size_bytes >= 0, "Artifact size is invalid")
        require(isinstance(path, str) and path, "Artifact path is missing")
        name = PurePosixPath(path).name
        names.add(name)
        content = client.bytes_request(
            f"/v1/artifacts/{urllib.parse.quote(artifact_id, safe='')}/file"
        )
        require(len(content) == size_bytes, f"Artifact {name} size changed after persistence")
        require(
            hashlib.sha256(content).hexdigest() == content_hash,
            f"Artifact {name} content hash changed after persistence",
        )
        if name == "summary.json":
            try:
                summary_payload = json.loads(content)
            except json.JSONDecodeError as error:
                raise SmokeFailure("Generated summary.json is not valid JSON") from error
        elif name == "environment.json":
            environment_artifact_hash = content_hash
        verified[artifact_id] = {
            "contentHash": content_hash,
            "path": path,
            "sizeBytes": size_bytes,
        }
    require("summary.json" in names, "Analysis did not persist generated summary.json")
    require("summary.csv" in names, "Analysis did not persist generated summary.csv")
    require(
        environment_artifact_hash is not None,
        "Analysis did not persist the runtime environment manifest",
    )
    require(
        run.get("environmentHash") == environment_artifact_hash,
        "Run environmentHash does not match the downloaded environment.json bytes",
    )
    require(
        summary_payload == {"rowCount": 3, "total": 60.0},
        "Generated summary.json has unexpected analysis results",
    )
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

    analysis_code = "\n".join(
        (
            "import csv",
            "import json",
            "from pathlib import Path",
            "with open(DATASET_PATH, encoding='utf-8', newline='') as source_file:",
            "    rows = list(csv.DictReader(source_file))",
            "values = [float(row['value']) for row in rows]",
            "summary = {'rowCount': len(rows), 'total': sum(values)}",
            "Path(RUN_DIR, 'summary.json').write_text(",
            "    json.dumps(summary, sort_keys=True), encoding='utf-8'",
            ")",
            "Path(RUN_DIR, 'summary.csv').write_text(",
            "    'metric,value\\nrow_count,3\\ntotal,60.0\\n', encoding='utf-8'",
            ")",
            "print(json.dumps(summary, sort_keys=True))",
        )
    )
    analysis_objective = "Count rows and sum the value column deterministically."
    expected_payload_sha256 = canonical_sha256(
        {
            "code": analysis_code,
            "datasetSourceId": dataset_id,
            "objective": analysis_objective,
        }
    )
    intent = client.json_request(
        "POST",
        f"/v1/projects/{encoded_project_id}/analysis-intents",
        payload={
            "datasetSourceId": dataset_id,
            "objective": analysis_objective,
            "code": analysis_code,
        },
    )
    intent_id = intent.get("id")
    require(isinstance(intent_id, str) and intent_id, "Analysis intent returned no ID")
    require(intent.get("status") == "waiting-approval", "Analysis intent is not awaiting approval")
    require(
        intent.get("payloadSha256") == expected_payload_sha256,
        "Analysis intent payload hash does not match the independently canonicalized request",
    )
    decided = client.json_request(
        "POST",
        f"/v1/analysis-intents/{urllib.parse.quote(intent_id, safe='')}/decision",
        payload={"decision": "approved"},
    )
    require(decided.get("status") == "approved", "Analysis intent approval was not persisted")
    run = client.json_request(
        "POST",
        f"/v1/analysis-intents/{urllib.parse.quote(intent_id, safe='')}/execute",
        timeout=180.0,
    )
    require(run.get("status") == "completed", f"Analysis failed: {run.get('error')}")
    require(run.get("intentId") == intent_id, "Analysis run points to a different intent")
    require(
        run.get("payloadSha256") == expected_payload_sha256,
        "Analysis run payload hash differs from the independently canonicalized approval payload",
    )
    require(
        isinstance(run.get("environmentHash"), str)
        and SHA256_RE.fullmatch(run["environmentHash"]) is not None,
        "Analysis run environment hash is invalid",
    )
    artifacts = validate_and_download_artifacts(client, run)

    event_hashes = {
        event["id"]: canonical_sha256(event)
        for event in events
        if isinstance(event.get("id"), str)
    }
    require(len(event_hashes) == len(events), "Workflow event IDs are missing or duplicated")
    state = {
        "artifactRecords": artifacts,
        "eventCursor": events_response["nextAfter"],
        "eventHashes": event_hashes,
        "eventTypes": sorted(event_types),
        "intentId": intent_id,
        "projectId": project_id,
        "runEnvironmentHash": run["environmentHash"],
        "runId": run["id"],
        "runPayloadSha256": run["payloadSha256"],
        "workflowId": workflow_id,
        "workflowRevision": terminal["workflow"]["revision"],
        "workflowStatus": terminal["workflow"]["status"],
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("x", encoding="utf-8") as state_file:
        json.dump(state, state_file, sort_keys=True)
        state_file.write("\n")
    state_path.chmod(0o600)
    print(
        "Initial smoke completed: "
        f"workflow={state['workflowStatus']}, run=completed, artifacts={len(artifacts)}."
    )


def verify_restart(client: ApiClient, state_path: Path) -> None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeFailure("Could not read the pre-restart smoke state") from error

    project_id = state["projectId"]
    workflow_id = state["workflowId"]
    projects = client.json_request("GET", "/v1/projects")
    require(
        any(project.get("id") == project_id for project in projects),
        "Project disappeared after science-core restart",
    )
    snapshot = client.json_request(
        "GET",
        f"/v1/workflows/{urllib.parse.quote(workflow_id, safe='')}",
    )
    require(snapshot.get("workflow", {}).get("id") == workflow_id, "Workflow ID changed")
    require(
        snapshot.get("workflow", {}).get("status") == state["workflowStatus"],
        "Workflow terminal status changed after restart",
    )
    require(
        snapshot.get("workflow", {}).get("revision") == state["workflowRevision"],
        "Workflow revision changed after restart",
    )
    validate_review_terminal(snapshot)

    persisted_events = workflow_events(client, workflow_id)
    require(
        persisted_events.get("nextAfter", 0) >= state["eventCursor"],
        "Workflow event cursor moved backwards after restart",
    )
    persisted_hashes = {
        event["id"]: canonical_sha256(event)
        for event in persisted_events["events"]
        if isinstance(event.get("id"), str)
    }
    for event_id, expected_hash in state["eventHashes"].items():
        require(
            event_id in persisted_hashes,
            f"Workflow event {event_id} disappeared after restart",
        )
        require(
            persisted_hashes[event_id] == expected_hash,
            f"Workflow event {event_id} changed after restart",
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
        "Restart persistence verified: workflow, run, events, and artifact hashes are intact."
    )


def create_skill_discovery_projects(client: ApiClient, state_path: Path) -> None:
    assert_bearer_required(client.base_url)
    projects = []
    for suffix in ("A", "B"):
        project = client.json_request(
            "POST",
            "/v1/projects",
            payload={
                "title": f"Project skill discovery {suffix}",
                "description": "Ephemeral local-only OpenCode discovery fixture",
                "researchDomain": "integration testing",
            },
        )
        project_id = project.get("id")
        require(
            isinstance(project_id, str) and project_id,
            f"Project {suffix} creation returned no ID",
        )
        projects.append(project_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("x", encoding="utf-8") as state_file:
        json.dump({"projectA": projects[0], "projectB": projects[1]}, state_file, sort_keys=True)
        state_file.write("\n")
    state_path.chmod(0o600)
    print(f"Created isolated project pair: A={projects[0]}, B={projects[1]}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Spark Agent Docker integration smoke driver")
    parser.add_argument(
        "command",
        choices=(
            "wait-ready",
            "exercise",
            "verify-restart",
            "create-skill-discovery-projects",
        ),
    )
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
        elif args.command == "create-skill-discovery-projects":
            create_skill_discovery_projects(client, args.state_file)
        else:
            verify_restart(client, args.state_file)
        return 0
    except SmokeFailure as error:
        print(f"Docker integration smoke failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
