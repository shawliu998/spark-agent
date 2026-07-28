from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, TypeAlias, cast

from pydantic import Field, ValidationError

from ..workflow.schemas import DatasetColumnProfile, DatasetProfile
from .prompts import (
    METHOD_REPAIR_PROMPT_VERSION,
    METHOD_REPAIR_SYSTEM_PROMPT,
    METHOD_SELECTOR_PROMPT_VERSION,
    METHOD_SELECTOR_SYSTEM_PROMPT,
    build_method_repair_user_prompt,
    build_method_selector_input_payload,
    build_method_selector_user_prompt,
)
from .schemas import (
    AnalysisSpec,
    ClarificationProposal,
    CorrelationOperation,
    DescriptiveOperation,
    ScientificClarification,
    ScientificClarificationOption,
    ScientificClarificationType,
    StrictAnalysisModel,
    TwoGroupComparisonOperation,
    UnsupportedAnalysis,
    canonical_model_sha256,
)

MethodSelection: TypeAlias = AnalysisSpec | ClarificationProposal | UnsupportedAnalysis
SelectorParseResult = Literal[
    "local-deterministic",
    "valid",
    "valid-after-repair",
    "model-output-invalid",
    "model-request-failed",
]

MINIMUM_METHOD_CONFIDENCE = 0.70
MAX_METHOD_REPAIR_ATTEMPTS = 2

_UNSUPPORTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "paired-test",
        re.compile(r"(?:\bpaired\b|配对(?:检验|样本)?|重复测量)", re.IGNORECASE),
    ),
    (
        "structural-equation-modeling",
        re.compile(r"(?:\bsem\b|structural\s+equation|结构方程)", re.IGNORECASE),
    ),
    ("anova", re.compile(r"(?:\banova\b|方差分析|多组比较)", re.IGNORECASE)),
    (
        "regression",
        re.compile(r"(?:\bregress(?:ion)?\b|回归分析|逻辑回归|线性回归)", re.IGNORECASE),
    ),
    (
        "time-series",
        re.compile(r"(?:time[- ]?series|forecast(?:ing)?|时间序列|预测未来)", re.IGNORECASE),
    ),
    ("survival-analysis", re.compile(r"(?:survival|cox\b|生存分析)", re.IGNORECASE)),
    (
        "causal-inference",
        re.compile(r"(?:causal|causation|treatment effect|因果(?:推断|效应|关系|路径))", re.IGNORECASE),
    ),
    ("clustering", re.compile(r"(?:cluster(?:ing)?|聚类)", re.IGNORECASE)),
    (
        "deep-learning",
        re.compile(r"(?:deep learning|neural network|深度学习|神经网络)", re.IGNORECASE),
    ),
    (
        "custom-python",
        re.compile(r"(?:custom python|arbitrary python|自定义\s*python|任意\s*代码)", re.IGNORECASE),
    ),
)

_CORRELATION_PATTERN = re.compile(
    r"(?:correlat(?:e|ed|ion)?|association|pearson|spearman|trend|趋势|"
    r"相关(?:性|分析)?|关联(?:性|分析)?)",
    re.IGNORECASE,
)
_TREND_PATTERN = re.compile(r"(?:trend|趋势)", re.IGNORECASE)
_TWO_GROUP_PATTERN = re.compile(
    r"(?:two[- ]?group|between\s+(?:the\s+)?groups?|compare|difference|welch|mann[- ]?whitney|"
    r"两组|组间|比较|差异)",
    re.IGNORECASE,
)
_DESCRIPTIVE_PATTERN = re.compile(
    r"(?:descriptive|describe|summari[sz]e|distribution|overview|描述(?:统计)?|汇总|概览|分布)",
    re.IGNORECASE,
)


class MethodSelectorGateway(Protocol):
    @property
    def configured(self) -> bool: ...

    @property
    def default_model(self) -> str | None: ...

    @property
    def endpoint_identity(self) -> str: ...

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict[str, Any]: ...


class _ModelMethodSelection(StrictAnalysisModel):
    confidence: Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
    decision: AnalysisSpec | ClarificationProposal | UnsupportedAnalysis


@dataclass(frozen=True, slots=True)
class MethodSelectorResult:
    decision: MethodSelection
    prompt_version: str
    input_sha256: str
    output_sha256: str
    model_output_sha256: str | None
    model_used: str | None
    endpoint_identity: str | None
    used_model: bool
    parse_result: SelectorParseResult
    validation_errors: tuple[str, ...]
    token_usage: dict[str, int]
    retry_count: int


class MethodSelectionValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def deterministic_method_selection(
    goal: str,
    profile: DatasetProfile,
    *,
    dataset_source_id: str,
    dataset_content_hash: str,
    dataset_profile_hash: str,
    answered_context: Sequence[Mapping[str, object]] = (),
) -> MethodSelection:
    normalized_goal = goal.strip()
    if not normalized_goal:
        return _clarification(
            "The analysis objective is missing.",
            [
                _request(
                    "analysis-objective",
                    "What scientific question should this dataset analysis answer?",
                )
            ],
        )
    unsupported = _unsupported_capability(normalized_goal)
    if unsupported is not None:
        return UnsupportedAnalysis(
            capability=unsupported,
            explanation=(
                f"The requested capability '{unsupported}' is outside the bounded analysis methods."
            ),
            supported_alternatives=[
                "descriptive",
                "two-group-comparison",
                "correlation",
            ],
        )
    identity_error = _profile_identity_error(
        profile,
        dataset_source_id=dataset_source_id,
        dataset_content_hash=dataset_content_hash,
        dataset_profile_hash=dataset_profile_hash,
    )
    if identity_error is not None:
        return UnsupportedAnalysis(
            capability="dataset-identity-mismatch",
            explanation=identity_error,
            supported_alternatives=[],
        )

    answers = _answered_values(answered_context)
    objective = _string_answer(answers, "analysis-objective") or normalized_goal
    operation_type = _operation_type(objective)
    if operation_type is None:
        return _clarification(
            "The goal does not identify one supported analysis objective.",
            [
                _request(
                    "analysis-objective",
                    "Should this analysis describe columns, compare two independent groups, or examine a correlation?",
                    ["descriptive", "two-group-comparison", "correlation"],
                )
            ],
        )

    columns = list(profile.columns)
    column_names = [column.name for column in columns]
    if len(column_names) != len(set(column_names)):
        return UnsupportedAnalysis(
            capability="ambiguous-dataset-columns",
            explanation="Duplicate dataset column names cannot be referenced safely.",
            supported_alternatives=[],
        )
    mentioned = _mentioned_columns(objective, columns)
    missing_policy = _missing_policy(answers)
    common: dict[str, Any] = {
        "schema_version": "1",
        "objective": objective,
        "dataset_source_id": dataset_source_id,
        "dataset_content_hash": dataset_content_hash,
        "dataset_profile_hash": dataset_profile_hash,
        "missing_value_policy": missing_policy,
        "confidence_level": 0.95,
        "random_seed": int(dataset_content_hash[:8], 16),
    }
    if operation_type == "descriptive":
        selected = mentioned or [column.name for column in columns]
        selected_profiles = [column for column in columns if column.name in selected]
        numeric_only = all(_is_numeric(column) for column in selected_profiles)
        categorical_only = all(_is_categorical(column) for column in selected_profiles)
        statistics = ["count", "missing", "mean", "std", "median", "min", "max", "q1", "q3"]
        plot: Literal["none", "histogram", "bar"] = "histogram"
        if categorical_only:
            statistics = ["count", "missing", "unique", "frequency"]
            plot = "bar"
        elif not numeric_only:
            statistics = ["count", "missing", "unique"]
            plot = "none"
        return AnalysisSpec(
            **common,
            operation=DescriptiveOperation(
                type="descriptive",
                columns=selected,
                statistics=cast(Any, statistics),
                plot=plot,
            ),
            assumptions=[],
            limitations=[
                "Descriptive summaries do not test inferential or causal hypotheses."
            ],
        )
    if operation_type == "correlation":
        numeric = [column for column in columns if _is_numeric(column)]
        x_name = _column_answer(answers, "x-column", columns)
        y_name = _column_answer(answers, "y-column", columns)
        mentioned_numeric = [name for name in mentioned if name in {item.name for item in numeric}]
        if _TREND_PATTERN.search(objective):
            trend_roles = _trend_role_binding(objective, mentioned_numeric)
            if trend_roles is not None:
                x_name = x_name or trend_roles[0]
                y_name = y_name or trend_roles[1]
        if x_name is None and mentioned_numeric:
            x_name = mentioned_numeric[0]
        if y_name is None and len(mentioned_numeric) > 1:
            y_name = mentioned_numeric[1]
        if len(numeric) == 2:
            x_name = x_name or numeric[0].name
            y_name = y_name or next(
                (column.name for column in numeric if column.name != x_name),
                None,
            )
        requests: list[ScientificClarification] = []
        numeric_names = [column.name for column in numeric]
        if x_name is None:
            requests.append(_request("x-column", "Which numeric column is x?", numeric_names))
        if y_name is None:
            requests.append(_request("y-column", "Which different numeric column is y?", numeric_names))
        if requests:
            return _clarification("Correlation columns are ambiguous.", requests)
        assert x_name is not None and y_name is not None
        if x_name == y_name:
            return _clarification(
                "Correlation requires two different numeric columns.",
                [_request("y-column", "Which different numeric column is y?", numeric_names)],
            )
        correlation_methods = {"auto", "pearson", "spearman"}
        explicit_method = _method_answer(answers, correlation_methods)
        method_mentions = [
            method
            for method, pattern in (
                ("pearson", r"pearson|皮尔逊"),
                ("spearman", r"spearman|斯皮尔曼"),
            )
            if re.search(pattern, objective, re.IGNORECASE)
        ]
        if _has_invalid_method_answer(answers, correlation_methods) or len(method_mentions) > 1:
            return _clarification(
                "The correlation method is ambiguous.",
                [
                    _request(
                        "method-confirmation",
                        "Which bounded correlation method should be used?",
                        ["auto", "pearson", "spearman"],
                    )
                ],
            )
        goal_method = method_mentions[0] if method_mentions else None
        method = explicit_method or goal_method or "auto"
        return AnalysisSpec(
            **common,
            operation=CorrelationOperation(
                type="correlation",
                x_column=x_name,
                y_column=y_name,
                method=cast(Any, method),
                confidence_interval=True,
                plot="scatter",
            ),
            assumptions=["The selected columns represent independent observation pairs."],
            limitations=["Correlation does not establish causation."],
        )

    numeric = [column for column in columns if _is_numeric(column)]
    categorical = [column for column in columns if _is_likely_group(column)]
    outcome = _column_answer(answers, "outcome-column", columns)
    group = _column_answer(answers, "group-column", columns)
    mentioned_numeric = [item.name for item in numeric if item.name in mentioned]
    mentioned_categorical = [item.name for item in categorical if item.name in mentioned]
    if outcome is None and len(mentioned_numeric) == 1:
        outcome = mentioned_numeric[0]
    if group is None:
        eligible_mentioned_groups = [
            name for name in mentioned_categorical if name != outcome
        ]
        if len(eligible_mentioned_groups) == 1:
            group = eligible_mentioned_groups[0]
    if outcome is None and len(numeric) == 1:
        outcome = numeric[0].name
    if group is None and len(categorical) == 1:
        group = categorical[0].name
    requests = []
    if outcome is None:
        requests.append(
            _request("outcome-column", "Which numeric outcome should be compared?", [item.name for item in numeric])
        )
    if group is None:
        requests.append(
            _request("group-column", "Which column defines the independent groups?", [item.name for item in categorical])
        )
    if requests:
        return _clarification("The two-group analysis columns are ambiguous.", requests)
    assert outcome is not None and group is not None
    group_profile = _column_by_name(columns, group)
    raw_groups = answers.get("group-values")
    groups = _group_values(raw_groups)
    available_groups = (
        list(group_profile.low_cardinality.values)
        if group_profile.low_cardinality is not None
        else []
    )
    if groups is None and len(available_groups) == 2:
        groups = (available_groups[0], available_groups[1])
    if groups is None:
        return _clarification(
            "Exactly two group values must be selected.",
            [_request("group-values", "Which two group values should be compared?", available_groups)],
        )
    independence = answers.get("independence-assumption")
    if independence is False:
        return UnsupportedAnalysis(
            capability="paired-test",
            explanation="The selected observations are paired, which this version does not support.",
            supported_alternatives=["descriptive"],
        )
    two_group_methods = {"auto", "welch-t-test", "mann-whitney-u"}
    explicit_method = _method_answer(answers, two_group_methods)
    method_mentions = [
        method
        for method, pattern in (
            ("welch-t-test", r"welch|韦尔奇"),
            ("mann-whitney-u", r"mann[- ]?whitney|曼.?惠特尼"),
        )
        if re.search(pattern, objective, re.IGNORECASE)
    ]
    if _has_invalid_method_answer(answers, two_group_methods) or len(method_mentions) > 1:
        return _clarification(
            "The two-group method is ambiguous.",
            [
                _request(
                    "method-confirmation",
                    "Which bounded two-group method should be used?",
                    ["auto", "welch-t-test", "mann-whitney-u"],
                )
            ],
        )
    goal_method = method_mentions[0] if method_mentions else None
    method = explicit_method or goal_method or "auto"
    effect_size = "rank-biserial" if method == "mann-whitney-u" else "hedges-g"
    return AnalysisSpec(
        **common,
        operation=TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column=outcome,
            group_column=group,
            groups=groups,
            method=cast(Any, method),
            effect_size=cast(Any, effect_size),
            check_assumptions=True,
            plot="boxplot",
        ),
        assumptions=["The two selected groups contain independent observations."],
        limitations=["The bounded method rule is not a complete statistical diagnostic."],
    )


async def select_analysis_method(
    goal: str,
    profile: DatasetProfile,
    *,
    dataset_source_id: str,
    dataset_content_hash: str,
    dataset_profile_hash: str,
    answered_context: Sequence[Mapping[str, object]] = (),
    gateway: MethodSelectorGateway | None = None,
    model: str | None = None,
    max_repair_attempts: int = MAX_METHOD_REPAIR_ATTEMPTS,
) -> MethodSelectorResult:
    if not 0 <= max_repair_attempts <= MAX_METHOD_REPAIR_ATTEMPTS:
        raise ValueError("method repair attempts must be between zero and two")
    selector_input = build_method_selector_input_payload(
        goal=goal.strip(),
        profile=profile,
        dataset_source_id=dataset_source_id,
        dataset_content_hash=dataset_content_hash,
        dataset_profile_hash=dataset_profile_hash,
        answered_context=answered_context,
        output_schema=_ModelMethodSelection.model_json_schema(),
    )
    selected_model = model or (gateway.default_model if gateway is not None else None)
    input_sha256 = canonical_json_sha256(
        {"model": selected_model, "selectorInput": selector_input}
    )
    local_decision = deterministic_method_selection(
        goal,
        profile,
        dataset_source_id=dataset_source_id,
        dataset_content_hash=dataset_content_hash,
        dataset_profile_hash=dataset_profile_hash,
        answered_context=answered_context,
    )
    if gateway is None or not gateway.configured or selected_model is None:
        return _selector_result(
            local_decision,
            input_sha256=input_sha256,
            parse_result="local-deterministic",
        )

    validation_errors: list[str] = []
    token_usage: dict[str, int] = {}
    invalid_output: object = {}
    last_output_hash: str | None = None
    for attempt in range(max_repair_attempts + 1):
        if attempt == 0:
            system_prompt = METHOD_SELECTOR_SYSTEM_PROMPT
            user_prompt = build_method_selector_user_prompt(
                goal=goal.strip(),
                profile=profile,
                dataset_source_id=dataset_source_id,
                dataset_content_hash=dataset_content_hash,
                dataset_profile_hash=dataset_profile_hash,
                answered_context=answered_context,
                output_schema=_ModelMethodSelection.model_json_schema(),
            )
        else:
            system_prompt = METHOD_REPAIR_SYSTEM_PROMPT
            user_prompt = build_method_repair_user_prompt(
                selector_input=selector_input,
                invalid_output=_bounded_invalid_output(invalid_output),
                validation_errors=validation_errors[-10:],
                repair_attempt=attempt,
            )
        try:
            output, usage = await _complete_with_metadata(
                gateway,
                system_prompt,
                user_prompt,
                selected_model,
            )
        except Exception:
            return _selector_result(
                local_decision,
                input_sha256=input_sha256,
                model_used=selected_model,
                endpoint_identity=gateway.endpoint_identity or None,
                used_model=True,
                parse_result="model-request-failed",
                validation_errors=tuple([*validation_errors, "model-request-failed"]),
                token_usage=token_usage,
                retry_count=attempt,
            )
        _merge_token_usage(token_usage, usage)
        invalid_output = output
        try:
            last_output_hash = canonical_json_sha256(output)
            envelope = _ModelMethodSelection.model_validate_json(
                canonical_json_bytes(output),
                strict=True,
            )
            if envelope.confidence < MINIMUM_METHOD_CONFIDENCE:
                raise MethodSelectionValidationError("low-confidence-method-selection")
            validate_method_selection(
                envelope.decision,
                profile,
                dataset_source_id=dataset_source_id,
                dataset_content_hash=dataset_content_hash,
                dataset_profile_hash=dataset_profile_hash,
            )
        except (TypeError, ValueError, ValidationError) as error:
            validation_errors.append(_validation_error_code(error))
            continue
        return _selector_result(
            envelope.decision,
            input_sha256=input_sha256,
            model_output_sha256=last_output_hash,
            model_used=selected_model,
            endpoint_identity=gateway.endpoint_identity or None,
            used_model=True,
            parse_result="valid" if attempt == 0 else "valid-after-repair",
            validation_errors=tuple(validation_errors),
            token_usage=token_usage,
            retry_count=attempt,
            prompt_version=(
                METHOD_SELECTOR_PROMPT_VERSION
                if attempt == 0
                else METHOD_REPAIR_PROMPT_VERSION
            ),
        )
    return _selector_result(
        local_decision,
        input_sha256=input_sha256,
        model_output_sha256=last_output_hash,
        model_used=selected_model,
        endpoint_identity=gateway.endpoint_identity or None,
        used_model=True,
        parse_result="model-output-invalid",
        validation_errors=tuple(validation_errors),
        token_usage=token_usage,
        retry_count=max_repair_attempts,
        prompt_version=(
            METHOD_REPAIR_PROMPT_VERSION
            if max_repair_attempts > 0
            else METHOD_SELECTOR_PROMPT_VERSION
        ),
    )


def validate_method_selection(
    decision: MethodSelection,
    profile: DatasetProfile,
    *,
    dataset_source_id: str,
    dataset_content_hash: str,
    dataset_profile_hash: str,
) -> None:
    if isinstance(decision, AnalysisSpec):
        if (
            decision.dataset_source_id != dataset_source_id
            or decision.dataset_content_hash != dataset_content_hash
            or decision.dataset_profile_hash != dataset_profile_hash
        ):
            raise MethodSelectionValidationError("analysis-spec-identity-mismatch")
        columns = list(profile.columns)
        names = [column.name for column in columns]
        if len(names) != len(set(names)):
            raise MethodSelectionValidationError("dataset-columns-ambiguous")
        referenced = _referenced_columns(decision)
        if not referenced.issubset(names):
            raise MethodSelectionValidationError("analysis-spec-column-not-found")
        if isinstance(decision.operation, TwoGroupComparisonOperation):
            outcome = _column_by_name(columns, decision.operation.outcome_column)
            group = _column_by_name(columns, decision.operation.group_column)
            if not _is_numeric(outcome):
                raise MethodSelectionValidationError("outcome-column-not-numeric")
            if not _is_categorical(group):
                raise MethodSelectionValidationError("group-column-not-categorical")
            available: set[str] = (
                set(group.low_cardinality.values)
                if group.low_cardinality is not None
                else set[str]()
            )
            if available and not set(decision.operation.groups).issubset(available):
                raise MethodSelectionValidationError("group-value-not-found")
        if isinstance(decision.operation, CorrelationOperation):
            if not all(
                _is_numeric(_column_by_name(columns, name))
                for name in (decision.operation.x_column, decision.operation.y_column)
            ):
                raise MethodSelectionValidationError("correlation-column-not-numeric")
    elif isinstance(decision, ClarificationProposal):
        names = {column.name for column in profile.columns}
        for request in decision.requests:
            if request.type in {
                "outcome-column",
                "group-column",
                "x-column",
                "y-column",
            } and any(option.value not in names for option in request.options):
                raise MethodSelectionValidationError("clarification-column-not-found")


def _selector_result(
    decision: MethodSelection,
    *,
    input_sha256: str,
    parse_result: SelectorParseResult,
    model_output_sha256: str | None = None,
    model_used: str | None = None,
    endpoint_identity: str | None = None,
    used_model: bool = False,
    validation_errors: tuple[str, ...] = (),
    token_usage: dict[str, int] | None = None,
    retry_count: int = 0,
    prompt_version: str = METHOD_SELECTOR_PROMPT_VERSION,
) -> MethodSelectorResult:
    return MethodSelectorResult(
        decision=decision,
        prompt_version=prompt_version,
        input_sha256=input_sha256,
        output_sha256=canonical_model_sha256(decision),
        model_output_sha256=model_output_sha256,
        model_used=model_used,
        endpoint_identity=endpoint_identity,
        used_model=used_model,
        parse_result=parse_result,
        validation_errors=validation_errors,
        token_usage=dict(token_usage or {}),
        retry_count=retry_count,
    )


async def _complete_with_metadata(
    gateway: MethodSelectorGateway,
    system_prompt: str,
    user_prompt: str,
    model: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    complete = cast(
        Callable[
            [str, str, str | None],
            Awaitable[tuple[dict[str, Any], dict[str, int]]],
        ]
        | None,
        getattr(gateway, "complete_json_with_metadata", None),
    )
    if complete is not None:
        return await complete(system_prompt, user_prompt, model)
    return await gateway.complete_json(system_prompt, user_prompt, model), {}


def _merge_token_usage(total: dict[str, int], update: Mapping[str, int]) -> None:
    for key, value in update.items():
        if not isinstance(value, bool) and value >= 0:
            total[key] = total.get(key, 0) + value


def _validation_error_code(error: Exception) -> str:
    if isinstance(error, MethodSelectionValidationError):
        return error.code
    if isinstance(error, ValidationError):
        return "model-output-schema-invalid"
    return "model-output-json-invalid"


def _bounded_invalid_output(value: object) -> object:
    try:
        raw = canonical_json_bytes(value)
    except (TypeError, ValueError):
        return {"omitted": True, "reason": "not-json-serializable"}
    if len(raw) <= 65_536:
        return value
    return {
        "omitted": True,
        "reason": "too-large",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _profile_identity_error(
    profile: DatasetProfile,
    *,
    dataset_source_id: str,
    dataset_content_hash: str,
    dataset_profile_hash: str,
) -> str | None:
    if profile.dataset_source_id != dataset_source_id:
        return "The dataset profile source identity does not match the selected source."
    if profile.content_hash != dataset_content_hash:
        return "The dataset profile content hash does not match the selected source."
    if re.fullmatch(r"[0-9a-f]{64}", dataset_profile_hash) is None:
        return "The dataset profile hash is invalid."
    return None


def _unsupported_capability(goal: str) -> str | None:
    return next(
        (capability for capability, pattern in _UNSUPPORTED_PATTERNS if pattern.search(goal)),
        None,
    )


def _operation_type(goal: str) -> Literal[
    "descriptive", "two-group-comparison", "correlation"
] | None:
    matches: list[Literal["descriptive", "two-group-comparison", "correlation"]] = []
    if _CORRELATION_PATTERN.search(goal):
        matches.append("correlation")
    if _TWO_GROUP_PATTERN.search(goal):
        matches.append("two-group-comparison")
    if _DESCRIPTIVE_PATTERN.search(goal):
        matches.append("descriptive")
    return matches[0] if len(set(matches)) == 1 else None


def _mentioned_columns(goal: str, columns: Sequence[DatasetColumnProfile]) -> list[str]:
    normalized = goal.casefold()
    matches: list[tuple[int, str]] = []
    for column in columns:
        name = column.name.casefold()
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        match = pattern.search(normalized)
        if match is not None:
            matches.append((match.start(), column.name))
    return [name for _position, name in sorted(matches)]


def _trend_role_binding(
    objective: str,
    mentioned_numeric: Sequence[str],
) -> tuple[str, str] | None:
    if len(mentioned_numeric) != 2:
        return None
    first, second = mentioned_numeric
    for y_name, x_name in ((first, second), (second, first)):
        role_pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(y_name)}(?![A-Za-z0-9_])"
            rf"\s*(?:over|versus|随)\s*"
            rf"(?<![A-Za-z0-9_]){re.escape(x_name)}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        if role_pattern.search(objective):
            return x_name, y_name
    return None


def _answered_values(
    answered_context: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    answers: dict[str, object] = {}
    for item in answered_context:
        raw_type = item.get(
            "type",
            item.get("clarificationType", item.get("semantic")),
        )
        if isinstance(raw_type, str) and "response" in item:
            answers[raw_type] = item["response"]
    return answers


def _string_answer(answers: Mapping[str, object], key: str) -> str | None:
    value = answers.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _column_answer(
    answers: Mapping[str, object],
    key: str,
    columns: Sequence[DatasetColumnProfile],
) -> str | None:
    value = _string_answer(answers, key)
    return value if value in {column.name for column in columns} else None


def _method_answer(answers: Mapping[str, object], allowed: set[str]) -> str | None:
    value = _string_answer(answers, "method-confirmation")
    return value if value in allowed else None


def _has_invalid_method_answer(
    answers: Mapping[str, object],
    allowed: set[str],
) -> bool:
    value = answers.get("method-confirmation")
    return value is not None and (not isinstance(value, str) or value.strip() not in allowed)


def _missing_policy(
    answers: Mapping[str, object],
) -> Literal["drop-per-operation", "report-only"]:
    value = _string_answer(answers, "missing-value-policy")
    return cast(Any, value) if value in {"drop-per-operation", "report-only"} else "drop-per-operation"


def _group_values(value: object) -> tuple[str, str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    items = cast(Sequence[object], value)
    if len(items) != 2:
        return None
    first, second = items
    if (
        isinstance(first, str)
        and isinstance(second, str)
        and first.strip()
        and second.strip()
        and first != second
    ):
        return first, second
    return None


def _is_numeric(column: DatasetColumnProfile) -> bool:
    return column.inferred_type in {"integer", "number"} and not column.mixed_type


def _is_categorical(column: DatasetColumnProfile) -> bool:
    return (
        not column.potential_id
        and column.low_cardinality is not None
        and column.unique_count >= 2
    )


def _is_likely_group(column: DatasetColumnProfile) -> bool:
    if not _is_categorical(column):
        return False
    if column.inferred_type in {"boolean", "categorical", "string"}:
        return True
    return bool(
        re.search(
            r"(?:^|[_\s-])(group|condition|arm|cohort|treatment)(?:$|[_\s-])",
            column.name,
            re.IGNORECASE,
        )
    )


def _column_by_name(
    columns: Sequence[DatasetColumnProfile],
    name: str,
) -> DatasetColumnProfile:
    matches = [column for column in columns if column.name == name]
    if len(matches) != 1:
        raise MethodSelectionValidationError("dataset-column-ambiguous-or-missing")
    return matches[0]


def _referenced_columns(spec: AnalysisSpec) -> set[str]:
    operation = spec.operation
    if isinstance(operation, DescriptiveOperation):
        return set(operation.columns)
    if isinstance(operation, TwoGroupComparisonOperation):
        return {operation.outcome_column, operation.group_column}
    return {operation.x_column, operation.y_column}


def _request(
    request_type: ScientificClarificationType,
    question: str,
    options: Sequence[str] = (),
) -> ScientificClarification:
    return ScientificClarification(
        type=request_type,
        question=question,
        options=[
            ScientificClarificationOption(value=option, label=option)
            for option in options
        ],
    )


def _clarification(
    reason: str,
    requests: list[ScientificClarification],
) -> ClarificationProposal:
    return ClarificationProposal(reason=reason, requests=requests)


__all__ = [
    "MAX_METHOD_REPAIR_ATTEMPTS",
    "MINIMUM_METHOD_CONFIDENCE",
    "MethodSelection",
    "MethodSelectionValidationError",
    "MethodSelectorGateway",
    "MethodSelectorResult",
    "SelectorParseResult",
    "deterministic_method_selection",
    "select_analysis_method",
    "validate_method_selection",
]
