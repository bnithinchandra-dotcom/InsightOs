from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Literal

import pysbd


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CHECKPOINT_PATH = REPOSITORY_ROOT / "backend" / "artifacts" / "qwen3-0.6b-hf-c1899de"
CHECKPOINT_REPOSITORY = "Qwen/Qwen3-0.6B"
CHECKPOINT_REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"
EXPECTED_WEIGHTS_BYTES = 1_503_300_328
SUITE_PATH = Path(__file__).with_name("suite_v1.json")
RESULTS_DIRECTORY = REPOSITORY_ROOT / "backend" / "artifacts" / "evaluation-results"
MAX_NEW_TOKENS = 192
REPETITIONS_DEFAULT = 1
CASE_MAX_OUTPUT_TOKENS = {
    "repeat_exact_token": 64,
    "json_profit_margin": 64,
    "instruction_exactly_two_sentences": 256,
    "business_revenue_profit": 768,
    "missing_churn_inputs": 768,
    "analysis_plan_profit_margin": 768,
    "tool_plan_without_execution": 768,
}
SYSTEM_PROMPT_VERSION = "insightos-analytical-agent-v3"
SYSTEM_PROMPT = """You are the InsightOS analytical assistant. Be concise but complete and follow the user's exact output contract. Use only supplied evidence; do not invent data, source columns, results, or execution. Revenue is income before costs; expenses are outflows; profit = revenue - expenses; profit margin (%) = profit / revenue * 100, never revenue / total revenue. If revenue is missing or zero, do not calculate a margin; identify the missing or undefined input. If revenue is positive and profit is zero, the margin is valid and equals 0%. Do not claim a margin is undefined merely because profit is zero. For churn questions, explicitly identify missing customer count, churn count, and time period without inventing a rate. For JSON-only requests, return one raw JSON object with the requested schema and no fences or surrounding text. For exact-token requests, return only the requested literal token, never a JSON wrapper. For analysis plans, identify revenue and expense/profit fields, validate the date column, group by month, state both financial formulas, and say when the source schema is unavailable; name required field roles without asserting that unprovided columns exist. A proposed plan is not execution; do not claim a file was inspected or a calculation/tool was run unless that actually happened."""

CaseStatus = Literal["passed", "failed", "error", "skipped", "needs_review"]
SCHEMA_VERSION = "insightos-hf-evaluation-report-v2"
RESCORED_SCHEMA_VERSION = "insightos-hf-rescored-report-v2"
SCORING_VERSION = "insightos-hf-scoring-v9"


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    category: str
    purpose: str
    prompt: str
    expected_behavior: str
    scorer: str
    human_review_rubric: tuple[str, ...]
    expected: dict[str, Any] | None = None
    repetitions: int = REPETITIONS_DEFAULT


@dataclass(frozen=True)
class Generation:
    text: str
    generated_tokens: int | None
    duration_ms: float
    tokens_per_second: float | None
    termination_reason: Literal["eos", "max_new_tokens", "unknown"] = "unknown"
    reasoning_leak: bool = False
    raw_leak: bool = False
    final_leak: bool = False
    truncation_stage: Literal[
        "before_think_close",
        "after_think_close",
        "no_think_block",
    ] | None = None
    raw_text: str | None = None
    final_text: str | None = None
    source: str = "model"
    max_output_tokens: int | None = None
    api_usage: dict[str, Any] | None = None


_ABBREVIATION_PATTERN = re.compile(
    r"\b(?:e\.g|i\.e|etc|Mr|Mrs|Ms|Dr|Prof|vs)\.",
    re.IGNORECASE,
)
_REASONING_LEAK_PATTERN = re.compile(
    r"</?think>|^\s*(?:okay[,! ]+(?:the user|so)\b|"
    r"hmm[,! ]+(?:the user|i)\b|let me\b|"
    r"i (?:should|need to|must|think|recall|will)\b|"
    r"first[,! ]+i\b|thinking\b|analysis\b|"
    r"the user (?:wants|asked|is asking)\b)",
    re.IGNORECASE,
)


def _has_reasoning_leak(text: str) -> bool:
    return _REASONING_LEAK_PATTERN.search(text) is not None


def _has_think_tags(text: str) -> bool:
    return re.search(r"</?think\s*>", text, re.IGNORECASE) is not None


def _final_answer_text(text: str) -> str:
    closing_tags = list(re.finditer(r"</think\s*>", text, re.IGNORECASE))
    if not closing_tags:
        return text
    return text[closing_tags[-1].end() :].strip()


def _truncation_stage(
    text: str,
    termination_reason: str,
) -> Literal["before_think_close", "after_think_close", "no_think_block"] | None:
    if termination_reason != "max_new_tokens":
        return None
    if not _has_think_tags(text):
        return "no_think_block"
    if re.search(r"</think\s*>", text, re.IGNORECASE):
        return "after_think_close"
    return "before_think_close"


def _strip_terminal_special_tokens(text: str, tokenizer: Any) -> str:
    eos_token = getattr(tokenizer, "eos_token", None)
    terminal_tokens = [token for token in (eos_token, "<|im_end|>", "<|endoftext|>", "</s>") if isinstance(token, str) and token]
    if not terminal_tokens:
        return text.strip()
    pattern = r"(?:\s*(?:" + "|".join(re.escape(token) for token in terminal_tokens) + r"))+\s*$"
    return re.sub(pattern, "", text).strip()


def _output_text(generation: Generation) -> str:
    return generation.final_text if generation.final_text is not None else generation.text


def _sentence_count(text: str) -> int:
    return len(pysbd.Segmenter(language="en", clean=False).segment(text))


def _has_abbreviation(text: str) -> bool:
    return bool(_ABBREVIATION_PATTERN.search(text))


def _has_terminal_punctuation(text: str) -> bool:
    return bool(re.search(r"[.!?][\"')\]]*\s*$", text.strip()))


def _strict_json_decoder() -> json.JSONDecoder:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}.")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-standard JSON numeric constant: {value}.")

    return json.JSONDecoder(
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )


def _json_loads_strict(text: str) -> Any:
    return _strict_json_decoder().decode(text)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def calculate_profit_margin(
    revenue: Any,
    *,
    expenses: Any = None,
    profit: Any = None,
) -> tuple[Decimal, Decimal] | None:
    if revenue is None or (expenses is None and profit is None):
        return None
    if expenses is not None and profit is not None:
        raise ValueError("Supply expenses or profit, not both.")

    def decimal_value(value: Any, name: str) -> Decimal:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a finite number.")
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not result.is_finite():
            raise ValueError(f"{name} must be a finite number.")
        return result

    revenue_value = decimal_value(revenue, "Revenue")
    if revenue_value <= 0:
        return None
    profit_value = (
        revenue_value - decimal_value(expenses, "Expenses")
        if expenses is not None
        else decimal_value(profit, "Profit")
    )
    return profit_value, profit_value / revenue_value * Decimal(100)


def _semantic_text(raw: str) -> str:
    value: Any = None
    try:
        value = _json_loads_strict(raw)
    except (json.JSONDecodeError, ValueError):
        fence_match = re.fullmatch(
            r"\s*```(?:json)?\s*(.*?)\s*```\s*",
            raw,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fence_match:
            try:
                value = _json_loads_strict(fence_match.group(1))
            except (json.JSONDecodeError, ValueError):
                value = None
    parts = [raw]

    def collect(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                parts.append(str(key))
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
        elif isinstance(item, str):
            parts.append(item)

    if value is not None:
        collect(value)
    return re.sub(r"[_-]+", " ", " ".join(parts)).casefold()


def _score_analysis_plan(case: EvaluationCase, text: str) -> dict[str, Any]:
    lowered = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).casefold()
    lowered = re.sub(r"[_`]", " ", lowered)
    invalid_formula = (
        _has_invalid_profit_margin_formula(lowered)
        or _has_incomplete_percentage_multiplier(lowered)
    )
    if case.id == "analysis_plan_profit_margin":
        requirements: dict[str, bool] = {
            "mentions_revenue": bool(re.search(r"\brevenue\b", lowered)),
            "mentions_cost_or_profit": bool(
                re.search(r"\b(?:costs?|expenses?|profit)\b", lowered)
            ),
            "mentions_date_column": _mentions_date_field(lowered),
            "validates_date_column": _validates_date_column(lowered),
            "acknowledges_schema_unavailable": bool(
                re.search(
                    r"\b(?:schema|column names?|field names?|source columns?)\b"
                    r".{0,50}\b(?:not provided|unavailable|unknown|unspecified|"
                    r"not supplied|not shown|cannot (?:confirm|determine|verify))\b"
                    r"|\b(?:cannot|can't|unable to)\s+(?:tell|confirm|determine|"
                    r"verify)\b.{0,50}\b(?:column|field|schema)\b"
                    r"|\bif\b.{0,35}\b(?:column|field)\b.{0,30}\bexists?\b",
                    lowered,
                )
            ),
            "groups_by_month": bool(
                re.search(
                    r"\b(?:group|aggregate|summari[sz]e|roll up)\b.{0,48}\bmonth\b"
                    r"|\bmonthly\b|\bby month\b",
                    lowered,
                )
            ),
            "states_profit_calculation": _has_profit_calculation(lowered),
            "states_profit_margin_formula": bool(
                _has_profit_margin_formula(lowered)
            ),
            "handles_missing_or_zero_revenue_safely": (
                _handles_missing_or_zero_revenue_safely(lowered)
            ),
            "does_not_use_incorrect_margin_formula": not invalid_formula,
        }
    else:
        requirements = {
            "mentions_revenue": bool(re.search(r"\brevenue\b", lowered)),
            "mentions_cost_or_profit": bool(
                re.search(r"\b(?:costs?|expenses?|profit)\b", lowered)
            ),
            "mentions_validation_or_missingness": bool(
                re.search(
                    r"\b(?:validat\w*|check\w*|verif\w*|missing\w*|null\w*|"
                    r"incomplete|quality)\b",
                    lowered,
                )
            ),
            "does_not_use_incorrect_margin_formula": not invalid_formula,
        }
        if case.id == "tool_plan_without_execution":
            requirements["forbidden_execution_phrases_absent"] = not bool(
                re.search(
                    r"\bi\s+(?:ran|executed)\b|\bthe result is\b",
                    lowered,
                )
            )

    claims_first_person_execution = bool(
        any(
            not re.search(
                r"\b(?:without|not|never|don't|do not|didn't|did not)\s+"
                r"(?:claim(?:ing)?|say(?:ing)?|state(?:ing)?|"
                r"suggest(?:ing)?|assert(?:ing)?)\s+(?:that\s+)?$",
                lowered[max(0, match.start() - 80) : match.start()],
            )
            for match in re.finditer(
                r"\b(?:i|we)\s+(?:have\s+)?"
                r"(?:opened|inspected|analy[sz]ed|read|loaded|examined|"
                r"calculated|executed|ran|called|used|confirmed|verified|found)\b",
                lowered,
            )
        )
    )
    file_content_pattern = re.compile(
        r"\b(?:the|this)\s+(?:file|csv|dataset)\s+"
        r"(?:contains|shows|includes|has)\b"
    )
    claims_file_content = False
    for match in file_content_pattern.finditer(lowered):
        preceding_text = lowered[max(0, match.start() - 80) : match.start()]
        is_conditional_or_instructional = re.search(
            r"\b(?:if|whether|check(?:\s+that)?|ensure(?:\s+that)?|"
            r"verify(?:\s+that)?|confirm(?:\s+that)?|validate(?:\s+that)?|"
            r"determine\s+(?:if|whether))\s+$",
            preceding_text,
        )
        if not is_conditional_or_instructional:
            claims_file_content = True
            break
    claims_execution = claims_first_person_execution or claims_file_content
    requirements["does_not_claim_execution"] = not claims_execution
    failed = [name for name, passed in requirements.items() if not passed]
    dimensions = {
        name: "passed" if passed else "failed"
        for name, passed in requirements.items()
    }
    dimensions["semantic_correctness"] = "pending_human_review"
    return {
        "status": "failed" if failed else "needs_review",
        "machine_checks": requirements,
        "dimension_scores": dimensions,
        "human_review_status": "pending",
        "human_review_rubric": list(case.human_review_rubric),
        "failure_category": (
            "incorrect_profit_margin_formula"
            if invalid_formula
            else "invalid_profit_margin_edge_case"
            if case.id == "analysis_plan_profit_margin"
            and _has_unsafe_zero_revenue_margin_claim(lowered)
            else "unsupported_execution_claim"
            if claims_execution
            else "analysis_plan_requirement_missing"
            if failed
            else None
        ),
    }


def load_suite(path: Path = SUITE_PATH) -> tuple[str, tuple[EvaluationCase, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    suite_version = payload["suite_version"]
    cases = tuple(
        EvaluationCase(
            id=item["id"],
            category=item["category"],
            purpose=item["purpose"],
            prompt=item["prompt"],
            expected_behavior=item["expected_behavior"],
            scorer=item["scorer"],
            human_review_rubric=tuple(item.get("human_review_rubric", ())),
            expected=item.get("expected"),
            repetitions=item.get("repetitions", REPETITIONS_DEFAULT),
        )
        for item in payload["cases"]
    )
    ids = [case.id for case in cases]
    if not suite_version or not cases or len(ids) != len(set(ids)):
        raise ValueError("Evaluation suite needs a version and unique case IDs.")
    if any(case.repetitions < 1 for case in cases):
        raise ValueError("Evaluation case repetitions must be at least one.")
    return suite_version, cases


def _has_profit_margin_formula(text: str) -> bool:
    normalized = re.sub(r"\\(?:text|mathrm)\s*\{([^{}]*)\}", r"\1", text)
    direct_formula = re.search(
        r"\b(?:profit|net income)\s*(?:divided by|over|/)\s*"
        r"(?:the\s+)?(?:total\s+)?revenue\b"
        r"|\bdivid(?:e|es|ed|ing)\s+(?:the\s+)?"
        r"(?:profit|net income)\s+by\s+(?:the\s+)?(?:total\s+)?revenue\b",
        normalized,
    )
    fractions = re.finditer(
        r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
        normalized,
    )
    fraction_formula = any(
        re.search(r"\brevenue\b", fraction.group(1))
        and re.search(r"\b(?:costs?|expenses?)\b", fraction.group(1))
        and re.search(r"\brevenue\b", fraction.group(2))
        for fraction in fractions
    )
    percentage_scale = bool(
        re.search(
            r"(?:×|\\times|\*)\s*100\b|\btimes\s+100\b|"
            r"\b(?:multiply|multiplying|multiplied|multiplies)\s+by\s+100\b|"
            r"\bas a percent(?:age)?\b",
            normalized,
        )
    )
    return bool((direct_formula or fraction_formula) and percentage_scale)


def _has_invalid_profit_margin_formula(text: str) -> bool:
    for match in re.finditer(
        r"\b(?:divide|divides|divided|dividing)\s+(?:the\s+)?revenue\s+by\s+"
        r"(?:the\s+)?(?:total\s+)?revenue\b"
        r"|\brevenue\s+(?:divided by|over)\s+(?:the\s+)?"
        r"(?:total\s+)?revenue\b"
        r"|\brevenue\s*/\s*(?:the\s+)?(?:total\s+)?revenue\b",
        text,
    ):
        prefix = text[max(0, match.start() - 100) : match.start()]
        if not re.search(
            r"\b(?:not|never|avoid|rather than|instead of)\b[^.!?]{0,60}$",
            prefix,
        ):
            return True
    return bool(
        re.search(
            r"\b(?:profit\s+)?margin\b.{0,100}"
            r"(?:=|\bis\b|\bequals\b|\bcalculated as\b).{0,100}"
            r"(?:\*|×|\btimes\b)\s*10\b",
            text,
        )
    )


def _has_incomplete_percentage_multiplier(text: str) -> bool:
    return re.search(r"\*\s*10(?!\s*0)", text) is not None


def _has_unsafe_zero_revenue_margin_claim(text: str) -> bool:
    patterns = (
        r"\b(?:0\s*%|zero\s+percent)(?!\w).{0,35}"
        r"\b(?:if|when|with)\s+(?:the\s+)?revenue\s+(?:is\s+)?(?:zero|0)\b",
        r"\b(?:if|when|with)\s+(?:the\s+)?revenue\s+(?:is\s+)?(?:zero|0)\b"
        r".{0,50}\b(?:the\s+)?(?:profit\s+)?margin\b.{0,35}"
        r"\b(?:0\s*%|zero\s+percent)(?!\w)",
        r"\brevenue\s+(?:of\s+)?(?:zero|0)\b.{0,50}"
        r"\b(?:the\s+)?(?:profit\s+)?margin\b.{0,35}"
        r"\b(?:0\s*%|zero\s+percent)(?!\w)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            matched_text = match.group(0)
            if re.search(
                r"\b(?:not|never|rather than|instead of)\s+"
                r"(?:zero\s+percent\b|0\s*%)",
                matched_text,
            ):
                continue
            preceding_text = text[max(0, match.start() - 60) : match.start()]
            if re.search(
                r"\b(?:do not|don't|never|avoid|incorrect(?:ly)?|"
                r"should not|must not)\b[^.!?]{0,50}$",
                preceding_text,
            ):
                continue
            return True
    return False


def _handles_missing_or_zero_revenue_safely(text: str) -> bool:
    mentions_unavailable_revenue = bool(
        re.search(
            r"\brevenue\b.{0,35}\b(?:missing|absent|unavailable|zero|0)\b"
            r"|\b(?:missing|absent|unavailable|zero|0)\b.{0,35}\brevenue\b",
            text,
        )
    )
    states_safe_handling = bool(
        re.search(
            r"\b(?:cannot|can't|unable to|do not|don't|should not|must not|"
            r"skip|withhold|flag|identify)\b.{0,60}\b(?:margin|calculation)\b"
            r"|\b(?:margin|calculation)\b.{0,60}"
            r"\b(?:undefined|not calculat\w*|cannot be calculated|"
            r"unavailable|missing)\b",
            text,
        )
    )
    return (
        mentions_unavailable_revenue
        and states_safe_handling
        and not _has_unsafe_zero_revenue_margin_claim(text)
    )


def _has_profit_calculation(text: str) -> bool:
    normalized = re.sub(r"\\(?:text|mathrm)\s*\{([^{}]*)\}", r"\1", text)
    normalized = normalized.replace("\N{MINUS SIGN}", "-")
    return bool(
        re.search(
            r"\bprofit\b.{0,35}\b(?:is|equals|calculated as|determined as)\b"
            r".{0,35}\brevenue\b.{0,20}\b(?:minus|less|-)\b.{0,20}"
            r"\b(?:expenses?|costs?)\b",
            normalized,
        )
        or re.search(
            r"\bprofit\s*=\s*revenue\s*(?:-|minus)\s*"
            r"(?:total\s+)?(?:expenses?|costs?)\b",
            normalized,
        )
        or re.search(
            r"\b(?:compute|calculate|derive)\s+profit\*{0,2}\s+(?:as|by)\s+"
            r"revenue\s*(?:-|minus)\s*(?:total\s+)?(?:expenses?|costs?)\b",
            normalized,
        )
        or re.search(
            r"\bsubtract\b.{0,30}\b(?:expenses?|costs?)\b.{0,30}\bfrom\b"
            r".{0,20}\brevenue\b.{0,30}\bprofit\b",
            normalized,
        )
        or re.search(
            r"\brevenue\s*(?:-|minus)\s*(?:total\s+)?(?:expenses?|costs?)\b"
            r".{0,40}\bprofit\b",
            normalized,
        )
    )


def _validates_date_column(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:validate|check|verify|parse|confirm)\b.{0,50}"
            r"\b(?:date|timestamp|datetime)\b.{0,25}\b(?:column|field|values?)\b"
            r"|\b(?:date|timestamp|datetime)\b.{0,25}"
            r"\b(?:column|field|values?)\b.{0,50}"
            r"\b(?:validate|check|verify|parse|confirm)\b",
            text,
        )
    )


def _mentions_date_field(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:order\s+)?(?:date|timestamp|datetime)\b"
            r"(?:\s+(?:column|field|values?))?\b",
            text,
        )
    )


def _score(case: EvaluationCase, outputs: list[str]) -> dict[str, Any]:
    if case.scorer == "human_review":
        if case.id == "business_revenue_profit":
            text = re.sub(r"[*_`]", "", outputs[0].casefold())
            describes_profit_as_outflow = bool(
                re.search(
                    r"\bprofit\s+(?:is|means|represents)\s+"
                    r"(?:the\s+)?(?:net\s+)?(?:outflow|expense|cost)\b",
                    text,
                )
            )
            return {
                "status": "failed" if describes_profit_as_outflow else "needs_review",
                "machine_checks": {
                    "profit_not_described_as_outflow": not describes_profit_as_outflow,
                },
                "dimension_scores": {
                    "semantic_correctness": "pending_human_review",
                    "financial_concept_contradiction": (
                        "failed" if describes_profit_as_outflow else "not_detected"
                    ),
                },
                "human_review_status": "pending",
                "human_review_rubric": list(case.human_review_rubric),
                "failure_category": (
                    "financial_concept_error"
                    if describes_profit_as_outflow
                    else None
                ),
            }
        if case.id in {
            "analysis_plan_profit_margin",
            "tool_plan_without_execution",
        }:
            return _score_analysis_plan(case, outputs[0])
        return {
            "status": "needs_review",
            "machine_checks": {},
            "dimension_scores": {"semantic_correctness": "pending_human_review"},
            "human_review_status": "pending",
            "human_review_rubric": list(case.human_review_rubric),
            "failure_category": None,
        }

    if case.scorer == "two_sentences":
        text = outputs[0].strip()
        sentence_count = _sentence_count(text)
        count_valid = sentence_count == 2 and _has_terminal_punctuation(text)
        abbreviation_free = not _has_abbreviation(text)
        valid = count_valid and abbreviation_free
        return {
            "status": "needs_review" if valid else "failed",
            "machine_checks": {
                "exactly_two_sentences": count_valid,
                "abbreviation_free": abbreviation_free,
            },
            "dimension_scores": {
                "sentence_count_compliance": "passed" if count_valid else "failed",
                "abbreviation_compliance": (
                    "passed" if abbreviation_free else "failed"
                ),
                "instruction_compliance": "passed" if valid else "failed",
                "semantic_correctness": "pending_human_review",
            },
            "human_review_status": "pending",
            "human_review_rubric": list(case.human_review_rubric),
            "failure_category": (
                None
                if valid
                else "sentence_count_mismatch"
                if not count_valid
                else "abbreviation_violation"
            ),
        }

    if case.scorer == "json_profit_margin":
        raw = outputs[0].strip()
        diagnostics: dict[str, Any] = {
            "json_parse_valid": False,
            "embedded_json_parse_valid": False,
            "markdown_fence_present": "```" in raw,
            "surrounding_text_present": False,
            "json_only_compliant": False,
            "required_keys_present": False,
            "exact_keys": False,
            "field_types_valid": False,
            "schema_compliant": False,
            "metric_matches": False,
            "numeric_value_within_tolerance": False,
            "unit_matches": False,
            "reference_calculation_valid": None,
        }
        value = None
        try:
            value = _json_loads_strict(raw)
            diagnostics["json_parse_valid"] = True
            diagnostics["embedded_json_parse_valid"] = True
        except (json.JSONDecodeError, ValueError):
            fence_match = re.fullmatch(
                r"\s*```(?:json)?\s*(.*?)\s*```\s*",
                raw,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if fence_match:
                diagnostics["markdown_fence_present"] = True
                try:
                    value = _json_loads_strict(fence_match.group(1))
                    diagnostics["embedded_json_parse_valid"] = True
                except (json.JSONDecodeError, ValueError):
                    pass
            else:
                decoder = _strict_json_decoder()
                for index, character in enumerate(raw):
                    if character not in "{[":
                        continue
                    try:
                        value, end = decoder.raw_decode(raw, index)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    diagnostics["surrounding_text_present"] = bool(
                        raw[:index].strip() or raw[end:].strip()
                    )
                    diagnostics["embedded_json_parse_valid"] = True
                    break

        expected = case.expected or {}
        formula_inputs = re.search(
            r"\brevenue\s+is\s+([\d,]+(?:\.\d+)?)\s+and\s+"
            r"profit\s+is\s+([\d,]+(?:\.\d+)?)\b",
            case.prompt,
            flags=re.IGNORECASE,
        )
        expected_value = expected.get("value")
        if formula_inputs:
            calculated = calculate_profit_margin(
                formula_inputs.group(1).replace(",", ""),
                profit=formula_inputs.group(2).replace(",", ""),
            )
            expected_from_prompt = float(calculated[1]) if calculated else None
            reference_matches = bool(
                expected_from_prompt is not None
                and isinstance(expected_value, (int, float))
                and math.isclose(
                    expected_value,
                    expected_from_prompt,
                    rel_tol=0.0,
                    abs_tol=expected.get("absolute_tolerance", 0.0),
                )
            )
            diagnostics["reference_calculation_valid"] = reference_matches
            diagnostics["formula_expected_value"] = expected_from_prompt
            if expected_from_prompt is not None:
                expected_value = expected_from_prompt
            else:
                expected_value = None
        is_object = isinstance(value, dict)
        keys = set(value) if is_object else set()
        diagnostics["required_keys_present"] = {
            "metric",
            "value",
            "unit",
        }.issubset(keys)
        diagnostics["exact_keys"] = keys == {"metric", "value", "unit"}
        numeric = value.get("value") if is_object else None
        valid_numeric_type = isinstance(numeric, (int, float)) and not isinstance(
            numeric, bool
        )
        try:
            finite_numeric = valid_numeric_type and math.isfinite(numeric)
        except OverflowError:
            finite_numeric = False
        diagnostics["field_types_valid"] = bool(
            is_object
            and isinstance(value.get("metric"), str)
            and finite_numeric
            and isinstance(value.get("unit"), str)
        )
        diagnostics["schema_compliant"] = bool(
            diagnostics["required_keys_present"]
            and diagnostics["exact_keys"]
            and diagnostics["field_types_valid"]
        )
        diagnostics["metric_matches"] = bool(
            is_object and value.get("metric") == expected.get("metric")
        )
        try:
            diagnostics["numeric_value_within_tolerance"] = bool(
                finite_numeric
                and math.isclose(
                    numeric,
                    expected_value,
                    rel_tol=0.0,
                    abs_tol=expected["absolute_tolerance"],
                )
            )
        except (KeyError, OverflowError, TypeError):
            diagnostics["numeric_value_within_tolerance"] = False
        diagnostics["unit_matches"] = bool(
            is_object and value.get("unit") == expected.get("unit")
        )
        diagnostics["json_only_compliant"] = bool(
            diagnostics["json_parse_valid"]
            and not diagnostics["markdown_fence_present"]
            and not diagnostics["surrounding_text_present"]
        )
        passed = bool(
            diagnostics["json_only_compliant"]
            and diagnostics["schema_compliant"]
            and diagnostics["metric_matches"]
            and diagnostics["numeric_value_within_tolerance"]
            and diagnostics["unit_matches"]
            and diagnostics["reference_calculation_valid"] is not False
        )
        if diagnostics["markdown_fence_present"] or diagnostics["surrounding_text_present"]:
            failure_category = "json_only_violation"
        elif diagnostics["reference_calculation_valid"] is False:
            failure_category = "reference_calculation_mismatch"
        elif not diagnostics["json_parse_valid"]:
            failure_category = "malformed_json"
        elif not diagnostics["schema_compliant"]:
            failure_category = "json_schema_mismatch"
        else:
            failure_category = None if passed else "json_value_mismatch"
        return {
            "status": "passed" if passed else "failed",
            "machine_checks": diagnostics,
            "canonical_json": (
                _canonical_json(value)
                if diagnostics["json_parse_valid"]
                or diagnostics["embedded_json_parse_valid"]
                else None
            ),
            "dimension_scores": {
                "json_compliance": (
                    "passed"
                    if diagnostics["json_only_compliant"]
                    and diagnostics["schema_compliant"]
                    else "failed"
                ),
                "calculation_correctness": (
                    "passed"
                    if diagnostics["metric_matches"]
                    and diagnostics["numeric_value_within_tolerance"]
                    and diagnostics["unit_matches"]
                    and diagnostics["reference_calculation_valid"] is not False
                    else "failed"
                ),
            },
            "human_review_status": "not_required",
            "human_review_rubric": list(case.human_review_rubric),
            "failure_category": failure_category,
        }

    if case.scorer == "missing_inputs":
        text = _semantic_text(outputs[0])
        identifies_absence = any(
            phrase in text
            for phrase in (
                "cannot calculate",
                "can't calculate",
                "cannot be calculated",
                "not enough information",
                "insufficient information",
                "impossible to calculate",
                "unable to calculate",
                "not possible to calculate",
                "not provided",
                "missing",
            )
        )
        if any(
            phrase in text
            for phrase in (
                "nothing is missing",
                "no information is missing",
                "no inputs are missing",
                "not missing any",
            )
        ):
            identifies_absence = False
        invents_percentage = bool(
            re.search(
                r"\b(?:\d+(?:\.\d+)?|"
                r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
                r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
                r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
                r"eighty|ninety)(?:[- ](?:one|two|three|four|five|six|seven|"
                r"eight|nine))?)\s*(?:%|percent\b)",
                text,
            )
            or re.search(
                r"\b(?:churn rate|customer churn|rate)\s*(?:is|of|:|=)\s*"
                r"(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|"
                r"eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
                r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
                r"fifty|sixty|seventy|eighty|ninety)\b",
                text,
            )
        )
        mentions_customer_count = bool(
            re.search(
                r"\b(?:customer|subscriber|account)\s+(?:counts?|numbers?|totals?)\b"
                r"|\bnumber of (?:customers|subscribers|accounts)\b"
                r"|\bstarting and ending (?:customer|subscriber|account) counts?\b",
                text,
            )
        )
        mentions_churn_count = bool(
            re.search(
                r"\b(?:churn(?:ed|ing)?|cancel(?:led|ed|lation)?)\s+"
                r"(?:customer\s+)?(?:counts?|numbers?|totals?)\b"
                r"|\bnumber of (?:customers|subscribers|accounts) "
                r"(?:who\s+)?(?:churned|cancelled|canceled)\b"
                r"|\bchurn(?:ed|ing)? customers?\b",
                text,
            )
        )
        mentions_period = bool(
            re.search(
                r"\b(?:time\s+period|reporting\s+period|date\s+range|"
                r"measurement\s+period|over\s+the\s+|during\s+the\s+)"
                r"(?:period|month|quarter|year|week|days?)?\b"
                r"|\b(?:month|quarter|year|week)\s+of\s+\w+",
                text,
            )
        )
        checks = {
            "identifies_missing_information": identifies_absence,
            "identifies_customer_count_input": mentions_customer_count,
            "identifies_churn_count_input": mentions_churn_count,
            "identifies_time_period_input": mentions_period,
            "does_not_invent_percentage": not invents_percentage,
        }
        passed = all(checks.values())
        return {
            "status": "needs_review" if passed else "failed",
            "machine_checks": checks,
            "dimension_scores": {
                "missing_information_recognized": (
                    "passed" if identifies_absence else "failed"
                ),
                "customer_count_input_identified": (
                    "passed" if mentions_customer_count else "failed"
                ),
                "churn_count_input_identified": (
                    "passed" if mentions_churn_count else "failed"
                ),
                "time_period_input_identified": (
                    "passed" if mentions_period else "failed"
                ),
                "percentage_not_invented": (
                    "passed" if not invents_percentage else "failed"
                ),
                "semantic_correctness": "pending_human_review",
            },
            "human_review_status": "pending",
            "human_review_rubric": list(case.human_review_rubric),
            "failure_category": None if passed else "abstention_check_failed",
        }

    if case.scorer == "repeat_token":
        normalized = [text.strip() for text in outputs]
        passed = (
            len(normalized) == case.repetitions
            and all(text == "ORBIT-72" for text in normalized)
        )
        return {
            "status": "passed" if passed else "failed",
            "machine_checks": {"all_repetitions_exact": passed},
            "dimension_scores": {
                "per_generation_exact_match": [
                    text == "ORBIT-72" for text in normalized
                ],
                "repeatability_for_this_prompt": "passed" if passed else "failed",
            },
            "human_review_status": "not_required",
            "human_review_rubric": list(case.human_review_rubric),
            "failure_category": None if passed else "repeat_token_mismatch",
        }

    raise ValueError(f"Unknown scorer {case.scorer!r} for case {case.id!r}.")


def _machine_failure(case: EvaluationCase, category: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "machine_checks": {},
        "dimension_scores": {},
        "human_review_status": "not_required",
        "human_review_rubric": list(case.human_review_rubric),
        "failure_category": category,
    }


def _safe_error(exc: Exception) -> dict[str, Any]:
    message = str(exc).replace(str(REPOSITORY_ROOT), "<repository>")
    message = message[:200]
    error = {
        "type": getattr(exc, "cause_type", None) or type(exc).__name__,
        "message": message,
    }
    category = getattr(exc, "category", None)
    if isinstance(category, str):
        error["category"] = category
    for name in ("cause_type", "cause_message", "http_status"):
        detail = getattr(exc, name, None)
        if isinstance(detail, str):
            error[name] = detail[:200]
        elif isinstance(detail, int):
            error[name] = detail
    return error


def _termination_reason(
    token_ids: list[int],
    *,
    eos_token_id: int | list[int] | None,
    max_new_tokens: int,
) -> Literal["eos", "max_new_tokens", "unknown"]:
    eos_ids = (
        {eos_token_id}
        if isinstance(eos_token_id, int)
        else set(eos_token_id or ())
    )
    if token_ids and token_ids[-1] in eos_ids:
        return "eos"
    if len(token_ids) >= max_new_tokens:
        return "max_new_tokens"
    return "unknown"


def evaluate_cases(
    cases: tuple[EvaluationCase, ...],
    generate: Callable[[EvaluationCase], Generation],
) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        generations: list[Generation] = []
        error = None
        error_phase = None
        for _ in range(case.repetitions):
            try:
                generations.append(generate(case))
            except Exception as exc:
                error = _safe_error(exc)
                error_phase = "generation"
                break

        if error is not None:
            result = {
                "status": "error",
                "machine_checks": {},
                "dimension_scores": {},
                "human_review_status": "not_required",
                "human_review_rubric": list(case.human_review_rubric),
                "failure_category": "generation_error",
            }
        else:
            try:
                result = _score(
                    case,
                    [_output_text(generation) for generation in generations],
                )
                if any(generation.final_leak for generation in generations):
                    result["status"] = "failed"
                    result["failure_category"] = "reasoning_leak"
                    result["machine_checks"]["final_leak_absent"] = False
                    result["dimension_scores"]["reasoning_leak"] = "failed"
            except Exception as exc:
                error = _safe_error(exc)
                error_phase = "scoring"
                result = {
                    "status": "error",
                    "machine_checks": {},
                    "dimension_scores": {},
                    "human_review_status": "not_required",
                    "human_review_rubric": list(case.human_review_rubric),
                    "failure_category": "scoring_error",
                }

        if any(
            generation.termination_reason == "max_new_tokens"
            for generation in generations
        ):
            result["dimension_scores"]["completion"] = "failed_max_new_tokens"
            result["completion_status"] = "truncated_at_generation_limit"
            if result["status"] != "error":
                result["status"] = "failed"
                result["failure_category"] = "output_truncated"
        elif generations:
            result["completion_status"] = "eos" if all(
                generation.termination_reason == "eos"
                for generation in generations
            ) else "termination_unknown"

        results.append(
            {
                "id": case.id,
                "category": case.category,
                "purpose": case.purpose,
                "prompt": case.prompt,
                "expected_behavior": case.expected_behavior,
                "scorer": case.scorer,
                **result,
                "generations": [asdict(generation) for generation in generations],
                "graded_text_source": "final_text",
                "error": error,
                "error_phase": error_phase,
            }
        )
    return results


def _score_generation(case: EvaluationCase, generation: Generation) -> dict[str, Any]:
    single_case = replace(case, repetitions=1)
    score = (
        _machine_failure(single_case, "reasoning_leak")
        if generation.final_leak
        else _score(single_case, [_output_text(generation)])
    )
    if generation.final_leak:
        score["machine_checks"]["final_leak_absent"] = False
        score["dimension_scores"]["reasoning_leak"] = "failed"
    if generation.termination_reason == "max_new_tokens":
        score["dimension_scores"]["completion"] = "failed_max_new_tokens"
        score["completion_status"] = "truncated_at_generation_limit"
        score["status"] = "failed"
        score["failure_category"] = "output_truncated"
    else:
        score["completion_status"] = (
            "eos"
            if generation.termination_reason == "eos"
            else "termination_unknown"
        )
    return score


def _validator_passed(score: dict[str, Any]) -> bool:
    return score["status"] in {"passed", "needs_review"} and not (
        score.get("completion_status") == "truncated_at_generation_limit"
    )


def _retry_feedback(score: dict[str, Any]) -> str:
    failed_checks = [
        name
        for name, passed in score.get("machine_checks", {}).items()
        if passed is False
    ]
    details = (
        "Failed checks: " + ", ".join(failed_checks) + "."
        if failed_checks
        else f"Failure category: {score.get('failure_category') or 'incomplete answer'}."
    )
    return (
        "Your previous answer did not pass validation. "
        f"{details} Correct the failed requirements, keep the response concise, "
        "and return only the requested final answer without reasoning."
    )


def _deterministic_fallback(case: EvaluationCase) -> str | None:
    if case.id == "repeat_exact_token":
        return "ORBIT-72"
    if case.id == "missing_churn_inputs":
        return (
            "The churn rate cannot be calculated because the total customer "
            "count, number of churned customers, and time period are missing."
        )
    if case.id == "json_profit_margin":
        values = re.search(
            r"\brevenue\s+is\s+([\d,]+(?:\.\d+)?)\s+and\s+"
            r"profit\s+is\s+([\d,]+(?:\.\d+)?)\b",
            case.prompt,
            re.IGNORECASE,
        )
        if values is None:
            raise ValueError("JSON fallback requires explicit revenue and profit inputs.")
        calculated = calculate_profit_margin(
            values.group(1).replace(",", ""),
            profit=values.group(2).replace(",", ""),
        )
        if calculated is None:
            raise ValueError("JSON fallback cannot calculate the supplied inputs.")
        return _canonical_json(
            {
                "metric": (case.expected or {}).get("metric", "profit_margin"),
                "value": float(calculated[1]),
                "unit": (case.expected or {}).get("unit", "%"),
            }
        )
    return None


def _fallback_generation(text: str, max_output_tokens: int | None) -> Generation:
    return Generation(
        text=text,
        generated_tokens=0,
        duration_ms=0.0,
        tokens_per_second=None,
        termination_reason="eos",
        reasoning_leak=False,
        raw_leak=False,
        final_leak=False,
        raw_text=text,
        final_text=text,
        source="deterministic_fallback",
        max_output_tokens=max_output_tokens,
    )


def evaluate_cases_with_retries(
    cases: tuple[EvaluationCase, ...],
    generate: Callable[..., Generation],
    *,
    trials_per_case: int | None = None,
    max_attempts: int = 3,
    deterministic_fallbacks: bool = True,
    stop_on_generation_error: bool = False,
) -> list[dict[str, Any]]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one.")
    results = []
    for case in cases:
        trial_count = trials_per_case or case.repetitions
        if trial_count < 1:
            raise ValueError("trials_per_case must be at least one.")
        effective_case = replace(case, repetitions=trial_count)
        single_case = replace(case, repetitions=1)
        trials = []
        initial_passes = 0
        final_passes = 0
        final_generations = []
        total_attempts = 0
        fallback_count = 0
        error = None
        completed_trial_count = 0

        for trial_index in range(trial_count):
            attempts = []
            feedback = None
            initial_score = None
            final_generation = None
            final_score = None
            trial_error = None

            for attempt_index in range(max_attempts):
                try:
                    generation = generate(case, feedback=feedback)
                except Exception as exc:
                    error = _safe_error(exc)
                    trial_error = error
                    break
                total_attempts += 1
                score = _score_generation(single_case, generation)
                if attempt_index == 0:
                    initial_score = score
                attempts.append(
                    {
                        "attempt": attempt_index + 1,
                        "generation": asdict(generation),
                        "score": score,
                        "graded_text_source": "final_text",
                    }
                )
                final_generation = generation
                final_score = score
                if _validator_passed(score):
                    break
                feedback = _retry_feedback(score)

            if trial_error is not None:
                trials.append(
                    {
                        "trial": trial_index + 1,
                        "completed": False,
                        "pass_at_1": (
                            _validator_passed(initial_score)
                            if initial_score is not None
                            else None
                        ),
                        "pass_after_retry": None,
                        "attempts": attempts,
                        "final_source": None,
                        "pass_at_1_score": initial_score,
                        "final_score": None,
                        "interruption_error": trial_error,
                    }
                )
                break

            if (
                final_score is not None
                and not _validator_passed(final_score)
                and deterministic_fallbacks
            ):
                fallback_text = _deterministic_fallback(case)
                if fallback_text is not None:
                    final_generation = _fallback_generation(
                        fallback_text,
                        CASE_MAX_OUTPUT_TOKENS.get(case.id),
                    )
                    final_score = _score_generation(single_case, final_generation)
                    fallback_count += 1
                    attempts.append(
                        {
                            "attempt": "deterministic_fallback",
                            "generation": asdict(final_generation),
                            "score": final_score,
                            "graded_text_source": "final_text",
                        }
                    )

            if final_generation is not None and final_score is not None:
                final_generations.append(final_generation)
                completed_trial_count += 1
                initial_passes += int(_validator_passed(initial_score or {}))
                final_passes += int(_validator_passed(final_score))
                trials.append(
                    {
                        "trial": trial_index + 1,
                        "completed": True,
                        "pass_at_1": _validator_passed(initial_score or {}),
                        "pass_after_retry": _validator_passed(final_score),
                        "attempts": attempts,
                        "final_source": final_generation.source,
                        "pass_at_1_score": initial_score,
                        "final_score": final_score,
                    }
                )

        if error is not None:
            result: dict[str, Any] = {
                "status": "error",
                "machine_checks": {},
                "dimension_scores": {},
                "human_review_status": "not_required",
                "human_review_rubric": list(case.human_review_rubric),
                "failure_category": "generation_error",
                "completion_status": "incomplete_after_generation_error",
            }
        elif len(final_generations) != trial_count:
            result = {
                "status": "error",
                "machine_checks": {},
                "dimension_scores": {},
                "human_review_status": "not_required",
                "human_review_rubric": list(case.human_review_rubric),
                "failure_category": "incomplete_evaluation",
            }
            error = {"type": "RuntimeError", "message": "Evaluation trial count mismatch."}
        else:
            result = _score(
                effective_case,
                [_output_text(generation) for generation in final_generations],
            )
            if any(generation.final_leak for generation in final_generations):
                result["status"] = "failed"
                result["failure_category"] = "reasoning_leak"
                result["machine_checks"]["final_leak_absent"] = False
                result["dimension_scores"]["reasoning_leak"] = "failed"
            if any(
                generation.termination_reason == "max_new_tokens"
                for generation in final_generations
            ):
                result["dimension_scores"]["completion"] = "failed_max_new_tokens"
                result["completion_status"] = "truncated_at_generation_limit"
                if result["status"] != "error":
                    result["status"] = "failed"
                    result["failure_category"] = "output_truncated"
            elif final_generations:
                result["completion_status"] = (
                    "eos"
                    if all(
                        generation.termination_reason == "eos"
                        for generation in final_generations
                    )
                    else "termination_unknown"
                )

        results.append(
            {
                "id": case.id,
                "category": case.category,
                "purpose": case.purpose,
                "prompt": case.prompt,
                "expected_behavior": case.expected_behavior,
                "scorer": case.scorer,
                **result,
                "generations": [asdict(generation) for generation in final_generations],
                "trials": trials,
                "graded_text_source": "final_text",
                "requested_trial_count": trial_count,
                "completed_trial_count": completed_trial_count,
                "incomplete_trial_count": trial_count - completed_trial_count,
                "incomplete": completed_trial_count < trial_count,
                "pass_at_1": {
                    "passed": initial_passes,
                    "total": completed_trial_count,
                    "rate": (
                        initial_passes / completed_trial_count
                        if completed_trial_count
                        else None
                    ),
                },
                "pass_after_retry": {
                    "passed": final_passes,
                    "total": completed_trial_count,
                    "rate": (
                        final_passes / completed_trial_count
                        if completed_trial_count
                        else None
                    ),
                },
                "generation_attempt_count": total_attempts,
                "fallback_count": fallback_count,
                "error": error,
            }
        )
        if error is not None and stop_on_generation_error:
            break
    return results


def _git_metadata() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=REPOSITORY_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    return {
        "commit": git("rev-parse", "HEAD"),
        "working_tree_status": git("status", "--short", "--branch"),
    }


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _load_local_model(checkpoint_path: Path) -> tuple[Any, Any, Any, float]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(
        str(checkpoint_path),
        local_files_only=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        str(checkpoint_path),
        local_files_only=True,
        dtype=torch.bfloat16,
        device_map="cpu",
    )
    model.eval()
    return torch, tokenizer, model, (time.perf_counter() - started) * 1000


def _generator(
    torch: Any,
    tokenizer: Any,
    model: Any,
    *,
    temperature: float = 0.0,
) -> Callable[..., Generation]:
    def generate(
        case: EvaluationCase,
        *,
        feedback: str | None = None,
    ) -> Generation:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": case.prompt},
        ]
        if feedback:
            messages.append({"role": "user", "content": feedback})
        inputs = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
            enable_thinking=False,
        )
        input_ids = inputs["input_ids"]
        max_output_tokens = CASE_MAX_OUTPUT_TOKENS.get(
            case.id,
            MAX_NEW_TOKENS,
        )
        started = time.perf_counter()
        generation_settings: dict[str, Any] = {
            "max_new_tokens": max_output_tokens,
            "do_sample": temperature > 0,
            "eos_token_id": tokenizer.eos_token_id,
            "pad_token_id": tokenizer.pad_token_id,
        }
        if temperature > 0:
            generation_settings["temperature"] = temperature
        with torch.inference_mode():
            output_ids = model.generate(
                **inputs,
                **generation_settings,
            )
        duration_ms = (time.perf_counter() - started) * 1000
        generated_ids = output_ids[0, input_ids.shape[1] :]
        token_count = int(generated_ids.shape[0])
        token_sequence = generated_ids.tolist()
        if token_sequence and isinstance(token_sequence[0], list):
            token_sequence = token_sequence[0]
        termination = _termination_reason(
            [int(token) for token in token_sequence],
            eos_token_id=tokenizer.eos_token_id,
            max_new_tokens=max_output_tokens,
        )
        raw_text = tokenizer.decode(generated_ids, skip_special_tokens=False)
        decoded_text = tokenizer.decode(generated_ids, skip_special_tokens=True)
        if re.search(r"</think\s*>", raw_text, re.IGNORECASE):
            final_text = _strip_terminal_special_tokens(
                _final_answer_text(raw_text),
                tokenizer,
            )
        else:
            final_text = decoded_text
        final_leak = _has_reasoning_leak(final_text)
        rate = token_count / (duration_ms / 1000) if duration_ms > 0 else None
        return Generation(
            text=raw_text,
            generated_tokens=token_count,
            duration_ms=round(duration_ms, 2),
            tokens_per_second=round(rate, 3) if rate is not None else None,
            termination_reason=termination,
            reasoning_leak=final_leak,
            raw_leak=_has_think_tags(raw_text),
            final_leak=final_leak,
            truncation_stage=_truncation_stage(raw_text, termination),
            raw_text=raw_text,
            final_text=final_text,
            max_output_tokens=max_output_tokens,
        )

    return generate


def _report(
    suite_version: str,
    cases: tuple[EvaluationCase, ...],
    results: list[dict[str, Any]],
    *,
    model_load_ms: float | None,
    run_error: dict[str, str] | None = None,
) -> dict[str, Any]:
    statuses = ("passed", "failed", "error", "skipped", "needs_review")
    counts = {
        status: sum(result["status"] == status for result in results)
        for status in statuses
    }
    categories: dict[str, dict[str, int]] = {}
    for result in results:
        category = categories.setdefault(
            result["category"], {status: 0 for status in statuses}
        )
        category[result["status"]] += 1
    config_path = CHECKPOINT_PATH / "config.json"
    config: dict[str, Any] = {}
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    weights_path = CHECKPOINT_PATH / "model.safetensors"
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_version": suite_version,
        "scoring_version": SCORING_VERSION,
        "run_id": uuid.uuid4().hex,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evaluator": "local_hugging_face_transformers",
        "repository": _git_metadata(),
        "checkpoint": {
            "repository": CHECKPOINT_REPOSITORY,
            "pinned_revision": CHECKPOINT_REVISION,
            "local_path": str(CHECKPOINT_PATH),
            "model_type": config.get("model_type"),
            "architectures": config.get("architectures"),
            "weights_bytes": weights_path.stat().st_size if weights_path.is_file() else None,
            "expected_weights_bytes": EXPECTED_WEIGHTS_BYTES,
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "torch": _package_version("torch"),
            "transformers": _package_version("transformers"),
            "tokenizers": _package_version("tokenizers"),
            "safetensors": _package_version("safetensors"),
        },
        "generation_settings": {
            "device": "cpu",
            "dtype": "bfloat16",
            "chat_template": "tokenizer.apply_chat_template",
            "system_prompt": {
                "version": SYSTEM_PROMPT_VERSION,
                "sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
            },
            "thinking_mode": False,
            "max_new_tokens": MAX_NEW_TOKENS,
            "do_sample": False,
            "eos_token_id": "tokenizer.eos_token_id",
            "pad_token_id": "tokenizer.pad_token_id",
            "output_slicing": "generated tokens only; prompt token prefix excluded",
        },
        "model_load_duration_ms": (
            round(model_load_ms, 2) if model_load_ms is not None else None
        ),
        "run_error": run_error,
        "counts": {"total_cases": len(cases), **counts},
        "category_counts": categories,
        "cases": results,
        "limitations": [
            "The suite is small and synthetic; it is diagnostic, not a general capability guarantee.",
            "Human-review cases remain needs_review until a person applies the included rubric.",
            "Greedy decoding improves repeatability but does not guarantee identical output across hardware or library versions.",
            "The pinned revision is recorded as declared metadata; the local checkpoint bytes are not cryptographically verified by this runner.",
            "No tools are executed; textual planning is not evidence of tool-call correctness.",
            "Sentence counting is a punctuation-boundary heuristic; abbreviations, decimals, ellipses and unconventional punctuation can be ambiguous.",
            "The sentence counter recognizes a small set of common abbreviations and decimal points; it is not a full natural-language sentence parser.",
            "Missing-input, financial-contradiction, and analysis-plan checks are narrow heuristics; absence of a detected issue is not proof of semantic correctness.",
            "Analysis-plan checks are lexical diagnostics only; semantic correctness and unsupported claims still require human review.",
        ],
    }


def _case_truncation_assessment(
    original_case: dict[str, Any],
) -> str:
    generations = original_case.get("generations", [])
    if not generations:
        return "not_assessable_no_generation"
    reasons = [
        generation.get("termination_reason")
        for generation in generations
        if isinstance(generation, dict)
    ]
    if "max_new_tokens" in reasons:
        return "confirmed_generation_limit"
    if all(reason == "eos" for reason in reasons) and reasons:
        return "eos_observed"

    for generation in generations:
        if not isinstance(generation, dict):
            continue
        text = generation.get("text", "")
        count = generation.get("generated_tokens")
        visibly_open = bool(text) and not re.search(r"[.!?][\"')\]]*\s*$", text)
        if count == MAX_NEW_TOKENS and visibly_open:
            return "likely_truncated_at_limit_termination_metadata_unavailable"
    return "termination_metadata_unavailable"


def rescore_saved_report(source_path: Path) -> dict[str, Any]:
    source = source_path.resolve()
    original = json.loads(source.read_text(encoding="utf-8"))
    suite_version, cases = load_suite()
    suite_by_id = {case.id: case for case in cases}
    rescored_cases = []
    statuses = ("passed", "failed", "error", "skipped", "needs_review")

    for original_case in original.get("cases", []):
        case_id = original_case.get("id")
        case = suite_by_id.get(case_id)
        generations = original_case.get("generations", [])
        outputs = [
            generation.get("text", "")
            for generation in generations
            if isinstance(generation, dict)
        ]
        if case is None:
            score = {
                "status": "skipped",
                "machine_checks": {},
                "dimension_scores": {},
                "human_review_status": "not_required",
                "human_review_rubric": [],
                "failure_category": "case_not_in_current_suite",
            }
        elif original_case.get("error") is not None:
            score = {
                "status": "error",
                "machine_checks": {},
                "dimension_scores": {},
                "human_review_status": "not_required",
                "human_review_rubric": list(case.human_review_rubric),
                "failure_category": "source_generation_error",
            }
        elif not outputs:
            score = {
                "status": "skipped",
                "machine_checks": {},
                "dimension_scores": {},
                "human_review_status": "not_required",
                "human_review_rubric": list(case.human_review_rubric),
                "failure_category": "source_has_no_generation",
            }
        else:
            try:
                score = _score(case, outputs)
            except Exception as exc:
                score = {
                    "status": "error",
                    "machine_checks": {},
                    "dimension_scores": {},
                    "human_review_status": "not_required",
                    "human_review_rubric": list(case.human_review_rubric),
                    "failure_category": "scoring_error",
                    "error": _safe_error(exc),
                }

        completion = _case_truncation_assessment(original_case)
        if completion == "confirmed_generation_limit":
            score["dimension_scores"]["completion"] = "failed_max_new_tokens"
            score["completion_status"] = "truncated_at_generation_limit"
            if score["status"] != "error":
                score["status"] = "failed"
                score["failure_category"] = "output_truncated"
        else:
            score["completion_status"] = completion

        rescored_cases.append(
            {
                "id": case_id,
                "category": original_case.get("category"),
                "original_status": original_case.get("status"),
                "rescored_status": score["status"],
                "completion_assessment": completion,
                "raw_generations": generations,
                **score,
            }
        )

    counts = {
        status: sum(item["rescored_status"] == status for item in rescored_cases)
        for status in statuses
    }
    category_counts: dict[str, dict[str, int]] = {}
    for item in rescored_cases:
        category = item["category"] or "uncategorized"
        counts_for_category = category_counts.setdefault(
            category, {status: 0 for status in statuses}
        )
        counts_for_category[item["rescored_status"]] += 1

    return {
        "schema_version": RESCORED_SCHEMA_VERSION,
        "scoring_version": SCORING_VERSION,
        "suite_version": suite_version,
        "rescored_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_report_path": str(source),
        "source_report_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_run_id": original.get("run_id"),
        "source_run_timestamp_utc": original.get("timestamp_utc"),
        "inference_performed": False,
        "model_identity": original.get("checkpoint"),
        "environment": original.get("environment"),
        "model_load_duration_ms": original.get("model_load_duration_ms"),
        "tokenizer": {
            "package": "tokenizers",
            "package_version": (original.get("environment") or {}).get("tokenizers"),
            "checkpoint_revision": (
                (original.get("checkpoint") or {}).get("pinned_revision")
            ),
        },
        "generation_settings": original.get("generation_settings"),
        "original_counts": original.get("counts"),
        "rescored_counts": counts,
        "category_counts": category_counts,
        "cases": rescored_cases,
        "limitations": [
            "This report reuses the original stored generations; no model load or inference was performed.",
            "Original raw report and original per-case statuses are preserved and were not modified.",
            "The source report contains no EOS/termination metadata; a likely-truncated judgment is heuristic and is not presented as confirmed.",
            "Machine-checkable scoring does not replace human review of semantic correctness.",
        ],
    }


def _write_report(report: dict[str, Any], output: Path | None) -> Path:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = output or RESULTS_DIRECTORY / (
        f"hf-qwen3-0.6b-{report['run_id']}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the pinned local Hugging Face Qwen3 checkpoint on synthetic cases."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional report path; existing files are never overwritten.",
    )
    parser.add_argument(
        "--rescore-report",
        type=Path,
        help="Rescore a saved report's generations offline; does not load a model.",
    )
    args = parser.parse_args(argv)
    suite_version, cases = load_suite()
    if args.rescore_report is not None:
        report = rescore_saved_report(args.rescore_report)
        report_path = _write_report(
            report,
            args.output
            or RESULTS_DIRECTORY
            / f"hf-qwen3-0.6b-rescored-{uuid.uuid4().hex}.json",
        )
        print(json.dumps(report["rescored_counts"], sort_keys=True))
        print(f"Offline rescore saved to {report_path}")
        return 1 if report["rescored_counts"]["failed"] or report["rescored_counts"]["error"] else 0

    try:
        if not (CHECKPOINT_PATH / "config.json").is_file() or not (
            CHECKPOINT_PATH / "model.safetensors"
        ).is_file():
            raise FileNotFoundError("Expected local checkpoint files are missing.")
        torch, tokenizer, model, model_load_ms = _load_local_model(CHECKPOINT_PATH)
    except Exception as exc:
        results = [
            {
                "id": case.id,
                "category": case.category,
                "purpose": case.purpose,
                "prompt": case.prompt,
                "expected_behavior": case.expected_behavior,
                "scorer": case.scorer,
                "status": "skipped",
                "machine_checks": {},
                "human_review_status": "not_required",
                "human_review_rubric": list(case.human_review_rubric),
                "failure_category": "model_load_error",
                "generations": [],
                "error": _safe_error(exc),
            }
            for case in cases
        ]
        report = _report(
            suite_version,
            cases,
            results,
            model_load_ms=None,
            run_error=_safe_error(exc),
        )
        report_path = _write_report(report, args.output)
        print(f"Model load failed; report saved to {report_path}")
        return 1

    results = evaluate_cases(cases, _generator(torch, tokenizer, model))
    report = _report(
        suite_version,
        cases,
        results,
        model_load_ms=model_load_ms,
    )
    report_path = _write_report(report, args.output)
    print(json.dumps(report["counts"], sort_keys=True))
    print(f"Report saved to {report_path}")
    return 1 if report["counts"]["failed"] or report["counts"]["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
