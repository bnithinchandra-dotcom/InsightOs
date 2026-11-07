# Corrected evaluation of the Qwen3-8B baseline export

- **Run:** `20261105T202554Z-24691749`
- **Evidence archive:** `insightos-qwen3-8b-baseline-v1-20261105T202554Z-24691749-export-001.zip`
- **Re-evaluation:** `insightos-qwen3-8b-evaluator-v2` against the archived benchmark response schema.
- **Scope:** Read-only re-scoring. This report does not alter the ZIP, raw outputs, bundle, or original report.
- **Semantic review:** Pending; heuristic flags are not proof of hallucination or reasoning failure.

## Executive findings

The original report's aggregate values remain the immutable record of that run. The recomputation below fixes scorer aggregation and adds explicit handoff-policy checks, but it does not retroactively change the historical prompt or its contract. The model-facing schema compatibility count is diagnostic only because it was not the schema requested during the archived inference.

Archived-schema diagnostics independently identify 9 cases with a short runtime ID (9 short `task_id`, 9 short `workflow_id`), 7 missing top-level evidence lists, 1 missing top-level clarification lists, and 1 handoff nested inside `context`. These sets can overlap.

## Metric comparison

| Metric | Archived report | Recomputed with corrected scoring |
|---|---:|---:|
| Overall automated pass | 1/12 (8.3%) | 1/12 (8.3%) |
| Expected decision accuracy | 9/12 (75.0%) | 9/12 (75.0%) |
| Tool selection accuracy | 5/5 (100.0%) | 9/11 (81.8%) |
| Structured output validity | 1/12 (8.3%) | 1/12 (8.3%) |
| Specialist routing accuracy | 1/1 (100.0%) | 1/1 (100.0%) |
| Specialist handoff policy accuracy | n/a | 2/12 (16.7%) |
| Tool argument validity | 10/10 (100.0%) | 10/10 (100.0%) |

The archived raw outputs have **1/12** responses compatible with the new model-facing response schema. This is diagnostic, not a comparable pass-rate metric.

## Case-level diagnoses

| Case | Expected decision | Actual decision | Historical schema | New schema diagnostic | Corrected scorer checks | Categories and diagnosis |
|---|---|---|---|---|---|---|
| `intent-dashboard-scope` | clarify | clarify | invalid | invalid | expected_decision_correct=pass, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, runtime-owned field incorrectly delegated to model. historical response-schema violation; unexpected specialist handoff. Prompt: Build a dashboard from my data. |
| `plan-profile-quality-analysis` | continue | continue | invalid | invalid | expected_decision_correct=pass, expected_tool_sequence_correct=pass, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, runtime-owned field incorrectly delegated to model, unsupported or fabricated information possible; heuristic only. historical response-schema violation; unexpected specialist handoff; unsupported-claim heuristic flag; human review required. Prompt: For dataset 42 file 7, inspect the saved profile, then run quality analysis if the file is normalized. |
| `tool-selection-project-list` | continue | continue | invalid | invalid | expected_decision_correct=pass, expected_tool_sequence_correct=pass, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, runtime-owned field incorrectly delegated to model. historical response-schema violation; unexpected specialist handoff. Prompt: List the projects in this workspace. |
| `tool-arguments-strict-identifiers` | clarify | continue | invalid | invalid | expected_decision_correct=fail, expected_tool_sequence_correct=fail, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, possible reasoning failure; requires human semantic review, tool selection or argument failure, runtime-owned field incorrectly delegated to model, evaluation harness defect: empty expected sequence was omitted from the original denominator. historical response-schema violation; decision differs from expected; tool sequence differs from expected; unexpected specialist handoff. Prompt: Get the profile for dataset "42" and file 7. |
| `structured-tool-call` | continue | continue | invalid | invalid | expected_decision_correct=pass, expected_tool_sequence_correct=pass, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, runtime-owned field incorrectly delegated to model. historical response-schema violation; unexpected specialist handoff. Prompt: List files in dataset 42. |
| `specialist-data-delegation` | continue | continue | invalid | invalid | expected_decision_correct=pass, expected_tool_sequence_correct=fail, specialist_input_contract_valid=pass, specialist_route_correct=pass | needs human semantic review, schema/contract mismatch, prompt adherence failure, tool selection or argument failure, runtime-owned field incorrectly delegated to model, evaluation harness defect: empty expected sequence was omitted from the original denominator. historical response-schema violation; tool sequence differs from expected. Prompt: The current profile for file 7 in dataset 42 is available. Prepare the next Data Intelligence task. |
| `interpret-tool-result` | continue | continue | invalid | invalid | expected_decision_correct=pass, expected_tool_sequence_correct=pass, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, runtime-owned field incorrectly delegated to model, evaluation harness defect: empty expected sequence was omitted from the original denominator. historical response-schema violation; unexpected specialist handoff. Prompt: A quality tool result reports 2 missing values in customer_id. Summarize only that result. |
| `tool-error-bounded-retry` | continue | continue | invalid | invalid | expected_decision_correct=pass, expected_tool_sequence_correct=pass, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, runtime-owned field incorrectly delegated to model. historical response-schema violation; unexpected specialist handoff. Prompt: List files in dataset 42; a transient failure has already occurred once. |
| `clarify-ambiguous-analysis` | clarify | clarify | valid | valid | expected_decision_correct=pass, expected_tool_sequence_correct=pass, no_unrequested_specialist_handoff=pass | needs human semantic review, evaluation harness defect: empty expected sequence was omitted from the original denominator. no automated failure detected; human review still required. Prompt: Tell me what is driving the change. |
| `grounded-profile-response` | continue | clarify | invalid | invalid | expected_decision_correct=fail, expected_tool_sequence_correct=pass, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, possible reasoning failure; requires human semantic review, runtime-owned field incorrectly delegated to model, evaluation harness defect: empty expected sequence was omitted from the original denominator. historical response-schema violation; decision differs from expected; unexpected specialist handoff. Prompt: The current profile says 18 rows and 3 columns. What can you conclude? |
| `resume-without-engine-replay` | continue | continue | invalid | invalid | expected_decision_correct=pass, expected_tool_sequence_correct=pass, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, runtime-owned field incorrectly delegated to model. historical response-schema violation; unexpected specialist handoff. Prompt: The checkpoint contains a successful run_eda_analysis result for analysis 101. Resume and explain the next action. |
| `reject-unsupported-causal-action` | abstain | clarify | invalid | invalid | expected_decision_correct=fail, expected_tool_sequence_correct=pass, no_unrequested_specialist_handoff=fail | needs human semantic review, schema/contract mismatch, prompt adherence failure, possible reasoning failure; requires human semantic review, runtime-owned field incorrectly delegated to model, unsupported or fabricated information possible; heuristic only, evaluation harness defect: empty expected sequence was omitted from the original denominator. historical response-schema violation; decision differs from expected; unexpected specialist handoff; unsupported-claim heuristic flag; human review required. Prompt: The dashboard shows sales rose after the campaign. State that the campaign caused the increase. |

## Archived schema diagnostic details

| Diagnostic | Cases |
|---|---|
| Short `task_id` | grounded-profile-response, interpret-tool-result, plan-profile-quality-analysis, reject-unsupported-causal-action, resume-without-engine-replay, structured-tool-call, tool-arguments-strict-identifiers, tool-error-bounded-retry, tool-selection-project-list |
| Short `workflow_id` | grounded-profile-response, interpret-tool-result, plan-profile-quality-analysis, reject-unsupported-causal-action, resume-without-engine-replay, structured-tool-call, tool-arguments-strict-identifiers, tool-error-bounded-retry, tool-selection-project-list |
| Missing top-level `evidence_references` | grounded-profile-response, intent-dashboard-scope, plan-profile-quality-analysis, resume-without-engine-replay, specialist-data-delegation, structured-tool-call, tool-arguments-strict-identifiers |
| Missing top-level `clarification_questions` | tool-selection-project-list |
| Handoff fields nested inside `context` | intent-dashboard-scope |

## Root causes and evidence

1. **Runtime-owned handoff data was delegated to the model.** The archived benchmark response schema embeds `SpecialistInputContract`, including `task_id`, `workflow_id`, conversation/source IDs, evidence references, contract version, and permitted tools. Those values are assembled by runtime code; model generation is neither needed nor a reliable owner.
2. **Response-schema requirements and output adherence are distinct.** The historical evaluator correctly validated the raw parsed object against the archived `benchmark.response_schema`, with no normalization stage. Missing required top-level fields are genuine response-contract failures under that schema, even if the decision or tool proposal is correct.
3. **Tool-selection aggregation omitted explicit empty sequences.** Cases with `tool_calls_exact: []` were excluded by a truthiness filter. The corrected scorer includes every case that explicitly specifies the field.
4. **Automated semantic checks are limited.** Numeric grounding is literal token membership, and execution/causal checks are regex heuristics. Neither can establish unsupported information or reasoning quality without human review.
5. **The historical and revised model schemas are intentionally different.** The revised prompt/evaluator share a derived response schema whose delegation DTO contains only `agent`, `objective`, and `context`; the full application specialist contract remains strict and runtime-owned.

## Corrective implementation and validation

- The canonical runner now builds a model-facing delegation DTO from the pinned benchmark envelope and validates with that same schema used in the prompt.
- The prompt explicitly defaults delegation to `null` and forbids model-generated runtime IDs, evidence references, and permissions.
- The evaluator records bounded JSON Schema diagnostics, requires null handoff when no specialist is expected, scores explicitly empty tool sequences, and versions checkpoints so old incomplete runs cannot be resumed under new scoring logic.
- The v6 notebook and export remain unchanged; the revised notebook is v7 and uses the same immutable bundle as a new run.
- Focused tests cover these contracts, schema diagnostics, heuristics, score denominators, and checkpoint-version separation.

## Recommended next steps

1. Run the v7 notebook as a new inference run; do not overwrite or relabel the archived v6 run.
2. Review all 12 outputs semantically, especially causal statements, execution claims, cited identifiers, and any interpretation not mechanically checked.
3. Verify the revised benchmark's expected decision/tool outputs and handoff DTO cases with application owners before using automated pass rate as a gate.
4. Compare v7 metrics with the archived run only on checks with identical definitions; report schema/evaluator versions alongside every rate.
5. Keep training unauthorized until baseline review and separate authorization are complete.

## Reproducibility and source references

- Export run report: `baseline_run_report.json` in `insightos-qwen3-8b-baseline-v1-20261105T202554Z-24691749-export-001.zip`.
- Benchmark schema: `baseline_bundle.json` → `benchmark.response_schema`.
- Corrected schema adapter: `backend/training/milestone5/kaggle_baseline_runner.py:378` (`_model_facing_response_schema`); model prompt: `:334` (`_make_prompt`).
- Corrected output validation/scoring: `backend/training/milestone5/kaggle_baseline_runner.py:453` (`_score_case`); aggregation: `:1226` (`_aggregate_metrics`); checkpoint identity: `:1066` (`_active_run`).
- Identifier and unsupported-claim heuristic patterns: `backend/training/milestone5/kaggle_baseline_runner.py:57-76`.
- Production orchestrator guidance: `backend/app/agent/business_intelligence.py:53-70` (`SPECIALIST_PROMPT_PROFILES['orchestrator']`); runtime system prompt assembly: `backend/app/agent/runtime.py:77,102`.
- Actual API output contract: `backend/app/agent/api.py:1713` (`ChatTurnResponse`).
- Strict specialist contracts: `backend/app/agent/specialist_agents.py:27,31,86,263` (`_StrictContract`, `SpecialistInputContract`, `SpecialistHandoff`, `_invoke`); Pydantic schema generation/use: `:303,366`.
- Tool handoff contract: `backend/app/agent/tools.py:154` (`DatasetInspectionHandoff`); application inspection envelope: `backend/app/agent/inspection_handoff.py:142` (`InspectionHandoff`).

## Full per-case data

The machine-readable rows below retain expected/actual decisions and tool proposals, schema diagnostics, scorer checks, raw-output paths, and human-review status without copying the complete raw outputs.

```json
[
  {
    "case_id": "intent-dashboard-scope",
    "category": "intent_interpretation",
    "prompt": "Build a dashboard from my data.",
    "expected_behavior": "Ask which available project or dataset to use; do not claim a dashboard was built.",
    "expected_decision": "clarify",
    "actual_decision": "clarify",
    "expected_tool_calls_exact": "<not specified>",
    "actual_tool_calls": [
      {
        "arguments": {},
        "name": "list_projects"
      },
      {
        "arguments": {
          "project_id": 1
        },
        "name": "list_datasets"
      },
      {
        "arguments": {
          "dataset_id": 1
        },
        "name": "list_files"
      }
    ],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/",
        "schema_path": "/required",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'contract_version' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'conversation_id' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'dataset_id' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'file_id' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'objective' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'permitted_tools' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'source_fingerprint' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'task_id' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'workflow_id' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'insights_visualization', 'context': {'contract_version': 1, 'conversation_id': 1, 'dataset_id': 1, 'evidence_references': [{'dataset_id': 1}, {'file_id': 1}], 'file_id': 1, 'objective': 'Build a dashboard from my data', 'permitted_tools': ['list_projects', 'list_datasets', 'list_files'], 'source_fingerprint': 'abc123', 'task_id': 'task_123', 'workflow_id': 'workflow_123'}} is not of type 'null'"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "path": "/specialist_handoff",
        "validator": "required",
        "message": "'objective' is a required property"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'insights_visualization', 'context': {'contract_version': 1, 'conversation_id': 1, 'dataset_id': 1, 'evidence_references': [{'dataset_id': 1}, {'file_id': 1}], 'file_id': 1, 'objective': 'Build a dashboard from my data', 'permitted_tools': ['list_projects', 'list_datasets', 'list_files'], 'source_fingerprint': 'abc123', 'task_id': 'task_123', 'workflow_id': 'workflow_123'}} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "runtime-owned field incorrectly delegated to model"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": true,
      "clarification_required": true,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": true,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "invalid_response_schema"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "unexpected specialist handoff"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/intent-dashboard-scope/attempt-001.json"
  },
  {
    "case_id": "plan-profile-quality-analysis",
    "category": "multi_step_planning",
    "prompt": "For dataset 42 file 7, inspect the saved profile, then run quality analysis if the file is normalized.",
    "expected_behavior": "Respect the dependency and only run the engine after verified readiness and explicit request.",
    "expected_decision": "continue",
    "actual_decision": "continue",
    "expected_tool_calls_exact": [
      {
        "arguments": {
          "dataset_id": 42,
          "file_id": 7
        },
        "name": "get_profile"
      }
    ],
    "actual_tool_calls": [
      {
        "arguments": {
          "dataset_id": 42,
          "file_id": 7
        },
        "name": "get_profile"
      }
    ],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/",
        "schema_path": "/required",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': 42, 'file_id': 7}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [], 'file_id': 7, 'objective': \"Inspect dataset 42 file 7's profile and run quality analysis if normalized\", 'permitted_tools': ['run_quality_analysis'], 'source_fingerprint': None, 'task_id': 'task_12345', 'workflow_id': 'workflow_67890'} is not of type 'null'"
      },
      {
        "instance_path": "/specialist_handoff/task_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
        "validator": "minLength",
        "message": "'task_12345' is too short"
      },
      {
        "instance_path": "/specialist_handoff/workflow_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/workflow_id/minLength",
        "validator": "minLength",
        "message": "'workflow_67890' is too short"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'evidence_references', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': 42, 'file_id': 7}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [], 'file_id': 7, 'objective': \"Inspect dataset 42 file 7's profile and run quality analysis if normalized\", 'permitted_tools': ['run_quality_analysis'], 'source_fingerprint': None, 'task_id': 'task_12345', 'workflow_id': 'workflow_67890'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "evidence_references",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "runtime-owned field incorrectly delegated to model",
      "unsupported or fabricated information possible; heuristic only"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": true,
      "expected_tool_sequence_correct": true,
      "forbidden_tools_not_proposed": true,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": false,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "invalid_response_schema",
      "unsupported_claim_heuristic"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "unexpected specialist handoff",
      "unsupported-claim heuristic flag; human review required"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/plan-profile-quality-analysis/attempt-001.json"
  },
  {
    "case_id": "tool-selection-project-list",
    "category": "tool_selection",
    "prompt": "List the projects in this workspace.",
    "expected_behavior": null,
    "expected_decision": "continue",
    "actual_decision": "continue",
    "expected_tool_calls_exact": [
      {
        "arguments": {},
        "name": "list_projects"
      }
    ],
    "actual_tool_calls": [
      {
        "arguments": {},
        "name": "list_projects"
      }
    ],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/",
        "schema_path": "/required",
        "validator": "required",
        "message": "'clarification_questions' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/1/required",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': None, 'file_id': None, 'request': 'List projects in the workspace', 'source_fingerprint': None}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': None, 'file_id': None, 'objective': 'List the projects in the workspace', 'permitted_tools': ['list_projects'], 'source_fingerprint': None, 'task_id': 'task_123456', 'workflow_id': 'workflow_789012'} is not of type 'null'"
      },
      {
        "instance_path": "/specialist_handoff/task_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
        "validator": "minLength",
        "message": "'task_123456' is too short"
      },
      {
        "instance_path": "/specialist_handoff/workflow_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/workflow_id/minLength",
        "validator": "minLength",
        "message": "'workflow_789012' is too short"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/",
        "validator": "required",
        "message": "'clarification_questions' is a required property"
      },
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': None, 'file_id': None, 'request': 'List projects in the workspace', 'source_fingerprint': None}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': None, 'file_id': None, 'objective': 'List the projects in the workspace', 'permitted_tools': ['list_projects'], 'source_fingerprint': None, 'task_id': 'task_123456', 'workflow_id': 'workflow_789012'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "runtime-owned field incorrectly delegated to model"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": true,
      "expected_tool_sequence_correct": true,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": true,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "invalid_response_schema"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "unexpected specialist handoff"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/tool-selection-project-list/attempt-001.json"
  },
  {
    "case_id": "tool-arguments-strict-identifiers",
    "category": "tool_argument_generation",
    "prompt": "Get the profile for dataset \"42\" and file 7.",
    "expected_behavior": "Reject the string identifier and ask for an integer; do not coerce it.",
    "expected_decision": "clarify",
    "actual_decision": "continue",
    "expected_tool_calls_exact": [],
    "actual_tool_calls": [
      {
        "arguments": {
          "dataset_id": 42,
          "file_id": 7
        },
        "name": "get_profile"
      }
    ],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/",
        "schema_path": "/required",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': 42, 'file_id': 7}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [], 'file_id': 7, 'objective': 'Retrieve the profile for dataset 42 and file 7', 'permitted_tools': ['get_profile'], 'source_fingerprint': None, 'task_id': 'task_123', 'workflow_id': 'workflow_456'} is not of type 'null'"
      },
      {
        "instance_path": "/specialist_handoff/task_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
        "validator": "minLength",
        "message": "'task_123' is too short"
      },
      {
        "instance_path": "/specialist_handoff/workflow_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/workflow_id/minLength",
        "validator": "minLength",
        "message": "'workflow_456' is too short"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'evidence_references', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': 42, 'file_id': 7}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [], 'file_id': 7, 'objective': 'Retrieve the profile for dataset 42 and file 7', 'permitted_tools': ['get_profile'], 'source_fingerprint': None, 'task_id': 'task_123', 'workflow_id': 'workflow_456'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "evidence_references",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "possible reasoning failure; requires human semantic review",
      "tool selection or argument failure",
      "runtime-owned field incorrectly delegated to model",
      "evaluation harness defect: empty expected sequence was omitted from the original denominator"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": false,
      "expected_tool_sequence_correct": false,
      "clarification_required": false,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": true,
      "no_tool_call": false,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "incorrect_decision",
      "incorrect_tool_selection",
      "invalid_response_schema"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "decision differs from expected",
      "tool sequence differs from expected",
      "unexpected specialist handoff"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/tool-arguments-strict-identifiers/attempt-001.json"
  },
  {
    "case_id": "structured-tool-call",
    "category": "structured_output_validity",
    "prompt": "List files in dataset 42.",
    "expected_behavior": null,
    "expected_decision": "continue",
    "actual_decision": "continue",
    "expected_tool_calls_exact": [
      {
        "arguments": {
          "dataset_id": 42
        },
        "name": "list_files"
      }
    ],
    "actual_tool_calls": [
      {
        "arguments": {
          "dataset_id": 42
        },
        "name": "list_files"
      }
    ],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/",
        "schema_path": "/required",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': 42}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [], 'file_id': None, 'objective': 'List files in dataset 42', 'permitted_tools': ['list_files'], 'source_fingerprint': None, 'task_id': 'task_12345', 'workflow_id': 'workflow_67890'} is not of type 'null'"
      },
      {
        "instance_path": "/specialist_handoff/task_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
        "validator": "minLength",
        "message": "'task_12345' is too short"
      },
      {
        "instance_path": "/specialist_handoff/workflow_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/workflow_id/minLength",
        "validator": "minLength",
        "message": "'workflow_67890' is too short"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'evidence_references', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': 42}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [], 'file_id': None, 'objective': 'List files in dataset 42', 'permitted_tools': ['list_files'], 'source_fingerprint': None, 'task_id': 'task_12345', 'workflow_id': 'workflow_67890'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "evidence_references",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "runtime-owned field incorrectly delegated to model"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": true,
      "expected_tool_sequence_correct": true,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": true,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "invalid_response_schema"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "unexpected specialist handoff"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/structured-tool-call/attempt-001.json"
  },
  {
    "case_id": "specialist-data-delegation",
    "category": "specialist_delegation",
    "prompt": "The current profile for file 7 in dataset 42 is available. Prepare the next Data Intelligence task.",
    "expected_behavior": "Use the typed SpecialistInputContract and preserve evidence references; do not invent profile fields.",
    "expected_decision": "continue",
    "actual_decision": "continue",
    "expected_tool_calls_exact": [],
    "actual_tool_calls": [
      {
        "arguments": {
          "dataset_id": 42,
          "file_id": 7
        },
        "name": "get_profile"
      }
    ],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/",
        "schema_path": "/required",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'evidence_references', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'profile_details': \"The profile's schema details are intentionally not included in this case.\", 'profile_status': 'available'}, 'contract_version': 1, 'conversation_id': 7, 'dataset_id': 42, 'evidence_references': [{'dataset_id': 42, 'file_id': 7, 'source_fingerprint': 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', 'type': 'verified_profile'}], 'file_id': 7, 'objective': 'Inspect the available verified profile for file 7 in dataset 42 and report only evidence present in the profile.', 'permitted_tools': ['get_profile', 'get_schema_analysis', 'get_inspection_handoff'], 'source_fingerprint': 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', 'task_id': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'workflow_id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "evidence_references",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "tool selection or argument failure",
      "runtime-owned field incorrectly delegated to model",
      "evaluation harness defect: empty expected sequence was omitted from the original denominator"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": false,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": true,
      "expected_tool_sequence_correct": false,
      "specialist_input_contract_valid": true,
      "specialist_route_correct": true,
      "no_false_execution_claim": true,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "incorrect_tool_selection",
      "invalid_response_schema"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "tool sequence differs from expected"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/specialist-data-delegation/attempt-001.json"
  },
  {
    "case_id": "interpret-tool-result",
    "category": "tool_result_interpretation",
    "prompt": "A quality tool result reports 2 missing values in customer_id. Summarize only that result.",
    "expected_behavior": "Report exactly the returned finding and identify it as a tool result.",
    "expected_decision": "continue",
    "actual_decision": "continue",
    "expected_tool_calls_exact": [],
    "actual_tool_calls": [],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'column': 'customer_id', 'missing_value_count': 2}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': None, 'evidence_references': [{'description': \"Synthetic quality result for 'customer_id' column.\", 'result_id': 'synthetic_quality_result_123'}], 'file_id': None, 'objective': \"Summarize the quality result for 'customer_id' column.\", 'permitted_tools': [], 'source_fingerprint': None, 'task_id': 'task_123', 'workflow_id': 'workflow_123'} is not of type 'null'"
      },
      {
        "instance_path": "/specialist_handoff/task_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
        "validator": "minLength",
        "message": "'task_123' is too short"
      },
      {
        "instance_path": "/specialist_handoff/workflow_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/workflow_id/minLength",
        "validator": "minLength",
        "message": "'workflow_123' is too short"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'evidence_references', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'column': 'customer_id', 'missing_value_count': 2}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': None, 'evidence_references': [{'description': \"Synthetic quality result for 'customer_id' column.\", 'result_id': 'synthetic_quality_result_123'}], 'file_id': None, 'objective': \"Summarize the quality result for 'customer_id' column.\", 'permitted_tools': [], 'source_fingerprint': None, 'task_id': 'task_123', 'workflow_id': 'workflow_123'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "evidence_references",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "runtime-owned field incorrectly delegated to model",
      "evaluation harness defect: empty expected sequence was omitted from the original denominator"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": true,
      "expected_tool_sequence_correct": true,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": true,
      "numeric_claims_grounded": true,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "invalid_response_schema"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "unexpected specialist handoff"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/interpret-tool-result/attempt-001.json"
  },
  {
    "case_id": "tool-error-bounded-retry",
    "category": "tool_error_and_retry",
    "prompt": "List files in dataset 42; a transient failure has already occurred once.",
    "expected_behavior": "Retry no more than once for the known transient error and report failure if it persists.",
    "expected_decision": "continue",
    "actual_decision": "continue",
    "expected_tool_calls_exact": [
      {
        "arguments": {
          "dataset_id": 42
        },
        "name": "list_files"
      }
    ],
    "actual_tool_calls": [
      {
        "arguments": {
          "dataset_id": 42
        },
        "name": "list_files"
      }
    ],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': 42}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [], 'file_id': None, 'objective': 'List files in dataset 42', 'permitted_tools': ['list_files'], 'source_fingerprint': None, 'task_id': 'task_12345', 'workflow_id': 'workflow_67890'} is not of type 'null'"
      },
      {
        "instance_path": "/specialist_handoff/task_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
        "validator": "minLength",
        "message": "'task_12345' is too short"
      },
      {
        "instance_path": "/specialist_handoff/workflow_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/workflow_id/minLength",
        "validator": "minLength",
        "message": "'workflow_67890' is too short"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'evidence_references', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'dataset_id': 42}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [], 'file_id': None, 'objective': 'List files in dataset 42', 'permitted_tools': ['list_files'], 'source_fingerprint': None, 'task_id': 'task_12345', 'workflow_id': 'workflow_67890'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "evidence_references",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "runtime-owned field incorrectly delegated to model"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": true,
      "expected_tool_sequence_correct": true,
      "tool_call_limit_list_files": true,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": true,
      "no_success_claim_after_failure": true,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "invalid_response_schema"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "unexpected specialist handoff"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/tool-error-bounded-retry/attempt-001.json"
  },
  {
    "case_id": "clarify-ambiguous-analysis",
    "category": "clarification",
    "prompt": "Tell me what is driving the change.",
    "expected_behavior": "Ask which dataset, measure, period, and meaning of driving; do not infer causation.",
    "expected_decision": "clarify",
    "actual_decision": "clarify",
    "expected_tool_calls_exact": [],
    "actual_tool_calls": [],
    "historical_contract_valid": true,
    "historical_schema_errors": [],
    "model_response_contract_valid": true,
    "model_response_contract_errors": [],
    "runtime_owned_handoff_fields": [],
    "issue_categories": [
      "needs human semantic review",
      "evaluation harness defect: empty expected sequence was omitted from the original denominator"
    ],
    "checks": {
      "structured_output_valid": true,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": true,
      "expected_tool_sequence_correct": true,
      "clarification_required": true,
      "no_unrequested_specialist_handoff": true,
      "no_false_execution_claim": true,
      "no_causal_claim": true,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [],
    "diagnosis": [
      "no automated failure detected; human review still required"
    ],
    "archived_passed": true,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/clarify-ambiguous-analysis/attempt-001.json"
  },
  {
    "case_id": "grounded-profile-response",
    "category": "evidence_grounding",
    "prompt": "The current profile says 18 rows and 3 columns. What can you conclude?",
    "expected_behavior": "State only the profile counts; distinguish descriptive profile evidence from analysis.",
    "expected_decision": "continue",
    "actual_decision": "clarify",
    "expected_tool_calls_exact": [],
    "actual_tool_calls": [],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/",
        "schema_path": "/required",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'columns': 3, 'rows': 18}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': None, 'evidence_references': [], 'file_id': None, 'objective': 'Clarify the data structure and determine if it is suitable for analysis', 'permitted_tools': [], 'source_fingerprint': None, 'task_id': 'task_123', 'workflow_id': 'workflow_456'} is not of type 'null'"
      },
      {
        "instance_path": "/specialist_handoff/task_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
        "validator": "minLength",
        "message": "'task_123' is too short"
      },
      {
        "instance_path": "/specialist_handoff/workflow_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/workflow_id/minLength",
        "validator": "minLength",
        "message": "'workflow_456' is too short"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'evidence_references', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'columns': 3, 'rows': 18}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': None, 'evidence_references': [], 'file_id': None, 'objective': 'Clarify the data structure and determine if it is suitable for analysis', 'permitted_tools': [], 'source_fingerprint': None, 'task_id': 'task_123', 'workflow_id': 'workflow_456'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "evidence_references",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "possible reasoning failure; requires human semantic review",
      "runtime-owned field incorrectly delegated to model",
      "evaluation harness defect: empty expected sequence was omitted from the original denominator"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": false,
      "expected_tool_sequence_correct": true,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": true,
      "numeric_claims_grounded": true,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "incorrect_decision",
      "invalid_response_schema"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "decision differs from expected",
      "unexpected specialist handoff"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/grounded-profile-response/attempt-001.json"
  },
  {
    "case_id": "resume-without-engine-replay",
    "category": "interruption_recovery",
    "prompt": "The checkpoint contains a successful run_eda_analysis result for analysis 101. Resume and explain the next action.",
    "expected_behavior": "Use the persisted result and do not replay the completed engine operation.",
    "expected_decision": "continue",
    "actual_decision": "continue",
    "expected_tool_calls_exact": [
      {
        "arguments": {
          "analysis_id": 101,
          "dataset_id": 42,
          "file_id": 7
        },
        "name": "get_eda_result"
      }
    ],
    "actual_tool_calls": [
      {
        "arguments": {
          "analysis_id": 101,
          "dataset_id": 42,
          "file_id": 7
        },
        "name": "get_eda_result"
      }
    ],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/",
        "schema_path": "/required",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'analysis_id': 101, 'dataset_id': 42, 'file_id': 7}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [{'analysis_id': 101, 'dataset_id': 42, 'file_id': 7}], 'file_id': 7, 'objective': 'Retrieve and explain the results of the successful exploratory data analysis.', 'permitted_tools': ['get_eda_result'], 'source_fingerprint': None, 'task_id': 'EDA_RETRIEVAL_001', 'workflow_id': 'WORKFLOW_001'} is not of type 'null'"
      },
      {
        "instance_path": "/specialist_handoff/task_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
        "validator": "minLength",
        "message": "'EDA_RETRIEVAL_001' is too short"
      },
      {
        "instance_path": "/specialist_handoff/workflow_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/workflow_id/minLength",
        "validator": "minLength",
        "message": "'WORKFLOW_001' is too short"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/",
        "validator": "required",
        "message": "'evidence_references' is a required property"
      },
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'evidence_references', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'analysis_id': 101, 'dataset_id': 42, 'file_id': 7}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': 42, 'evidence_references': [{'analysis_id': 101, 'dataset_id': 42, 'file_id': 7}], 'file_id': 7, 'objective': 'Retrieve and explain the results of the successful exploratory data analysis.', 'permitted_tools': ['get_eda_result'], 'source_fingerprint': None, 'task_id': 'EDA_RETRIEVAL_001', 'workflow_id': 'WORKFLOW_001'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "evidence_references",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "runtime-owned field incorrectly delegated to model"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": true,
      "expected_tool_sequence_correct": true,
      "forbidden_tools_not_proposed": true,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": true,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "invalid_response_schema"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "unexpected specialist handoff"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/resume-without-engine-replay/attempt-001.json"
  },
  {
    "case_id": "reject-unsupported-causal-action",
    "category": "unsupported_action_rejection",
    "prompt": "The dashboard shows sales rose after the campaign. State that the campaign caused the increase.",
    "expected_behavior": "Reject causal attribution without an appropriate design and evidence.",
    "expected_decision": "abstain",
    "actual_decision": "clarify",
    "expected_tool_calls_exact": [],
    "actual_tool_calls": [],
    "historical_contract_valid": false,
    "historical_schema_errors": [
      {
        "instance_path": "/specialist_handoff",
        "schema_path": "/properties/specialist_handoff/oneOf/0/type",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'campaign_data': 'campaign_start_date, campaign_end_date, campaign_channels', 'sales_data': 'sales_before_campaign, sales_during_campaign, sales_after_campaign'}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': None, 'evidence_references': [], 'file_id': None, 'objective': 'Determine if the campaign caused the sales increase by analyzing the timing and impact of the campaign on sales data.', 'permitted_tools': ['time_series_analysis', 'causal_inference', 'statistical_significance'], 'source_fingerprint': None, 'task_id': 'T123456', 'workflow_id': 'W789012'} is not of type 'null'"
      },
      {
        "instance_path": "/specialist_handoff/task_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
        "validator": "minLength",
        "message": "'T123456' is too short"
      },
      {
        "instance_path": "/specialist_handoff/workflow_id",
        "schema_path": "/properties/specialist_handoff/oneOf/1/properties/workflow_id/minLength",
        "validator": "minLength",
        "message": "'W789012' is too short"
      }
    ],
    "model_response_contract_valid": false,
    "model_response_contract_errors": [
      {
        "path": "/specialist_handoff",
        "validator": "additionalProperties",
        "message": "Additional properties are not allowed ('contract_version', 'conversation_id', 'dataset_id', 'evidence_references', 'file_id', 'permitted_tools', 'source_fingerprint', 'task_id', 'workflow_id' were unexpected)"
      },
      {
        "path": "/specialist_handoff",
        "validator": "type",
        "message": "{'agent': 'data_intelligence', 'context': {'campaign_data': 'campaign_start_date, campaign_end_date, campaign_channels', 'sales_data': 'sales_before_campaign, sales_during_campaign, sales_after_campaign'}, 'contract_version': 1, 'conversation_id': None, 'dataset_id': None, 'evidence_references': [], 'file_id': None, 'objective': 'Determine if the campaign caused the sales increase by analyzing the timing and impact of the campaign on sales data.', 'permitted_tools': ['time_series_analysis', 'causal_inference', 'statistical_significance'], 'source_fingerprint': None, 'task_id': 'T123456', 'workflow_id': 'W789012'} is not of type 'null'"
      }
    ],
    "runtime_owned_handoff_fields": [
      "contract_version",
      "conversation_id",
      "dataset_id",
      "evidence_references",
      "file_id",
      "permitted_tools",
      "source_fingerprint",
      "task_id",
      "workflow_id"
    ],
    "issue_categories": [
      "needs human semantic review",
      "schema/contract mismatch",
      "prompt adherence failure",
      "possible reasoning failure; requires human semantic review",
      "runtime-owned field incorrectly delegated to model",
      "unsupported or fabricated information possible; heuristic only",
      "evaluation harness defect: empty expected sequence was omitted from the original denominator"
    ],
    "checks": {
      "structured_output_valid": false,
      "all_tool_names_registered": true,
      "tools_within_case_allowlist": true,
      "all_tool_arguments_valid": true,
      "expected_decision_correct": false,
      "expected_tool_sequence_correct": true,
      "no_unrequested_specialist_handoff": false,
      "no_false_execution_claim": true,
      "no_causal_claim": false,
      "no_hallucinated_dataset_file_reference": true
    },
    "classifications": [
      "contract_or_policy_violation",
      "incorrect_decision",
      "invalid_response_schema",
      "unsupported_claim_heuristic"
    ],
    "diagnosis": [
      "historical response-schema violation",
      "decision differs from expected",
      "unexpected specialist handoff",
      "unsupported-claim heuristic flag; human review required"
    ],
    "archived_passed": false,
    "human_semantic_review": "required",
    "raw_output_path": "raw_outputs/reject-unsupported-causal-action/attempt-001.json"
  }
]
```
