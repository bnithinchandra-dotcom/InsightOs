from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_REPORT = ROOT / "qwen3-8b-baseline-corrected-evaluation-v1.md"
RUNNER_PATH = ROOT / "kaggle_baseline_runner.py"


def _runner_namespace() -> dict[str, Any]:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    marker = "# %% [RUN_BASELINE]"
    if source.count(marker) != 1:
        raise ValueError(f"{RUNNER_PATH.name} has an unexpected baseline marker")
    namespace: dict[str, Any] = {}
    exec(compile(source.split(marker, maxsplit=1)[0], str(RUNNER_PATH), "exec"), namespace)
    return namespace


def _archive_files(archive: zipfile.ZipFile) -> dict[str, bytes]:
    names = archive.namelist()
    if len(names) != len(set(names)) or "execution_manifest.json" not in names:
        raise ValueError("Export has duplicate paths or no execution manifest")
    manifest = json.loads(archive.read("execution_manifest.json"))
    listed_files = {
        item["path"]: item["sha256"]
        for item in manifest.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    if set(names) - {"execution_manifest.json"} != set(listed_files):
        raise ValueError("Export contents do not match the execution manifest")
    contents: dict[str, bytes] = {}
    for path, expected_hash in listed_files.items():
        payload = archive.read(path)
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise ValueError(f"Export file hash mismatch: {path}")
        contents[path] = payload
    return contents


def _json_file(files: dict[str, bytes], name: str) -> Any:
    try:
        return json.loads(files[name])
    except KeyError as error:
        raise ValueError(f"Export is missing {name}") from error


def _render_rate(rate: dict[str, Any]) -> str:
    value = rate.get("value")
    if value is None:
        return f"{rate.get('numerator', 0)}/{rate.get('denominator', 0)} (n/a)"
    return f"{rate['numerator']}/{rate['denominator']} ({value:.1%})"


def reevaluate_export(export_path: Path) -> str:
    from jsonschema import Draft202012Validator

    runner = _runner_namespace()
    with zipfile.ZipFile(export_path, "r") as archive:
        files = _archive_files(archive)

    bundle = _json_file(files, "baseline_bundle.json")
    benchmark = _json_file(files, "benchmark.json")
    original_report = _json_file(files, "baseline_run_report.json")
    original_metrics = _json_file(files, "aggregate_metrics.json")
    cases = benchmark["cases"]
    legacy_response_validator = Draft202012Validator(benchmark["response_schema"])
    model_response_schema = runner["_model_facing_response_schema"](
        benchmark["response_schema"]
    )
    model_response_validator = Draft202012Validator(model_response_schema)
    specialist_validator = Draft202012Validator(
        bundle["structured_output_contracts"]["specialist_input"]
    )
    tool_by_name = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in bundle["tool_schemas"]
    }

    case_result_rows = {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in files["case_results.jsonl"].splitlines()
            if line
        )
    }
    raw_files = {
        path: json.loads(payload)
        for path, payload in files.items()
        if path.startswith("raw_outputs/") and path.endswith(".json")
    }
    raw_by_case: dict[str, tuple[str, dict[str, Any]]] = {}
    for path, record in raw_files.items():
        case_id = record.get("case_id")
        if isinstance(case_id, str):
            raw_by_case[case_id] = (path, record)

    scored_rows: list[dict[str, Any]] = []
    case_diagnoses: list[dict[str, Any]] = []
    future_schema_valid = 0
    short_task_id_cases: set[str] = set()
    short_workflow_id_cases: set[str] = set()
    missing_top_level_evidence_cases: set[str] = set()
    missing_clarification_cases: set[str] = set()
    nested_handoff_cases: set[str] = set()
    for case in cases:
        raw_path, raw_record = raw_by_case.get(case["id"], (None, None))
        archived_row = case_result_rows.get(case["id"], {})
        output = raw_record.get("parsed_output") if raw_record else None
        parse_error = raw_record.get("parse_error") if raw_record else None
        model_response_contract_valid = False
        if output is not None:
            score = runner["_score_case"](
                case,
                output,
                response_validator=legacy_response_validator,
                specialist_validator=specialist_validator,
                tool_by_name=tool_by_name,
            )
            future_errors = sorted(
                runner["_flatten_schema_errors"](
                    list(model_response_validator.iter_errors(output))
                ),
                key=lambda error: (list(map(str, error.absolute_path)), error.message),
            )
            future_schema_valid += not future_errors
            model_response_contract_valid = not future_errors
            future_schema_diagnostics = [
                {
                    "path": "/" + "/".join(map(str, error.absolute_path)),
                    "validator": error.validator,
                    "message": error.message,
                }
                for error in future_errors[:16]
            ]
        else:
            score = {
                "checks": {
                    "structured_output_valid": False,
                    "expected_decision_correct": False,
                },
                "applicable_checks": [
                    "structured_output_valid",
                    "expected_decision_correct",
                ],
                "failure_classifications": [
                    "invalid_json" if parse_error else "inference_missing"
                ],
                "errors": [
                    parse_error.get("message", "Unknown parse failure")
                    if parse_error
                    else "No archived raw output"
                ],
                "passed": False,
                "tool_call_count": 0,
                "tool_calls_with_valid_arguments": 0,
                "unregistered_tool_names": [],
                "out_of_scope_dataset_file_references": [],
                "dataset_file_reference_count": 0,
                "unsupported_claim_heuristic": False,
            }
            future_schema_diagnostics = []

        for error in score.get("response_schema_errors", []):
            instance_path = error.get("instance_path", "")
            if error.get("validator") == "minLength":
                if instance_path.endswith("/task_id"):
                    short_task_id_cases.add(case["id"])
                if instance_path.endswith("/workflow_id"):
                    short_workflow_id_cases.add(case["id"])
            if error.get("validator") == "required":
                if (
                    instance_path in {"", "/"}
                    and "evidence_references" in error.get("message", "")
                ):
                    missing_top_level_evidence_cases.add(case["id"])
                if (
                    instance_path in {"", "/"}
                    and "clarification_questions" in error.get("message", "")
                ):
                    missing_clarification_cases.add(case["id"])
        if isinstance(output, dict):
            handoff_value = output.get("specialist_handoff")
            if (
                isinstance(handoff_value, dict)
                and isinstance(handoff_value.get("context"), dict)
                and any(
                    key in handoff_value["context"] and key not in handoff_value
                    for key in (
                        "objective",
                        "contract_version",
                        "conversation_id",
                        "dataset_id",
                        "file_id",
                        "source_fingerprint",
                        "evidence_references",
                        "permitted_tools",
                        "task_id",
                        "workflow_id",
                    )
                )
            ):
                nested_handoff_cases.add(case["id"])

        row = {
            "case_id": case["id"],
            "category": case["category"],
            "status": "evaluated" if raw_record else "inference_failed",
            "raw_output_path": raw_path,
            **score,
        }
        scored_rows.append(row)
        expected = case.get("expected", {})
        check_flags = score["checks"]
        handoff = output.get("specialist_handoff") if isinstance(output, dict) else None
        runtime_owned_fields = sorted(
            {
                "contract_version",
                "workflow_id",
                "task_id",
                "conversation_id",
                "dataset_id",
                "file_id",
                "source_fingerprint",
                "evidence_references",
                "permitted_tools",
            }
            & (set(handoff) if isinstance(handoff, dict) else set())
        )
        issue_labels = []
        issue_categories = ["needs human semantic review"]
        if not check_flags.get("structured_output_valid", False):
            issue_labels.append("historical response-schema violation")
            issue_categories.extend(
                ["schema/contract mismatch", "prompt adherence failure"]
            )
        if not check_flags.get("expected_decision_correct", False):
            issue_labels.append("decision differs from expected")
            issue_categories.append(
                "possible reasoning failure; requires human semantic review"
            )
        if (
            "expected_tool_sequence_correct" in check_flags
            and not check_flags["expected_tool_sequence_correct"]
        ):
            issue_labels.append("tool sequence differs from expected")
            issue_categories.extend(
                ["tool selection or argument failure", "prompt adherence failure"]
            )
        if not check_flags.get("all_tool_arguments_valid", True):
            issue_categories.append("tool selection or argument failure")
        if not check_flags.get("specialist_route_correct", True):
            issue_labels.append("specialist route differs from expected")
            issue_categories.append("specialist routing failure")
        if not check_flags.get("specialist_input_contract_valid", True):
            issue_labels.append("runtime-owned specialist contract fields invalid")
            issue_categories.append("runtime-owned field incorrectly delegated to model")
        if not check_flags.get("no_unrequested_specialist_handoff", True):
            issue_labels.append("unexpected specialist handoff")
            issue_categories.extend(
                [
                    "prompt adherence failure",
                    "runtime-owned field incorrectly delegated to model",
                ]
            )
        if runtime_owned_fields:
            issue_categories.append("runtime-owned field incorrectly delegated to model")
        if "unsupported_claim_heuristic" in score["failure_classifications"]:
            issue_labels.append("unsupported-claim heuristic flag; human review required")
            issue_categories.append(
                "unsupported or fabricated information possible; heuristic only"
            )
        if expected.get("tool_calls_exact") == []:
            issue_categories.append(
                "evaluation harness defect: empty expected sequence was omitted from the original denominator"
            )
        issue_categories = list(dict.fromkeys(issue_categories))
        if not issue_labels:
            issue_labels.append("no automated failure detected; human review still required")
        case_diagnoses.append(
            {
                "case_id": case["id"],
                "category": case["category"],
                "prompt": case["prompt"],
                "expected_behavior": case.get("expected_behavior"),
                "expected_decision": expected.get("decision"),
                "actual_decision": output.get("decision") if isinstance(output, dict) else None,
                "expected_tool_calls_exact": expected.get("tool_calls_exact", "<not specified>"),
                "actual_tool_calls": output.get("tool_calls") if isinstance(output, dict) else None,
                "historical_contract_valid": check_flags.get("structured_output_valid", False),
                "historical_schema_errors": score.get("response_schema_errors", []),
                "model_response_contract_valid": model_response_contract_valid,
                "model_response_contract_errors": future_schema_diagnostics,
                "runtime_owned_handoff_fields": runtime_owned_fields,
                "issue_categories": issue_categories,
                "checks": check_flags,
                "classifications": score["failure_classifications"],
                "diagnosis": issue_labels,
                "archived_passed": archived_row.get("passed"),
                "human_semantic_review": (
                    "required" if case.get("human_semantic_review_required", True) else "recommended"
                ),
                "raw_output_path": raw_path,
            }
        )

    metrics = runner["_aggregate_metrics"](cases, scored_rows)
    historical_schema_diagnostics = {
        "short_task_id": sorted(short_task_id_cases),
        "short_workflow_id": sorted(short_workflow_id_cases),
        "missing_top_level_evidence_references": sorted(
            missing_top_level_evidence_cases
        ),
        "missing_top_level_clarification_questions": sorted(
            missing_clarification_cases
        ),
        "handoff_fields_nested_inside_context": sorted(nested_handoff_cases),
    }
    archive_name = export_path.name
    lines = [
        "# Corrected evaluation of the Qwen3-8B baseline export",
        "",
        f"- **Run:** `{original_report.get('run_id', 'unknown')}`",
        f"- **Evidence archive:** `{archive_name}`",
        f"- **Re-evaluation:** `{runner['EVALUATOR_VERSION']}` against the archived benchmark response schema.",
        "- **Scope:** Read-only re-scoring. This report does not alter the ZIP, raw outputs, bundle, or original report.",
        "- **Semantic review:** Pending; heuristic flags are not proof of hallucination or reasoning failure.",
        "",
        "## Executive findings",
        "",
        "The original report's aggregate values remain the immutable record of that run. The recomputation below fixes scorer aggregation and adds explicit handoff-policy checks, but it does not retroactively change the historical prompt or its contract. The model-facing schema compatibility count is diagnostic only because it was not the schema requested during the archived inference.",
        "",
        "Archived-schema diagnostics independently identify "
        f"{len(short_task_id_cases | short_workflow_id_cases)} cases with a short runtime ID "
        f"({len(short_task_id_cases)} short `task_id`, {len(short_workflow_id_cases)} short `workflow_id`), "
        f"{len(missing_top_level_evidence_cases)} missing top-level evidence lists, "
        f"{len(missing_clarification_cases)} missing top-level clarification lists, "
        f"and {len(nested_handoff_cases)} handoff nested inside `context`. These sets can overlap.",
        "",
        "## Metric comparison",
        "",
        "| Metric | Archived report | Recomputed with corrected scoring |",
        "|---|---:|---:|",
        f"| Overall automated pass | {_render_rate(original_metrics['overall_automated_pass_rate'])} | {_render_rate(metrics['overall_automated_pass_rate'])} |",
        f"| Expected decision accuracy | {_render_rate(original_metrics['expected_decision_accuracy'])} | {_render_rate(metrics['expected_decision_accuracy'])} |",
        f"| Tool selection accuracy | {_render_rate(original_metrics['tool_selection_accuracy'])} | {_render_rate(metrics['tool_selection_accuracy'])} |",
        f"| Structured output validity | {_render_rate(original_metrics['structured_output_validity'])} | {_render_rate(metrics['structured_output_validity'])} |",
        f"| Specialist routing accuracy | {_render_rate(original_metrics['specialist_routing_accuracy'])} | {_render_rate(metrics['specialist_routing_accuracy'])} |",
        f"| Specialist handoff policy accuracy | n/a | {_render_rate(metrics['specialist_handoff_policy_accuracy'])} |",
        f"| Tool argument validity | {_render_rate(original_metrics['tool_argument_schema_validity'])} | {_render_rate(metrics['tool_argument_schema_validity'])} |",
        "",
        f"The archived raw outputs have **{future_schema_valid}/{len(cases)}** responses compatible with the new model-facing response schema. This is diagnostic, not a comparable pass-rate metric.",
        "",
        "## Case-level diagnoses",
        "",
        "| Case | Expected decision | Actual decision | Historical schema | New schema diagnostic | Corrected scorer checks | Categories and diagnosis |",
        "|---|---|---|---|---|---|---|",
    ]
    for diagnosis in case_diagnoses:
        historical = "valid" if diagnosis["historical_contract_valid"] else "invalid"
        future = "valid" if diagnosis["model_response_contract_valid"] else "invalid"
        checks = ", ".join(
            f"{name}={'pass' if value else 'fail'}"
            for name, value in diagnosis["checks"].items()
            if name in {
                "expected_decision_correct",
                "expected_tool_sequence_correct",
                "specialist_route_correct",
                "specialist_input_contract_valid",
                "no_unrequested_specialist_handoff",
            }
        )
        prompt = diagnosis["prompt"].replace("|", "\\|").replace("\n", " ")
        issue_text = (
            ", ".join(diagnosis["issue_categories"])
            + ". "
            + "; ".join(diagnosis["diagnosis"])
        ).replace("|", "\\|")
        lines.append(
            f"| `{diagnosis['case_id']}` | {diagnosis['expected_decision'] or '—'} "
            f"| {diagnosis['actual_decision'] or '—'} | {historical} | {future} "
            f"| {checks or '—'} | {issue_text}. Prompt: {prompt} |"
        )

    lines.extend(
        [
            "",
            "## Archived schema diagnostic details",
            "",
            "| Diagnostic | Cases |",
            "|---|---|",
            f"| Short `task_id` | {', '.join(historical_schema_diagnostics['short_task_id']) or 'none'} |",
            f"| Short `workflow_id` | {', '.join(historical_schema_diagnostics['short_workflow_id']) or 'none'} |",
            f"| Missing top-level `evidence_references` | {', '.join(historical_schema_diagnostics['missing_top_level_evidence_references']) or 'none'} |",
            f"| Missing top-level `clarification_questions` | {', '.join(historical_schema_diagnostics['missing_top_level_clarification_questions']) or 'none'} |",
            f"| Handoff fields nested inside `context` | {', '.join(historical_schema_diagnostics['handoff_fields_nested_inside_context']) or 'none'} |",
            "",
            "## Root causes and evidence",
            "",
            "1. **Runtime-owned handoff data was delegated to the model.** The archived benchmark response schema embeds `SpecialistInputContract`, including `task_id`, `workflow_id`, conversation/source IDs, evidence references, contract version, and permitted tools. Those values are assembled by runtime code; model generation is neither needed nor a reliable owner.",
            "2. **Response-schema requirements and output adherence are distinct.** The historical evaluator correctly validated the raw parsed object against the archived `benchmark.response_schema`, with no normalization stage. Missing required top-level fields are genuine response-contract failures under that schema, even if the decision or tool proposal is correct.",
            "3. **Tool-selection aggregation omitted explicit empty sequences.** Cases with `tool_calls_exact: []` were excluded by a truthiness filter. The corrected scorer includes every case that explicitly specifies the field.",
            "4. **Automated semantic checks are limited.** Numeric grounding is literal token membership, and execution/causal checks are regex heuristics. Neither can establish unsupported information or reasoning quality without human review.",
            "5. **The historical and revised model schemas are intentionally different.** The revised prompt/evaluator share a derived response schema whose delegation DTO contains only `agent`, `objective`, and `context`; the full application specialist contract remains strict and runtime-owned.",
            "",
            "## Corrective implementation and validation",
            "",
            "- The canonical runner now builds a model-facing delegation DTO from the pinned benchmark envelope and validates with that same schema used in the prompt.",
            "- The prompt explicitly defaults delegation to `null` and forbids model-generated runtime IDs, evidence references, and permissions.",
            "- The evaluator records bounded JSON Schema diagnostics, requires null handoff when no specialist is expected, scores explicitly empty tool sequences, and versions checkpoints so old incomplete runs cannot be resumed under new scoring logic.",
            "- The v6 notebook and export remain unchanged; the revised notebook is v7 and uses the same immutable bundle as a new run.",
            "- Focused tests cover these contracts, schema diagnostics, heuristics, score denominators, and checkpoint-version separation.",
            "",
            "## Recommended next steps",
            "",
            "1. Run the v7 notebook as a new inference run; do not overwrite or relabel the archived v6 run.",
            "2. Review all 12 outputs semantically, especially causal statements, execution claims, cited identifiers, and any interpretation not mechanically checked.",
            "3. Verify the revised benchmark's expected decision/tool outputs and handoff DTO cases with application owners before using automated pass rate as a gate.",
            "4. Compare v7 metrics with the archived run only on checks with identical definitions; report schema/evaluator versions alongside every rate.",
            "5. Keep training unauthorized until baseline review and separate authorization are complete.",
            "",
            "## Reproducibility and source references",
            "",
            f"- Export run report: `baseline_run_report.json` in `{archive_name}`.",
            "- Benchmark schema: `baseline_bundle.json` → `benchmark.response_schema`.",
            "- Corrected schema adapter: `backend/training/milestone5/kaggle_baseline_runner.py:378` (`_model_facing_response_schema`); model prompt: `:334` (`_make_prompt`).",
            "- Corrected output validation/scoring: `backend/training/milestone5/kaggle_baseline_runner.py:453` (`_score_case`); aggregation: `:1226` (`_aggregate_metrics`); checkpoint identity: `:1066` (`_active_run`).",
            "- Identifier and unsupported-claim heuristic patterns: `backend/training/milestone5/kaggle_baseline_runner.py:57-76`.",
            "- Production orchestrator guidance: `backend/app/agent/business_intelligence.py:53-70` (`SPECIALIST_PROMPT_PROFILES['orchestrator']`); runtime system prompt assembly: `backend/app/agent/runtime.py:77,102`.",
            "- Actual API output contract: `backend/app/agent/api.py:1713` (`ChatTurnResponse`).",
            "- Strict specialist contracts: `backend/app/agent/specialist_agents.py:27,31,86,263` (`_StrictContract`, `SpecialistInputContract`, `SpecialistHandoff`, `_invoke`); Pydantic schema generation/use: `:303,366`.",
            "- Tool handoff contract: `backend/app/agent/tools.py:154` (`DatasetInspectionHandoff`); application inspection envelope: `backend/app/agent/inspection_handoff.py:142` (`InspectionHandoff`).",
            "",
            "## Full per-case data",
            "",
            "The machine-readable rows below retain expected/actual decisions and tool proposals, schema diagnostics, scorer checks, raw-output paths, and human-review status without copying the complete raw outputs.",
            "",
            "```json",
            json.dumps(case_diagnoses, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="Archived baseline ZIP export")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"Report destination (default: {DEFAULT_REPORT})",
    )
    args = parser.parse_args()
    report = reevaluate_export(args.export)
    args.output.write_text(report, encoding="utf-8", newline="\n")
    print(f"Corrected evaluation report written to {args.output}")


if __name__ == "__main__":
    main()
