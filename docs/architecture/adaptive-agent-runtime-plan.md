# InsightOS runtime readiness and adaptive-agent architecture audit

**Audit date:** 2026-10-24
**Status:** Audit and proposal only. No orchestration or runtime configuration
was implemented by this audit.

## Scope and evidence labels

- **Verified now** means observed in the current worktree, a current local
  service, or a test run in this audit.
- **Historical evidence** means a saved, dated report or the preceding
  temporary chatbot smoke test. It is not evidence that the same behavior was
  revalidated today.
- **Proposed** means a design recommendation, not an implemented capability.

The worktree is on `main` at `47f4cbc`, aligned with `origin/main`, and contains
pre-existing edits to adapter, documentation, test, Qwen baseline, and frontend
files, plus untracked `.vscode/` and BA/DA benchmark work. Those changes and
reports were preserved. This report is the only file created by this audit.
Nothing was staged, committed, or pushed.

No paid inference, model download, Docker startup, or 4B inference was
performed. The configured-model health check below only queried Ollama's model
list and version endpoints.

## 1. Current runtime and configuration map

### Configuration resolution

`get_agent_settings()` reads Python process environment variables with
`os.getenv`; it does not load `.env` itself. An unset provider resolves to
`none`. The code defaults are a 30-second provider timeout, 1,200 output
tokens, four model turns, six tool calls, and 40 history messages; configuration
validates hard upper bounds. The `.env.example` currently contains
`INSIGHTOS_LLM_PROVIDER=none` and `OLLAMA_MODEL=qwen3:4b-instruct`. A model tag
in the example does not enable that model.

For the Compose backend, `docker-compose.yml` supplies the root `.env` through
`env_file`; Compose/service environment overrides and explicit process
environment determine the values seen by Python. The repository `.env.example`
is a template, not an automatically loaded file. A host-launched Python
process likewise needs its variables set by its launcher; the application
doesn't implicitly read `.env`. The frontend has no provider configuration
screen; it reads the provider-status endpoint and displays its status.

**Verified current cause of `provider=none`:**

1. The current process environment has no LLM provider/model variables.
2. The ignored root `.env` exists, but has no `INSIGHTOS_LLM_PROVIDER`,
   `OLLAMA_MODEL`, `OLLAMA_BASE_URL`, or agent-limit entries.
3. `.env.example` explicitly selects `none`.
4. `get_agent_settings()` defaults to `none`, and the existing API at
   `127.0.0.1:8000` reports `provider=none`, `status=not_configured`, and no
   model.

Therefore, the temporary environment from the earlier browser smoke test was
not the standard application's configuration.

### Local model readiness

Ollama is already running locally at `127.0.0.1:11434`. Its model list includes
`qwen3:4b-instruct`, `qwen3:4b`, and `qwen3:0.6b`; `ollama ps` showed no loaded
model. At the latest check, free physical memory was about **1.91 GiB** and
the `qwen3:4b-instruct` layer is about **2.50 GB** (about 2.33 GiB), before
runtime and context-cache overhead. This is not sufficient evidence of safe
headroom, so 4B inference was not attempted. The user's stated preference to
use the higher model was respected; 0.6B was not used for this audit.

A temporary, process-scoped `TestClient` check configured the normal FastAPI
provider-status route for `ollama` / `qwen3:4b-instruct`, using in-memory
SQLite (`sqlite://`). The route returned `healthy`, Ollama `0.40.10`, and the
installed model digest. This proves the selected model is listed by Ollama; it
does **not** prove generation, tool calling, or that loading it is safe under
current memory pressure. No persistent settings or files were changed.

The smallest eventual developer-local configuration is to set only
`INSIGHTOS_LLM_PROVIDER=ollama`, `OLLAMA_MODEL=qwen3:4b-instruct`, and a
backend-reachable `OLLAMA_BASE_URL` in the ignored local `.env`, after
resource headroom is adequate and the user authorizes changing that file. Use
`http://127.0.0.1:11434` when the backend runs on the host; for a Compose
backend reaching host Ollama on Windows, the README specifies
`http://host.docker.internal:11434`. No API key or remote endpoint is needed.
No `.env` edit is made in this audit.

### Historical model evidence

The earlier live React → FastAPI smoke test used `qwen3:0.6b`; it is evidence
for that specific temporary setup, not the current standard configuration or
4B behavior.

The repository's existing local evaluation artifacts also show prior actual
`qwen3:4b-instruct` inference:

- `ollama-qwen3-4b-instruct-analysis-plan-targeted-v10-20261024-0009.json`
  records model digest `0edcdef3…168ba0`, Ollama `0.40.9`, suite
  `insightos-hf-baseline-v2`, scoring `insightos-hf-scoring-v9`, evaluator
  prompt v5, a 300-second evaluator timeout, and 60,829.85 ms completion
  latency. Its one analysis-plan case passed all 11 machine checks but remains
  `needs_review` with human review pending.
- The saved seven-case reconciled report for run `ed8642b…` records suite v1
  and scoring v6. Four cases received human passes; one case remains
  human-unreviewed and is an automated failure. This report predates the v10
  targeted result and must not be treated as its review.

These artifacts establish that Qwen3 4B inference occurred in previous
evaluation runs. They do not establish current 4B application readiness,
successful autonomous tool selection, or approval of the latest analysis-plan
answer.

## 2. Verified root causes and remaining defects

| Finding | Evidence and status | Impact |
|---|---|---|
| Standard app does not select Ollama. | **Verified configuration blocker.** Process and `.env` lack provider selection; template and code default to `none`; running API reports `none`. This is a safe opt-in default, not a provider bug. | Chat requests cannot use a model until explicitly configured. |
| Model health is not generation readiness. | **Verified contract limitation.** Health checks `/api/tags` and `/api/version`; the temporary API check was healthy without loading the model. | UI can show healthy while memory is insufficient or inference fails. |
| 4B resource readiness is not established today. | **Verified current constraint.** 1.91 GiB free versus a 2.50 GB model layer, with additional runtime/context memory required. | Do not run the model until headroom is rechecked and adequate. |
| Default API timeout is below a measured 4B completion. | **Verified mismatch, not a reproduced API failure.** The v10 target took 60.8 seconds with the evaluator's 300-second timeout; application default is 30 seconds. | A comparable application request may time out. Current runtime must be measured before selecting a production timeout. |
| Context length is not explicitly bounded in Ollama requests. | **Verified code gap.** The adapter bounds output tokens and the runtime bounds history bytes, but does not send an Ollama `num_ctx` limit. | Model/runtime memory use can vary with prompt and Ollama defaults. |
| Tool timeouts/cancellation and global resource scheduling are absent from chat. | **Verified code gap.** `ToolRegistry` calls `run_task` without a timeout or cancellation event; backend agent target does not declare enforced timeout/cancellation. There is no global model semaphore or process-wide tool budget. | Synchronous requests can tie up API workers, and concurrent chats can compete for 16 GB RAM. |
| No autonomous 4B tool-selection result is available. | **Verified evidence gap.** Evaluation runner states that tools are not executed; earlier direct tool prompt did not establish model-selected use. A separately supplied tool-call round trip only tested adapter/runtime consumption. | Tool-calling capability must remain unverified until a model-generated call is observed and validated end to end. |
| Application claims are not independently checked against tool evidence. | **Verified design gap.** System instructions tell the model not to fabricate, but there is no deterministic claim-to-evidence validator for final numeric assertions. | Unsupported numeric or execution claims can still be phrased by the model. |
| Per-turn model identity and latency are not persisted. | **Verified code gap.** Usage tokens are included in outcome metadata; provider/model digest, prompt hash, per-call timing, and fallback reason are not recorded with a conversation turn. | Production failures and quality regressions are difficult to reproduce or compare. |
| Conversation processing is synchronous, with a long lease and no client idempotency key. | **Verified API shape.** A per-conversation processing token prevents a superseded result from being saved; the lease is 2,460 seconds. A retried POST has no request idempotency key. | A crashed request can leave a long processing state; retries can duplicate user turns. |
| Analytics tools currently retrieve rather than execute analyses. | **Verified capability boundary.** The nine registered tools are read-only project/dataset/file/profile/quality/EDA-result/schema-result discovery and retrieval. EDA and Schema Architect APIs themselves are deterministic, but are not callable as execute-analysis tools from this assistant. | Don't describe the current assistant as running arbitrary EDA, SQL, joins, forecasts, or models. |

## 3. Exact files and symbols suitable for reuse

| Reusable component | Current role | Reuse in proposal |
|---|---|---|
| `backend/app/agent/contracts.py`: `ModelRequest`, `ModelResponse`, `ToolCall`, `ToolResult`, `ToolActivity`, `AgentOutcome`, `ProviderCapabilities` | Provider-neutral, typed message and outcome contracts. | Extend only where required for request/run IDs, provider-call telemetry, and evidence references; don't make a second provider abstraction. |
| `backend/app/agent/providers.py`: `ModelProvider`, `OllamaProvider`, `create_provider` | Local Ollama and compatible-provider selection, request translation, bounded parsing, safe errors. | Keep Ollama as the default *when explicitly enabled*. Add optional provider strategy behind this interface only if later approved. |
| `backend/app/agent/runtime.py`: `execute_agent_request`, `_bounded_history`, `public_outcome_metadata` | Bounded single-agent model/tool loop and persisted public metadata shape. | Refactor incrementally into route/plan/execute/verify stages, preserving existing caps and result format. |
| `backend/app/agent/tools.py`: `ToolDefinition`, strict Pydantic argument models, `TOOL_DEFINITIONS`, `ToolRegistry.execute` | Tool allowlist, argument validation, bounded JSON result handling, execution-target placement. | Extend the registry with explicitly approved deterministic operations; never let a plan name an arbitrary callable. |
| `backend/app/task_execution.py`: `TaskRequirements`, `CapabilityRegistry`, `select_execution_target`, `ExecutionContext`, `run_task` | Capability, locality, classification, result-size, cancellation and deadline contracts. | Reuse for typed specialist tasks; close the current gap by only claiming timeout/cancellation when the underlying operation enforces it. |
| `backend/app/eda.py`: `execute_eda_task`, `analyze_normalized_file` | Deterministic EDA algorithms and provenance-aware output. | Use as analyst execution, not as LLM-calculated statistics. |
| `backend/app/schema_architect.py`: `analyze_schema` | Deterministic schema and relationship evidence across selected normalized tables. | Use for schema discovery; current recommendations are candidates, not confirmed join semantics. |
| `backend/app/quality_analysis.py`, `backend/app/quality_rules.py`, `backend/app/provenance.py` | Deterministic quality reports, input keys, artifact/schema fingerprints, provenance validation. | Attach evidence to planned operations and verified claims. |
| `backend/training/evaluation/hf_runner.py`: `load_suite`, `_score`, `evaluate_cases_with_retries`, `rescore_saved_report` | Versioned suite loading, machine checks, retry/scoring, immutable rescoring. | Reuse scorer patterns and report provenance; disable deterministic benchmark fallbacks for model/orchestrator comparisons. |
| `backend/training/evaluation/ollama_runner.py`: `_generator`, `_status_counts`, `run_evaluation` | Local-only Ollama calls, usage/latency and response capture, report output. | Reuse local endpoint validation and telemetry. Current runner is text-only, not an agent/tool benchmark. |
| `backend/training/evaluation/human_review.py`: `reconcile_report` | Source-hash-bound human-review reconciliation. | Keep machine and human outcomes separate; never erase `needs_review`. |

## 4. Current behavior versus proposed behavior

| Concern | Current | Proposed |
|---|---|---|
| Routing | Every valid chat request enters one model/tool loop, and the loop advertises the registered read-only tool schemas. | Deterministic fast paths first; invoke a planner only for genuinely complex data tasks. |
| Analytics | Assistant tools retrieve persisted evidence; deterministic EDA/schema APIs are separate. | Typed analyst operations call existing deterministic algorithms with explicit scope and provenance. |
| Modeling | No forecast/prediction specialist is wired into the chat tool registry. | Add only when a supported deterministic method, input sufficiency checks, and evaluation exist. |
| Verification | Prompt instructions plus tool result status/warnings. | Deterministic evidence/claim checks before synthesis and persistence. |
| Provider | Explicit provider, default `none`; no automatic fallback. | Retain local-first behavior; optional stronger-provider interface remains disabled by default. |
| Execution | Synchronous request, bounded per-request turns/calls/results. | Bounded request graph with end-to-end deadline, cancellation, idempotency, and global resource limits. |
| Telemetry | Outcome status, errors, tool activity, structured results, and usage. | Also persist provider/model digest, prompt version/hash, per-call latency, tool duration, resource estimate, and fallback decision. |

## 5. Adaptive routing decision table

| Request type | Route and evidence policy |
|---|---|
| Simple arithmetic | Deterministic, bounded arithmetic parser/calculator; no planner, data tools, or model call. Reject unsupported syntax; never use Python `eval`. |
| Direct factual/general question | One local synthesis call only when useful. Do not attach dataset tools unless the request depends on workspace data. |
| One-dataset question | Resolve selected context; inspect file/profile/quality/EDA evidence with allowlisted tools. If no dataset is unambiguously selected, ask one concise clarification. |
| EDA or metric explanation | Run/retrieve deterministic EDA and calculations first; synthesize only from returned evidence. Missing denominator, field, or time range means clarify/abstain. |
| Multi-dataset join or schema discovery | Use deterministic Schema Architect evidence for candidates. Do not execute joins based on guessed key meaning; ask for join intent/keys when ambiguous. |
| Forecasting, prediction, anomaly detection | Route only to a registered, deterministic modeling task with sufficient data, validation, and explicit method limits. Otherwise explain that no supported model operation is available. |
| Independent operations | Build a validated dependency DAG; parallelize only independent, read-only, low-memory tasks under the global semaphore. |
| Tool failure | Preserve completed results, mark failed operations, return a partial outcome only when remaining evidence is sufficient. Never convert failure into success. |
| Contradictory or incomplete evidence | Compare source IDs, freshness, versions, and provenance. Prefer neither silently; report the conflict or ask for clarification. No unsupported number is emitted. |

## 6. Target architecture and request lifecycle

The roles below are logical stages, not a requirement for four model instances:

1. **Router/planner:** deterministic classifiers handle arithmetic, simple
   retrieval, and supported single-dataset routes. For complex requests, one
   local model call may propose a typed plan. A strict schema validator accepts
   only named tasks, bounded inputs, and a dependency DAG. The plan is
   declarative data, never code or SQL.
2. **Data analyst:** deterministic functions and current application services
   perform profiling, quality, EDA, schema, and supported metrics. They return
   typed results and provenance. SQL is not exposed to the model.
3. **Modeling specialist:** a future optional deterministic/statistical worker
   handles only registered prediction, forecast, anomaly, or evaluation
   methods. It must not invent an analysis when no supported method exists.
4. **Verifier/synthesizer:** deterministic checks bind each factual/numeric
   claim to result IDs, validate units/denominators/freshness, and reject
   unsupported claims. A single local model call may explain verified results
   in user language; it does not calculate or replace source values.

Request lifecycle: validate message and conversation context; establish a
request/idempotency ID and bounded deadline; persist the user turn; take a
snapshot of selected dataset/file/analysis IDs and source fingerprints; route
direct or plan; validate the plan against the task registry; execute eligible
tasks under resource controls; collect typed outcomes, warnings, references,
and provenance; verify claims; synthesize only if needed; persist completed,
partial, failed, cancelled, or timed-out status plus telemetry; return the
existing response shape. A cancellation or timeout must not erase completed
evidence.

## 7. State, tool execution, provenance, and failure semantics

- Reuse conversation messages and `public_outcome_metadata`; add a request ID,
  idempotency key, provider/model identity, prompt hash, timing, and structured
  claim/evidence references. Preserve response-size bounds.
- Each task result should carry task name/version, input IDs and fingerprints,
  algorithm version, start/end time, status, warnings, result digest, and
  artifact references. Existing provenance formats are authoritative for
  artifact-backed EDA, quality, and schema results.
- Validate task names and strict Pydantic arguments before execution. The
  model/planner cannot add tools, set targets, loosen privacy, or supply
  executable code. Revalidate every project/dataset/file/analysis ID through
  existing service functions.
- Use the current conversation processing token as a concurrency guard, but
  add request-level idempotency so a client retry cannot create a duplicate
  user turn. Keep the lease derived from a bounded request deadline rather than
  a fixed 41-minute period.
- Define terminal outcomes distinctly: `completed` only when required tasks
  and verification pass; `partial` when useful verified results exist but a
  nonessential task failed; `failed` for invalid/unsupported output or
  unsatisfied required work; `timed_out`/`cancelled` for those conditions.
  Persist sanitized errors, never raw provider bodies or internal reasoning.
- Tool outputs are evidence, not instructions. Treat dataset strings as
  untrusted. Bound bytes, rows, columns, time, and result count at both tool
  and request levels.

## 8. Hybrid model strategy and fallback policy

**Default:** local Ollama `qwen3:4b-instruct` only after explicit local
configuration and resource preflight. Do not automatically downgrade to
`qwen3:0.6b`; it is not the selected quality target. No paid/remote provider is
enabled by default.

Keep the existing `ModelProvider` interface as the optional provider seam.
An approved stronger local/self-hosted provider could be configured later; a
remote provider requires separate explicit opt-in and data-transfer policy.

- A single retry on a transient local timeout/connection error may be allowed
  within the same request deadline. A stronger-provider fallback is only
  eligible if explicitly enabled, available, and allowed for the data
  classification.
- Never fallback for invalid input, authorization/context failures, strict
  argument errors, failed/missing evidence, stale or contradictory results,
  unsupported capability, malformed model output, or a tool execution error.
  These need a clear stop/clarification, not a different model.
- Do not send sensitive/local-only data, raw records, or tool results to a
  remote endpoint. By default, keep all prompts and evidence local. A future
  opt-in transfer must be explicit, scoped, and auditable.
- Enforce one fallback maximum and no fallback loops. Do not recursively
  delegate fallback tasks to agents.
- Record primary/fallback provider, model and digest, reason, latency, usage,
  prompt version, and whether data transfer was allowed. Label fallback
  outcomes so they cannot be attributed to the primary model.

## 9. Execution safety and 16 GB laptop limits

Starting policy proposal (measure and tune before relaxing):

- One Ollama generation at a time process-wide. The 4B layer alone is about
  2.33 GiB; current free memory is lower than that and context/runtime memory
  is additional. Require a preflight margin based on measured peak model
  residency, configured context, and at least 2 GiB of remaining OS/workload
  headroom. If the preflight fails, return a clear unavailable/resource status
  without attempting to load the model.
- Set and test an explicit Ollama context limit (initial candidate 8,192
  tokens) in addition to output/history byte bounds; do not rely on advertised
  model maximum context.
- At most two independent low-memory read-only tasks may run concurrently;
  profile/EDA/modeling scans remain one-at-a-time. Start with six total tool
  operations and one planner-repair attempt maximum.
- Limit planning to one plan call plus at most one validated repair; one final
  synthesis call. Cap at four model calls total, a 120-second per-call ceiling,
  and a 300-second end-to-end deadline, then revise from measured 4B
  distributions. These are proposed caps, not current settings.
- Enforce tool timeout/cancellation in the actual blocking service, not only
  in `ExecutionContext`; set a process-wide semaphore around model and
  high-memory tasks. On cancellation, retain completed task records and do not
  persist a success-shaped answer.
- Use only deterministic, tested calculations for numeric outputs. Verification
  must check each numeric claim against tool/calculator output and reject a
  model-generated value that is not supported.

## 10. Reproducible baseline-versus-orchestrator benchmark

Reuse `load_suite`, scoring primitives, and immutable report/reconciliation
patterns from `hf_runner.py`; reuse the local-only endpoint checks and latency
and token capture from `ollama_runner.py`; use `human_review.py` to reconcile
human decisions. Existing reports remain untouched. The current Ollama
evaluation suite is synthetic and text-only, while its documented baseline
comparison may use a different prompt/scoring version; it does not measure
agent tool correctness. The controlled comparison runner has model comparison
and system memory snapshots, but does not exercise this chat runtime's tool
graph.

Create a small versioned agent suite with synthetic in-memory fixtures and at
least these seven scenarios:

1. `10+10` or equivalent deterministic arithmetic.
2. One-dataset metric retrieval and explanation.
3. Multi-dataset schema/relationship discovery with explicit uncertainty.
4. EDA result plus evidence-grounded explanation.
5. Forecast/anomaly request with supported-method guard and insufficient-data
   case.
6. Tool failure followed by bounded partial-result/recovery behavior.
7. Contradictory or incomplete source evidence requiring abstention.

Run baseline and orchestrator on the **same** `qwen3:4b-instruct` digest,
application system prompt/version, temperature, context/output bounds, fixture
snapshot, and timeouts. Use paired prompts and three repetitions for the first
small pilot, serialize all 4B requests, and produce uniquely named reports.
Disable evaluator deterministic fallbacks for model attribution; if a
deterministic route handles a task, label it separately rather than counting
it as a model pass. Preserve machine status, human-review status, source and
rescored status separately.

Record task correctness, grounding/claim support, latency (per call and total,
median/p95), model calls, tool calls, retries/fallbacks, peak process and
system memory where measurable, token usage where available, and failure,
timeout, cancellation, and recovery rates. Record OS, Ollama version, model
digest, prompt/suite/scoring versions, context, resource preflight, warm/cold
state, and fixture provenance.

Proposed initial acceptance thresholds:

- 100% of reported numbers and execution claims are backed by deterministic
  outputs; zero unallowlisted calls, privacy transfers, or reasoning leaks.
- At least 95% of machine-checkable tasks are correct; semantic tasks with
  human-review requirements remain `needs_review` until explicitly reconciled.
- Deterministic arithmetic makes zero model/tool calls and completes under one
  second at p95.
- For tasks requiring the same work, orchestrator p95 latency is no more than
  1.3× baseline; complex tasks remain within the 300-second request deadline.
- No correctness regression versus baseline; complex-task grounding improves
  by at least 10 percentage points or tool/model call count falls by at least
  25% without correctness loss.
- No out-of-memory, swap-thrash, or failed preflight. Publish peak memory and
  require the configured headroom margin on every run.

These thresholds are proposals for a small pilot, not results or promises.
Do not run the existing ten-trial comparison or a large suite without separate
approval and sufficient measured memory.

## 11. Phased implementation roadmap

1. **Runtime readiness (before orchestration):** after authorization and a
   successful resource preflight, configure the ignored local `.env` for
   `qwen3:4b-instruct`; reconcile API timeout and add an explicit context cap;
   verify the normal provider-status route and one isolated application smoke
   test. Do not change defaults for all users.
2. **Evidence contract:** add request/task IDs and provider-call telemetry;
   define claim-to-evidence references and failure semantics, with unit/API
   tests.
3. **Deterministic router:** add arithmetic and simple route paths; test that
   simple tasks do not load planner tools or trigger unnecessary model calls.
4. **Typed bounded plan:** introduce a validated task DAG over existing
   functions; no code generation or arbitrary SQL.
5. **Verifier and auditability:** validate all factual/numeric claims against
   source outputs and add bounded retries, idempotency, cancellation, and
   timeouts.
6. **Modeling specialist only when ready:** implement and benchmark a specific
   deterministic/statistical method; do not expose a general-purpose model
   execution tool.
7. **Small paired benchmark:** add the seven scenarios, compare baseline and
   orchestrator on one model digest, reconcile human reviews, and publish
   immutable reports before expanding.

## 12. Recommended first implementation task

**Establish a safe developer-local Qwen3 4B runtime readiness path, without
multi-agent orchestration.**

Proceed only after explicit approval to edit the ignored local `.env` and after
a memory preflight meets the measured model-plus-context-plus-OS headroom
requirement. Make no global environment or committed-default change. Use the
host or Docker-specific local URL described in the README; don't start Docker
for the validation.

Acceptance criteria:

1. The actual application provider-status route reports
   `ollama` / `qwen3:4b-instruct` and the expected digest, while clearly
   describing health as model availability rather than generation proof.
2. Requests set a bounded context and an empirically adequate timeout; a
   resource-preflight failure refuses inference clearly and safely.
3. With temporary test persistence, the normal frontend-to-backend route
   verifies `10+10`, greeting, and history follow-up on the 4B model.
4. A real model-selected read-only tool call is observed in the provider
   response and executed only through `ToolRegistry`; an adapter-supplied
   tool-call fixture alone does not satisfy this criterion.
5. Mocked tests continue to verify empty/malformed output, sanitized errors,
   timeout, unavailable-model, persistence, visible failure, and retry
   behavior.
6. Report separately the direct local model, API, browser, and mocked test
   evidence. No paid API, Docker startup, model download, staging, or commit.

## 13. Risks, assumptions, and unresolved questions

- **Memory is the immediate blocker.** The 4B model is installed and has
  historical evaluation evidence, but current free memory is below its layer
  size. A health check is not a safe-load signal.
- **Quality is not approved.** The latest targeted analysis plan has 11/11
  machine checks but still needs human review. Prior score/review artifacts
  use different suite, scoring, and prompt versions.
- **Tool selection is unverified.** No actual 4B model-generated tool call has
  been proven end to end. The old 0.6B direct tool prompt did not select one.
- **Current APIs lack auth/ownership enforcement.** Continue to treat the
  supported deployment as trusted local development, not a shared or remote
  service.
- **Join semantics are not implied by schema similarity.** Schema Architect
  relationships are candidates; a real join requires confirmed keys and
  grain.
- **Local endpoint assumptions:** host Ollama must be reachable from the
  backend network namespace. Compose-on-Windows and host-native execution use
  different base URLs.
- **Resource and latency limits are provisional.** The 60.8-second result is
  one targeted run on a prior Ollama version and machine state; it is not a
  stable p95.
- **Open product question:** should future analytics execution be allowed
  directly from chat, or should it require a user confirmation step? Until
  decided, keep mutations and expensive analyses outside the assistant's
  callable tool registry.
- **Open privacy question:** is any stronger-model fallback ever allowed to
  receive dataset-derived content? The default proposed answer is no; any
  exception needs explicit policy and user consent.

## Audit validation performed

- Backend mocked/runtime/API/evaluation regression command:
  `python -m pytest tests\test_agent_runtime.py tests\test_agent_api.py tests\test_agent_evaluation.py tests\test_ollama_evaluation.py tests\test_comparison_runner.py tests\test_human_review.py -q`
  from `backend/`: **93 passed**, 38 subtests passed, 122 existing
  Pydantic/SQLAlchemy deprecation warnings.
- Frontend command `npm test -- --run` from `frontend/`: **22 tests passed**
  across five files, with no React duplicate-key warning. `npm run lint`
  passed.
- Temporary in-process FastAPI provider-status check for
  `qwen3:4b-instruct`: **healthy / model listed**; no generation was run.
- Existing API status: `provider=none`, `not_configured`.
- No 4B inference, browser E2E, or autonomous model-selected tool test was run
  during this audit because current memory headroom is insufficient and the
  current provider is disabled.
