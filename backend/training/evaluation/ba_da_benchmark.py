from __future__ import annotations

import ast
import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
BENCHMARK_PATH = ROOT / "ba_da_diagnostic_v1.json"
REFERENCES_PATH = ROOT / "ba_da_references_v1.json"
SCHEMA_PATH = ROOT / "ba_da_diagnostic.schema.json"
MODES = {"base", "context_assisted", "tool_assisted"}
SPLITS = {"development", "validation", "hidden_test"}
SCORING_DIMENSIONS = {
    "conceptual_correctness",
    "formula_method_selection",
    "numerical_correctness",
    "statistical_interpretation",
    "business_relevance",
    "evidence_uncertainty",
    "tool_use_correctness",
    "output_instruction_compliance",
    "abstention_accuracy",
    "unsupported_claims",
    "sentence_count_compliance",
    "abbreviation_compliance",
    "sql_safety",
    "query_result_correctness",
    "query_intent",
    "clarification_completeness",
    "execution_claim_integrity",
    "result_interpretation_error",
}
FAILURE_CATEGORIES = {
    "concept_gap",
    "formula_selection_error",
    "arithmetic_error",
    "statistical_interpretation_error",
    "missing_context_error",
    "retrieval_error",
    "tool_selection_error",
    "invalid_tool_arguments",
    "execution_failure",
    "result_interpretation_error",
    "output_format_failure",
    "unsupported_execution_claim",
    "unsupported_claim",
    "reference_ambiguity",
    "scorer_error",
}
RELATED_FAMILY_GROUPS = {
    "percentage_rate_denominator": {
        "da_segment_rates",
        "calc_region_ratio",
        "tool_arguments_valid",
        "data_unequal_group_sizes",
    },
    "weighted_average_variants": {
        "da_weighted_vs_unweighted",
        "calc_weighted_avg",
    },
    "causal_inference_variants": {
        "ba_root_cause_correlation",
        "stat_correlation_causation",
        "data_trend_vs_cause",
    },
    "clarify_missing_information": {
        "ba_retention_kpi",
        "ba_churn_definition",
        "tool_statistical_tool_choice",
    },
    "profit_margin_calculations": {
        "calc_profit",
        "calc_margin",
    },
}


class BenchmarkValidationError(ValueError):
    failure_category = "scorer_error"


def _deterministic_dimensions(reference: dict[str, Any]) -> set[str]:
    scorer_dimensions = {
        "numeric_values": {"numerical_correctness"},
        "json_object": {"output_instruction_compliance"},
        "sentence_count": {
            "output_instruction_compliance",
            "sentence_count_compliance",
            "abbreviation_compliance",
        },
        "clarification": {
            "clarification_completeness",
            "unsupported_claims",
        },
        "sql_fixture": {
            "output_instruction_compliance",
            "sql_safety",
            "tool_use_correctness",
            "query_result_correctness",
        },
        "tool_arguments": {
            "tool_use_correctness",
            "numerical_correctness",
            "formula_method_selection",
        },
        "tool_trace": {
            "tool_use_correctness",
            "execution_claim_integrity",
        },
        "human_review": set(),
    }
    scorer_type = reference["scorer"].get("type")
    return scorer_dimensions.get(scorer_type, set()) & set(
        reference.get("dimension_rubric", {})
    )


def load_documents(
    benchmark_path: Path = BENCHMARK_PATH,
    references_path: Path = REFERENCES_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    references = json.loads(references_path.read_text(encoding="utf-8"))
    return benchmark, references


def _validate_document_shape(benchmark: dict[str, Any]) -> None:
    required = {
        "benchmark_version",
        "modes",
        "diagnostic_pairs",
        "categories",
        "context_cards",
        "tool_contracts",
        "cases",
    }
    if set(benchmark) != required:
        raise ValueError("Benchmark root fields do not match the data contract.")
    if benchmark["benchmark_version"] != "insightos-ba-da-diagnostic-v2":
        raise ValueError("Unsupported benchmark version.")
    if set(benchmark["modes"]) != MODES:
        raise ValueError("Benchmark must declare all three diagnostic modes.")
    category_ids = [category.get("id") for category in benchmark["categories"]]
    if len(category_ids) != 6 or len(category_ids) != len(set(category_ids)):
        raise ValueError("Benchmark must declare six unique categories.")
    if len(benchmark["modes"]) != len(MODES):
        raise ValueError("Diagnostic modes must be unique.")
    required_case_fields = {
        "case_id",
        "family_id",
        "category",
        "skill",
        "difficulty",
        "split",
        "prompt",
        "inputs",
        "modes",
        "allowed_context_ids",
        "allowed_tool_ids",
        "scoring_dimensions",
        "likely_failure_categories",
    }
    cases = benchmark["cases"]
    if len(cases) != 60:
        raise ValueError(f"Expected 60 cases; found {len(cases)}.")
    if any(not required_case_fields.issubset(case) for case in cases):
        raise ValueError("A case lacks required fields.")
    case_modes = {
        case["case_id"]: set(case["modes"])
        for case in cases
    }
    pair_ids: set[str] = set()
    for pair in benchmark["diagnostic_pairs"]:
        if pair["pair_id"] in pair_ids:
            raise ValueError("Diagnostic pair IDs must be unique.")
        pair_ids.add(pair["pair_id"])
        if pair["case_id"] not in case_modes:
            raise ValueError(f"Diagnostic pair references unknown case {pair['case_id']}.")
        if not set(pair["compare_modes"]).issubset(case_modes[pair["case_id"]]):
            raise ValueError(f"Diagnostic pair uses an unavailable mode for {pair['case_id']}.")

    context_ids = [card.get("id") for card in benchmark["context_cards"]]
    tool_ids = [tool.get("id") for tool in benchmark["tool_contracts"]]
    if len(context_ids) != len(set(context_ids)):
        raise ValueError("Context-card IDs must be unique.")
    if len(tool_ids) != len(set(tool_ids)):
        raise ValueError("Tool-contract IDs must be unique.")
    contexts = set(context_ids)
    tools = set(tool_ids)

    case_ids = [case.get("case_id") for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Case IDs must be unique.")
    families: dict[str, set[str]] = {}
    family_cases: dict[str, set[str]] = {}
    category_counts: dict[str, int] = {}
    split_counts: dict[str, int] = {}
    category_split_counts: dict[str, dict[str, int]] = {}

    for case in cases:
        allowed_case_fields = required_case_fields | {"visible_tool_trace"}
        if set(case) - allowed_case_fields:
            raise ValueError(f"Case {case.get('case_id')!r} has unknown fields.")
        if case["difficulty"] not in {"introductory", "intermediate", "advanced"}:
            raise ValueError(f"Invalid difficulty for {case['case_id']}.")
        if case["split"] not in SPLITS:
            raise ValueError(f"Invalid split for {case['case_id']}.")
        if not case["modes"] or not set(case["modes"]).issubset(MODES):
            raise ValueError(f"Invalid modes for {case['case_id']}.")
        if not set(case["allowed_context_ids"]).issubset(contexts):
            raise ValueError(f"Unknown context-card ID for {case['case_id']}.")
        if not set(case["allowed_tool_ids"]).issubset(tools):
            raise ValueError(f"Unknown tool-contract ID for {case['case_id']}.")
        if not set(case["scoring_dimensions"]).issubset(SCORING_DIMENSIONS):
            raise ValueError(f"Unknown scoring dimension for {case['case_id']}.")
        if not set(case["likely_failure_categories"]).issubset(FAILURE_CATEGORIES):
            raise ValueError(f"Unknown failure category for {case['case_id']}.")
        if (
            case["allowed_context_ids"]
            and "context_assisted" not in case["modes"]
        ):
            raise ValueError(f"Context is not permitted by mode for {case['case_id']}.")
        if case["allowed_tool_ids"] and "tool_assisted" not in case["modes"]:
            raise ValueError(f"Tools are not permitted by mode for {case['case_id']}.")
        if "visible_tool_trace" in case and case["visible_tool_trace"] is not None:
            if not case["allowed_tool_ids"]:
                raise ValueError(f"Trace has no permitted tool for {case['case_id']}.")
        families.setdefault(case["family_id"], set()).add(case["split"])
        family_cases.setdefault(case["family_id"], set()).add(case["case_id"])
        category_counts[case["category"]] = category_counts.get(case["category"], 0) + 1
        split_counts[case["split"]] = split_counts.get(case["split"], 0) + 1
        per_split = category_split_counts.setdefault(case["category"], {})
        per_split[case["split"]] = per_split.get(case["split"], 0) + 1

    declared_categories = {
        category["id"]: category["case_count"]
        for category in benchmark["categories"]
    }
    if category_counts != declared_categories or any(count != 10 for count in category_counts.values()):
        raise ValueError("Each declared category must contain exactly 10 cases.")
    if any(len(splits) != 1 for splits in families.values()):
        raise ValueError("A case family crosses split boundaries.")
    for family_id, expected_case_ids in RELATED_FAMILY_GROUPS.items():
        if family_cases.get(family_id) != expected_case_ids:
            raise ValueError(f"Related cases are not grouped in family {family_id}.")
    if len(family_cases) == len(cases):
        raise ValueError("Family isolation is vacuous: all case families are singletons.")
    if split_counts != {split: 20 for split in SPLITS}:
        raise ValueError("Each split must contain exactly 20 cases.")
    for category, counts in category_split_counts.items():
        if sorted(counts.get(split, 0) for split in SPLITS) != [3, 3, 4]:
            raise ValueError(f"Category {category} does not use a 4/3/3 split.")


def validate_references(
    benchmark: dict[str, Any],
    references: dict[str, Any],
) -> None:
    if references.get("reference_version") != "insightos-ba-da-references-v3":
        raise ValueError("Unsupported reference version.")
    if set(references) != {"reference_version", "score_scale", "failure_categories", "references"}:
        raise ValueError("Reference root fields do not match the data contract.")
    if set(references["failure_categories"]) != FAILURE_CATEGORIES:
        raise ValueError("Reference failure taxonomy is incomplete or inconsistent.")
    case_ids = {case["case_id"] for case in benchmark["cases"]}
    reference_ids = [item.get("case_id") for item in references["references"]]
    if len(reference_ids) != len(set(reference_ids)) or set(reference_ids) != case_ids:
        raise ValueError("Every benchmark case must have exactly one reference.")
    statuses = {
        status: sum(item["review_status"] == status for item in references["references"])
        for status in ("approved", "proposed_reference", "revised", "not_required", "pending")
    }
    if statuses != {
        "approved": 25,
        "proposed_reference": 35,
        "revised": 0,
        "not_required": 0,
        "pending": 0,
    }:
        raise ValueError(f"Reference lifecycle counts are inconsistent: {statuses}.")
    previous_statuses = {
        status: sum(
            item.get("previous_review_status") == status
            for item in references["references"]
        )
        for status in ("revised", "not_required")
    }
    if previous_statuses != {"revised": 19, "not_required": 16}:
        raise ValueError(
            f"Proposed-reference provenance counts are inconsistent: {previous_statuses}."
        )

    for reference in references["references"]:
        required = {
            "case_id",
            "reference_answer",
            "reference_calculation",
            "units",
            "tolerances",
            "scorer",
            "dimension_rubric",
            "human_review_required",
            "review_status",
        }
        is_proposed = reference["review_status"] == "proposed_reference"
        proposed_fields = {"previous_review_status", "operational_scoring"}
        if set(reference) != required | (proposed_fields if is_proposed else set()):
            raise ValueError(f"Reference {reference.get('case_id')} has invalid fields.")
        if reference["review_status"] not in {
            "pending",
            "approved",
            "proposed_reference",
            "revised",
            "not_required",
        }:
            raise ValueError(f"Invalid review status for {reference['case_id']}.")
        if reference["review_status"] == "proposed_reference":
            if reference["previous_review_status"] not in {"revised", "not_required"}:
                raise ValueError(
                    f"Invalid proposal provenance for {reference['case_id']}."
                )
            if not isinstance(reference["human_review_required"], bool):
                raise ValueError(
                    f"Invalid human-review flag for {reference['case_id']}."
                )
            expected_deterministic = _deterministic_dimensions(reference)
            operational_scoring = reference["operational_scoring"]
            if set(operational_scoring) != set(reference["dimension_rubric"]):
                raise ValueError(
                    f"Operational scoring must cover every dimension for "
                    f"{reference['case_id']}."
                )
            for dimension, policy in operational_scoring.items():
                expected_disposition = (
                    "deterministic_component"
                    if dimension in expected_deterministic
                    else "not_assessed_by_deterministic_scorer"
                )
                if (
                    not isinstance(policy, dict)
                    or set(policy) != {"disposition", "scorer", "rationale"}
                    or policy["disposition"] != expected_disposition
                    or policy["scorer"] != (
                        reference["scorer"]["type"]
                        if dimension in expected_deterministic
                        else None
                    )
                    or not isinstance(policy["rationale"], str)
                    or not policy["rationale"].strip()
                ):
                    raise ValueError(
                        f"Invalid operational scoring policy for "
                        f"{reference['case_id']}:{dimension}."
                    )
        elif reference["human_review_required"] != (
            reference["review_status"] in {"pending", "revised"}
        ):
            raise ValueError(f"Review requirement/status mismatch for {reference['case_id']}.")
        if reference["review_status"] == "pending":
            raise ValueError(f"Reference resolution is incomplete: {reference['case_id']}.")
        case = next(
            item for item in benchmark["cases"]
            if item["case_id"] == reference["case_id"]
        )
        if set(reference["dimension_rubric"]) != set(case["scoring_dimensions"]):
            raise ValueError(
                f"Dimension rubric must cover exactly the scored dimensions for "
                f"{reference['case_id']}."
            )
        if reference["scorer"].get("type") in {"numeric_values", "tool_arguments"}:
            expected = reference["scorer"].get("expected_values")
            if not isinstance(expected, dict) or not expected:
                raise ValueError(f"Numeric reference lacks expected values: {reference['case_id']}.")
            for name, value in expected.items():
                _decimal(value, f"{reference['case_id']} expected {name}")
                tolerance = _tolerance(reference, name)
                if tolerance < 0:
                    raise ValueError(f"Negative tolerance for {reference['case_id']}.")


def validate_benchmark(
    benchmark_path: Path = BENCHMARK_PATH,
    references_path: Path = REFERENCES_PATH,
) -> dict[str, Any]:
    benchmark, references = load_documents(benchmark_path, references_path)
    try:
        _validate_document_shape(benchmark)
        validate_references(benchmark, references)
        for pair in duplicate_prompt_pairs(
            benchmark_path,
            similarity_threshold=0.55,
        ):
            if pair["cross_split"] or not pair["same_family"]:
                raise ValueError(
                    f"Near-duplicate prompts are not isolated by family and split: "
                    f"{pair['case_ids']}."
                )
    except ValueError as exc:
        raise BenchmarkValidationError(str(exc)) from exc
    return {
        "benchmark_version": benchmark["benchmark_version"],
        "case_count": len(benchmark["cases"]),
        "category_counts": {
            category["id"]: sum(
                case["category"] == category["id"] for case in benchmark["cases"]
            )
            for category in benchmark["categories"]
        },
        "split_counts": {
            split: sum(case["split"] == split for case in benchmark["cases"])
            for split in sorted(SPLITS)
        },
        "reference_count": len(references["references"]),
        "family_count": len({case["family_id"] for case in benchmark["cases"]}),
        "review_status_counts": {
            status: sum(
                item["review_status"] == status
                for item in references["references"]
            )
            for status in (
                "approved",
                "proposed_reference",
                "revised",
                "not_required",
                "pending",
            )
        },
    }


def duplicate_prompt_pairs(
    benchmark_path: Path = BENCHMARK_PATH,
    *,
    similarity_threshold: float = 0.55,
) -> list[dict[str, Any]]:
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    cases = benchmark["cases"]
    findings: list[dict[str, Any]] = []
    for index, first in enumerate(cases):
        first_prompt = re.sub(r"\W+", " ", first["prompt"].casefold()).strip()
        for second in cases[index + 1 :]:
            second_prompt = re.sub(r"\W+", " ", second["prompt"].casefold()).strip()
            prompt_similarity = SequenceMatcher(None, first_prompt, second_prompt).ratio()
            if prompt_similarity >= similarity_threshold:
                findings.append(
                    {
                        "case_ids": [first["case_id"], second["case_id"]],
                        "splits": [first["split"], second["split"]],
                        "cross_split": first["split"] != second["split"],
                        "family_ids": [first["family_id"], second["family_id"]],
                        "same_family": first["family_id"] == second["family_id"],
                        "prompt_similarity": round(prompt_similarity, 3),
                    }
                )
    return findings


def model_input(
    case_id: str,
    mode: str,
    *,
    benchmark_path: Path = BENCHMARK_PATH,
) -> dict[str, Any]:
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    if mode not in MODES:
        raise ValueError(f"Unknown diagnostic mode: {mode}.")
    case = next((item for item in benchmark["cases"] if item["case_id"] == case_id), None)
    if case is None:
        raise KeyError(f"Unknown benchmark case: {case_id}.")
    if mode not in case["modes"]:
        raise ValueError(f"Mode {mode} is not allowed for case {case_id}.")

    projection: dict[str, Any] = {
        "mode": mode,
        "prompt": case["prompt"],
        "inputs": case["inputs"],
    }
    if mode == "context_assisted":
        cards = {card["id"]: card for card in benchmark["context_cards"]}
        projection["context_cards"] = [
            {"id": identifier, "text": cards[identifier]["text"]}
            for identifier in case["allowed_context_ids"]
        ]
    if mode == "tool_assisted":
        contracts = {tool["id"]: tool for tool in benchmark["tool_contracts"]}
        projection["tool_contracts"] = [
            contracts[identifier] for identifier in case["allowed_tool_ids"]
        ]
        if case.get("visible_tool_trace") is not None:
            projection["observed_tool_trace"] = case["visible_tool_trace"]
    return projection


def _reference(case_id: str) -> dict[str, Any]:
    _, references = load_documents()
    for item in references["references"]:
        if item["case_id"] == case_id:
            return item
    raise KeyError(f"Unknown benchmark reference: {case_id}.")


def _with_model_response_score(
    reference: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    declared_dimensions = set(reference.get("dimension_rubric", {}))
    deterministic_dimensions = _deterministic_dimensions(reference)
    raw_scores = result.get("dimension_scores", {})
    scored_dimensions: dict[str, int | str] = {}
    for dimension, score in raw_scores.items():
        if (
            dimension in declared_dimensions
            and dimension in deterministic_dimensions
            and (type(score) is int and score in {0, 1, 2} or score == "NA")
        ):
            scored_dimensions[dimension] = score
    unassessed_dimensions = sorted(declared_dimensions - set(scored_dimensions))
    failed = result.get("status") == "failed" or any(
        score == 0 for score in scored_dimensions.values()
    )
    response_status = (
        "failed"
        if failed
        else "scored"
        if not unassessed_dimensions
        else "partially_scored"
    )
    return {
        **result,
        "reference_validation": {
            "lifecycle_status": reference["review_status"],
            "human_certified": reference["review_status"] == "approved",
            "human_review_required": reference["human_review_required"],
            "operationally_usable": reference["review_status"] in {
                "approved",
                "proposed_reference",
                "not_required",
            },
        },
        "model_response_score": {
            "status": response_status,
            "dimension_scores": scored_dimensions,
            "dimension_dispositions": {
                dimension: scored_dimensions.get(
                    dimension,
                    "not_assessed_by_deterministic_scorer",
                )
                for dimension in sorted(declared_dimensions)
            },
            "not_assessed_dimensions": unassessed_dimensions,
            "aggregate_score": None,
        },
    }


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric, not boolean.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} is not a finite decimal.") from exc
    if not result.is_finite():
        raise ValueError(f"{label} is not finite.")
    return result


def _tolerance(reference: dict[str, Any], value_name: str) -> Decimal:
    tolerances = reference["tolerances"]
    candidate = tolerances.get(value_name)
    if candidate is None and "percent" in value_name.casefold():
        candidate = tolerances.get("percent")
    if candidate is None:
        candidate = tolerances.get("value")
    if candidate is None and len(tolerances) == 1:
        candidate = next(iter(tolerances.values()))
    if candidate is None:
        candidate = "0"
    return _decimal(candidate, f"{reference['case_id']} tolerance")


def score_reference_values(
    case_id: str,
    observed_values: dict[str, Any],
) -> dict[str, Any]:
    reference = _reference(case_id)
    scorer = reference["scorer"]
    if scorer.get("type") != "numeric_values":
        raise ValueError(f"Case {case_id} is not scored by numeric values.")
    expected_values = scorer["expected_values"]
    comparisons: dict[str, dict[str, Any]] = {}
    for name, expected in expected_values.items():
        if name not in observed_values:
            comparisons[name] = {"score": 0, "reason": "missing_observation"}
            continue
        actual_decimal = _decimal(observed_values[name], f"observed {name}")
        expected_decimal = _decimal(expected, f"expected {name}")
        tolerance = _tolerance(reference, name)
        passed = abs(actual_decimal - expected_decimal) <= tolerance
        comparisons[name] = {
            "score": 2 if passed else 0,
            "expected": str(expected_decimal),
            "observed": str(actual_decimal),
            "absolute_tolerance": str(tolerance),
        }
    unexpected = sorted(set(observed_values) - set(expected_values))
    all_correct = (
        all(item["score"] == 2 for item in comparisons.values())
        and not unexpected
    )
    unscored_rubric_dimensions = (
        set(reference["dimension_rubric"]) - {"numerical_correctness"}
    )
    status = (
        "failed"
        if not all_correct
        else "needs_review"
        if reference["review_status"] not in {"approved", "not_required"}
        or unscored_rubric_dimensions
        else "passed"
    )
    result = {
        "status": status,
        "dimension_scores": {
            "numerical_correctness": (
                2 if all_correct else 0
            )
        },
        "value_checks": comparisons,
        "unexpected_observations": unexpected,
        "human_review_status": (
            "pending"
            if reference["human_review_required"] or unscored_rubric_dimensions
            else reference["review_status"]
        ),
        "semantic_review_required": bool(unscored_rubric_dimensions),
        "unscored_rubric_dimensions": sorted(unscored_rubric_dimensions),
    }
    return _with_model_response_score(reference, result)


def score_json_output(case_id: str, output: str) -> dict[str, Any]:
    reference = _reference(case_id)
    scorer = reference["scorer"]
    if scorer.get("type") != "json_object":
        raise ValueError(f"Case {case_id} is not scored as strict JSON.")
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        result = {
            "status": "failed",
            "failure_category": "output_format_failure",
            "dimension_scores": {"output_instruction_compliance": 0},
        }
        return _with_model_response_score(reference, result)
    expected = scorer["expected_object"]
    valid = (
        isinstance(parsed, dict)
        and set(parsed) == set(scorer["exact_keys"])
        and all(
            type(parsed.get(key)) is type(value) and parsed.get(key) == value
            for key, value in expected.items()
        )
    )
    status = (
        "needs_review"
        if valid and reference["human_review_required"]
        else "passed" if valid else "failed"
    )
    result = {
        "status": status,
        "failure_category": None if valid else "output_format_failure",
        "dimension_scores": {"output_instruction_compliance": 2 if valid else 0},
        "human_review_status": reference["review_status"],
    }
    return _with_model_response_score(reference, result)


def score_sentence_count(case_id: str, output: str) -> dict[str, Any]:
    reference = _reference(case_id)
    scorer = reference["scorer"]
    if scorer.get("type") != "sentence_count":
        raise ValueError(f"Case {case_id} is not scored by sentence count.")
    sentences = _split_sentences(output.strip())
    count_is_correct = len(sentences) == scorer["expected_count"]
    no_abbreviations = not _contains_abbreviation(output)
    result = {
        "status": (
            "failed"
            if not count_is_correct or not no_abbreviations
            else "needs_review"
        ),
        "failure_category": (
            "output_format_failure"
            if not count_is_correct or not no_abbreviations
            else None
        ),
        "dimension_scores": {
            "output_instruction_compliance": (
                2 if count_is_correct and no_abbreviations else 0
            ),
            "sentence_count_compliance": 2 if count_is_correct else 0,
            "abbreviation_compliance": 2 if no_abbreviations else 0,
            "conceptual_correctness": "needs_review",
        },
        "human_review_status": "pending",
    }
    return _with_model_response_score(reference, result)


def _split_sentences(output: str) -> list[str]:
    sentences: list[str] = []
    abbreviation_spans = _abbreviation_spans(output)
    start = 0
    index = 0
    while index < len(output):
        character = output[index]
        if character not in ".!?":
            index += 1
            continue
        if any(start <= index < end for start, end in abbreviation_spans):
            index += 1
            continue
        if (
            character == "."
            and index > 0
            and index + 1 < len(output)
            and output[index - 1].isdigit()
            and output[index + 1].isdigit()
        ):
            index += 1
            continue
        end = index + 1
        while end < len(output) and output[end] in ".!?":
            end += 1
        if end == len(output) or output[end].isspace():
            sentence = output[start:end].strip()
            if sentence:
                sentences.append(sentence)
            start = end
        index = end
    remainder = output[start:].strip()
    if remainder:
        sentences.append(remainder)
    return sentences


_COMMON_ABBREVIATION_PATTERN = re.compile(
    r"\b(?:e\.g|i\.e|etc|Mr|Mrs|Ms|Dr|Prof|vs)\.",
    re.IGNORECASE,
)
_ACRONYM_PATTERN = re.compile(r"\b(?:[A-Z]{2,}|(?:[A-Z]\.){2,})")


def _contains_abbreviation(output: str) -> bool:
    return bool(_abbreviation_spans(output))


def _abbreviation_spans(output: str) -> list[tuple[int, int]]:
    matches = list(_COMMON_ABBREVIATION_PATTERN.finditer(output))
    matches.extend(_ACRONYM_PATTERN.finditer(output))
    return [(match.start(), match.end()) for match in matches]


def score_clarification(
    case_id: str,
    requested_fields: set[str],
    *,
    invented_values: set[str] | None = None,
) -> dict[str, Any]:
    reference = _reference(case_id)
    scorer = reference["scorer"]
    if scorer.get("type") != "clarification":
        raise ValueError(f"Case {case_id} is not scored as a clarification.")
    required = set(scorer["required_fields"])
    missing = sorted(required - requested_fields)
    invented = sorted(invented_values or set())
    complete = not missing and not invented
    result = {
        "status": (
            "needs_review"
            if reference["review_status"] == "revised" or complete
            else "failed"
        ),
        "failure_category": (
            None
            if complete
            else "unsupported_claim"
            if invented
            else "missing_context_error"
        ),
        "dimension_scores": {
            "clarification_completeness": 2 if not missing else 0,
            "unsupported_claims": 0 if invented else 2,
        },
        "requested_fields": sorted(requested_fields),
        "missing_fields": missing,
        "invented_values": invented,
        "human_review_status": reference["review_status"],
        "note": "The helper scores structured extracted observations, not raw prose.",
    }
    return _with_model_response_score(reference, result)


def score_tool_trace(
    case_id: str,
    observed_trace: dict[str, Any],
    *,
    claims_execution_succeeded: bool | None = None,
) -> dict[str, Any]:
    reference = _reference(case_id)
    scorer = reference["scorer"]
    if scorer.get("type") != "tool_trace":
        raise ValueError(f"Case {case_id} is not scored by a tool trace.")
    matches = (
        observed_trace.get("tool") == scorer["expected_tool"]
        and observed_trace.get("status") == scorer["expected_status"]
    )
    if "expected_error" in scorer:
        matches = matches and observed_trace.get("error") == scorer["expected_error"]
    if "expected_result" in scorer:
        matches = matches and observed_trace.get("result") == scorer["expected_result"]
    trace_status = observed_trace.get("status")
    if (
        claims_execution_succeeded is not None
        and claims_execution_succeeded != (trace_status == "succeeded")
    ):
        result = {
            "status": "failed",
            "failure_category": "unsupported_execution_claim",
            "dimension_scores": {
                "unsupported_claims": 0,
                "execution_claim_integrity": 0,
            },
            "execution_status": trace_status,
            "trace_matches": matches,
        }
        return _with_model_response_score(reference, result)
    if not matches:
        result = {
            "status": "failed",
            "failure_category": "execution_failure",
            "dimension_scores": {"tool_use_correctness": 0},
            "execution_status": trace_status,
            "trace_matches": False,
        }
        return _with_model_response_score(reference, result)
    if claims_execution_succeeded is None and scorer.get("must_not_claim_success"):
        result = {
            "status": "needs_review",
            "failure_category": None,
            "dimension_scores": {
                "unsupported_claims": "needs_review",
                "execution_claim_integrity": "needs_review",
                "tool_use_correctness": 2,
                "result_interpretation_error": "needs_review",
            },
            "human_review_status": "pending",
            "execution_status": trace_status,
            "trace_matches": True,
        }
        return _with_model_response_score(reference, result)
    result = {
        "status": "needs_review",
        "failure_category": None,
        "dimension_scores": {
            "tool_use_correctness": 2,
            "result_interpretation_error": "needs_review",
            "execution_claim_integrity": (
                2 if claims_execution_succeeded is not None else "needs_review"
            ),
        },
        "human_review_status": "pending",
        "execution_status": trace_status,
        "trace_matches": True,
    }
    return _with_model_response_score(reference, result)


def score_tool_arguments(
    case_id: str,
    tool_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    reference = _reference(case_id)
    scorer = reference["scorer"]
    if scorer.get("type") != "tool_arguments":
        raise ValueError(f"Case {case_id} is not scored by tool arguments.")
    if tool_id != scorer["tool_id"]:
        result = {
            "status": "failed",
            "failure_category": "tool_selection_error",
            "dimension_scores": {"tool_use_correctness": 0},
        }
        return _with_model_response_score(reference, result)
    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    contracts = {item["id"]: item for item in benchmark["tool_contracts"]}
    schema = contracts[tool_id]["arguments_schema"]
    properties = schema["properties"]
    valid_shape = (
        set(arguments) == set(schema["required"])
        and all(
            name in properties
            and (
                (properties[name]["type"] == "string" and isinstance(value, str))
                or (
                    properties[name]["type"] == "number"
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                )
            )
            for name, value in arguments.items()
        )
    )
    calculation_matches = False
    formula_matches = False
    if valid_shape:
        try:
            formula_matches = _matches_requested_percentage_formula(
                arguments["expression"]
            )
            actual = _evaluate_decimal_expression(arguments["expression"])
            expected = _decimal(
                scorer["expected_values"]["percent"],
                "expected tool result",
            )
            calculation_matches = abs(actual - expected) <= _tolerance(
                reference,
                "percent",
            )
        except (ValueError, ZeroDivisionError):
            calculation_matches = False
    matches = valid_shape and calculation_matches and formula_matches
    deterministic_dimensions = {
        "tool_use_correctness",
        "numerical_correctness",
        "formula_method_selection",
    }
    semantic_review_required = bool(
        set(reference.get("dimension_rubric", {})) - deterministic_dimensions
    )
    status = (
        "failed"
        if not matches
        else "needs_review"
        if reference["human_review_required"] or semantic_review_required
        else "passed"
    )
    failure_category = (
        None
        if matches
        else "invalid_tool_arguments"
        if not valid_shape
        else "formula_selection_error"
        if not formula_matches
        else "arithmetic_error"
    )
    result = {
        "status": status,
        "failure_category": failure_category,
        "dimension_scores": {
            "tool_use_correctness": 2 if valid_shape else 0,
            "numerical_correctness": 2 if calculation_matches else 0,
            "formula_method_selection": 2 if formula_matches else 0,
        },
        "human_review_status": (
            "pending"
            if reference["human_review_required"] or semantic_review_required
            else reference["review_status"]
        ),
        "semantic_review_required": semantic_review_required,
    }
    return _with_model_response_score(reference, result)


def _matches_requested_percentage_formula(expression: str) -> bool:
    try:
        parsed = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return False
    constants: list[int | float] = []

    def is_percentage_formula(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            if type(node.value) not in {int, float}:
                return False
            constants.append(node.value)
            return node.value in {18, 100, 600}
        return (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, (ast.Mult, ast.Div))
            and is_percentage_formula(node.left)
            and is_percentage_formula(node.right)
        )

    if not is_percentage_formula(parsed) or sorted(constants) != [18, 100, 600]:
        return False
    try:
        return _evaluate_decimal_expression(expression) == Decimal("3")
    except (ValueError, ZeroDivisionError):
        return False


def _evaluate_decimal_expression(expression: str) -> Decimal:
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid arithmetic expression.") from exc

    def evaluate(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return _decimal(node.value, "expression constant")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div),
        ):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise ZeroDivisionError("Division by zero.")
            return left / right
        raise ValueError("Only decimal constants and basic arithmetic are allowed.")

    return evaluate(parsed)


def score_dimensions(
    case_id: str,
    scores: dict[str, int | str],
) -> dict[str, Any]:
    benchmark, _ = load_documents()
    case = next((item for item in benchmark["cases"] if item["case_id"] == case_id), None)
    if case is None:
        raise KeyError(f"Unknown benchmark case: {case_id}.")
    if not set(scores).issubset(SCORING_DIMENSIONS):
        raise ValueError(f"Unknown scoring dimension for {case_id}.")
    if any(
        dimension not in case["scoring_dimensions"] and value != "NA"
        for dimension, value in scores.items()
    ):
        raise ValueError(f"Score includes an inapplicable dimension for {case_id}.")
    if any(
        not (
            (type(value) is int and value in {0, 1, 2})
            or (isinstance(value, str) and value in {"NA", "needs_review"})
        )
        for value in scores.values()
    ):
        raise ValueError("Dimension scores must be 0, 1, 2, NA, or needs_review.")
    reference = _reference(case_id)
    applicable_dimensions = set(reference["dimension_rubric"])
    missing_dimensions = sorted(applicable_dimensions - set(scores))
    scored_applicable_dimensions = applicable_dimensions & set(scores)
    scoring_completeness = (
        "unscored"
        if not scored_applicable_dimensions
        else "partially_scored"
        if missing_dimensions
        else "complete"
    )
    has_review = (
        reference["human_review_required"]
        or "needs_review" in scores.values()
    )
    if has_review:
        status = "needs_review"
    elif scoring_completeness == "unscored":
        status = "unscored"
    elif scoring_completeness == "partially_scored":
        status = "partially_scored"
    else:
        status = "scored"
    return {
        "status": status,
        "reference_validation": {
            "lifecycle_status": reference["review_status"],
            "human_certified": reference["review_status"] == "approved",
            "human_review_required": reference["human_review_required"],
            "operationally_usable": reference["review_status"] in {
                "approved",
                "proposed_reference",
                "not_required",
            },
        },
        "dimension_scores": scores,
        "human_review_status": (
            "pending"
            if has_review
            else "incomplete"
            if scoring_completeness != "complete"
            else "complete"
        ),
        "scoring_completeness": scoring_completeness,
        "missing_dimensions": missing_dimensions,
        "aggregate_score": None,
    }


def _verified_numeric_references(
    benchmark: dict[str, Any],
) -> dict[str, dict[str, Decimal]]:
    D = lambda value: _decimal(value, "benchmark input")
    inputs = {case["case_id"]: case["inputs"] for case in benchmark["cases"]}
    verified: dict[str, dict[str, Decimal]] = {}

    stage_hours = inputs["ba_order_bottleneck"]["stage_hours"]
    verified["ba_order_bottleneck"] = {
        "bottleneck_hours": max(D(value) for value in stage_hours.values())
    }
    pilot = inputs["ba_evidence_recommendation"]
    difference = D(pilot["before_mean_days"]) - D(pilot["after_mean_days"])
    verified["ba_evidence_recommendation"] = {
        "difference_days": difference,
        "relative_reduction_percent": difference / D(pilot["before_mean_days"]) * 100,
    }
    join = inputs["da_one_to_many_join"]["orders_per_customer"]
    verified["da_one_to_many_join"] = {
        "joined_rows": sum(D(value) for value in join.values())
    }
    for case_id, source, names in (
        (
            "da_segment_rates",
            inputs["da_segment_rates"],
            {"North": ("returns", "orders"), "South": ("returns", "orders")},
        ),
        (
            "calc_region_ratio",
            inputs["calc_region_ratio"],
            {"North": ("numerator", "denominator"), "South": ("numerator", "denominator")},
        ),
    ):
        verified[case_id] = {
            f"{region}_percent": D(source[region][numerator]) / D(
                source[region][denominator]
            ) * 100
            for region, (numerator, denominator) in names.items()
        }
    groups = inputs["da_weighted_vs_unweighted"]["groups"]
    verified["da_weighted_vs_unweighted"] = {
        "weighted_mean": sum(D(g["count"]) * D(g["mean"]) for g in groups)
        / sum(D(g["count"]) for g in groups),
        "simple_mean": sum(D(g["mean"]) for g in groups) / D(len(groups)),
    }
    verified["da_insight_communication"] = {
        "difference_percentage_points": D(
            inputs["da_insight_communication"]["current_rate_percent"]
        )
        - D(inputs["da_insight_communication"]["prior_rate_percent"])
    }

    pct = inputs["calc_pct_change"]
    verified["calc_pct_change"] = {
        "percent_change": (D(pct["new"]) - D(pct["original"]))
        / D(pct["original"])
        * 100
    }
    profit = inputs["calc_profit"]
    verified["calc_profit"] = {"profit": D(profit["revenue"]) - D(profit["costs"])}
    margin = inputs["calc_margin"]
    verified["calc_margin"] = {
        "margin_percent": D(margin["profit"]) / D(margin["revenue"]) * 100
    }
    conversion = inputs["calc_conversion_denominator"]
    verified["calc_conversion_denominator"] = {
        "conversion_percent": D(conversion["conversions"])
        / D(conversion["eligible_leads"])
        * 100
    }
    weighted = inputs["calc_weighted_avg"]["groups"]
    verified["calc_weighted_avg"] = {
        "weighted_average": sum(D(group["count"]) * D(group["mean"]) for group in weighted)
        / sum(D(group["count"]) for group in weighted)
    }
    cagr = inputs["calc_cagr"]
    verified["calc_cagr"] = {
        "cagr_percent": (
            D(cagr["ending"]) / D(cagr["beginning"])
        ) ** (D("1") / D(cagr["years"])) * 100 - 100
    }
    units = inputs["calc_units"]
    verified["calc_units"] = {
        "kilograms": D(units["tonnes"]) * D(units["kilograms_per_tonne"])
    }
    multi = inputs["calc_multistep_profit_margin"]
    revenue_total = D(multi["units"]) * D(multi["price_per_unit"])
    costs_total = D(multi["variable_costs"]) + D(multi["fixed_costs"])
    profit_total = revenue_total - costs_total
    verified["calc_multistep_profit_margin"] = {
        "revenue": revenue_total,
        "costs": costs_total,
        "profit": profit_total,
        "margin_percent": profit_total / revenue_total * 100,
    }

    values = [D(value) for value in inputs["stat_mean_median_outlier"]["values"]]
    ordered = sorted(values)
    verified["stat_mean_median_outlier"] = {
        "mean": sum(values) / D(len(values)),
        "median": (ordered[1] + ordered[2]) / 2,
    }
    variance = inputs["stat_population_sample"]
    variance_values = [D(value) for value in variance["values"]]
    variance_mean = sum(variance_values) / D(len(variance_values))
    squared_deviations = sum((value - variance_mean) ** 2 for value in variance_values)
    verified["stat_population_sample"] = {
        "population_variance": squared_deviations / D(variance["population_denominator"]),
        "sample_variance": squared_deviations / D(variance["sample_denominator"]),
    }
    sorted_values = [D(value) for value in inputs["stat_iqr"]["sorted_values"]]
    lower_half, upper_half = sorted_values[:4], sorted_values[4:]
    q1 = (lower_half[1] + lower_half[2]) / 2
    q3 = (upper_half[1] + upper_half[2]) / 2
    verified["stat_iqr"] = {"Q1": q1, "Q3": q3, "IQR": q3 - q1}
    conditional = inputs["stat_conditional_probability"]
    verified["stat_conditional_probability"] = {
        "conditional_probability_percent": D(conditional["mobile_users_who_renew"])
        / D(conditional["mobile_users"])
        * 100
    }
    multiple = inputs["stat_multiple_comparisons"]
    verified["stat_multiple_comparisons"] = {
        "expected_false_positives": D(multiple["tests"]) * D(multiple["per_test_alpha"])
    }

    monthly: dict[str, Decimal] = {}
    for row in inputs["data_monthly_margin"]["rows"]:
        row_profit = D(row["revenue"]) - D(row["costs"])
        monthly[f"{row['month']}_profit"] = row_profit
        monthly[f"{row['month']}_margin_percent"] = row_profit / D(row["revenue"]) * 100
    verified["data_monthly_margin"] = monthly
    sales = inputs["data_sales_vs_margin"]
    verified["data_sales_vs_margin"] = {
        f"{region}_margin_percent": D(values["profit"]) / D(values["revenue"]) * 100
        for region, values in sales.items()
    }
    unequal = inputs["data_unequal_group_sizes"]
    verified["data_unequal_group_sizes"] = {
        f"{segment}_percent": D(values["successes"]) / D(values["trials"]) * 100
        for segment, values in unequal.items()
    }
    simpson = inputs["data_simpsons_paradox"]
    simpson_rates: dict[str, Decimal] = {}
    for stratum, treatments in simpson.items():
        for treatment, values in treatments.items():
            stratum_name = stratum.removesuffix("_risk")
            simpson_rates[f"{treatment}_{stratum_name}_percent"] = (
                D(values["successes"]) / D(values["trials"]) * 100
            )
    for treatment in ("A", "B"):
        successes = sum(
            D(simpson[stratum][treatment]["successes"])
            for stratum in ("high_risk", "low_risk")
        )
        trials = sum(
            D(simpson[stratum][treatment]["trials"])
            for stratum in ("high_risk", "low_risk")
        )
        simpson_rates[f"{treatment}_overall_percent"] = successes / trials * 100
    verified["data_simpsons_paradox"] = simpson_rates
    arguments = inputs["tool_arguments_valid"]["requested_operation"]
    verified["tool_arguments_valid"] = {"percent": _evaluate_decimal_expression(arguments)}
    return verified


def validate_numeric_references(
    references_path: Path = REFERENCES_PATH,
    benchmark_path: Path = BENCHMARK_PATH,
) -> int:
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    references = json.loads(references_path.read_text(encoding="utf-8"))
    reference_by_id = {item["case_id"]: item for item in references["references"]}
    verified = _verified_numeric_references(benchmark)
    for case_id, values in verified.items():
        expected = reference_by_id[case_id]["scorer"]["expected_values"]
        if set(values) != set(expected):
            raise ValueError(f"Reference calculation keys mismatch for {case_id}.")
        for name, calculated in values.items():
            stored = _decimal(expected[name], f"{case_id} reference")
            tolerance = _tolerance(reference_by_id[case_id], name)
            if abs(stored - calculated) > tolerance:
                raise ValueError(f"Independent calculation mismatch: {case_id}.{name}.")
    return len(verified)


def validate_sql_reference() -> list[tuple[Any, ...]]:
    benchmark, references = load_documents()
    case = next(item for item in benchmark["cases"] if item["case_id"] == "tool_sql_group_query")
    schema = case["inputs"]["schema"]
    if schema != "orders(region TEXT, revenue NUMERIC)":
        raise ValueError("Unexpected SQL fixture schema.")
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE orders (region TEXT, revenue NUMERIC)")
        connection.executemany(
            "INSERT INTO orders(region, revenue) VALUES (?, ?)",
            case["inputs"]["rows"],
        )
        reference = next(item for item in references["references"] if item["case_id"] == case["case_id"])
        query = reference["scorer"]["sql"].strip()
        if not query.casefold().startswith("select "):
            raise ValueError("Reference SQL must be one SELECT statement.")
        rows, columns = _execute_fixture_sql(connection, query)
        if columns != reference["scorer"]["expected_columns"]:
            raise ValueError("SQL fixture columns do not match the stored reference.")
        expected_rows = [tuple(row) for row in reference["scorer"]["expected_rows"]]
        if rows != expected_rows:
            raise ValueError("SQL fixture results do not match the stored reference.")
        return rows
    finally:
        connection.close()


def validate_baseline_readiness() -> dict[str, Any]:
    structure = validate_benchmark()
    numeric_reference_count = validate_numeric_references()
    sql_rows = validate_sql_reference()
    _, references = load_documents()
    proposal_count = sum(
        item["review_status"] == "proposed_reference"
        for item in references["references"]
    )
    unassessed_dimension_count = sum(
        policy["disposition"] == "not_assessed_by_deterministic_scorer"
        for reference in references["references"]
        if reference["review_status"] == "proposed_reference"
        for policy in reference["operational_scoring"].values()
    )
    return {
        "ready_for_offline_diagnostic": (
            structure["case_count"] == 60
            and proposal_count == 35
            and numeric_reference_count == 25
            and sql_rows == [("East", 25), ("West", 12)]
        ),
        "reference_lifecycle_counts": structure["review_status_counts"],
        "proposed_reference_count": proposal_count,
        "numerically_verified_reference_count": numeric_reference_count,
        "sql_fixture_rows": sql_rows,
        "explicitly_unassessed_dimension_count": unassessed_dimension_count,
        "human_semantic_approval_claimed": False,
        "model_inference_performed": False,
    }


def _execute_fixture_sql(
    connection: sqlite3.Connection,
    query: str,
) -> tuple[list[tuple[Any, ...]], list[str]]:
    if not query.strip() or len(query) > 10_000:
        raise ValueError("SQL query is empty or exceeds the fixture limit.")
    allowed_actions = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
    }

    def authorize(
        action: int,
        arg1: str | None,
        arg2: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        del database, source
        if action not in allowed_actions:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            return (
                sqlite3.SQLITE_OK
                if arg1 == "orders" and arg2 in {"region", "revenue"}
                else sqlite3.SQLITE_DENY
            )
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (arg2 or arg1 or "").casefold()
            return sqlite3.SQLITE_OK if function_name == "sum" else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    operation_callbacks = 0

    def limit_operations() -> int:
        nonlocal operation_callbacks
        operation_callbacks += 1
        return int(operation_callbacks > 100)

    connection.set_authorizer(authorize)
    connection.set_progress_handler(limit_operations, 1_000)
    try:
        cursor = connection.execute(query)
        rows = cursor.fetchmany(1_001)
        if len(rows) > 1_000:
            raise ValueError("SQL fixture result exceeded the row limit.")
        columns = [description[0] for description in cursor.description or ()]
        return [tuple(row) for row in rows], columns
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_authorizer(None)


def score_sql_output(case_id: str, output: str) -> dict[str, Any]:
    reference = _reference(case_id)
    scorer = reference["scorer"]
    if scorer.get("type") != "sql_fixture":
        raise ValueError(f"Case {case_id} is not scored as a SQL fixture.")
    output_only_sql = bool(re.match(r"^\s*SELECT\b", output, re.IGNORECASE))
    result: dict[str, Any] = {
        "status": "failed",
        "failure_category": "output_format_failure" if not output_only_sql else None,
        "dimension_scores": {
            "output_instruction_compliance": 2 if output_only_sql else 0,
            "sql_safety": 0,
            "tool_use_correctness": 0,
            "query_result_correctness": 0,
            "query_intent": "needs_review",
        },
        "human_review_status": reference["review_status"],
    }
    if not output_only_sql:
        return _with_model_response_score(reference, result)
    benchmark, _ = load_documents()
    case = next(item for item in benchmark["cases"] if item["case_id"] == case_id)
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE orders (region TEXT, revenue NUMERIC)")
        connection.executemany(
            "INSERT INTO orders(region, revenue) VALUES (?, ?)",
            case["inputs"]["rows"],
        )
        rows, columns = _execute_fixture_sql(connection, output)
    except (sqlite3.Error, ValueError) as exc:
        result["execution_error"] = str(exc)
        result["failure_category"] = (
            "invalid_tool_arguments"
            if isinstance(exc, sqlite3.Error)
            else "output_format_failure"
        )
        return _with_model_response_score(reference, result)
    finally:
        connection.close()

    expected_rows = [tuple(row) for row in scorer["expected_rows"]]
    columns_match = columns == scorer.get("expected_columns", [])
    rows_match = _typed_rows_equal(rows, expected_rows)
    result["dimension_scores"].update(
        {
            "sql_safety": 2,
            "tool_use_correctness": 2 if columns_match and rows_match else 0,
            "query_result_correctness": 2 if columns_match and rows_match else 0,
        }
    )
    result["failure_category"] = (
        None if columns_match and rows_match else "result_interpretation_error"
    )
    result["status"] = "needs_review" if columns_match and rows_match else "failed"
    result["columns"] = columns
    result["rows"] = rows
    result["note"] = (
        "Result and SQLite authorization were checked on the synthetic fixture; "
        "query intent still requires review. Generated SQL was not run against a real database."
    )
    return _with_model_response_score(reference, result)


def _typed_rows_equal(
    observed: list[tuple[Any, ...]],
    expected: list[tuple[Any, ...]],
) -> bool:
    if len(observed) != len(expected):
        return False
    for observed_row, expected_row in zip(observed, expected):
        if len(observed_row) != len(expected_row):
            return False
        for actual, gold in zip(observed_row, expected_row):
            if type(gold) is str:
                if type(actual) is not str or actual != gold:
                    return False
            else:
                try:
                    if _decimal(actual, "SQL observed value") != _decimal(
                        gold,
                        "SQL expected value",
                    ):
                        return False
                except ValueError:
                    return False
    return True


def score_case(case_id: str, output: str) -> dict[str, Any]:
    reference = _reference(case_id)
    scorer_type = reference["scorer"].get("type")
    if scorer_type == "json_object":
        result = score_json_output(case_id, output)
    elif scorer_type == "sentence_count":
        result = score_sentence_count(case_id, output)
    elif scorer_type == "sql_fixture":
        result = score_sql_output(case_id, output)
    elif scorer_type == "clarification":
        result = {
            "status": "needs_review",
            "failure_category": None,
            "dimension_scores": {"clarification_completeness": "needs_review"},
            "human_review_status": reference["review_status"],
            "note": (
                "Use score_clarification with structured extracted fields; "
                "raw prose is not automatically classified."
            ),
        }
    elif scorer_type == "human_review":
        result = {
            "status": "needs_review",
            "failure_category": None,
            "dimension_scores": {
                dimension: "needs_review"
                for dimension in reference["dimension_rubric"]
            },
            "human_review_status": reference["review_status"],
        }
    else:
        result = {
            "status": "needs_review",
            "failure_category": None,
            "dimension_scores": {},
            "human_review_status": reference["review_status"],
            "note": "Provide validated structured observations to the matching scorer; free-form semantic text is not keyword-scored.",
        }
    return _with_model_response_score(reference, result)
