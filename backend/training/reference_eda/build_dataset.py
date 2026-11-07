from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from training.reference_eda.corpus import (
    SCENARIOS,
    build_examples,
    corpus_counts,
    fixture_payload,
    metric_summary,
    profile_for,
    validate_sql_examples,
)
from training.validate_sft_dataset import validate_dataset


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PACKAGE_DIR / "dataset_v1"
SPLIT_FILENAMES = {
    "train": "train.jsonl",
    "validation": "validation.jsonl",
    "test": "held_out.jsonl",
}


def _write_jsonl(path: Path, examples: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for example in examples:
            output.write(
                json.dumps(example, ensure_ascii=False, sort_keys=True) + "\n"
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evaluation_suite(examples: list[dict[str, Any]]) -> dict[str, Any]:
    scenarios = {scenario.key: scenario for scenario in SCENARIOS}
    weighted_rubric = {
        "business_understanding": 15,
        "analytical_correctness": 20,
        "tool_selection_and_execution_discipline": 15,
        "kpi_and_statistical_interpretation": 15,
        "evidence_and_hallucination_resistance": 15,
        "stakeholder_communication": 10,
        "recommendations_and_next_steps": 10,
    }
    case_review_criteria = {
        "marketing": {
            "acceptance_criteria": [
                "Compare qualified leads and qualified CPA, not raw lead volume alone.",
                "Use attributed net revenue divided by spend for ROAS and name the last-click/30-day assumptions.",
                "State that aggregate fixture rows cannot independently audit attribution and do not establish causal lift.",
                "Treat zero-denominator CPA and ROAS as undefined; do not report CTR because impressions are absent.",
                "Recommend a controlled budget experiment rather than a causal conclusion.",
            ],
            "rejection_criteria": [
                "Choose a channel solely because it has more leads.",
                "Describe attributed ROAS as incremental or causal return.",
                "Invent impression counts, CTR, touch-level attribution evidence, or a zero-denominator result.",
            ],
            "rubric": {
                "qualified_acquisition_comparison": 20,
                "roas_and_cpa_calculation": 25,
                "attribution_and_causality_limits": 20,
                "evidence_and_denominator_handling": 20,
                "actionable_experiment": 15,
            },
        },
        "maintenance": {
            "acceptance_criteria": [
                "Define the target as confirmed failure strictly after the observation and within 30 days.",
                "Use only sensor observations available at the prediction timestamp as features.",
                "Call out incomplete sensor coverage and communicate uncertainty without confirming failure.",
                "Recommend qualified inspection rather than automatic shutdown.",
                "Do not interpret repeated positive observation rows as unique failure-event counts.",
            ],
            "rejection_criteria": [
                "Use future failure timestamps or post-observation readings as model features.",
                "State that a predicted risk proves a machine failure.",
                "Recommend automatic shutdown solely from the supplied risk score or incomplete sensors.",
            ],
            "rubric": {
                "horizon_and_label_definition": 20,
                "time_series_leakage_control": 25,
                "sensor_uncertainty_and_risk_language": 20,
                "safe_maintenance_action": 20,
                "fixture_scope_and_evidence": 15,
            },
        },
        "ecommerce": {
            "acceptance_criteria": [
                "Calculate net line revenue as quantity times unit price less allocated line discount and refund.",
                "Exclude cancelled lines and calculate COGS at line grain without multiplying order-level facts.",
                "Describe the result as fixture net line sales, not complete accounting revenue; tax and shipping are absent.",
                "Return an undefined margin when net revenue is zero.",
                "Reconcile competing dashboard and SQL values before selecting either.",
            ],
            "rejection_criteria": [
                "Multiply order-level totals across line joins or deduct discounts more than once.",
                "Ignore refunds or include cancelled lines under the fixture definition.",
                "Report a defined margin for a zero-revenue group or invent unavailable tax/shipping.",
            ],
            "rubric": {
                "metric_definition_and_grain": 25,
                "discount_refund_cancellation_treatment": 20,
                "margin_and_zero_denominator": 20,
                "scope_and_reconciliation": 20,
                "clear_stakeholder_explanation": 15,
            },
        },
        "fraud": {
            "acceptance_criteria": [
                "Calculate precision and recall only among completed, verified reviews.",
                "Distinguish suspicious flagged transactions from confirmed fraud.",
                "Explain false-positive investigation cost, false-negative harm, and review capacity.",
                "Use compatible validation data for any threshold comparison and do not tune on held-out data.",
                "Recommend human investigation rather than automatic fraud confirmation.",
            ],
            "rejection_criteria": [
                "Treat every flag or risk score as confirmed fraud.",
                "Report precision or recall using unreviewed labels or mismatched populations.",
                "Recommend a threshold without considering operational costs or validation evidence.",
            ],
            "rubric": {
                "verified_label_population": 20,
                "precision_recall_correctness": 25,
                "error_cost_and_capacity_tradeoff": 25,
                "threshold_evaluation_discipline": 15,
                "safe_action_and_communication": 15,
            },
        },
        "healthcare": {
            "acceptance_criteria": [
                "Use eligible index discharges with complete 30-day follow-up as the rate denominator.",
                "Count unplanned readmissions within 30 days and exclude planned readmissions from the numerator.",
                "Exclude features unavailable at discharge and do not present a leaky risk score.",
                "Frame risk as decision support and retain qualified human clinical review.",
                "Use only synthetic non-identifying episode data and communicate the small-sample limitation.",
            ],
            "rejection_criteria": [
                "Use all encounters or incomplete follow-up as the denominator without qualification.",
                "Mix planned readmissions into the unplanned outcome or use post-discharge features.",
                "Recommend automatic clinical intervention or describe the score as a diagnosis.",
            ],
            "rubric": {
                "eligible_denominator_and_outcome": 25,
                "followup_and_planned_readmission_rules": 20,
                "feature_leakage_and_privacy": 20,
                "clinical_human_review": 20,
                "evidence_and_limitations": 15,
            },
        },
    }
    critical_cases = {
        "marketing": {
            "prompt": (
                "Facebook generates more leads, but Google produces more qualified "
                "leads per rupee. Which channel should receive more budget?"
            ),
            "expected_behavior": (
                "Compare qualified leads, cost per qualified lead, attributed net "
                "revenue, and ROAS under the stated last-click, 30-day attribution "
                "window. Explain that attribution is not causal evidence and "
                "recommend a controlled budget experiment before reallocating."
            ),
        },
        "maintenance": {
            "prompt": (
                "A machine has a 78% predicted failure risk, but its recent sensor "
                "readings are incomplete. What should the operations team do?"
            ),
            "expected_behavior": (
                "Describe the 78% as a model risk estimate, not a confirmed failure. "
                "Call out incomplete sensor coverage and prediction-horizon "
                "uncertainty, verify readings, and recommend qualified inspection "
                "rather than automatic shutdown. Features must not use observations "
                "after the prediction timestamp."
            ),
        },
        "ecommerce": {
            "prompt": (
                "Power BI reports ₹12.8 lakh in revenue while SQL reports ₹12.1 "
                "lakh. Which number is correct?"
            ),
            "expected_behavior": (
                "Do not choose a value before reconciling metric definitions, "
                "filters, order versus line grain, join cardinality, allocated "
                "discounts, refunds, cancellations, tax, and shipping. State that "
                "the fixture's net line revenue subtracts allocated discounts and "
                "refunds and excludes cancelled lines; aggregate order facts before "
                "joining to lines."
            ),
        },
        "fraud": {
            "prompt": (
                "Fraud recall improved after lowering the alert threshold, but "
                "false positives also increased. Should we keep the new threshold?"
            ),
            "expected_behavior": (
                "Explain the recall-versus-investigation-workload trade-off using "
                "confirmed, reviewed fraud labels. Quantify precision and recall "
                "only from compatible validation results; include false-positive "
                "and false-negative costs and review capacity. A flagged transaction "
                "is suspicious, not confirmed fraud."
            ),
        },
        "healthcare": {
            "prompt": (
                "A patient receives a high 30-day readmission-risk score. What "
                "action should the care team take?"
            ),
            "expected_behavior": (
                "Treat the score as decision support, not a diagnosis or automatic "
                "clinical intervention. Confirm eligible discharge and unplanned "
                "30-day outcome definitions, guard against post-discharge feature "
                "leakage, and retain qualified human clinical review."
            ),
        },
    }
    cases = []
    for example in examples:
        if example["split"] != "test" or example["category"] == "tool_selection":
            continue
        scenario_key = example["id"].split("-")[1]
        scenario = scenarios[scenario_key]
        review_criteria = case_review_criteria[scenario_key]
        user_prompt = "\n\n".join(
            message["content"]
            for message in example["messages"]
            if message["role"] == "user"
        )
        reference = example["messages"][-1]["content"]
        critical_case = (
            critical_cases.get(scenario_key)
            if example["id"].endswith("-results")
            else None
        )
        if critical_case:
            user_prompt = critical_case["prompt"]
            reference = critical_case["expected_behavior"]
        cases.append(
            {
                "id": example["id"],
                "category": example["category"],
                "purpose": (
                    "Held-out synthetic business-analytics behavior; individual "
                    "record review is required before evaluation use."
                ),
                "prompt": user_prompt,
                "expected_behavior": reference,
                "dataset_profile": {
                    "scenario": scenario.key,
                    "title": scenario.title,
                    "grain": scenario.grain,
                    "columns": scenario.columns,
                    "profile": profile_for(scenario),
                    "deterministic_fixture_results": metric_summary(scenario),
                    "definitions": scenario.definitions,
                },
                "mandatory_facts": [
                    scenario.grain,
                    *scenario.definitions.values(),
                    scenario.topics[-1].pitfall,
                ],
                "acceptance_criteria": review_criteria["acceptance_criteria"],
                "rejection_criteria": review_criteria["rejection_criteria"],
                "case_rubric": review_criteria["rubric"],
                "incorrect_behaviors": [
                    "Inventing a calculation, tool result, source field, or causal explanation.",
                    "Using an undefined, inconsistent, or unsupported denominator.",
                    "Presenting a risk, flag, attribution, or descriptive association as a confirmed outcome or cause.",
                    "Omitting material data limitations or required human review.",
                ],
                "scorer": "human_review",
                "scoring_rubric": weighted_rubric,
                "human_review_rubric": [
                    "Scores each weighted dimension using only supplied evidence.",
                    "Checks the mandatory facts and penalizes every listed incorrect behavior.",
                    "Uses only registered tools and accurately distinguishes proposed from executed work.",
                    "Keeps recommendations proportional to fixture size and operational risk.",
                ],
                "validation_status": "draft",
                "reviewer_notes": (
                    "Draft evaluation case; independently inspect the prompt, fixture "
                    "calculations, expected behavior, and rubric before approval."
                ),
            }
        )
    return {
        "suite_version": "insightos-reference-eda-human-review-v2",
        "description": (
            "Group-held-out synthetic cases spanning marketing, predictive "
            "maintenance, e-commerce, fraud, and healthcare. The weighted rubric "
            "totals 100 points. All cases require human review; "
            "tool-call held-outs remain in held_out.jsonl for workflow evaluation."
        ),
        "split_policy": {
            "train": "fine-tuning only",
            "validation": "training configuration and overfitting monitoring",
            "test": "final generalization evaluation only; never use to tune training or rewrite examples",
            "grouping": "all examples from a scenario topic and synthetic fixture stay in one split",
            "scenario_coverage": (
                "The versioned dataset split policy determines scenario coverage; "
                "domain-held-out releases intentionally hold out domains."
            ),
        },
        "cases": cases,
    }


def _validate_combined(examples: list[dict[str, Any]]) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".jsonl", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        for example in examples:
            temporary.write(json.dumps(example, ensure_ascii=False) + "\n")
    try:
        return validate_dataset(temporary_path, require_approved=False)
    finally:
        temporary_path.unlink(missing_ok=True)


def build_dataset(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    examples = build_examples()
    counts = corpus_counts(examples)
    if counts["examples"] != 250:
        raise ValueError(f"Expected 250 examples; generated {counts['examples']}")
    if counts["leaking_groups"]:
        raise ValueError(f"Groups cross split boundaries: {counts['leaking_groups']}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for split, filename in SPLIT_FILENAMES.items():
        _write_jsonl(
            output_dir / filename,
            [example for example in examples if example["split"] == split],
        )
    fixtures_path = output_dir / "synthetic_fixtures.json"
    fixtures_path.write_text(
        json.dumps(fixture_payload(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    suite_path = output_dir / "human_review_suite.json"
    suite = _evaluation_suite(examples)
    suite_path.write_text(
        json.dumps(suite, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    schema_validation = _validate_combined(examples)
    code_examples = validate_sql_examples(examples)
    from training.reference_eda.validate_dataset import (
        validate_dataset as validate_artifacts,
    )

    artifact_validation = validate_artifacts(output_dir)
    tool_calls = Counter(
        call["function"]["name"]
        for example in examples
        for message in example["messages"]
        for call in message.get("tool_calls", [])
    )
    report: dict[str, Any] = {
        "dataset_version": "insightos-reference-eda-v2",
        "generated_by": "training/reference_eda/build_dataset.py",
        "source_note": (
            "Original synthetic examples only. The referenced EDA PDF and source "
            "archive were unavailable; the user-supplied reference_eda_reviewed.zip "
            "was inspected as a review input."
        ),
        "scenario_keys": [scenario.key for scenario in SCENARIOS],
        "scenario_coverage_by_split": {
            split: sorted(
                {
                    example["id"].split("-")[1]
                    for example in examples
                    if example["split"] == split
                }
            )
            for split in ("train", "validation", "test")
        },
        **counts,
        "schema_validation": schema_validation,
        "artifact_validation": artifact_validation,
        "validated_code_examples": code_examples,
        "tool_call_counts": dict(sorted(tool_calls.items())),
        "evaluation_cases": len(suite["cases"]),
        "review_manifest_sha256": _sha256(PACKAGE_DIR / "review_manifest.json"),
        "human_review": {
            "training_examples": "remediation implemented; individual review pending",
            "split_structure": "approved; content review pending",
            "evaluation_suite": "scaffold expanded with case-level rubrics; individual review pending",
            "synthetic_calculations": "verified with stated conditions",
            "claim_validation": "fail-closed policy retained; advanced coverage incomplete",
            "approved_record_count": 0,
            "fine_tuning_authorized": False,
        },
        "training_ready": schema_validation["training_ready"],
        "training_readiness_reason": (
            "Training examples remain draft-only until explicitly approved by a "
            "qualified human."
            if not schema_validation["training_ready"]
            else "Training examples are marked approved; run train_lora.py preflight "
            "to verify reviewer identity, hardware, dependencies, and split integrity."
        ),
        "file_sha256": {
            filename: _sha256(output_dir / filename)
            for filename in (
                *SPLIT_FILENAMES.values(),
                fixtures_path.name,
                suite_path.name,
            )
        },
    }
    report_path = output_dir / "dataset_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and validate the draft-only synthetic EDA corpus."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    try:
        report = build_dataset(args.output_dir)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Dataset build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
