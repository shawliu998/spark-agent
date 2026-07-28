from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast

from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from ..schemas import ApiModel, BoundingBoxOut
from .discovery_schemas import (
    DiscoveryProvider,
    DiscoverySort,
    DiscoveryStopPolicy,
    QueryId,
    QueryText,
    Sha256,
)

WorkflowStatus = Literal[
    "routing",
    "waiting-clarification",
    "planning",
    "waiting-plan-approval",
    "running",
    "reviewing",
    "completed",
    "unsupported",
    "blocked",
    "failed",
    "cancelled",
]
PlanStatus = Literal["pending-approval", "approved", "rejected", "superseded"]
WorkflowType = Literal["literature-synthesis", "dataset-analysis"]
TaskStepType = Literal[
    "inspect-sources",
    "extract-local-evidence",
    "synthesize-extractive-claims",
    "dataset-inspection",
    "prepare-analysis",
    "python-data-analysis",
    "collect-artifacts",
    "paper-discovery",
]
TaskStatus = Literal[
    "pending",
    "queued",
    "running",
    "waiting-approval",
    "completed",
    "blocked",
    "failed",
    "cancelled",
]
AllowedAction = Literal[
    "approve-plan",
    "approve-analysis",
    "reject-analysis",
    "accept-review-warnings",
    "cancel",
    "retry",
    "resume",
]
LiteratureAllowedAction = Literal["approve-plan", "cancel", "retry", "resume"]
LiteratureReviewVerdict = Literal[
    "passed",
    "revision-required",
    "blocked",
    "failed",
]
DatasetAnalysisReviewVerdict = Literal[
    "passed",
    "passed-with-warnings",
    "revision-required",
    "blocked",
    "failed",
]
ReviewVerdict = LiteratureReviewVerdict | DatasetAnalysisReviewVerdict
ReviewType = Literal[
    "deterministic-claims-v1",
    "deterministic-claims-v2",
    "deterministic-analysis-v1",
]
ClaimSupportStatus = Literal[
    "pending-review",
    "supported",
    "partially-supported",
    "contradicted",
    "insufficient-evidence",
    "not-applicable",
]
GenerationMode = Literal["local-deterministic", "remote-model-assisted"]
RemoteDataCategory = Literal[
    "user-goal",
    "dataset-profile",
    "source-metadata",
    "user-answer",
]
AUTONOMOUS_REMOTE_DATA_CATEGORIES: tuple[RemoteDataCategory, ...] = (
    "user-goal",
    "dataset-profile",
    "source-metadata",
    "user-answer",
)
WorkflowRiskLevel = Literal["low", "medium", "high"]

DatasetAnalysisStepKey = Literal[
    "inspect-dataset",
    "prepare-analysis",
    "execute-analysis",
    "collect-artifacts",
]
DatasetAnalysisArtifactKind = Literal[
    "dataset-profile",
    "analysis-intent",
    "executed-notebook",
    "summary-table",
    "figure",
    "analysis-log",
    "environment-manifest",
]
DatasetAnalysisExpectedOutput = Literal[
    "dataset-profile",
    "analysis-code",
    "executed-notebook",
    "summary-table",
    "figures",
    "analysis-log",
    "environment-manifest",
]
DatasetAnalysisRuntimeArtifactType = Literal[
    "notebook-input",
    "notebook-executed",
    "environment",
    "stdout",
    "stderr",
    "log",
    "figure",
    "dataset",
    "structured-data",
]
DatasetColumnInferredType = Literal[
    "boolean",
    "integer",
    "number",
    "datetime",
    "categorical",
    "string",
    "empty",
    "mixed",
]
DatasetInspectionWarningCode = Literal[
    "encoding-fallback",
    "duplicate-column-name",
    "mixed-column-type",
    "malformed-row",
    "sample-limited",
    "other",
]

_DATASET_ANALYSIS_EXECUTION_OUTPUT_SEQUENCES = {
    ("executed-notebook", "analysis-log", "environment-manifest"),
    (
        "executed-notebook",
        "summary-table",
        "analysis-log",
        "environment-manifest",
    ),
    (
        "executed-notebook",
        "figures",
        "analysis-log",
        "environment-manifest",
    ),
    (
        "executed-notebook",
        "summary-table",
        "figures",
        "analysis-log",
        "environment-manifest",
    ),
}
_DATASET_ANALYSIS_EXECUTION_ARTIFACT_SEQUENCES: dict[
    tuple[DatasetAnalysisExpectedOutput, ...],
    tuple[DatasetAnalysisArtifactKind, ...],
] = {
    ("executed-notebook", "analysis-log", "environment-manifest"): (
        "executed-notebook",
        "analysis-log",
        "environment-manifest",
    ),
    (
        "executed-notebook",
        "summary-table",
        "analysis-log",
        "environment-manifest",
    ): (
        "executed-notebook",
        "summary-table",
        "analysis-log",
        "environment-manifest",
    ),
    (
        "executed-notebook",
        "figures",
        "analysis-log",
        "environment-manifest",
    ): (
        "executed-notebook",
        "figure",
        "analysis-log",
        "environment-manifest",
    ),
    (
        "executed-notebook",
        "summary-table",
        "figures",
        "analysis-log",
        "environment-manifest",
    ): (
        "executed-notebook",
        "summary-table",
        "figure",
        "analysis-log",
        "environment-manifest",
    ),
}

StepKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9-]*$",
    ),
]
NonEmptyPlanText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]


class StrictApiModel(ApiModel):
    model_config = ConfigDict(extra="forbid")


class DatasetNumericRange(StrictApiModel):
    minimum: float | None
    maximum: float | None

    @model_validator(mode="after")
    def validate_range(self) -> DatasetNumericRange:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("numeric range minimum cannot exceed maximum")
        return self


class DatasetLowCardinalitySummary(StrictApiModel):
    values: list[str] = Field(max_length=50)
    truncated: bool

    @field_validator("values")
    @classmethod
    def validate_bounded_values(cls, value: list[str]) -> list[str]:
        if any(len(item) > 200 or "\x00" in item for item in value):
            raise ValueError("low-cardinality values must be safe bounded display strings")
        if len(set(value)) != len(value):
            raise ValueError("low-cardinality values must be unique")
        return value


class DatasetColumnProfile(StrictApiModel):
    index: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=1_000)
    inferred_type: DatasetColumnInferredType
    missing_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    numeric_range: DatasetNumericRange | None
    low_cardinality: DatasetLowCardinalitySummary | None
    potential_date: bool
    potential_id: bool
    mixed_type: bool


class DatasetInspectionWarning(StrictApiModel):
    code: DatasetInspectionWarningCode
    message: str = Field(min_length=1, max_length=1_000)
    column_name: str | None = Field(default=None, max_length=1_000)


class DatasetSamplingRecord(StrictApiModel):
    method: Literal["head-and-reservoir-v1"]
    rows_read: int = Field(ge=0)
    rows_profiled: int = Field(ge=0)
    max_sample_rows: int = Field(ge=1, le=10_000)
    seed: int = Field(ge=0, le=2**32 - 1)

    @model_validator(mode="after")
    def validate_sampling_counts(self) -> DatasetSamplingRecord:
        if self.rows_profiled > self.rows_read:
            raise ValueError("rows_profiled cannot exceed rows_read")
        if self.rows_profiled > self.max_sample_rows:
            raise ValueError("rows_profiled cannot exceed max_sample_rows")
        return self


class DatasetProfile(StrictApiModel):
    schema_version: Literal["1"]
    dataset_source_id: str = Field(min_length=1, max_length=36)
    filename: str = Field(min_length=1, max_length=255)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_size_bytes: int = Field(ge=1)
    encoding: str = Field(min_length=1, max_length=100)
    delimiter: str = Field(min_length=1, max_length=1)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=1, le=10_000)
    columns: list[DatasetColumnProfile] = Field(min_length=1, max_length=10_000)
    sampling: DatasetSamplingRecord
    warnings: list[DatasetInspectionWarning] = Field(max_length=1_000)

    @field_validator("filename")
    @classmethod
    def validate_basename(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("filename must be a basename, not a path")
        return value

    @model_validator(mode="after")
    def validate_profile_shape(self) -> DatasetProfile:
        if self.column_count != len(self.columns):
            raise ValueError("column_count must match the number of column profiles")
        indexes = [column.index for column in self.columns]
        if indexes != list(range(self.column_count)):
            raise ValueError("column profile indexes must be contiguous and ordered")
        if self.sampling.rows_read != self.row_count:
            raise ValueError("sampling rows_read must match the streamed row_count")
        if any(
            column.missing_count > self.row_count or column.unique_count > self.row_count
            for column in self.columns
        ):
            raise ValueError("column counts cannot exceed the dataset row_count")
        return self


class FrozenSourceDescriptor(StrictApiModel):
    source_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=1_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class InspectSourcesInput(StrictApiModel):
    source_kind: Literal["pdf"] = "pdf"
    # Retained only so an old local plan can still be parsed. New remote plans
    # use frozen_sources because IDs alone do not authorize immutable content.
    source_ids: list[str] | None = Field(default=None, max_length=100)
    frozen_sources: list[FrozenSourceDescriptor] | None = Field(
        default=None,
        max_length=100,
    )

    @field_validator("source_ids")
    @classmethod
    def validate_source_allowlist(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 36 for item in normalized):
            raise ValueError("source_ids must contain non-empty record identifiers")
        if len(set(normalized)) != len(normalized):
            raise ValueError("source_ids must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_frozen_sources(self) -> InspectSourcesInput:
        if self.source_ids is not None and self.frozen_sources is not None:
            raise ValueError("source_ids and frozen_sources are mutually exclusive")
        if self.frozen_sources is not None:
            source_ids = [source.source_id for source in self.frozen_sources]
            if len(set(source_ids)) != len(source_ids):
                raise ValueError("frozen_sources must contain unique source identifiers")
        return self


class ExtractLocalEvidenceInput(StrictApiModel):
    query: str = Field(min_length=2, max_length=8_000)
    max_passages: int = Field(default=12, ge=1, le=40)
    max_per_source: int = Field(default=4, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class SynthesizeExtractiveClaimsInput(StrictApiModel):
    max_claims: int = Field(default=8, ge=1, le=20)


class SequentialStepSpec(StrictApiModel):
    key: StepKey
    type: TaskStepType
    objective: str = Field(min_length=1, max_length=2_000)
    inputs: InspectSourcesInput | ExtractLocalEvidenceInput | SynthesizeExtractiveClaimsInput
    expected_outputs: list[Literal["sources", "evidence", "claims", "evidence-map"]]
    acceptance_criteria: list[
        Literal[
            "at-least-one-ready-pdf",
            "at-least-one-verified-evidence",
            "at-least-one-claim",
            "every-claim-has-verified-evidence",
        ]
    ] = Field(min_length=1)


class PlanSpec(StrictApiModel):
    schema_version: Literal["1"] = "1"
    goal: str = Field(min_length=2, max_length=8_000)
    steps: list[SequentialStepSpec] = Field(min_length=3, max_length=3)

    @field_validator("steps")
    @classmethod
    def validate_frozen_sequence(cls, value: list[SequentialStepSpec]) -> list[SequentialStepSpec]:
        expected = [
            "inspect-sources",
            "extract-local-evidence",
            "synthesize-extractive-claims",
        ]
        if [step.type for step in value] != expected:
            raise ValueError("the first workflow version requires the frozen three-step sequence")
        if len({step.key for step in value}) != len(value):
            raise ValueError("plan step keys must be unique")
        expected_input_types = (
            InspectSourcesInput,
            ExtractLocalEvidenceInput,
            SynthesizeExtractiveClaimsInput,
        )
        if any(type(step.inputs) is not kind for step, kind in zip(value, expected_input_types)):
            raise ValueError("step input does not match its step type")
        return value


class DatasetInspectionStepInput(StrictApiModel):
    dataset_source_id: str = Field(min_length=1, max_length=36)
    dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sampling_method: Literal["head-and-reservoir-v1"]
    max_sample_rows: int = Field(ge=1, le=10_000)


class PrepareAnalysisStepInput(StrictApiModel):
    dataset_source_id: str = Field(min_length=1, max_length=36)
    dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_step_key: Literal["inspect-dataset"]


class ExecuteAnalysisStepInput(StrictApiModel):
    dataset_source_id: str = Field(min_length=1, max_length=36)
    dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preparation_step_key: Literal["prepare-analysis"]
    expected_outputs: tuple[DatasetAnalysisExpectedOutput, ...] = Field(min_length=1)
    timeout_seconds: int = Field(ge=1, le=3_600)

    @field_validator("expected_outputs")
    @classmethod
    def validate_execution_outputs(
        cls,
        value: tuple[DatasetAnalysisExpectedOutput, ...],
    ) -> tuple[DatasetAnalysisExpectedOutput, ...]:
        if value not in _DATASET_ANALYSIS_EXECUTION_OUTPUT_SEQUENCES:
            raise ValueError(
                "expected_outputs must use the canonical sequence with notebook, "
                "analysis log, and environment manifest"
            )
        return value


class CollectArtifactsStepInput(StrictApiModel):
    execution_step_key: Literal["execute-analysis"]
    expected_outputs: tuple[DatasetAnalysisExpectedOutput, ...] = Field(min_length=1)

    @field_validator("expected_outputs")
    @classmethod
    def validate_execution_outputs(
        cls,
        value: tuple[DatasetAnalysisExpectedOutput, ...],
    ) -> tuple[DatasetAnalysisExpectedOutput, ...]:
        if value not in _DATASET_ANALYSIS_EXECUTION_OUTPUT_SEQUENCES:
            raise ValueError(
                "expected_outputs must use the canonical sequence with notebook, "
                "analysis log, and environment manifest"
            )
        return value


class DatasetAnalysisStepBase(StrictApiModel):
    objective: str = Field(min_length=1, max_length=8_000)
    expected_artifacts: tuple[DatasetAnalysisArtifactKind, ...] = Field(min_length=1)
    acceptance_criteria: tuple[NonEmptyPlanText, ...] = Field(min_length=1)

    @field_validator("expected_artifacts", "acceptance_criteria")
    @classmethod
    def validate_unique_step_requirements(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("step requirements must be unique")
        return value


class DatasetInspectionPlanStep(DatasetAnalysisStepBase):
    key: Literal["inspect-dataset"]
    type: Literal["dataset-inspection"]
    dependencies: tuple[()]
    inputs: DatasetInspectionStepInput
    risk_level: Literal["low"]


class PrepareAnalysisPlanStep(DatasetAnalysisStepBase):
    key: Literal["prepare-analysis"]
    type: Literal["prepare-analysis"]
    dependencies: tuple[Literal["inspect-dataset"]]
    inputs: PrepareAnalysisStepInput
    risk_level: Literal["medium"]


class ExecuteAnalysisPlanStep(DatasetAnalysisStepBase):
    key: Literal["execute-analysis"]
    type: Literal["python-data-analysis"]
    dependencies: tuple[Literal["prepare-analysis"]]
    inputs: ExecuteAnalysisStepInput
    risk_level: Literal["high"]


class CollectArtifactsPlanStep(DatasetAnalysisStepBase):
    key: Literal["collect-artifacts"]
    type: Literal["collect-artifacts"]
    dependencies: tuple[Literal["execute-analysis"]]
    inputs: CollectArtifactsStepInput
    risk_level: Literal["low"]


class DatasetAnalysisPlanSpec(StrictApiModel):
    schema_version: Literal["1"]
    workflow_type: Literal["dataset-analysis"]
    goal: str = Field(min_length=2, max_length=8_000)
    dataset_source_id: str = Field(min_length=1, max_length=36)
    dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_spec_id: str | None = Field(default=None, min_length=1, max_length=36)
    analysis_spec_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    assumptions: list[NonEmptyPlanText]
    questions_for_user: list[NonEmptyPlanText]
    steps: tuple[
        DatasetInspectionPlanStep,
        PrepareAnalysisPlanStep,
        ExecuteAnalysisPlanStep,
        CollectArtifactsPlanStep,
    ]

    @field_validator("assumptions", "questions_for_user")
    @classmethod
    def validate_unique_plan_notes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("plan assumptions and questions must be unique")
        return value

    @model_validator(mode="after")
    def validate_dataset_bindings(self) -> DatasetAnalysisPlanSpec:
        if (self.analysis_spec_id is None) != (self.analysis_spec_sha256 is None):
            raise ValueError("dataset plans must bind both analysis spec identity fields")
        dataset_inputs = (self.steps[0].inputs, self.steps[1].inputs, self.steps[2].inputs)
        if any(
            step_input.dataset_source_id != self.dataset_source_id
            or step_input.dataset_content_hash != self.dataset_content_hash
            for step_input in dataset_inputs
        ):
            raise ValueError("every dataset-bound step must use the plan's dataset identity")
        if self.steps[2].inputs.expected_outputs != self.steps[3].inputs.expected_outputs:
            raise ValueError("artifact collection must bind the execution expected outputs")
        if self.steps[0].expected_artifacts != ("dataset-profile",):
            raise ValueError("dataset inspection must produce exactly the dataset profile")
        if self.steps[1].expected_artifacts != ("analysis-intent",):
            raise ValueError("analysis preparation must produce exactly one analysis intent")

        execution_output_sequence = self.steps[2].inputs.expected_outputs
        execution_outputs = set(execution_output_sequence)
        required_outputs = {
            "executed-notebook",
            "analysis-log",
            "environment-manifest",
        }
        if not required_outputs.issubset(execution_outputs):
            raise ValueError(
                "execution outputs must include the notebook, analysis log, and "
                "environment manifest"
            )
        if execution_outputs & {"dataset-profile", "analysis-code"}:
            raise ValueError("execution outputs cannot claim planning-stage outputs")

        required_artifacts = _DATASET_ANALYSIS_EXECUTION_ARTIFACT_SEQUENCES[
            execution_output_sequence
        ]
        if self.steps[2].expected_artifacts != required_artifacts:
            raise ValueError("execution artifacts must exactly match the declared outputs")
        if self.steps[3].expected_artifacts != self.steps[2].expected_artifacts:
            raise ValueError("artifact collection must bind the execution artifacts")
        return self


class PaperDiscoveryStepInput(StrictApiModel):
    schema_version: Literal["1"]
    discovery_spec_id: str = Field(min_length=1, max_length=36)
    discovery_spec_revision: int = Field(ge=1)
    discovery_spec_sha256: Sha256
    query_id: QueryId
    query: QueryText
    provider: DiscoveryProvider
    year_from: int | None = Field(default=None, ge=1800, le=2100)
    year_to: int | None = Field(default=None, ge=1800, le=2100)
    sort: DiscoverySort
    max_results_per_provider: int = Field(ge=1, le=50)
    derived_maximum_results: int = Field(ge=1, le=200)
    stop_policy: DiscoveryStopPolicy
    download_open_access_pdfs: Literal[False]
    max_pdf_downloads: Literal[0]

    @model_validator(mode="after")
    def validate_exact_operation_scope(self) -> PaperDiscoveryStepInput:
        if self.year_from is not None and self.year_to is not None:
            if self.year_from > self.year_to:
                raise ValueError("yearFrom must not exceed yearTo")
        if self.derived_maximum_results != self.max_results_per_provider:
            raise ValueError(
                "a provider-specific discovery step must bind its exact single-provider budget"
            )
        return self


class PaperDiscoveryPlanStep(StrictApiModel):
    key: StepKey
    order_index: int = Field(ge=1, le=32)
    objective: str = Field(min_length=1, max_length=2_000)
    task_type: Literal["paper-discovery"]
    inputs: PaperDiscoveryStepInput
    expected_outputs: tuple[Literal["discovery-observation"]]
    acceptance_criteria: tuple[Literal["persist-structured-discovery-observation"]]
    permissions: tuple[Literal["remote-paper-search"]]
    risk_level: Literal["medium"]
    timeout_seconds: Literal[120]

    @property
    def type(self) -> Literal["paper-discovery"]:
        """Expose the canonical task type used by shared plan integrity code."""
        return self.task_type


class PaperDiscoveryPlanSpec(StrictApiModel):
    schema_version: Literal["1"]
    plan_type: Literal["paper-discovery"]
    goal: str = Field(min_length=3, max_length=2_000)
    discovery_spec_id: str = Field(min_length=1, max_length=36)
    discovery_spec_revision: int = Field(ge=1)
    discovery_spec_sha256: Sha256
    steps: list[PaperDiscoveryPlanStep] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_exact_material_scope(self) -> PaperDiscoveryPlanSpec:
        if [step.order_index for step in self.steps] != list(
            range(1, len(self.steps) + 1)
        ):
            raise ValueError("paper discovery plan order indexes must be contiguous")
        if len({step.key for step in self.steps}) != len(self.steps):
            raise ValueError("paper discovery plan step keys must be unique")
        for step in self.steps:
            inputs = step.inputs
            if (
                inputs.discovery_spec_id != self.discovery_spec_id
                or inputs.discovery_spec_revision != self.discovery_spec_revision
                or inputs.discovery_spec_sha256 != self.discovery_spec_sha256
            ):
                raise ValueError(
                    "every paper discovery step must bind the plan discovery specification"
                )
        return self


ResearchPlanSpec = PlanSpec | DatasetAnalysisPlanSpec | PaperDiscoveryPlanSpec


class ModelInspectStepProposal(StrictApiModel):
    type: Literal["inspect-sources"]
    objective: str = Field(min_length=1, max_length=2_000)


class ModelEvidenceStepProposal(StrictApiModel):
    type: Literal["extract-local-evidence"]
    objective: str = Field(min_length=1, max_length=2_000)
    query: str = Field(min_length=2, max_length=8_000)
    max_passages: int = Field(default=12, ge=1, le=40)
    max_per_source: int = Field(default=4, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class ModelSynthesisStepProposal(StrictApiModel):
    type: Literal["synthesize-extractive-claims"]
    objective: str = Field(min_length=1, max_length=2_000)
    max_claims: int = Field(default=8, ge=1, le=20)


class ModelPlanProposal(StrictApiModel):
    schema_version: Literal["1"] = "1"
    steps: list[
        ModelInspectStepProposal | ModelEvidenceStepProposal | ModelSynthesisStepProposal
    ] = Field(min_length=3, max_length=3)

    @field_validator("steps")
    @classmethod
    def validate_frozen_sequence(
        cls,
        value: list[
            ModelInspectStepProposal | ModelEvidenceStepProposal | ModelSynthesisStepProposal
        ],
    ) -> list[ModelInspectStepProposal | ModelEvidenceStepProposal | ModelSynthesisStepProposal]:
        expected = [
            "inspect-sources",
            "extract-local-evidence",
            "synthesize-extractive-claims",
        ]
        if [step.type for step in value] != expected:
            raise ValueError("model plan must preserve the frozen three-step sequence")
        return value


class ModelClaimProposal(StrictApiModel):
    statement: str = Field(min_length=20, max_length=2_000)
    evidence_id: str = Field(min_length=1, max_length=36)
    passage: str = Field(min_length=20, max_length=20_000)

    @field_validator("statement", "passage")
    @classmethod
    def reject_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("extractive text must not contain surrounding whitespace")
        return value


class ModelSynthesisProposal(StrictApiModel):
    schema_version: Literal["1"] = "1"
    claims: list[ModelClaimProposal] = Field(min_length=1, max_length=20)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("unresolved_questions")
    @classmethod
    def validate_questions(cls, value: list[str]) -> list[str]:
        normalized = [" ".join(item.split()) for item in value]
        if any(not item or len(item) > 1_000 for item in normalized):
            raise ValueError("unresolved questions must be non-empty and at most 1000 characters")
        if any(not item.endswith(("?", "？")) for item in normalized):
            raise ValueError("unresolved questions must be explicitly phrased as questions")
        return normalized


class WorkflowCreateIn(StrictApiModel):
    goal: str = Field(min_length=2, max_length=8_000)
    workflow_type: Literal["literature-synthesis"] = "literature-synthesis"
    generation_mode: GenerationMode = "local-deterministic"
    remote_data_approved: StrictBool = False

    @field_validator("goal")
    @classmethod
    def reject_blank_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def require_explicit_remote_approval(self) -> WorkflowCreateIn:
        if self.generation_mode == "remote-model-assisted" and not self.remote_data_approved:
            raise ValueError(
                "remote_data_approved must be true before the research goal is sent "
                "to the configured remote model"
            )
        if self.generation_mode == "local-deterministic" and self.remote_data_approved:
            raise ValueError(
                "remote_data_approved is only valid for remote-model-assisted generation"
            )
        return self


class DatasetWorkflowCreateIn(StrictApiModel):
    goal: str = Field(min_length=2, max_length=8_000)
    workflow_type: Literal["dataset-analysis"]
    dataset_source_id: str = Field(min_length=1, max_length=36)
    generation_mode: GenerationMode = "local-deterministic"
    remote_data_approved: StrictBool = False

    @field_validator("goal")
    @classmethod
    def reject_blank_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def require_explicit_remote_approval(self) -> DatasetWorkflowCreateIn:
        if self.generation_mode != "local-deterministic":
            raise ValueError("dataset-analysis currently supports local-deterministic only")
        if self.remote_data_approved:
            raise ValueError("remote_data_approved is not valid for local dataset analysis")
        return self


def _default_literature_workflow_type(value: object) -> object:
    if isinstance(value, dict) and "workflow_type" not in value and "workflowType" not in value:
        return {
            **cast(dict[str, object], value),
            "workflowType": "literature-synthesis",
        }
    if isinstance(value, dict):
        return cast(dict[object, object], value)
    return value


ResearchWorkflowCreateIn = Annotated[
    WorkflowCreateIn | DatasetWorkflowCreateIn,
    Field(discriminator="workflow_type"),
    BeforeValidator(_default_literature_workflow_type),
]


class ApprovePlanIn(StrictApiModel):
    approval_id: str = Field(min_length=1, max_length=36)
    plan_id: str = Field(min_length=1, max_length=36)
    plan_version: int = Field(ge=1)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_workflow_revision: int = Field(ge=1)


class WorkflowMutationIn(StrictApiModel):
    expected_workflow_revision: int | None = Field(default=None, ge=1)


class RetryWorkflowIn(WorkflowMutationIn):
    task_id: str | None = Field(default=None, max_length=36)


class WorkflowAnalysisDecisionIn(StrictApiModel):
    approval_id: str = Field(min_length=1, max_length=36)
    decision: Literal["approved", "rejected"]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_workflow_revision: int = Field(ge=1)


class AcceptReviewWarningsIn(StrictApiModel):
    review_id: str = Field(min_length=1, max_length=36)
    review_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_workflow_revision: int = Field(ge=1)
    decision: Literal["accepted"]


class BlockingReasonOut(ApiModel):
    code: str
    user_message: str
    retryable: bool


class WorkflowStateOut(ApiModel):
    id: str
    project_id: str
    workflow_type: WorkflowType
    dataset_source_id: str | None = None
    dataset_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    goal: str
    generation_mode: GenerationMode
    status: WorkflowStatus
    revision: int
    plan_version: int | None
    current_step_id: str | None
    retry_count: int
    blocking_reason: BlockingReasonOut | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @model_validator(mode="after")
    def validate_dataset_identity(self) -> WorkflowStateOut:
        has_source = self.dataset_source_id is not None
        has_hash = self.dataset_content_hash is not None
        if self.workflow_type == "dataset-analysis" and not (has_source and has_hash):
            raise ValueError("dataset-analysis workflow snapshots require immutable identity")
        if self.workflow_type == "literature-synthesis" and (has_source or has_hash):
            raise ValueError("literature workflow snapshots cannot carry dataset identity")
        return self


class MaterializedStepOut(StrictApiModel):
    id: str = Field(min_length=1, max_length=36)
    key: StepKey
    order_index: int = Field(ge=0)
    type: TaskStepType
    objective: str = Field(min_length=1, max_length=2_000)
    status: TaskStatus
    retry_count: int = Field(ge=0)
    started_at: datetime | None
    completed_at: datetime | None
    output_summary: str | None


class PlanSnapshotOut(StrictApiModel):
    id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)
    status: PlanStatus
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator: str
    model: str | None
    prompt_version: str | None
    spec: ResearchPlanSpec
    steps: list[MaterializedStepOut]
    created_at: datetime
    approved_at: datetime | None

    @model_validator(mode="after")
    def validate_materialized_steps(self) -> PlanSnapshotOut:
        if isinstance(self.spec, PaperDiscoveryPlanSpec):
            if self.status == "pending-approval":
                if self.steps:
                    raise ValueError(
                        "pending paper discovery plans cannot materialize executable steps"
                    )
                return self
            if len(self.steps) != len(self.spec.steps):
                raise ValueError(
                    "approved paper discovery plans require every declared operation"
                )
            for declared_index, (materialized, declared) in enumerate(
                zip(self.steps, self.spec.steps, strict=True),
                start=1,
            ):
                if (
                    materialized.order_index != declared_index
                    or materialized.key != declared.key
                    or materialized.type != declared.task_type
                    or materialized.objective != declared.objective
                ):
                    raise ValueError(
                        "materialized discovery steps must exactly match the approved plan spec"
                    )
            return self
        if not isinstance(self.spec, DatasetAnalysisPlanSpec):
            return self
        if len(self.steps) != len(self.spec.steps):
            raise ValueError("dataset plans require exactly four materialized steps")
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("materialized dataset step identifiers must be unique")
        for order_index, (materialized, declared) in enumerate(
            zip(self.steps, self.spec.steps, strict=True)
        ):
            if (
                materialized.order_index != order_index
                or materialized.key != declared.key
                or materialized.type != declared.type
                or materialized.objective != declared.objective
            ):
                raise ValueError(
                    "materialized dataset steps must exactly match the approved plan spec"
                )
        return self


class PendingApprovalOut(StrictApiModel):
    id: str
    workflow_id: str
    plan_id: str
    task_id: None
    kind: Literal["plan"]
    status: Literal["waiting"]
    subject_type: Literal["plan"]
    subject_id: str
    action: str
    payload_sha256: str
    risk_level: WorkflowRiskLevel
    reason: str
    affected_resources: list[str]
    created_at: datetime
    decided_at: datetime | None


class DatasetPlanPendingApprovalOut(StrictApiModel):
    id: str
    workflow_id: str
    plan_id: str
    task_id: None
    kind: Literal["plan"]
    status: Literal["waiting"]
    subject_type: Literal["plan"]
    subject_id: str
    workflow_type: Literal["dataset-analysis"]
    action: Literal["approve-plan"]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_level: Literal["medium"]
    reason: str
    affected_resources: list[str]
    approval_schema_version: Literal["workflow-plan-approval-v3"]
    plan_version: int = Field(ge=1)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_workflow_revision: int = Field(ge=1)
    dataset_source_id: str = Field(min_length=1, max_length=36)
    dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    decided_at: datetime | None

    @model_validator(mode="after")
    def validate_plan_subject(self) -> DatasetPlanPendingApprovalOut:
        if self.subject_id != self.plan_id:
            raise ValueError("dataset plan approval subject must be the exact plan")
        return self


class AnalysisExecutionPendingApprovalOut(StrictApiModel):
    id: str
    workflow_id: str
    plan_id: str
    task_id: str
    kind: Literal["analysis-execution"]
    status: Literal["waiting"]
    subject_type: Literal["analysis-intent"]
    subject_id: str
    action: Literal["execute-python-data-analysis"]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_level: Literal["high"]
    reason: str
    affected_resources: list[str]
    approval_schema_version: Literal[
        "analysis-intent-v2", "analysis-intent-v3", "analysis-intent-v4"
    ]
    expected_workflow_revision: int = Field(ge=1)
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    plan_step_id: Literal["execute-analysis"]
    dataset_source_id: str = Field(min_length=1, max_length=36)
    dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_outputs: tuple[DatasetAnalysisExpectedOutput, ...] = Field(min_length=1)
    timeout_seconds: int = Field(ge=1, le=3_600)
    code: str = Field(min_length=1, max_length=200_000)
    code_diff: str | None = Field(default=None, max_length=200_000)
    analysis_spec_id: str | None = Field(default=None, min_length=1, max_length=36)
    spec_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dataset_profile_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    compiler_version: str | None = Field(default=None, min_length=1, max_length=100)
    code_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime_policy_id: str | None = Field(default=None, min_length=1, max_length=100)
    created_at: datetime
    decided_at: datetime | None

    @model_validator(mode="after")
    def validate_analysis_subject(self) -> AnalysisExecutionPendingApprovalOut:
        if self.subject_id != self.analysis_intent_id:
            raise ValueError("analysis approval subject must be the exact intent")
        if self.expected_outputs not in _DATASET_ANALYSIS_EXECUTION_OUTPUT_SEQUENCES:
            raise ValueError(
                "analysis approval must bind the canonical mandatory execution outputs"
            )
        provenance = (
            self.analysis_spec_id,
            self.spec_sha256,
            self.dataset_profile_sha256,
            self.compiler_version,
            self.code_sha256,
            self.runtime_policy_id,
        )
        if self.approval_schema_version == "analysis-intent-v4":
            if any(value is None for value in provenance):
                raise ValueError("v4 analysis approval requires compiled provenance")
        elif any(value is not None for value in provenance):
            raise ValueError("legacy analysis approval cannot expose compiled provenance")
        return self


WorkflowPendingApprovalOut = (
    PendingApprovalOut | DatasetPlanPendingApprovalOut | AnalysisExecutionPendingApprovalOut
)


class EvidenceRelationshipOut(ApiModel):
    evidence_id: str
    source_id: str
    source_title: str | None
    source_content_hash: str | None
    source_page_manifest_hash: str | None
    page_index: int
    page_label: str | None
    text: str
    bbox: BoundingBoxOut | None
    coordinate_space: Literal["normalized-rotated-top-left-v1"]
    quote_hash: str
    extraction_method: str
    confidence: float
    verified: bool
    relationship: Literal["supporting", "contradicting"]


class WorkflowClaimOut(ApiModel):
    id: str
    statement: str
    support_status: ClaimSupportStatus
    confidence: float
    evidence: list[EvidenceRelationshipOut]


class WorkflowResultOut(ApiModel):
    answer_id: str
    summary: str
    generator: str
    model: str | None
    prompt_version: str | None
    integrity_status: Literal["verified-frozen-v2", "unfrozen"]
    claims: list[WorkflowClaimOut]
    unresolved_questions: list[str]


class ReviewCheck(StrictApiModel):
    code: str = Field(min_length=1, max_length=100)
    status: Literal["passed", "failed"]
    message: str = Field(min_length=1, max_length=1_000)
    claim_id: str | None = None
    evidence_id: str | None = None


class ClaimReviewResult(StrictApiModel):
    claim_id: str
    status: ClaimSupportStatus
    evidence_ids: list[str]
    relationships: list[Literal["supporting", "contradicting"]]


class DeterministicReviewResult(StrictApiModel):
    schema_version: Literal["1", "2"] = "1"
    verdict: LiteratureReviewVerdict
    checks: list[ReviewCheck]
    claim_results: list[ClaimReviewResult]
    required_revisions: list[str]
    result_snapshot_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    result_snapshot: WorkflowResultOut | None = None

    @model_validator(mode="after")
    def validate_result_snapshot_binding(self) -> DeterministicReviewResult:
        if self.schema_version == "1" and (
            self.result_snapshot_sha256 is not None or self.result_snapshot is not None
        ):
            raise ValueError("review result schema 1 cannot contain a frozen result snapshot")
        if self.schema_version == "2" and (
            self.result_snapshot_sha256 is None or self.result_snapshot is None
        ):
            raise ValueError("review result schema 2 requires an immutable result snapshot")
        if (self.result_snapshot_sha256 is None) != (self.result_snapshot is None):
            raise ValueError("result snapshot and its hash must be stored together")
        return self


class DatasetAnalysisReviewCheck(StrictApiModel):
    code: str = Field(min_length=1, max_length=100)
    status: Literal["passed", "warning", "failed"]
    message: str = Field(min_length=1, max_length=1_000)
    artifact_id: str | None


class DatasetAnalysisReviewIssue(StrictApiModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1_000)
    artifact_id: str | None


class DatasetAnalysisReviewResult(StrictApiModel):
    schema_version: Literal["1"]
    verdict: DatasetAnalysisReviewVerdict
    checks: list[DatasetAnalysisReviewCheck]
    artifact_issues: list[DatasetAnalysisReviewIssue]
    numeric_issues: list[DatasetAnalysisReviewIssue]
    method_warnings: list[DatasetAnalysisReviewIssue]
    required_revisions: list[NonEmptyPlanText]
    run_id: str = Field(min_length=1, max_length=36)
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    input_dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    conclusion: str | None = Field(default=None, min_length=1, max_length=8_000)
    analysis_spec_id: str | None = Field(default=None, min_length=1, max_length=36)
    structured_result_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_verdict_evidence(self) -> DatasetAnalysisReviewResult:
        compiled_review_fields = (
            self.conclusion,
            self.analysis_spec_id,
            self.structured_result_sha256,
        )
        if any(value is None for value in compiled_review_fields) and any(
            value is not None for value in compiled_review_fields
        ):
            raise ValueError(
                "compiled review conclusion and result identity must be stored together"
            )
        has_warning = any(check.status == "warning" for check in self.checks) or bool(
            self.method_warnings
        )
        has_failure = any(check.status == "failed" for check in self.checks) or bool(
            self.artifact_issues or self.numeric_issues
        )
        if self.verdict == "passed" and (has_warning or has_failure):
            raise ValueError("a passed review cannot contain warnings or failures")
        if self.verdict == "passed-with-warnings" and (not has_warning or has_failure):
            raise ValueError("passed-with-warnings requires a warning and cannot contain failures")
        if self.verdict == "revision-required" and not (has_failure and self.required_revisions):
            raise ValueError(
                "revision-required needs a failed check or issue and a required revision"
            )
        if self.verdict in {"passed", "passed-with-warnings"} and self.required_revisions:
            raise ValueError("successful reviews cannot require revisions")
        return self


class ReviewSnapshotOut(StrictApiModel):
    id: str
    review_type: ReviewType
    verdict: ReviewVerdict
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: DeterministicReviewResult | DatasetAnalysisReviewResult
    created_at: datetime

    @model_validator(mode="after")
    def validate_review_contract(self) -> ReviewSnapshotOut:
        if self.verdict != self.result.verdict:
            raise ValueError("review verdict must match the persisted result verdict")
        if self.review_type == "deterministic-analysis-v1":
            if not isinstance(self.result, DatasetAnalysisReviewResult):
                raise ValueError("analysis reviews require an analysis review result")
            return self
        if not isinstance(self.result, DeterministicReviewResult):
            raise ValueError("claims reviews require a claims review result")
        expected_schema = "1" if self.review_type == "deterministic-claims-v1" else "2"
        if self.result.schema_version != expected_schema:
            raise ValueError("claims review type must match its result schema version")
        return self


class AnalysisErrorSummaryOut(StrictApiModel):
    schema_version: Literal["1"]
    category: Literal[
        "policy",
        "runtime",
        "timeout",
        "input-integrity",
        "artifact-integrity",
        "unknown",
    ]
    code: str = Field(min_length=1, max_length=100)
    user_message: str = Field(min_length=1, max_length=2_000)
    stderr_excerpt: str | None = Field(default=None, max_length=4_000)
    retryable: StrictBool


class WorkflowAnalysisIntentOut(StrictApiModel):
    id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    dataset_source_id: str = Field(min_length=1, max_length=36)
    dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    objective: str = Field(min_length=1, max_length=8_000)
    code: str = Field(min_length=1, max_length=200_000)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_level: Literal["high"]
    affected_resources: list[str]
    status: Literal[
        "waiting-approval",
        "approved",
        "rejected",
        "executing",
        "completed",
        "failed",
    ]
    decision: Literal["approved", "rejected"] | None
    workflow_id: str = Field(min_length=1, max_length=36)
    plan_step_id: Literal["execute-analysis"]
    previous_intent_id: str | None = Field(default=None, max_length=36)
    expected_outputs: tuple[DatasetAnalysisExpectedOutput, ...] = Field(min_length=1)
    timeout_seconds: int = Field(ge=1, le=3_600)
    repair_attempt: Literal[0, 1, 2]
    error_summary: AnalysisErrorSummaryOut | None
    code_diff: str | None = Field(default=None, max_length=200_000)
    analysis_spec_id: str | None = Field(default=None, min_length=1, max_length=36)
    spec_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dataset_profile_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    compiler_version: str | None = Field(default=None, min_length=1, max_length=100)
    code_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime_policy_id: str | None = Field(default=None, min_length=1, max_length=100)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_workflow_intent(self) -> WorkflowAnalysisIntentOut:
        provenance = (
            self.analysis_spec_id,
            self.spec_sha256,
            self.dataset_profile_sha256,
            self.compiler_version,
            self.code_sha256,
            self.runtime_policy_id,
        )
        if any(value is None for value in provenance) and any(
            value is not None for value in provenance
        ):
            raise ValueError("compiled intent provenance must be all present or all absent")
        if self.analysis_spec_id is not None and self.repair_attempt != 0:
            raise ValueError("compiled AnalysisSpec intents cannot use repair lineage")
        if self.expected_outputs not in _DATASET_ANALYSIS_EXECUTION_OUTPUT_SEQUENCES:
            raise ValueError("workflow intents require the canonical execution outputs")
        if self.repair_attempt == 0 and (
            self.previous_intent_id is not None or self.code_diff is not None
        ):
            raise ValueError("the initial analysis intent cannot contain repair lineage")
        if self.repair_attempt > 0 and (
            self.previous_intent_id is None
            or self.error_summary is None
            or self.code_diff is None
            or not self.code_diff.strip()
        ):
            raise ValueError("repair intents must bind the prior failure and code diff")
        if self.repair_attempt == 0 and self.status != "failed" and self.error_summary is not None:
            raise ValueError("an initial intent can record an error only after failure")
        if self.status == "failed" and self.error_summary is None:
            raise ValueError("a failed workflow intent requires a safe error summary")
        if self.status == "rejected" and self.decision != "rejected":
            raise ValueError("a rejected intent must record the rejected decision")
        if self.status in {"approved", "executing", "completed", "failed"} and (
            self.decision != "approved"
        ):
            raise ValueError("an executable intent must record the approved decision")
        if self.status == "waiting-approval" and self.decision is not None:
            raise ValueError("a waiting intent cannot already have a decision")
        return self


class AnalysisSpecSnapshotOut(StrictApiModel):
    id: str = Field(min_length=1, max_length=36)
    revision: int = Field(ge=1)
    status: Literal["pending-approval", "approved", "superseded", "rejected"]
    selector_kind: Literal["local-deterministic", "remote-model-assisted"]
    selector_reason: str = Field(min_length=1, max_length=2_000)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=100)
    dataset_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec: dict[str, object]
    created_at: datetime


class StructuredAnalysisResultSnapshotOut(StrictApiModel):
    id: str = Field(min_length=1, max_length=36)
    analysis_spec_id: str = Field(min_length=1, max_length=36)
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: dict[str, object]
    created_at: datetime


class WorkflowAnalysisArtifactOut(StrictApiModel):
    id: str = Field(min_length=1, max_length=36)
    artifact_type: DatasetAnalysisRuntimeArtifactType
    path: str = Field(min_length=1, max_length=4_096)
    mime_type: str = Field(min_length=1, max_length=200)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    created_at: datetime


class WorkflowAnalysisRunOut(StrictApiModel):
    id: str = Field(min_length=1, max_length=36)
    intent_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    dataset_source_id: str = Field(min_length=1, max_length=36)
    objective: str = Field(min_length=1, max_length=8_000)
    code: str = Field(min_length=1, max_length=100_000)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "running", "completed", "failed"]
    environment_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_artifacts: list[str]
    output_artifacts: list[str]
    stdout: str
    stderr: str
    log: str
    logs: str
    error: str | None
    artifacts: list[WorkflowAnalysisArtifactOut]
    created_at: datetime
    finished_at: datetime | None

    @model_validator(mode="after")
    def validate_terminal_state(self) -> WorkflowAnalysisRunOut:
        if len({artifact.id for artifact in self.artifacts}) != len(self.artifacts):
            raise ValueError("analysis artifact identifiers must be unique")
        if len({artifact.path for artifact in self.artifacts}) != len(self.artifacts):
            raise ValueError("analysis artifact paths must be unique")
        if self.status == "completed" and (
            self.environment_hash is None or self.finished_at is None or self.error is not None
        ):
            raise ValueError("a completed analysis run requires terminal integrity fields")
        if self.status == "completed":
            artifact_types = {artifact.artifact_type for artifact in self.artifacts}
            required_types = {
                "notebook-executed",
                "environment",
                "stdout",
                "stderr",
                "log",
            }
            if not required_types.issubset(artifact_types):
                raise ValueError("a completed analysis run requires all runtime audit artifacts")
            environment_artifacts = [
                artifact for artifact in self.artifacts if artifact.artifact_type == "environment"
            ]
            if (
                len(environment_artifacts) != 1
                or environment_artifacts[0].content_hash != self.environment_hash
            ):
                raise ValueError("the environment artifact must bind the run environment hash")
            artifact_paths = {artifact.path for artifact in self.artifacts}
            if (
                len(self.output_artifacts) != len(set(self.output_artifacts))
                or set(self.output_artifacts) != artifact_paths
            ):
                raise ValueError("run output_artifacts must exactly list its artifact records")
        if self.status == "failed" and (self.finished_at is None or not self.error):
            raise ValueError("a failed analysis run requires a safe terminal error")
        if self.status in {"pending", "running"} and self.finished_at is not None:
            raise ValueError("a non-terminal analysis run cannot have a finished time")
        return self


class DatasetReviewWarningAcceptanceOut(StrictApiModel):
    event_id: str = Field(min_length=1, max_length=36)
    review_id: str = Field(min_length=1, max_length=36)
    review_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_workflow_revision: int = Field(ge=1)
    decision: Literal["accepted"]
    accepted_at: datetime


class ResearchWorkflowSnapshot(StrictApiModel):
    workflow: WorkflowStateOut
    plan: PlanSnapshotOut | None
    pending_approvals: list[WorkflowPendingApprovalOut]
    result: WorkflowResultOut | None
    latest_review: ReviewSnapshotOut | None
    dataset_profile: DatasetProfile | None = None
    analysis_intent: WorkflowAnalysisIntentOut | None = None
    analysis_run: WorkflowAnalysisRunOut | None = None
    analysis_spec: AnalysisSpecSnapshotOut | None = None
    structured_result: StructuredAnalysisResultSnapshotOut | None = None
    review_warning_acceptance: DatasetReviewWarningAcceptanceOut | None = None
    allowed_actions: list[AllowedAction]
    event_cursor: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_workflow_contract(self) -> ResearchWorkflowSnapshot:
        if self.plan is not None and self.plan.workflow_id != self.workflow.id:
            raise ValueError("the plan must belong to the snapshot workflow")
        if (self.plan is None) != (self.workflow.plan_version is None):
            raise ValueError("workflow plan_version and the current plan must appear together")
        if self.plan is not None and self.workflow.plan_version != self.plan.version:
            raise ValueError("workflow plan_version must match the current plan")
        if self.workflow.current_step_id is not None and (
            self.plan is None
            or self.workflow.current_step_id not in {step.id for step in self.plan.steps}
        ):
            raise ValueError("workflow current_step_id must belong to the current plan")
        if any(approval.workflow_id != self.workflow.id for approval in self.pending_approvals):
            raise ValueError("every approval must belong to the snapshot workflow")

        if self.workflow.workflow_type == "dataset-analysis":
            if any(
                not isinstance(
                    approval,
                    (DatasetPlanPendingApprovalOut, AnalysisExecutionPendingApprovalOut),
                )
                for approval in self.pending_approvals
            ):
                raise ValueError("dataset workflows require content-bound approval envelopes")
            if self.result is not None:
                raise ValueError("dataset workflow results are represented by intent and run data")
            if self.plan is not None:
                if not isinstance(self.plan.spec, DatasetAnalysisPlanSpec):
                    raise ValueError("dataset workflows require the fixed dataset analysis plan")
                if (
                    self.plan.spec.goal != self.workflow.goal
                    or self.plan.spec.dataset_source_id != self.workflow.dataset_source_id
                    or self.plan.spec.dataset_content_hash != self.workflow.dataset_content_hash
                ):
                    raise ValueError("the dataset plan must bind the workflow dataset identity")
            if self.dataset_profile is not None and (
                self.dataset_profile.dataset_source_id != self.workflow.dataset_source_id
                or self.dataset_profile.content_hash != self.workflow.dataset_content_hash
            ):
                raise ValueError("the dataset profile must bind the workflow dataset identity")
            if self.analysis_intent is not None:
                if self.plan is None:
                    raise ValueError("a workflow analysis intent requires its materialized plan")
                if self.plan.status != "approved":
                    raise ValueError("analysis intents require an approved dataset plan")
                if not isinstance(self.plan.spec, DatasetAnalysisPlanSpec):
                    raise ValueError("analysis intents require the fixed dataset plan")
                execute_task = self.plan.steps[2]
                execute_spec = self.plan.spec.steps[2]
                if (
                    self.analysis_intent.workflow_id != self.workflow.id
                    or self.analysis_intent.project_id != self.workflow.project_id
                    or self.analysis_intent.dataset_source_id != self.workflow.dataset_source_id
                    or self.analysis_intent.dataset_content_hash
                    != self.workflow.dataset_content_hash
                    or self.analysis_intent.task_id != execute_task.id
                    or self.analysis_intent.objective != execute_spec.objective
                    or self.analysis_intent.expected_outputs != execute_spec.inputs.expected_outputs
                    or self.analysis_intent.timeout_seconds != execute_spec.inputs.timeout_seconds
                ):
                    raise ValueError(
                        "the analysis intent must bind the workflow dataset and execute task"
                    )
            if self.analysis_run is not None:
                if self.analysis_intent is None:
                    raise ValueError("an analysis run requires its exact workflow intent")
                if (
                    self.analysis_run.intent_id != self.analysis_intent.id
                    or self.analysis_run.task_id != self.analysis_intent.task_id
                    or self.analysis_run.project_id != self.workflow.project_id
                    or self.analysis_run.dataset_source_id != self.workflow.dataset_source_id
                    or self.analysis_run.payload_sha256 != self.analysis_intent.payload_sha256
                    or self.analysis_run.objective != self.analysis_intent.objective
                    or self.analysis_run.code != self.analysis_intent.code
                ):
                    raise ValueError("the analysis run must bind its exact workflow intent")
                expected_intent_status = (
                    "executing"
                    if self.analysis_run.status in {"pending", "running"}
                    else self.analysis_run.status
                )
                if self.analysis_intent.status != expected_intent_status:
                    raise ValueError("analysis run and intent lifecycle states must agree")
                if self.analysis_run.input_artifacts != [self.workflow.dataset_source_id]:
                    raise ValueError("the analysis run must use only the approved dataset input")
                actual_artifact_types = {
                    artifact.artifact_type for artifact in self.analysis_run.artifacts
                }
                required_artifact_types = {
                    "notebook-executed",
                    "environment",
                    "stdout",
                    "stderr",
                    "log",
                }
                allowed_artifact_types = required_artifact_types | {"notebook-input"}
                if "summary-table" in self.analysis_intent.expected_outputs:
                    required_artifact_types.add("dataset")
                    allowed_artifact_types.add("dataset")
                if "figures" in self.analysis_intent.expected_outputs:
                    required_artifact_types.add("figure")
                    allowed_artifact_types.add("figure")
                if self.analysis_intent.analysis_spec_id is not None:
                    required_artifact_types.add("structured-data")
                    allowed_artifact_types.add("structured-data")
                if self.analysis_run.status == "completed" and (
                    not required_artifact_types.issubset(actual_artifact_types)
                    or not actual_artifact_types.issubset(allowed_artifact_types)
                ):
                    raise ValueError(
                        "completed run artifacts must exactly respect the declared outputs"
                    )
            elif self.analysis_intent is not None and self.analysis_intent.status not in {
                "waiting-approval",
                "approved",
                "rejected",
            }:
                raise ValueError("an executing or terminal workflow intent requires its run")
            if self.analysis_spec is not None:
                if (
                    self.plan is None
                    or not isinstance(self.plan.spec, DatasetAnalysisPlanSpec)
                    or self.plan.spec.analysis_spec_id != self.analysis_spec.id
                    or self.plan.spec.analysis_spec_sha256 != self.analysis_spec.spec_sha256
                    or self.analysis_spec.spec.get("datasetSourceId")
                    != self.workflow.dataset_source_id
                    or self.analysis_spec.spec.get("datasetContentHash")
                    != self.workflow.dataset_content_hash
                    or self.analysis_spec.spec.get("datasetProfileHash")
                    != self.analysis_spec.dataset_profile_sha256
                ):
                    raise ValueError("the AnalysisSpec snapshot must bind the plan and dataset")
                if self.analysis_intent is not None and (
                    self.analysis_intent.analysis_spec_id != self.analysis_spec.id
                    or self.analysis_intent.spec_sha256 != self.analysis_spec.spec_sha256
                    or self.analysis_intent.dataset_profile_sha256
                    != self.analysis_spec.dataset_profile_sha256
                ):
                    raise ValueError(
                        "the compiled intent must bind the exact AnalysisSpec snapshot"
                    )
            elif (
                self.analysis_intent is not None
                and self.analysis_intent.analysis_spec_id is not None
            ):
                raise ValueError("a compiled intent requires its AnalysisSpec snapshot")
            if self.structured_result is not None:
                if (
                    self.analysis_spec is None
                    or self.analysis_intent is None
                    or self.analysis_run is None
                    or self.structured_result.analysis_spec_id != self.analysis_spec.id
                    or self.structured_result.analysis_intent_id != self.analysis_intent.id
                    or self.structured_result.run_id != self.analysis_run.id
                    or self.analysis_run.status != "completed"
                ):
                    raise ValueError(
                        "the structured result must bind the completed Spec, Intent, and Run"
                    )
            if self.latest_review is not None:
                if not isinstance(self.latest_review.result, DatasetAnalysisReviewResult):
                    raise ValueError("dataset workflows require deterministic analysis reviews")
                if self.analysis_intent is None or self.analysis_run is None:
                    raise ValueError("an analysis review requires its exact intent and run")
                if (
                    self.latest_review.result.analysis_intent_id != self.analysis_intent.id
                    or self.latest_review.result.run_id != self.analysis_run.id
                    or self.latest_review.result.input_dataset_content_hash
                    != self.workflow.dataset_content_hash
                ):
                    raise ValueError("the analysis review must bind the exact reviewed run")
                review_spec_id = self.latest_review.result.analysis_spec_id
                if review_spec_id is not None:
                    if (
                        self.analysis_spec is None
                        or self.structured_result is None
                        or review_spec_id != self.analysis_spec.id
                        or self.latest_review.result.structured_result_sha256
                        != self.structured_result.result_sha256
                    ):
                        raise ValueError(
                            "the compiled review must bind the exact Spec and structured result"
                        )
            if self.review_warning_acceptance is not None:
                if (
                    self.latest_review is None
                    or self.latest_review.verdict != "passed-with-warnings"
                    or self.review_warning_acceptance.review_id != self.latest_review.id
                    or self.review_warning_acceptance.review_input_sha256
                    != self.latest_review.input_sha256
                ):
                    raise ValueError(
                        "warning acceptance must bind a warning-bearing analysis review"
                    )
            for approval in self.pending_approvals:
                if isinstance(approval, DatasetPlanPendingApprovalOut):
                    if (
                        self.plan is None
                        or approval.dataset_source_id != self.workflow.dataset_source_id
                        or approval.dataset_content_hash != self.workflow.dataset_content_hash
                        or approval.plan_id != self.plan.id
                        or approval.plan_version != self.plan.version
                        or approval.plan_sha256 != self.plan.plan_sha256
                        or approval.expected_workflow_revision != self.workflow.revision
                        or self.plan.status != "pending-approval"
                        or self.analysis_intent is not None
                        or self.analysis_run is not None
                    ):
                        raise ValueError("dataset plan approval identity does not match")
                elif isinstance(approval, AnalysisExecutionPendingApprovalOut):
                    if (
                        self.plan is None
                        or approval.dataset_source_id != self.workflow.dataset_source_id
                        or approval.dataset_content_hash != self.workflow.dataset_content_hash
                        or self.analysis_intent is None
                        or approval.plan_id != self.plan.id
                        or approval.analysis_intent_id != self.analysis_intent.id
                        or approval.task_id != self.analysis_intent.task_id
                        or approval.payload_sha256 != self.analysis_intent.payload_sha256
                        or approval.plan_step_id != self.analysis_intent.plan_step_id
                        or approval.expected_outputs != self.analysis_intent.expected_outputs
                        or approval.timeout_seconds != self.analysis_intent.timeout_seconds
                        or approval.code != self.analysis_intent.code
                        or approval.code_diff != self.analysis_intent.code_diff
                        or approval.expected_workflow_revision != self.workflow.revision
                        or self.plan.status != "approved"
                        or self.analysis_intent.status != "waiting-approval"
                        or self.analysis_intent.decision is not None
                        or self.analysis_run is not None
                    ):
                        raise ValueError("analysis approval must bind the current exact intent")
            plan_approvals = [
                approval
                for approval in self.pending_approvals
                if isinstance(approval, DatasetPlanPendingApprovalOut)
            ]
            analysis_approvals = [
                approval
                for approval in self.pending_approvals
                if isinstance(approval, AnalysisExecutionPendingApprovalOut)
            ]
            plan_waiting = self.plan is not None and self.plan.status == "pending-approval"
            intent_waiting = (
                self.analysis_intent is not None
                and self.analysis_intent.status == "waiting-approval"
            )
            if plan_waiting != (len(plan_approvals) == 1):
                raise ValueError("a pending dataset plan requires its one exact approval")
            if intent_waiting != (len(analysis_approvals) == 1):
                raise ValueError("a waiting analysis intent requires its one exact approval")
            if len(self.pending_approvals) > 1:
                raise ValueError("dataset workflows can expose only one current approval")
            if self.workflow.status == "completed":
                if (
                    self.plan is None
                    or self.plan.status != "approved"
                    or any(step.status != "completed" for step in self.plan.steps)
                    or self.pending_approvals
                    or self.dataset_profile is None
                    or self.analysis_intent is None
                    or self.analysis_intent.status != "completed"
                    or self.analysis_run is None
                    or self.analysis_run.status != "completed"
                    or self.latest_review is None
                    or self.latest_review.verdict not in {"passed", "passed-with-warnings"}
                    or self.workflow.completed_at is None
                    or self.workflow.current_step_id is not None
                    or self.workflow.blocking_reason is not None
                    or self.workflow.cancel_requested_at is not None
                    or self.allowed_actions
                    or (
                        self.latest_review.verdict == "passed-with-warnings"
                        and self.review_warning_acceptance is None
                    )
                ):
                    raise ValueError(
                        "completed dataset workflows require the approved, executed, "
                        "reviewed, and fully materialized evidence chain"
                    )
            return self

        if any(type(approval) is not PendingApprovalOut for approval in self.pending_approvals):
            raise ValueError("literature workflows cannot contain dataset approvals")
        if self.plan is not None and not isinstance(
            self.plan.spec,
            (PlanSpec, PaperDiscoveryPlanSpec),
        ):
            raise ValueError("literature workflows require a literature synthesis plan")
        if self.plan is not None and self.plan.spec.goal != self.workflow.goal:
            raise ValueError("the literature plan goal must match its workflow")
        if isinstance(
            self.plan.spec if self.plan is not None else None,
            PaperDiscoveryPlanSpec,
        ):
            plan_waiting = self.plan is not None and self.plan.status == "pending-approval"
            if plan_waiting != (len(self.pending_approvals) == 1):
                raise ValueError(
                    "a pending paper discovery plan requires its one exact approval"
                )
            if self.result is not None or self.latest_review is not None:
                raise ValueError(
                    "discovery candidate metadata cannot become a literature result or review"
                )
        if self.latest_review is not None and not isinstance(
            self.latest_review.result, DeterministicReviewResult
        ):
            raise ValueError("literature workflows require deterministic claims reviews")
        if any(
            action not in {"approve-plan", "cancel", "retry", "resume"}
            for action in self.allowed_actions
        ):
            raise ValueError("literature workflows cannot expose dataset-only actions")
        if any(
            value is not None
            for value in (
                self.dataset_profile,
                self.analysis_intent,
                self.analysis_run,
                self.review_warning_acceptance,
            )
        ):
            raise ValueError("literature workflows cannot contain dataset analysis state")
        return self


class CreatedEventData(StrictApiModel):
    workflow_type: WorkflowType
    goal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_mode: GenerationMode = "local-deterministic"


class RemoteDataApprovalEventData(StrictApiModel):
    provider: Literal["openai-compatible"]
    endpoint_host: str = Field(min_length=1, max_length=253)
    endpoint_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model: str | None = Field(default=None, max_length=200)
    data_categories: list[RemoteDataCategory]

    @field_validator("data_categories")
    @classmethod
    def validate_disclosure_categories(
        cls,
        value: list[RemoteDataCategory],
    ) -> list[RemoteDataCategory]:
        if value not in (
            ["user-goal"],
            ["user-goal", "dataset-profile"],
            list(AUTONOMOUS_REMOTE_DATA_CATEGORIES),
        ):
            raise ValueError("data_categories must match a registered remote disclosure profile")
        return value


class StatusChangedEventData(StrictApiModel):
    previous_status: WorkflowStatus
    status: WorkflowStatus
    reason_code: str | None = None


class PlanEventData(StrictApiModel):
    plan_id: str
    version: int
    plan_sha256: str


class ApprovalEventData(StrictApiModel):
    approval_id: str
    subject_type: str
    subject_id: str
    action: str
    payload_sha256: str
    risk_level: str | None = None
    reason: str | None = None
    affected_resources: list[str] | None = None
    approval_schema_version: str | None = None


class TaskEventData(StrictApiModel):
    task_id: str
    step_key: str
    order_index: int
    status: TaskStatus
    output_count: int | None = None
    error_code: str | None = None


class JobEventData(StrictApiModel):
    job_id: str
    kind: str
    attempt: int
    error_code: str | None = None


class ReviewEventData(StrictApiModel):
    review_id: str
    verdict: ReviewVerdict
    claim_count: int | None = None


class CancelEventData(StrictApiModel):
    requested: bool


class AnalysisIntentCreatedEventData(StrictApiModel):
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    job_id: str = Field(min_length=1, max_length=36)
    plan_step_id: Literal["execute-analysis"]
    dataset_source_id: str = Field(min_length=1, max_length=36)
    dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repair_attempt: Literal[0, 1, 2]


class AnalysisApprovalEventData(StrictApiModel):
    approval_id: str = Field(min_length=1, max_length=36)
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    job_id: str | None = Field(default=None, max_length=36)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_schema_version: Literal[
        "analysis-intent-v2", "analysis-intent-v3", "analysis-intent-v4"
    ]
    expected_workflow_revision: int = Field(ge=1)


class AnalysisRunEventData(StrictApiModel):
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    job_id: str = Field(min_length=1, max_length=36)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifact_count: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, min_length=1, max_length=100)


class AnalysisRunProgressEventData(StrictApiModel):
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    job_id: str = Field(min_length=1, max_length=36)
    stage: Literal["preparing-input", "executing-runtime", "collecting-artifacts"]
    elapsed_seconds: float = Field(ge=0)


class AnalysisArtifactCreatedEventData(StrictApiModel):
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    job_id: str = Field(min_length=1, max_length=36)
    artifact_id: str = Field(min_length=1, max_length=36)
    artifact_type: DatasetAnalysisRuntimeArtifactType
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str = Field(min_length=1, max_length=4_096)


class DatasetReviewWarningsAcceptedEventData(StrictApiModel):
    review_id: str = Field(min_length=1, max_length=36)
    review_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_workflow_revision: int = Field(ge=1)
    decision: Literal["accepted"]


class AgentRunCreatedEventData(StrictApiModel):
    goal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ids: list[str] = Field(max_length=100)
    mode: Literal["autonomous"]
    generation_mode: GenerationMode


class IntentDecisionEventData(StrictApiModel):
    intent_decision_id: str = Field(min_length=1, max_length=36)
    intent: Literal[
        "literature-synthesis",
        "dataset-analysis",
        "mixed-research",
        "clarification-required",
        "unsupported",
    ]
    confidence: float = Field(ge=0, le=1)
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalysisClarificationRequestedEventData(StrictApiModel):
    interaction_id: str = Field(min_length=1, max_length=36)
    clarification_type: str = Field(min_length=1, max_length=100)
    selector_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selector_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalysisMethodSelectionStartedEventData(StrictApiModel):
    dataset_source_id: str = Field(min_length=1, max_length=36)
    dataset_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalysisSpecEventData(StrictApiModel):
    analysis_spec_id: str = Field(min_length=1, max_length=36)
    revision: int = Field(ge=1)
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selector_kind: Literal["local-deterministic", "remote-model-assisted"]
    prompt_version: str | None = Field(default=None, min_length=1, max_length=100)


class AnalysisCompiledEventData(StrictApiModel):
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    analysis_spec_id: str = Field(min_length=1, max_length=36)
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_version: str = Field(min_length=1, max_length=100)
    approved_code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_policy_id: str = Field(min_length=1, max_length=100)


class AnalysisStructuredResultEventData(StrictApiModel):
    structured_result_id: str = Field(min_length=1, max_length=36)
    analysis_spec_id: str = Field(min_length=1, max_length=36)
    analysis_intent_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalysisUnsupportedEventData(StrictApiModel):
    capability: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=2_000)
    supported_alternatives: list[Literal["descriptive", "two-group-comparison", "correlation"]] = (
        Field(max_length=3)
    )
    selector_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selector_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentObservationCreatedEventData(StrictApiModel):
    observation_id: str = Field(min_length=1, max_length=36)
    decision_id: None = None
    action: None = None
    task_id: str | None = Field(default=None, min_length=1, max_length=36)
    target_step_key: None = None
    previous_analysis_spec_id: None = None
    proposed_analysis_spec_id: None = None
    expected_workflow_revision: int = Field(ge=1)
    reason_code: str = Field(min_length=1, max_length=100)


class DiscoverySelectionOperationSignal(StrictApiModel):
    operation_key: str = Field(min_length=1, max_length=300)
    step_key: str = Field(min_length=1, max_length=100)
    query_id: QueryId
    provider: DiscoveryProvider
    query_attempt_count: int = Field(ge=0, le=8)
    provider_attempt_count: int = Field(ge=0, le=8)
    query_no_novelty_count: int = Field(ge=0, le=8)
    query_novel_candidate_count: int = Field(ge=0)
    query_duplicate_count: int = Field(ge=0)
    tie_break_sha256: Sha256
    rank: int = Field(ge=1, le=32)


class DiscoverySelectionProjection(StrictApiModel):
    schema_version: Literal["1"]
    policy_version: Literal["discovery-next-operation-v1"]
    workflow_id: str = Field(min_length=1, max_length=36)
    plan_id: str = Field(min_length=1, max_length=36)
    plan_sha256: Sha256
    discovery_spec_id: str = Field(min_length=1, max_length=36)
    discovery_spec_revision: int = Field(ge=1)
    discovery_spec_sha256: Sha256
    eligible_operations: list[DiscoverySelectionOperationSignal] = Field(
        min_length=1,
        max_length=32,
    )
    selected_operation_key: str = Field(min_length=1, max_length=300)
    selected_step_key: str = Field(min_length=1, max_length=100)
    selection_snapshot_sha256: Sha256
    reason_code: Literal[
        "only-eligible-operation",
        "query-coverage-gap",
        "provider-coverage-gap",
        "lower-query-no-novelty",
        "higher-observed-novelty",
        "lower-duplicate-burden",
        "stable-tie-break",
    ]
    postcondition: Literal["queue-selected-pending-approved-operation-only"]

    @model_validator(mode="after")
    def validate_selected_operation(self) -> DiscoverySelectionProjection:
        if [item.rank for item in self.eligible_operations] != list(
            range(1, len(self.eligible_operations) + 1)
        ):
            raise ValueError("discovery selection ranks must be contiguous")
        selected = [
            item
            for item in self.eligible_operations
            if item.operation_key == self.selected_operation_key
        ]
        if (
            len(selected) != 1
            or selected[0].rank != 1
            or selected[0].step_key != self.selected_step_key
        ):
            raise ValueError("selected Discovery operation must be the unique first rank")
        return self


class AgentDecisionEventData(StrictApiModel):
    observation_id: str = Field(min_length=1, max_length=36)
    decision_id: str = Field(min_length=1, max_length=36)
    action: Literal[
        "continue",
        "request-clarification",
        "revise-analysis-spec",
        "retry-step",
        "complete",
        "stop",
    ]
    task_id: str | None = Field(default=None, min_length=1, max_length=36)
    target_step_key: str | None = Field(default=None, min_length=1, max_length=100)
    previous_analysis_spec_id: str | None = Field(default=None, min_length=1, max_length=36)
    proposed_analysis_spec_id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_workflow_revision: int = Field(ge=1)
    reason_code: str = Field(min_length=1, max_length=100)
    research_context_snapshot_id: str | None = Field(
        default=None, min_length=1, max_length=36
    )
    research_context_snapshot_sha256: Sha256 | None = None
    discovery_selection: DiscoverySelectionProjection | None = None
    discovery_selection_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_provenance(self) -> AgentDecisionEventData:
        if (self.research_context_snapshot_id is None) != (
            self.research_context_snapshot_sha256 is None
        ):
            raise ValueError(
                "Research context snapshot identity and hash must be recorded together"
            )
        if (self.discovery_selection is None) != (
            self.discovery_selection_sha256 is None
        ):
            raise ValueError("Discovery selection and hash must be recorded together")
        if self.discovery_selection is not None and (
            self.action != "continue"
            or self.target_step_key != self.discovery_selection.selected_step_key
        ):
            raise ValueError("Discovery selection must bind the continued target")
        return self


class AgentStepRetryRequestedEventData(AgentDecisionEventData):
    @model_validator(mode="after")
    def validate_retry_event(self) -> AgentStepRetryRequestedEventData:
        if (
            self.action != "retry-step"
            or self.task_id is None
            or self.target_step_key is None
            or self.previous_analysis_spec_id is not None
            or self.proposed_analysis_spec_id is not None
        ):
            raise ValueError("retry events require only a task and target step")
        return self


class AgentAnalysisSpecRevisionEventData(AgentDecisionEventData):
    @model_validator(mode="after")
    def validate_revision_event(self) -> AgentAnalysisSpecRevisionEventData:
        if (
            self.action != "revise-analysis-spec"
            or self.previous_analysis_spec_id is None
            or self.proposed_analysis_spec_id is None
        ):
            raise ValueError("revision events require both AnalysisSpec identities")
        return self


class AgentLoopLimitReachedEventData(AgentDecisionEventData):
    limit_name: Literal[
        "agent-steps",
        "plan-revisions",
        "analysis-spec-revisions",
        "step-retries",
        "clarification-rounds",
        "model-decisions",
        "invalid-model-decisions",
    ]

    @model_validator(mode="after")
    def validate_limit_event(self) -> AgentLoopLimitReachedEventData:
        if self.action != "stop" or self.target_step_key is not None:
            raise ValueError("loop limit events must stop without a target step")
        return self


class AgentStoppedEventData(AgentDecisionEventData):
    @model_validator(mode="after")
    def validate_stopped_event(self) -> AgentStoppedEventData:
        if self.action != "stop" or self.target_step_key is not None:
            raise ValueError("stopped events must stop without a target step")
        return self


class _InteractionEventData(StrictApiModel):
    interaction_id: str = Field(min_length=1, max_length=36)
    request_type: Literal[
        "single-choice",
        "multi-choice",
        "text",
        "number",
        "boolean",
        "column-selection",
        "method-confirmation",
        "assumption-confirmation",
    ]
    required: bool
    expected_workflow_revision: int = Field(ge=1)


class InteractionRequestedEventData(_InteractionEventData):
    response_id: None = None
    response_revision: None = None


class InteractionAnsweredEventData(_InteractionEventData):
    response_id: str = Field(min_length=1, max_length=36)
    response_revision: int = Field(ge=1)


WorkflowEventData = (
    CreatedEventData
    | RemoteDataApprovalEventData
    | StatusChangedEventData
    | PlanEventData
    | ApprovalEventData
    | TaskEventData
    | JobEventData
    | ReviewEventData
    | CancelEventData
    | AnalysisIntentCreatedEventData
    | AnalysisApprovalEventData
    | AnalysisRunEventData
    | AnalysisRunProgressEventData
    | AnalysisArtifactCreatedEventData
    | DatasetReviewWarningsAcceptedEventData
    | AgentRunCreatedEventData
    | IntentDecisionEventData
    | AnalysisClarificationRequestedEventData
    | AnalysisMethodSelectionStartedEventData
    | AnalysisSpecEventData
    | AnalysisCompiledEventData
    | AnalysisStructuredResultEventData
    | AnalysisUnsupportedEventData
    | AgentObservationCreatedEventData
    | AgentStepRetryRequestedEventData
    | AgentAnalysisSpecRevisionEventData
    | AgentLoopLimitReachedEventData
    | AgentStoppedEventData
    | AgentDecisionEventData
    | InteractionRequestedEventData
    | InteractionAnsweredEventData
)

WorkflowEventType = Literal[
    "agent.observation-created",
    "agent.decision-proposed",
    "agent.decision-approved",
    "agent.decision-rejected",
    "agent.decision-applied",
    "agent.step-retry-requested",
    "agent.analysis-spec-revision-proposed",
    "agent.analysis-spec-revision-approved",
    "agent.loop-limit-reached",
    "agent.stopped",
    "agent-run.created",
    "intent.decision-recorded",
    "analysis.method-selection-started",
    "analysis.clarification-requested",
    "analysis.spec-created",
    "analysis.spec-superseded",
    "analysis.spec-approved",
    "analysis.compiled",
    "analysis.execution-approval-requested",
    "analysis.execution-started",
    "analysis.structured-result-created",
    "analysis.review-completed",
    "analysis.unsupported",
    "interaction.requested",
    "interaction.answered",
    "workflow.created",
    "remote-data.approved",
    "workflow.status-changed",
    "plan.generated",
    "plan.approved",
    "approval.requested",
    "step.queued",
    "step.started",
    "step.completed",
    "step.failed",
    "job.failed",
    "job.retried",
    "review.completed",
    "workflow.cancel-requested",
    "analysis.intent-created",
    "analysis.approval-requested",
    "analysis.approved",
    "analysis.rejected",
    "analysis.run-started",
    "analysis.run-progress",
    "analysis.run-completed",
    "analysis.run-failed",
    "artifact.created",
    "analysis.review-warnings-accepted",
]

_WORKFLOW_EVENT_DATA_TYPES: dict[str, type[StrictApiModel]] = {
    "agent.observation-created": AgentObservationCreatedEventData,
    "agent.decision-proposed": AgentDecisionEventData,
    "agent.decision-approved": AgentDecisionEventData,
    "agent.decision-rejected": AgentDecisionEventData,
    "agent.decision-applied": AgentDecisionEventData,
    "agent.step-retry-requested": AgentStepRetryRequestedEventData,
    "agent.analysis-spec-revision-proposed": AgentAnalysisSpecRevisionEventData,
    "agent.analysis-spec-revision-approved": AgentAnalysisSpecRevisionEventData,
    "agent.loop-limit-reached": AgentLoopLimitReachedEventData,
    "agent.stopped": AgentStoppedEventData,
    "agent-run.created": AgentRunCreatedEventData,
    "intent.decision-recorded": IntentDecisionEventData,
    "analysis.method-selection-started": AnalysisMethodSelectionStartedEventData,
    "analysis.clarification-requested": AnalysisClarificationRequestedEventData,
    "analysis.spec-created": AnalysisSpecEventData,
    "analysis.spec-superseded": AnalysisSpecEventData,
    "analysis.spec-approved": AnalysisSpecEventData,
    "analysis.compiled": AnalysisCompiledEventData,
    "analysis.execution-approval-requested": AnalysisApprovalEventData,
    "analysis.execution-started": AnalysisRunEventData,
    "analysis.structured-result-created": AnalysisStructuredResultEventData,
    "analysis.review-completed": ReviewEventData,
    "analysis.unsupported": AnalysisUnsupportedEventData,
    "interaction.requested": InteractionRequestedEventData,
    "interaction.answered": InteractionAnsweredEventData,
    "workflow.created": CreatedEventData,
    "remote-data.approved": RemoteDataApprovalEventData,
    "workflow.status-changed": StatusChangedEventData,
    "plan.generated": PlanEventData,
    "plan.approved": PlanEventData,
    "approval.requested": ApprovalEventData,
    "step.queued": TaskEventData,
    "step.started": TaskEventData,
    "step.completed": TaskEventData,
    "step.failed": TaskEventData,
    "job.failed": JobEventData,
    "job.retried": JobEventData,
    "review.completed": ReviewEventData,
    "workflow.cancel-requested": CancelEventData,
    "analysis.intent-created": AnalysisIntentCreatedEventData,
    "analysis.approval-requested": AnalysisApprovalEventData,
    "analysis.approved": AnalysisApprovalEventData,
    "analysis.rejected": AnalysisApprovalEventData,
    "analysis.run-started": AnalysisRunEventData,
    "analysis.run-progress": AnalysisRunProgressEventData,
    "analysis.run-completed": AnalysisRunEventData,
    "analysis.run-failed": AnalysisRunEventData,
    "artifact.created": AnalysisArtifactCreatedEventData,
    "analysis.review-warnings-accepted": DatasetReviewWarningsAcceptedEventData,
}


class WorkflowEventOut(ApiModel):
    id: str
    sequence: int
    type: WorkflowEventType
    task_id: str | None
    job_id: str | None
    data: WorkflowEventData
    created_at: datetime

    @model_validator(mode="after")
    def validate_event_contract(self) -> WorkflowEventOut:
        expected_type = _WORKFLOW_EVENT_DATA_TYPES[self.type]
        if not isinstance(self.data, expected_type):
            raise ValueError(f"event {self.type} has an incompatible data payload")
        if (
            isinstance(
                self.data,
                (
                    TaskEventData,
                    AnalysisIntentCreatedEventData,
                    AnalysisApprovalEventData,
                    AnalysisRunEventData,
                    AnalysisRunProgressEventData,
                    AnalysisArtifactCreatedEventData,
                ),
            )
            and self.task_id != self.data.task_id
        ):
            raise ValueError("event task_id must match its payload task_id")
        if (
            isinstance(
                self.data,
                (
                    JobEventData,
                    AnalysisIntentCreatedEventData,
                    AnalysisApprovalEventData,
                    AnalysisRunEventData,
                    AnalysisRunProgressEventData,
                    AnalysisArtifactCreatedEventData,
                ),
            )
            and self.job_id != self.data.job_id
        ):
            raise ValueError("event job_id must match its payload job_id")
        if isinstance(self.data, AnalysisRunEventData):
            if self.type in {"analysis.run-started", "analysis.execution-started"} and (
                self.data.environment_hash is not None
                or self.data.artifact_count is not None
                or self.data.error_code is not None
            ):
                raise ValueError("a started analysis run cannot contain terminal fields")
            if self.type == "analysis.run-completed" and (
                self.data.environment_hash is None
                or self.data.artifact_count is None
                or self.data.artifact_count < 5
                or self.data.error_code is not None
            ):
                raise ValueError(
                    "a completed analysis run requires environment and mandatory artifacts"
                )
            if self.type == "analysis.run-failed" and self.data.error_code is None:
                raise ValueError("a failed analysis run requires a stable error code")
        return self


class WorkflowEventsOut(ApiModel):
    events: list[WorkflowEventOut]
    next_after: int
    has_more: bool
