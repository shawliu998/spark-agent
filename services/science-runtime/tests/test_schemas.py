from __future__ import annotations

import pytest
from pydantic import ValidationError

from open_science_runtime.schemas import ExecuteIn


def _valid_payload() -> dict[str, object]:
    return {
        "runId": "analysis-01",
        "runDir": "/runtime-data/runs/analysis-01",
        "datasetPath": "/runtime-data/input.csv",
        "objective": "Summarize the dataset",
        "code": "print('ok')",
        "timeoutSeconds": 120,
        "payloadSha256": "a" * 64,
        "policyProfileId": "approved-python-container-v1",
        "policyTemplate": None,
    }


def test_execute_input_accepts_camel_case_and_serializes_only_api_aliases() -> None:
    payload = _valid_payload()
    payload["runId"] = "  analysis-01  "

    parsed = ExecuteIn.model_validate(payload)

    assert parsed.run_id == "analysis-01"
    assert parsed.model_dump(mode="json", by_alias=True) == {
        **payload,
        "runId": "analysis-01",
        "analysisSpecId": None,
        "analysisSpecSha256": None,
        "datasetProfileSha256": None,
        "compilerVersion": None,
        "approvedCodeSha256": None,
    }


def test_execute_input_accepts_objective_at_size_limit() -> None:
    payload = _valid_payload()
    payload["objective"] = "x" * 8000

    parsed = ExecuteIn.model_validate(payload)

    assert len(parsed.objective) == 8000


def test_execute_input_rejects_objective_above_size_limit() -> None:
    payload = _valid_payload()
    payload["objective"] = "x" * 8001

    with pytest.raises(ValidationError):
        ExecuteIn.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runId", "../escape"),
        ("runId", "x" * 129),
        ("payloadSha256", "A" * 64),
        ("payloadSha256", "a" * 63),
        ("timeoutSeconds", 0),
        ("timeoutSeconds", 121),
        ("runDir", "/runtime-data/run\x00escape"),
        ("datasetPath", "/runtime-data/input.csv\x00"),
        ("code", " \n\t "),
        ("code", "print('ok')\x00"),
    ],
)
def test_execute_input_rejects_malformed_or_unbounded_fields(
    field: str,
    value: object,
) -> None:
    payload = _valid_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        ExecuteIn.model_validate(payload)


def test_execute_input_rejects_unknown_fields() -> None:
    payload = _valid_payload()
    payload["authorization"] = "must-not-be-accepted"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExecuteIn.model_validate(payload)


@pytest.mark.parametrize(
    ("policy_profile_id", "policy_template"),
    [
        ("dataset-analysis-fixed-v1", None),
        ("approved-python-container-v1", "baseline"),
        ("unknown-policy", None),
    ],
)
def test_execute_input_rejects_incoherent_policy_contracts(
    policy_profile_id: str,
    policy_template: str | None,
) -> None:
    payload = _valid_payload()
    payload["policyProfileId"] = policy_profile_id
    payload["policyTemplate"] = policy_template

    with pytest.raises(ValidationError):
        ExecuteIn.model_validate(payload)


def test_execute_input_accepts_explicit_fixed_policy_contract() -> None:
    payload = _valid_payload()
    payload["policyProfileId"] = "dataset-analysis-fixed-v1"
    payload["policyTemplate"] = "repair-2"

    parsed = ExecuteIn.model_validate(payload)

    assert parsed.policy_profile_id == "dataset-analysis-fixed-v1"
    assert parsed.policy_template == "repair-2"


def test_execute_input_requires_an_explicit_policy_profile() -> None:
    payload = _valid_payload()
    del payload["policyProfileId"]

    with pytest.raises(ValidationError, match="policyProfileId"):
        ExecuteIn.model_validate(payload)


def test_execute_input_accepts_exact_compiled_policy_provenance() -> None:
    payload = _valid_payload()
    payload.update(
        {
            "policyProfileId": "dataset-analysis-spec-v1",
            "policyTemplate": "analysis-spec-compiler-v1",
            "analysisSpecId": "spec-1",
            "analysisSpecSha256": "b" * 64,
            "datasetProfileSha256": "c" * 64,
            "compilerVersion": "analysis-spec-compiler-v1",
            "approvedCodeSha256": "d" * 64,
        }
    )

    parsed = ExecuteIn.model_validate(payload)

    assert parsed.analysis_spec_id == "spec-1"
    assert parsed.policy_profile_id == "dataset-analysis-spec-v1"
    assert parsed.policy_template == "analysis-spec-compiler-v1"


@pytest.mark.parametrize(
    "field",
    [
        "analysisSpecId",
        "analysisSpecSha256",
        "datasetProfileSha256",
        "compilerVersion",
        "approvedCodeSha256",
    ],
)
def test_execute_input_rejects_partial_compiled_policy_provenance(field: str) -> None:
    payload = _valid_payload()
    payload.update(
        {
            "policyProfileId": "dataset-analysis-spec-v1",
            "policyTemplate": "analysis-spec-compiler-v1",
            "analysisSpecId": "spec-1",
            "analysisSpecSha256": "b" * 64,
            "datasetProfileSha256": "c" * 64,
            "compilerVersion": "analysis-spec-compiler-v1",
            "approvedCodeSha256": "d" * 64,
        }
    )
    del payload[field]

    with pytest.raises(ValidationError, match="exact provenance"):
        ExecuteIn.model_validate(payload)


def test_fixed_and_general_policies_reject_compiled_provenance() -> None:
    for profile, template in (
        ("approved-python-container-v1", None),
        ("dataset-analysis-fixed-v1", "baseline"),
    ):
        payload = _valid_payload()
        payload.update(
            {
                "policyProfileId": profile,
                "policyTemplate": template,
                "analysisSpecId": "spec-1",
            }
        )
        with pytest.raises(ValidationError):
            ExecuteIn.model_validate(payload)
