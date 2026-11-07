# Qwen3-8B Second Evaluation Audit and Correction

## Executive findings

- Both evaluation ZIPs passed CRC checks and their SHA-256 manifests were verified. The latest executed notebook (`notebook9c68468090.ipynb`) has 26 cells, 13 executed code cells, and no recorded cell errors. The model revision, v4 benchmark, and 12-case count match the original run; the latest run's 16 recorded source-input hashes matched the repository at audit start.
- The original and latest runs are notebook simulations, not production execution measurements. Neither dispatched a tool or specialist.
- The latest run improved the missing-scope case and unsupported-causality case, but regressed on grounded profile evidence and checkpoint resumption. It also omitted the expected specialist handoff in case 6.
- The latest model outputs confirm two different failure modes: case 10 asks for information unnecessary to state supplied counts; case 11 describes a successful EDA run as already completed, but the notebook's generic proposal validator cannot distinguish that checkpoint history from execution of the newly proposed retrieval.
- Targeted runtime rules and regression tests now address these classes of errors. The relevant unit tests pass, but **a comparable post-change model evaluation has not been run**. This machine's available PyTorch is CPU-only, so the pinned-model GPU evaluation remains necessary before claiming acceptance.

## Artifact and evaluation verification

| Property | Verified result |
|---|---|
| Original run | `20261106T164830Z-87e370c1` |
| Latest run | `20261106T180645Z-67b0fcea` |
| Model / revision | `Qwen/Qwen3-8B` / `b968826d9c46dd6066d109eabc6255188de91218` |
| Benchmark | `insightos-qwen3-8b-orchestration-benchmark-v4`, 12 cases in each run |
| Latest notebook | `C:\Users\NITHIN\Downloads\notebook9c68468090.ipynb`; all 13 code cells executed without a recorded error |
| ZIP verification | CRC and every manifest-listed SHA-256 digest passed for both archives |
| Latest source inputs | All 16 recorded hashes matched repository inputs at audit start |
| Execution boundary | Notebook-local simulation; zero actual tool dispatches and zero specialist dispatches |

Artifacts:

- Original notebook: `C:\Users\NITHIN\Downloads\notebookf141cf87f0.ipynb`
- Original results: `C:\Users\NITHIN\Downloads\InsightOS_Qwen3_8B_Runtime_Evaluation_20261106T164830Z-87e370c1.zip`
- Latest notebook: `C:\Users\NITHIN\Downloads\notebook9c68468090.ipynb`
- Latest results: `C:\Users\NITHIN\Downloads\InsightOS_Qwen3_8B_Runtime_Evaluation_20261106T180645Z-67b0fcea.zip`

The notebook harness simulates proposal validation and result assembly. It does not exercise production authorization, actual tool execution, specialist dispatch, or end-to-end checkpoint retrieval. Its latency figures are generation latency only and must not be presented as production agent latency.

## Independently verified metric comparison

| Metric | Original | Latest | Interpretation |
|---|---:|---:|---|
| Slim schema validity | 11/12 | 11/12 | Unchanged; the invalid case moved from causal rejection to checkpoint resumption. |
| Decision accuracy | 8/12 | 9/12 | Improved by one case: dashboard scope. Grounded profile interpretation remains wrong. |
| Tool selection | 10/11 | 10/11 | Unchanged; see denominator caveat below. |
| Tool arguments | 10/11 | 10/11 | Unchanged; see denominator caveat below. |
| Exact tool sequence | 10/11 | 10/11 | Unchanged; no actual tool was dispatched. |
| Clarification accuracy | 10/12 | 10/12 | Unchanged in aggregate; case 1 improved while case 10 regressed. |
| Repair success | 2/3 | 4/5 | Higher fraction, but different denominator and failure population; not a controlled comparison. |
| Mean latency | 21.25268 s | 26.79315 s | Latest was 5.54047 s higher per case. Simulation generation time, not production latency. |
| P95 latency | 59.42784 s | 63.53 s | Latest was 4.10216 s higher per case. Same simulation-only limitation. |

Tool selection, argument, and sequence denominators include the schema-invalid case 12 as a miss despite the absence of a parsed final proposal. Thus, these rates conflate response validity with tool quality. The latest run's actual proposals were schema-valid (5/5 final proposals; 8/8 attempts); no execution was simulated. Case 1's unexpected project-list proposal is not included in the exact-tool denominator because its benchmark case has no exact tool-call expectation.

## Case-by-case comparison

`Prepared` means only that the notebook constructed a proposal. It does not mean the tool ran.

| Case | Expected | Original → latest | Diagnosis |
|---|---|---|---|
| `intent-dashboard-scope` | Clarify missing project/dataset scope | `continue` + `list_projects` → `clarify` | **Improved.** Latest asks for scope and prepares no tool. |
| `plan-profile-quality-analysis` | Continue with profile lookup | `continue` + `get_profile(42,7)` → same | **Unchanged, correct.** Profile proposal only; no engine execution. |
| `tool-selection-project-list` | Continue with project listing | `continue` + `list_projects` → same | **Unchanged, correct.** |
| `tool-arguments-strict-identifiers` | Clarify quoted integer identifier | `clarify` → same | **Unchanged, correct.** No tool proposal. |
| `structured-tool-call` | Continue with file listing | `continue` + `list_files(dataset_id=42)` → same | **Unchanged, correct.** |
| `specialist-data-delegation` | Propose Data Intelligence handoff | Handoff present → handoff absent | **Regression.** Latest run has a missing handoff; specialist dispatch was zero in both runs. The notebook's synthetic input is not proof of an app-trusted profile checkpoint. |
| `interpret-tool-result` | Continue; report only supplied tool result | `answer` → `answer` | **Unchanged decision error.** Both outputs correctly restate the reported `2` missing values in `customer_id`; the user asks for a workspace-result interpretation, not a standalone answer. |
| `tool-error-bounded-retry` | Continue with at most one retry proposal | `continue` + `list_files(dataset_id=42)` → same | **Unchanged, correct proposal.** Not executed. |
| `clarify-ambiguous-analysis` | Clarify missing analysis scope | `clarify` → same | **Unchanged, correct.** |
| `grounded-profile-response` | Continue with only the supplied 18-row / 3-column facts | `answer` with extra size conclusions → `clarify` | **Regression and human-review item.** Latest asks for column types/structure even though the question is answerable descriptively from the supplied counts. Earlier output also inferred the dataset was “relatively small” and had “limited” variables. |
| `resume-without-engine-replay` | Retrieve persisted EDA result, do not rerun | `continue` + `get_eda_result(42,7,101)` → invalid after two repairs | **Regression caused by validation/state mismatch.** Latest raw response says EDA was completed and proposes retrieval. The generic execution-claim check rejects the historical completion wording instead of matching it to a trusted checkpoint. No retrieval or engine operation ran. |
| `reject-unsupported-causal-action` | Abstain without tools or handoff | Invalid after two repairs → `abstain` | **Improved.** Latest response is schema-valid; the earlier schema/repair path failed. Human review remains appropriate for semantic abstention quality. |

## Root causes and implementation changes

### Decision classification and evidence provenance

The archived `interpret-tool-result` output accurately describes the supplied tool result but sets `decision="answer"`. The grounded-profile outputs likewise show the same dataset-profile counts can elicit an unnecessary clarification. These are decision/prompt adherence failures; neither case has an invalid tool proposal.

`validate_runtime_turn` now requires `continue` for recognized workspace-data interpretation, requires clarification for a dashboard/report workflow with no explicit or trusted scope, and rejects unrelated clarification when the user has supplied sufficient profile/result facts. For those user-supplied facts, it requires explicit attribution, disallows new numeric facts, and rejects common qualitative judgments not supported by the message. The orchestrator instructions now emphasize these same distinctions.

This deterministic enforcement is intentionally limited to recognizable workspace/result/profile language and supplied evidence. It is not a general semantic proof system; unseen paraphrases and nuanced conclusions still need evaluation and human review.

### Historical execution claims and persisted-result retrieval

The latest `resume-without-engine-replay` raw attempts all propose `get_eda_result` with the correct IDs. The response's “has been successfully completed” language trips the evaluator's broad proposed-action check. In application code, `_trusted_runtime_state` previously retained successful tool names and verified profiles but no completed engine-operation identity, so validation could not safely distinguish historical EDA completion from a newly completed action.

The runtime now carries completed EDA operation identity only from a successful structured `run_eda_analysis` result with `Completed` or `PartiallyCompleted` status and positive integer dataset, file, and analysis IDs. Runtime validation allows historical execution wording only when it clearly signals checkpoint/persisted history and matches all trusted IDs. A resume request with a matching completed dataset/file cannot propose another `run_eda_analysis`; it must use `get_eda_result` unless the user explicitly requests a rerun. This preserves the execution-integrity check rather than permitting arbitrary past-tense claims.

The latest notebook fixture is flat/synthetic, not the same app-shaped trusted structured result used by this runtime implementation. Therefore the archived failure identifies a real broad-validator problem but cannot itself verify the production checkpoint adaptation.

### Other mandatory contract checks

- Added runtime enforcement of one to three response sentences; JSON parsing and the existing strict Pydantic schema remain the format boundary.
- Causal abstentions must explain that timing/correlation does not establish causation and that an appropriate causal design is required; hedged causal claims are rejected.
- Existing quoted-string integer clarification remains in force.
- Handoff proposals remain limited to `agent` and `objective`; a handoff is described as proposed, never dispatched/completed.
- No benchmark case IDs, expected decisions, or gold responses were added to production instructions.
- The three-model architecture and deterministic engines were not replaced or collapsed.

### Changed files

- [`slim_orchestrator.py`](../../app/agent/slim_orchestrator.py#L172): general decision, evidence, causal, resume/no-replay, historical-claim, and response-length validation; aligned core instructions.
- [`runtime.py`](../../app/agent/runtime.py#L211): extraction and transport of trusted completed EDA IDs from structured runtime results; provides a handoff-availability signal only when a verified profile matches the active dataset/file.
- [`orchestrator.py`](../../app/agent/orchestrator.py#L725): routes a descriptive question about explicitly supplied profile row/column counts to the model instead of blocking it as a new inspection request; questions about other profile facts still require inspection.
- [`test_slim_orchestrator.py`](../../tests/test_slim_orchestrator.py#L406): generalized positive/negative regressions for decisions, scope, provenance, handoffs, checkpoint retrieval/replay, causal abstention, and response length.

## Test results

Command:

```text
cd backend
python -m unittest tests.test_agent_runtime tests.test_agent_api tests.test_agent_business_intelligence tests.test_slim_orchestrator
```

Result at the time of this audit: **116 tests passed**. Expected test-fixture logs include simulated provider failures, workflow failures, and deprecation warnings; the suite exited successfully.

Focused orchestrator suite also passed independently: **29 tests**.

## Remaining risks and required re-evaluation

No post-change Qwen3-8B inference was run. Local PyTorch is CPU-only, and the original evaluation requires the pinned model revision on a CUDA environment. Unit tests prove deterministic guards and checkpoint extraction, not model adherence or benchmark improvement.

Before acceptance:

1. Re-run the exact 12-case v4 benchmark with the same model revision, generation settings, and evaluation contract, using refreshed source hashes.
2. Verify that app-shaped trusted history (not only notebook fixtures) yields a `get_eda_result` proposal and blocks replay of the matching `run_eda_analysis` operation.
3. Compare every case and recompute denominators; separately report proposal validity, tool selection among parsed proposals, clarification, handoff presence, repair, and latency.
4. Human-review `interpret-tool-result`, `grounded-profile-response`, specialist handoff, causal abstention, and any case with factual/provenance heuristics.
5. Treat evaluation success as unproven until the comparable post-change run demonstrates it. The model may continue to miss decision semantics despite runtime repairs.

## Follow-up code review and fixes (2026-11-07)

The three requested files were reviewed with their runtime call paths. That review found an additional producer-side block: `execute_orchestrated_inspection` intercepted a user-supplied profile-count question mentioning rows/columns, then returned `dataset_context_required` before the slim orchestrator could answer from the supplied counts. This caused the grounded-profile regression independently of model behavior. The route now yields to the orchestrator only when profile row and column counts are present and those are the only requested inspection facts; a request for missing values or another unavailable fact continues to require a dataset.

The checkpoint validator now accepts a completion statement only when the user is resuming a task, a successful trusted EDA checkpoint matches the dataset/file/analysis IDs in the claimed sentence, and the sentence does not claim that result retrieval itself has completed. A matching resume must propose `get_eda_result`; replaying the completed EDA engine remains rejected. This addresses the observed raw output (“EDA ... has been successfully completed” followed by a retrieval proposal) without accepting unverified execution claims.

For specialist handoffs, runtime validation now recognizes an explicit Data Intelligence task-preparation/delegation request and requires a handoff proposal only when trusted verified profile state exists for the active dataset and, when selected, file. The runtime still constructs the specialist contract from trusted evidence; it does not dispatch the specialist or accept model-generated runtime-owned fields. Generic questions about the specialist are not forced into handoffs, and an unverified profile still cannot support one.

### Evaluation-fixture limitation

The supplied, read-only evaluation-builder snapshot confirms that two v4 fixtures do not represent the production trusted-state contract:

- The resume fixture stores `tool`, `status`, and IDs at the top level, while `_completed_engine_operation` intentionally reads an app-shaped successful result under `data` and requires a completed engine status. The runtime correctly does not treat the flat fixture as a trusted completed operation.
- The specialist case provides `specialist_input_context`, but the builder's `make_case_runtime_state` and `build_case_messages` consume only `persisted_result_fixture` and `tool_result_fixture`; they do not pass that specialist context into trusted runtime state or the model input. Therefore, its handoff-presence score cannot prove whether the model follows a handoff when a verified profile is actually available.

These are evaluation-fixture defects, not reasons to weaken production trust checks. The attached builder snapshot was not edited. A future comparable evaluation should use app-shaped `persisted_result_fixture` records and expose verified profile state—not gold handoff fields—as an inference-time fixture. This changes evaluation inputs and must be versioned/rehashed before comparing scores.

### Follow-up tests

Re-ran:

```text
cd backend
python -m unittest tests.test_slim_orchestrator tests.test_agent_runtime tests.test_agent_api tests.test_agent_business_intelligence
```

Result: **118 tests passed**. Added integration coverage verifies that an unnecessary profile clarification is repaired into an attributed, count-only response; a request for an unavailable missing-value count still requires dataset context; checkpoint-matched EDA completion can accompany a `get_eda_result` proposal but cannot claim retrieval completed; explicit specialist delegation is repaired into a handoff only when a matching verified profile is present; and a specialist handoff remains a proposal, not dispatch.

Pylance reported no diagnostics for the changed Python files. No post-change Qwen inference was run; the local interpreter has `torch 2.14.0+cpu` and `torch.cuda.is_available() == False`. Model-level improvement remains unverified.

### Refreshed Kaggle evaluation package

Generated the following v2.2.0 files under `C:\Users\NITHIN\Desktop\data`:

- `InsightOS_Qwen3_8B_Runtime_Evaluation_v2.2.0.ipynb`
- `InsightOS_Qwen3_8B_Runtime_Evaluation_v2.2.0_Kaggle_Inputs.zip`
- `InsightOS_Qwen3_8B_Runtime_Evaluation_v2.2.0_README.md`

The package refreshes the 16-file input manifest from the current worktree without changing the benchmark/gold payloads or the supplied original archive, notebook, or v2.1.2 input package. The notebook adapter now constructs sanitized app-shaped trusted checkpoint/profile state from the non-gold synthetic fixtures. Notebook Python-cell syntax, ZIP CRC, all input SHA-256 values, source policy probes, checkpoint retrieval/no-replay, and verified-profile handoff adapter preflights passed. The pinned Qwen inference itself remains pending and must run on Kaggle GPU.
