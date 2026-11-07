# Qwen3-8B Runtime Evaluation Audit

**Run:** `20261106T164830Z-87e370c1`  
**Benchmark:** `insightos-qwen3-8b-orchestration-benchmark-v4`  
**Model:** `Qwen/Qwen3-8B`, revision `b968826d9c46dd6066d109eabc6255188de91218`

## Executive findings

- The results ZIP passed CRC validation. Its manifest lists eight output files, and all eight SHA-256 digests matched. The ZIP does **not** contain source snapshots as separate members; the executed notebook records 16 input hashes. Those 16 benchmark/application/test inputs matched the repository files when the audit began.
- The executed notebook is nbformat 4.5 with 26 cells. All 13 code cells have execution counts and none records a cell error. It reports 12 cases, a pinned model/tokenizer snapshot, and a passed CUDA/NF4 preflight on a Kaggle Tesla T4.
- The reported accuracy and latency metrics were independently recomputed from per-case outputs and raw generation attempts; all listed numerators, denominators, mean latency, and p95 match the aggregate file.
- This was a **notebook-local runtime simulation**, not production execution: tool and specialist dispatch counts are zero. The specialist handoff in case 6 is blocked because the test input lacks the trusted app-shaped profile state required by the runtime. These are not production agent execution or end-to-end specialist-coordination metrics.
- Four cases do not meet the benchmark's automated expectations. Case 12 is an actual response-contract/repair failure; cases 1, 7, and 10 are valid-schema decision mismatches (case 1 also makes an unsupported dashboard plan). The two grounded-result responses are semantically sound but use `answer` where the prompt/gold requires `continue`.
- Production prompt construction previously injected all 12 benchmark-derived few-shot responses. The evaluated notebook explicitly injected zero few-shot examples, so this is not the cause of the run's failures, but it violates the no-test-answer-in-inference-prompt constraint. The active production prompt path has now been corrected.
- Focused runtime, API, orchestrator, business-intelligence, and slim-contract tests pass after the changes. A new pinned-model run is **not available from this machine**: its Python environment is CPU-only (`torch 2.14.0+cpu`, `torch.cuda.is_available() == False`). No post-change model metrics are claimed.

## Artifact verification and run configuration

Primary evidence was read from:

- `C:\Users\NITHIN\Downloads\notebookf141cf87f0.ipynb`
- `C:\Users\NITHIN\Downloads\InsightOS_Qwen3_8B_Runtime_Evaluation_20261106T164830Z-87e370c1.zip`

The ZIP contains the eight run outputs plus `sha256_manifest.json`. CRC validation passed and every output digest matched the manifest. The configuration reports:

| Property | Verified value |
|---|---|
| Run ID / UTC creation time | `20261106T164830Z-87e370c1` / `2026-11-06T16:52:46.113972+00:00` |
| Model / tokenizer revision | `Qwen/Qwen3-8B` / `b968826d9c46dd6066d109eabc6255188de91218` |
| Benchmark | `insightos-qwen3-8b-orchestration-benchmark-v4`, 12 cases |
| Generation | greedy, thinking disabled, 16,384 input tokens, 512 output tokens, seed `20261105` |
| Quantization | 4-bit NF4, FP16 compute, double quantization |
| Evaluation host | Kaggle, Python 3.13.16, Torch 2.11.0+cu130, Tesla T4, 14.562 GiB VRAM |
| CUDA preflight | passed |
| Adapter boundary | source-grounded notebook simulation; no production app/tool dispatch |
| Specialist dispatch | false |

The notebook metadata marks the run `zero-shot-leakage-free`; it audits the bundled production examples but passes **0** few-shot examples to the evaluation model. It also records 12/12 independent cases, and every notebook code cell executed without an error output.

The results archive itself contains no separate benchmark/source snapshot files. It embeds each benchmark case in `per_case_evaluation_results.json` and records 16 input hashes in `execution_configuration.json`. Those 16 hashes matched the corresponding repository inputs at audit start; the three implementation files listed below have since intentionally changed.

## Independently verified metrics

| Metric | Verified result | Interpretation |
|---|---:|---|
| Slim schema validity | 11/12 | Case 12 exhausted the two repairs without a valid DTO. |
| Decision accuracy | 8/12 | The two grounded-result cases output `answer` rather than the expected `continue`; case 1 chose `continue` rather than `clarify`; invalid case 12 scores incorrect. |
| Tool selection accuracy | 10/11 | The denominator is cases with an exact expected tool sequence; invalid case 12 is scored as a miss despite containing no parsed tool proposal. |
| Tool argument accuracy | 10/11 | Same denominator and invalid-response penalty; this is not evidence of an invalid argument. |
| Exact tool sequence | 10/11 | Includes expected empty sequences; invalid case 12 is scored as a sequence mismatch. |
| Tool proposal validation | 6/6 final; 7/7 all attempts | Every proposal actually produced passed the adapter's tool-schema validator. |
| Clarification accuracy | 10/12 | Case 1 did not clarify; case 12 was invalid. |
| Specialist handoff presence | 12/12 | Presence only; case 6 was blocked and no specialist was dispatched. |
| Repair success | 2/3 | Two repaired cases passed; case 12 failed both repairs. |
| Mean latency | 21.25268 s/case | Per-case total includes all generation attempts. |
| P95 latency | 59.42784 s/case | Same per-case, retry-inclusive measure. |

The first six rates above match the stored aggregate numerators and denominators exactly. However, the tool-selection/argument denominators conflate response validity with tool quality: case 12 has no parsed tool call, so its schema failure also counts as a tool and argument miss. Every proposal that was actually produced passed proposal validation (6/6 final proposals, 7/7 across attempts). Case 1's unexpected `list_projects` proposal is also outside the exact-tool denominator because its gold case has no `tool_calls_exact` field; its decision/clarification errors are still counted. Mean and p95 were separately calculated by summing generation latency per case from `raw_model_outputs.json`. Latencies are not per-generation latency and must not be reported as production agent latency.

## Case-by-case results

`Prepared` means only that the notebook adapter prepared a proposal; it does not mean a tool executed.

| Case | Expected → actual | Schema | Tool/handoff result | Diagnosis |
|---|---|---:|---|---|
| `intent-dashboard-scope` | clarify → continue | valid | `list_projects` prepared | Missed required scope clarification. The plan also promises future dashboard creation despite no dashboard capability in the available schema. No dashboard was created. |
| `plan-profile-quality-analysis` | continue → continue | valid | `get_profile` prepared | Expected first step and arguments; the engine call was correctly deferred pending profile evidence. |
| `tool-selection-project-list` | continue → continue | valid | `list_projects` prepared | Correct tool and empty arguments. |
| `tool-arguments-strict-identifiers` | clarify → clarify | valid | no dispatch; one repair | Correctly kept a quoted numeric identifier uncoerced and asked for clarification. |
| `structured-tool-call` | continue → continue | valid | `list_files` prepared | Correct tool proposal and dataset argument. |
| `specialist-data-delegation` | continue → continue | valid | handoff blocked | The model requested Data Intelligence. The adapter correctly refused to construct a trusted handoff from the evaluator's expected contract fixture; no specialist was dispatched. |
| `interpret-tool-result` | continue → answer | valid | no dispatch; one repair | Response faithfully reports the supplied `2` missing values in `customer_id`. Decision differs from contract guidance/gold. First generation was prose-only JSON and was repaired. |
| `tool-error-bounded-retry` | continue → continue | valid | one `list_files` proposal prepared | Proposal matches the allowed single retry; it was not executed in this simulation. |
| `clarify-ambiguous-analysis` | clarify → clarify | valid | no dispatch | Asked for missing context and avoided a causal conclusion. |
| `grounded-profile-response` | continue → answer | valid | no dispatch | Response restates 18 rows and 3 columns and limits conclusions to structure. Decision differs from contract guidance/gold. |
| `resume-without-engine-replay` | continue → continue | valid | `get_eda_result` prepared | Correctly proposes retrieving the persisted result rather than rerunning analysis; no engine executed. |
| `reject-unsupported-causal-action` | abstain → invalid after 2 repairs | invalid | safe failure; no dispatch | First attempt abstained and rejected causal attribution, but included follow-up questions that the DTO rejected. Subsequent repair attempts introduced malformed/mutually incompatible fields. |

## Failure root causes

### `intent-dashboard-scope`

- **Expected:** ask which project/dataset to use (`clarify`).
- **Observed:** valid `continue` response with `list_projects`; its plan says to create a dashboard, though no such capability is available in this case.
- **Cause:** the evaluated source prompt directed data tasks toward approved tools but did not make essential-scope clarification and unavailable-capability limits explicit. The registry correctly exposed only the case's allowed tools; it did not authorize dashboard execution. This is a prompt-adherence/capability-communication failure, not a schema or tool-argument failure.
- **Correction:** the slim instructions now require essential project/dataset/file scope before a data workflow, prohibit workspace browsing as a substitute unless discovery was requested, and prohibit promises/plans for unavailable capabilities. See `SLIM_ORCHESTRATOR_INSTRUCTIONS` in `backend/app/agent/slim_orchestrator.py`.
- **Unresolved:** prompt changes have not been tested with the pinned model; the deterministic runtime cannot guarantee that free-form plan text never overpromises.

### `interpret-tool-result`

- **Expected:** `continue`; observed `answer`.
- **Observed content:** “The quality tool result indicates that there are 2 missing values in the 'customer_id' column.” This is grounded in the fixture and does not add unsupported values.
- **Cause:** no schema, tool, or reasoning-content failure. The prompt's distinction between a grounded continuation and a final answer was too easy to misapply in a self-contained summary task. The source contract already directs grounded supplied results to `continue`.
- **Correction:** the decision guidance now explicitly says a response grounded in supplied InsightOS results/profile state is `continue` even when no further tool is needed; `answer` is reserved for general/standalone responses. See `SLIM_ORCHESTRATOR_INSTRUCTIONS` in `backend/app/agent/slim_orchestrator.py`.
- **Unresolved:** only a new model run can show whether this distinction changes the model's output.

### `grounded-profile-response`

- **Expected:** `continue`; observed `answer`.
- **Observed content:** accurately restates the supplied 18-row/3-column profile and says further analysis is needed for relationships.
- **Cause and correction:** same classification ambiguity as `interpret-tool-result`; no evidence of an incorrect profile conclusion, invalid schema, or unsupported tool action. The same instruction change addresses both without case-specific logic.

### `reject-unsupported-causal-action`

- **Expected:** valid `abstain`.
- **Observed first attempt:** it chose `abstain`, rejected definitive causal attribution, and asked what period/other factors should be assessed. `SlimOrchestratorTurn.validate_decision` rejected this because any non-`clarify` turn was forbidden from carrying clarification questions. Repair feedback then reduced Pydantic errors to locations/types, omitting the actionable message. The two retries respectively emitted an incorrect key (`hand`) and a handoff with `clarify`, which causal-policy validation rejects.
- **Cause:** contract invariant too restrictive for a safe abstention with an optional follow-up question, compounded by unhelpful repair feedback. This is primarily a DTO/repair design issue; the first model response followed the causal-abstention policy.
- **Correction:** an `abstain` may now carry follow-up questions but still cannot carry tools or a handoff; repair feedback includes the validation message without echoing Pydantic input values and instructs the model to preserve unaffected fields. See `SlimOrchestratorTurn.validate_decision` (`backend/app/agent/slim_orchestrator.py`) and `_complete_slim_orchestrator_turn` (`backend/app/agent/runtime.py`).
- **Unresolved:** the exact run output has not been rerun with Qwen. Unit tests verify the formerly rejected abstention shape and targeted feedback.

## Additional architecture finding: benchmark-answer prompt leakage

Before this correction, production `_slim_contract_prompt` serialized `load_few_shot_examples()`, whose integrity-checked bundle consists of the 12 canonical gold responses. The evaluation notebook avoided this by explicitly substituting an empty example list, so this did not contaminate the recorded zero-shot run. It did mean production and evaluation prompts differed, and production requests could see test answers. The active production prompt no longer includes the few-shot bundle. The bundle/loader remains available to integrity tests but is not called by model prompt construction.

This correction does not change model routing, specialist agents, tool schemas, runtime-owned IDs, permissions, deterministic engines, or dispatch rules.

## Implementation and test results

Changed files:

- `backend/app/agent/slim_orchestrator.py` — allow questions on `abstain`; clarify scope readiness, grounded-result decision semantics, and unavailable capability limits.
- `backend/app/agent/runtime.py` — stop inserting benchmark few-shots into production prompts; make bounded repair feedback include actionable validation messages and preserve unaffected fields.
- `backend/tests/test_slim_orchestrator.py` — assert no gold examples enter the constructed prompt, exercise abstention with a follow-up question, check decision guidance, and verify targeted repair feedback.

Command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'backend').Path
python -m pytest 'backend\tests\test_slim_orchestrator.py' 'backend\tests\test_agent_runtime.py' 'backend\tests\test_agent_api.py' 'backend\tests\test_agent_orchestrator.py' 'backend\tests\test_agent_business_intelligence.py' -q
```

Result: **121 passed, 22 subtests passed**. The run emitted 252 deprecation warnings from existing Pydantic/SQLAlchemy code; no test failed. Pylance reported no diagnostics in `slim_orchestrator.py`; `runtime.py` had two unrelated existing unused-variable warnings.

## Before/after evaluation status

| Measure | Before correction | After correction |
|---|---:|---|
| Pinned Qwen run | 11/12 schema; 8/12 decision; 10/11 tool selection/arguments/exact sequence; 10/12 clarification; 2/3 repair | **Not measured** |
| Contract/prompt regression tests | Not applicable | Pass: no gold prompt examples; abstain-with-question accepted; repair feedback includes actionable error |
| Real tool/specialist dispatch | 0 | Not run |

The original notebook and ZIP remain unchanged. The local environment has no CUDA device, so running this 8B NF4 Kaggle workload locally would not be a valid comparable re-evaluation. A new run must use the same pinned revision, benchmark/gold hashes, and generation settings, while packaging the post-change source hashes into a fresh Kaggle input dataset. Do not compare the new result to the old one until that run completes.

## Remaining issues

1. Re-run the 12-case benchmark on a Kaggle T4 (or equivalent) with refreshed source inputs and the same model revision/settings; compare case-by-case with the verified run.
2. Human-review all 12 outputs. The run's human-review CSV leaves reviewer decisions blank; automated safety/reference checks are heuristics.
3. Keep the simulation boundary explicit. The benchmark does not exercise production authorization, registered tool dispatch, engine execution, task scheduling, or specialist dispatch.
4. Case 6 requires an application-shaped trusted profile fixture to assess production handoff preparation; do not promote an expected contract fixture to trusted state.
