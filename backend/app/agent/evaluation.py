from __future__ import annotations

import json
import platform
import re
import sys
import time
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Literal

from pydantic import ValidationError

from app.agent.config import AgentSettings, get_agent_settings
from app.agent.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderHealth,
    ProviderMessage,
)
from app.agent.providers import ModelProvider, create_provider
from app.agent.runtime import SYSTEM_INSTRUCTIONS
from app.agent.tools import TOOL_DEFINITIONS


EVALUATION_PROMPT_VERSION = "insightos-model-eval-v2"
EvaluationStatus = Literal["passed", "failed", "skipped"]


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    category: str
    prompt: str
    expected_behavior: str
    response_format: Literal["json"] | None = None
    repetitions: int = 1
    tools: tuple[dict[str, Any], ...] = ()
    expected_tool_name: str | None = None
    expected_arguments: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvaluationCaseResult:
    name: str
    category: str
    prompt: str
    expected_behavior: str
    response_format: Literal["json"] | None
    tools: tuple[dict[str, Any], ...]
    status: EvaluationStatus
    latency_ms: tuple[float, ...]
    raw_responses: tuple[dict[str, object], ...] = ()
    failure_category: str | None = None


@dataclass(frozen=True)
class EvaluationReport:
    provider: str
    model: str | None
    runtime_version: str | None
    python_version: str
    platform: str
    prompt_version: str
    system_prompt: str
    generation_settings: dict[str, object]
    test_count: int
    executed_count: int
    pass_count: int
    fail_count: int
    skipped_count: int
    average_latency_ms: float | None
    total_input_tokens: int | None
    total_output_tokens: int | None
    category_results: dict[str, dict[str, int]]
    failure_categories: dict[str, int]
    unverified_capabilities: dict[str, str]
    cases: tuple[EvaluationCaseResult, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_TOOL_DEFINITIONS = {definition.name: definition for definition in TOOL_DEFINITIONS}
_TOOL_SCHEMAS = {
    name: definition.provider_schema()
    for name, definition in _TOOL_DEFINITIONS.items()
}
_PROFILE_TOOLS = tuple(
    _TOOL_SCHEMAS[name]
    for name in ("get_profile", "get_quality_report", "list_files")
)


EVALUATION_CASES = (
    EvaluationCase(
        name="instruction_following",
        category="instruction_following",
        prompt="Reply with exactly this word and nothing else: READY",
        expected_behavior="Return only READY.",
    ),
    EvaluationCase(
        name="synthetic_context_extraction",
        category="grounding",
        prompt=(
            "Synthetic note: Project Cedar has 17 active stores. "
            "Reply with the number of active stores only."
        ),
        expected_behavior="Return exactly 17 and do not add another number.",
    ),
    EvaluationCase(
        name="business_metric_definition",
        category="business_reasoning",
        prompt=(
            "Define gross margin and give its formula using revenue and cost "
            "of goods sold (COGS). Do not calculate a value."
        ),
        expected_behavior=(
            "Identify gross margin as (revenue - COGS) / revenue, not a "
            "calculation from unprovided business data."
        ),
    ),
    EvaluationCase(
        name="schema_interpretation",
        category="schema_understanding",
        prompt=(
            "Synthetic schema: ordered_at is DATE, net_revenue is "
            "DECIMAL(12,2), and customer_id is STRING. Describe the likely "
            "data types/roles, and state what business meaning cannot be "
            "inferred from these names and types alone."
        ),
        expected_behavior=(
            "Recognize date, numeric decimal, and customer identifier; "
            "identify unresolved business semantics."
        ),
    ),
    EvaluationCase(
        name="structured_output",
        category="structured_output",
        prompt=(
            "Return only JSON matching this exact shape: "
            '{"status":"needs_clarification","questions":[{"field":"time_period",'
            '"reason":"A concise reason"}]}. Use no additional keys.'
        ),
        response_format="json",
        expected_behavior=(
            "Emit valid JSON with exactly status and questions keys; status "
            "is the required string and questions has one object with string "
            "field and non-empty string reason."
        ),
    ),
    EvaluationCase(
        name="evidence_abstention",
        category="grounding_safety",
        prompt=(
            "Use only this synthetic note: The project opened in 2024. "
            "What was its revenue? If the note does not state it, say so."
        ),
        expected_behavior="Explicitly state that revenue is not in the note.",
    ),
    EvaluationCase(
        name="ambiguity_clarification",
        category="clarification",
        prompt="Compare performance.",
        expected_behavior=(
            "Ask a concise question because the metric, dataset, and period "
            "are unspecified."
        ),
    ),
    EvaluationCase(
        name="format_constraints",
        category="structured_output",
        prompt=(
            "Return exactly three lines, numbered 1., 2., and 3., with these "
            "words in order: Cedar, Maple, Birch. Do not add any other text."
        ),
        expected_behavior="Return only the three requested numbered lines.",
    ),
    EvaluationCase(
        name="tool_get_profile",
        category="tool_selection",
        prompt=(
            "Retrieve the stored profile for file 2 in dataset 7. Do not "
            "summarize or claim a result before the tool returns."
        ),
        expected_behavior=(
            "Call only get_profile with integer dataset_id=7 and file_id=2."
        ),
        tools=_PROFILE_TOOLS,
        expected_tool_name="get_profile",
        expected_arguments={"dataset_id": 7, "file_id": 2},
    ),
    EvaluationCase(
        name="tool_list_files",
        category="tool_selection",
        prompt="List the files and table structures in dataset 7.",
        expected_behavior="Call only list_files with integer dataset_id=7.",
        tools=_PROFILE_TOOLS,
        expected_tool_name="list_files",
        expected_arguments={"dataset_id": 7},
    ),
    EvaluationCase(
        name="tool_result_grounding",
        category="grounding_safety",
        prompt=(
            'Synthetic get_profile result: {"row_count":120,'
            '"columns":[{"name":"order_id","data_type":"string"}]}. '
            "What was total revenue? Use only the supplied result."
        ),
        expected_behavior=(
            "State that revenue is not available in the supplied profile."
        ),
    ),
    EvaluationCase(
        name="unsupported_action",
        category="grounding_safety",
        prompt=(
            "Run a new EDA analysis on dataset 7, file 2, and report its "
            "results. Do not retrieve an old result instead."
        ),
        expected_behavior=(
            "Do not make a tool call; explain that the supplied tools cannot "
            "start a new analysis."
        ),
        tools=_PROFILE_TOOLS,
    ),
    EvaluationCase(
        name="repeat_consistency",
        category="repeat_consistency",
        prompt=(
            "Synthetic token: ORBIT-72. Return only the exact token, "
            "with no explanation."
        ),
        expected_behavior="Return exactly ORBIT-72 on every run.",
        repetitions=3,
    ),
)


def _failure_category(
    case: EvaluationCase,
    responses: list[ModelResponse],
) -> str | None:
    for response in responses:
        if response.finish_reason.casefold() in {"length", "max_tokens"}:
            return "output_truncated"

    if case.expected_tool_name:
        if len(responses) != 1:
            return "tool_call_count_mismatch"
        calls = responses[0].message.tool_calls
        if len(calls) != 1:
            return "tool_call_count_mismatch"
        call = calls[0]
        if call.name != case.expected_tool_name:
            return "tool_selection_mismatch"
        definition = _TOOL_DEFINITIONS.get(call.name)
        if definition is None:
            return "unapproved_tool"
        try:
            definition.arguments_model.model_validate(call.arguments)
        except ValidationError:
            return "tool_arguments_invalid"
        return (
            None
            if call.arguments == case.expected_arguments
            else "tool_arguments_mismatch"
        )

    if any(response.message.tool_calls for response in responses):
        return "unexpected_tool_call"

    outputs = [response.message.content.strip() for response in responses]
    if case.name == "instruction_following":
        return None if outputs[0].strip(" .!`").casefold() == "ready" else "instruction_mismatch"
    if case.name == "synthetic_context_extraction":
        numbers = re.findall(r"\b\d+\b", outputs[0])
        return None if numbers == ["17"] else "context_extraction_mismatch"
    if case.name == "business_metric_definition":
        normalized = outputs[0].casefold()
        formula_terms = (
            "gross margin" in normalized
            and "revenue" in normalized
            and ("cogs" in normalized or "cost of goods sold" in normalized)
        )
        numerator = re.search(
            r"revenue\s*(?:-|−|minus)\s*(?:cogs|cost of goods sold)",
            normalized,
        )
        denominator = re.search(
            r"(?:/\s*revenue|divided by\s+revenue|over\s+revenue)",
            normalized,
        )
        return (
            None
            if formula_terms and numerator and denominator
            else "metric_definition_mismatch"
        )
    if case.name == "schema_interpretation":
        normalized = outputs[0].casefold()
        has_date = "ordered_at" in normalized and "date" in normalized
        has_decimal = (
            "net_revenue" in normalized
            and ("decimal" in normalized or "numeric" in normalized)
        )
        has_identifier = (
            "customer_id" in normalized
            and any(
                marker in normalized.replace("_", " ")
                for marker in ("identifier", "primary key", "customer id", " key")
            )
        )
        identifies_unknowns = any(
            marker in normalized
            for marker in (
                "cannot infer",
                "cannot determine",
                "not specified",
                "business meaning",
                "units",
                "need to confirm",
                "unknown",
            )
        )
        return (
            None
            if has_date and has_decimal and has_identifier and identifies_unknowns
            else "schema_interpretation_mismatch"
        )
    if case.name == "structured_output":
        try:
            value = json.loads(outputs[0])
        except json.JSONDecodeError:
            return "malformed_structured_output"
        if not isinstance(value, dict) or set(value) != {"status", "questions"}:
            return "structured_output_mismatch"
        questions = value["questions"]
        if (
            value["status"] != "needs_clarification"
            or not isinstance(questions, list)
            or len(questions) != 1
            or not isinstance(questions[0], dict)
            or set(questions[0]) != {"field", "reason"}
            or questions[0]["field"] != "time_period"
            or not isinstance(questions[0]["reason"], str)
            or not questions[0]["reason"].strip()
        ):
            return "structured_output_mismatch"
        return None
    if case.name in {"evidence_abstention", "tool_result_grounding"}:
        abstention_markers = (
            "cannot determine",
            "unable to determine",
            "insufficient information",
            "does not state",
            "doesn't state",
            "not provided",
            "not specified",
            "not mentioned",
            "not given",
            "not stated",
            "not available",
            "not in the",
            "does not include",
            "doesn't include",
            "no information",
            "cannot tell",
            "unknown",
        )
        normalized = outputs[0].casefold()
        if case.name == "tool_result_grounding" and any(
            marker in normalized
            for marker in (
                "indicates the number of rows",
                "shows the number of rows",
                "implies the number of rows",
            )
        ):
            return "unsupported_claim"
        return (
            None
            if any(marker in normalized for marker in abstention_markers)
            else "unsupported_claim"
        )
    if case.name == "ambiguity_clarification":
        return None if "?" in outputs[0] else "clarification_missing"
    if case.name == "unsupported_action":
        normalized = outputs[0].casefold()
        refusal_markers = (
            "cannot",
            "can't",
            "not able",
            "not available",
            "not supported",
            "do not have",
            "does not support",
        )
        return (
            None
            if any(marker in normalized for marker in refusal_markers)
            else "unsupported_action_not_declined"
        )
    if case.name == "format_constraints":
        lines = [line.rstrip() for line in outputs[0].splitlines()]
        matches = [
            re.match(r"^([1-3])[.)]\s+(.+)$", line)
            for line in lines
        ]
        valid_lines = (
            len(lines) == 3
            and all(match is not None for match in matches)
            and [match.group(1) for match in matches if match is not None]
            == ["1", "2", "3"]
            and [
                match.group(2).casefold()
                for match in matches
                if match is not None
            ]
            == ["cedar", "maple", "birch"]
        )
        return None if valid_lines else "format_constraint_mismatch"
    if case.name == "repeat_consistency":
        normalized = [
            output.strip(" .!`'\"").casefold()
            for output in outputs
        ]
        return (
            None
            if len(normalized) == case.repetitions
            and all(value == "orbit-72" for value in normalized)
            else "repeat_consistency_mismatch"
        )
    raise ValueError(f"Unknown evaluation case: {case.name}")


def _skipped_results(category: str) -> tuple[EvaluationCaseResult, ...]:
    return tuple(
        EvaluationCaseResult(
            name=case.name,
            category=case.category,
            prompt=case.prompt,
            expected_behavior=case.expected_behavior,
            response_format=case.response_format,
            tools=case.tools,
            status="skipped",
            latency_ms=(),
            failure_category=category,
        )
        for case in EVALUATION_CASES
    )


def _raw_response(response: ModelResponse) -> dict[str, object]:
    return {
        "content": response.message.content,
        "tool_calls": [
            {
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in response.message.tool_calls
        ],
        "finish_reason": response.finish_reason,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }


def _report(
    *,
    provider_name: str,
    model: str | None,
    runtime_version: str | None,
    results: tuple[EvaluationCaseResult, ...],
    unverified_capabilities: dict[str, str],
    max_output_tokens: int | None,
) -> EvaluationReport:
    latencies = [
        latency
        for result in results
        for latency in result.latency_ms
    ]
    failure_categories: dict[str, int] = {}
    for result in results:
        if result.failure_category:
            category = result.failure_category
            failure_categories[category] = failure_categories.get(category, 0) + 1
    category_results: dict[str, dict[str, int]] = {}
    for result in results:
        category_counts = category_results.setdefault(
            result.category,
            {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
        )
        category_counts["total"] += 1
        category_counts[result.status] += 1
    pass_count = sum(result.status == "passed" for result in results)
    fail_count = sum(result.status == "failed" for result in results)
    skipped_count = sum(result.status == "skipped" for result in results)
    input_tokens = [
        response["usage"]["input_tokens"]
        for result in results
        for response in result.raw_responses
        if isinstance(response["usage"]["input_tokens"], int)
    ]
    output_tokens = [
        response["usage"]["output_tokens"]
        for result in results
        for response in result.raw_responses
        if isinstance(response["usage"]["output_tokens"], int)
    ]
    return EvaluationReport(
        provider=provider_name,
        model=model,
        runtime_version=runtime_version,
        python_version=sys.version.split()[0],
        platform=f"{platform.system()} {platform.release()}",
        prompt_version=EVALUATION_PROMPT_VERSION,
        system_prompt=SYSTEM_INSTRUCTIONS,
        generation_settings={
            "max_output_tokens": max_output_tokens,
            "provider_sampling": "provider/model defaults; not overridden",
            "tool_execution": "disabled; model tool calls are scored only",
        },
        test_count=len(results),
        executed_count=pass_count + fail_count,
        pass_count=pass_count,
        fail_count=fail_count,
        skipped_count=skipped_count,
        average_latency_ms=round(mean(latencies), 2) if latencies else None,
        total_input_tokens=sum(input_tokens) if input_tokens else None,
        total_output_tokens=sum(output_tokens) if output_tokens else None,
        category_results=category_results,
        failure_categories=failure_categories,
        unverified_capabilities=unverified_capabilities,
        cases=results,
    )


def run_evaluation(
    *,
    settings: AgentSettings | None = None,
    provider: ModelProvider | None = None,
) -> EvaluationReport:
    selected_settings = settings
    try:
        selected_settings = selected_settings or get_agent_settings()
        selected_provider = provider or create_provider(selected_settings)
        max_output_tokens = min(selected_settings.max_output_tokens, 256)
    except ProviderError as error:
        return _report(
            provider_name=(
                selected_settings.provider if selected_settings else "invalid"
            ),
            model=selected_settings.model if selected_settings else None,
            runtime_version=None,
            results=_skipped_results(error.code),
            unverified_capabilities={
                "text_generation": "unverified",
                "structured_output": "unverified",
                "tool_calling": "unverified",
                "streaming": "unsupported",
            },
            max_output_tokens=None,
        )

    capability_statuses = {
        "text_generation": "unverified",
        "structured_output": "unverified",
        "tool_calling": "unverified",
        "streaming": "unsupported",
    }
    try:
        health: ProviderHealth = selected_provider.check_health()
    except ProviderError as error:
        return _report(
            provider_name=selected_settings.provider,
            model=selected_settings.model,
            runtime_version=None,
            results=_skipped_results(error.code),
            unverified_capabilities=capability_statuses,
            max_output_tokens=max_output_tokens,
        )
    if health.status != "healthy":
        return _report(
            provider_name=selected_settings.provider,
            model=selected_settings.model,
            runtime_version=health.runtime_version,
            results=_skipped_results("provider_unavailable"),
            unverified_capabilities=capability_statuses,
            max_output_tokens=max_output_tokens,
        )

    results: list[EvaluationCaseResult] = []
    for case in EVALUATION_CASES:
        responses: list[ModelResponse] = []
        latencies: list[float] = []
        failure_category = None
        for _ in range(case.repetitions):
            request = ModelRequest(
                model=selected_settings.model or "",
                messages=(
                    ProviderMessage(role="system", content=SYSTEM_INSTRUCTIONS),
                    ProviderMessage(role="user", content=case.prompt),
                ),
                tools=case.tools,
                max_output_tokens=max_output_tokens,
                response_format=case.response_format,
            )
            started_at = time.perf_counter()
            try:
                response = selected_provider.complete(request)
            except ProviderError as error:
                latencies.append((time.perf_counter() - started_at) * 1000)
                failure_category = error.code
                break
            latencies.append((time.perf_counter() - started_at) * 1000)
            responses.append(response)

        if failure_category is None:
            failure_category = _failure_category(case, responses)
        results.append(
            EvaluationCaseResult(
                name=case.name,
                category=case.category,
                prompt=case.prompt,
                expected_behavior=case.expected_behavior,
                response_format=case.response_format,
                tools=case.tools,
                status="passed" if failure_category is None else "failed",
                latency_ms=tuple(round(value, 2) for value in latencies),
                raw_responses=tuple(_raw_response(response) for response in responses),
                failure_category=failure_category,
            )
        )

    return _report(
        provider_name=selected_settings.provider,
        model=selected_settings.model,
        runtime_version=health.runtime_version,
        results=tuple(results),
        unverified_capabilities=capability_statuses,
        max_output_tokens=max_output_tokens,
    )
