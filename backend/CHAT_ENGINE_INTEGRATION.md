# Chat dataset-engine integration

The chat agent can invoke three existing backend engines through its allowlisted
tool registry:

| Tool | Existing engine | Effect |
| --- | --- | --- |
| `normalize_file` | Dataset normalization | Writes normalized artifacts and metadata. Available only after an explicit normalization request. |
| `run_quality_analysis` | Deterministic quality analysis | Persists a quality report for a ready, normalized file. |
| `run_eda_analysis` | Deterministic EDA | Persists univariate, bivariate, or multivariate results for explicitly selected columns. |

The registry also exposes explicit join recommendations and multi-dataset
analysis (`recommend_multi_dataset_joins`, `analyze_multiple_datasets`),
evidence-backed grouped reports (`generate_evidence_report`), and reports of
completed EDA results (`generate_eda_evidence_report`). These tools use the
existing deterministic analytics/report handlers; joins, dimensions, metrics,
and chart specifications are validated rather than inferred as completed
results by the model.

Engine tools are advertised only when the chat request explicitly asks for the
matching operation and a dataset is selected. The model must provide a
dataset/file pair and, for EDA, an analysis type and selected columns. The
registry validates strict argument schemas, pins execution to the conversation
dataset, verifies the file through the owner-checked dataset-file lookup, and
permits at most one engine operation per chat request. The existing engine
handlers remain responsible for readiness, artifact integrity, and execution.
Preprocessing transformations are not exposed through chat.

After an engine call, the response is constructed from the structured engine
result rather than from model-generated claims. The full bounded result,
warnings, and execution references are included in the conversation activity.
Each engine call also records a typed orchestration trace for the coordinator,
data-understanding, analytics, verification, and presentation stages. The
trace is persisted with the assistant conversation message and checks that
the engine output identifies the selected dataset and file. Before dispatch,
the data-understanding handoff retrieves the owner-checked file record and
includes bounded schema/profile metadata and the source fingerprint in the
structured engine result. The workflow trace includes ordered task dependencies
from planning to inspection, engine execution, verification, and presentation.
These outputs are persisted with the assistant conversation message. Failed
and partially completed engine calls remain explicit; text generation alone
does not mark an engine operation successful.

Chat requests also create a durable `WorkflowExecution` linked to the owning
workspace user and conversation. The additive
`e4a2d7c91b60_add_durable_workflows` migration adds workflow, task, and
dependency records. The response includes a `workflow_id`, and
`GET /api/v1/chat/workflows/{workflow_id}` retrieves the saved objective,
status, selected source scope, task trace, evidence references, and final
response after rechecking workspace ownership. Existing conversation payloads
remain additive-compatible. `POST /api/v1/chat/workflows/{workflow_id}/recover`
provides explicit recovery for stale workflows.

Registered tool tasks, specialist-stage tasks, and validated inputs are
persisted before execution and dispatched through `app.workflow_execution`, which provides
dependency-cycle validation, a bounded task scheduler, completed-task reuse,
bounded retries for explicitly idempotent tasks, and conservative interruption
handling. Recovery validates current inputs, reuses valid completed tasks, and
can retry eligible failed or stale idempotent tasks through the explicit
recovery endpoint. It does not replay engine operations or run automatically at
startup. Per-task timeout budgets are stored, but the scheduler does not
forcibly interrupt a blocking synchronous handler; handlers must use
cooperative or provider/engine timeouts. This is limited explicit recovery,
not full crash recovery.

## Sequential model-backed specialists

For explicit engine requests, the existing provider-backed orchestrator chooses
the registered tool. It then persists and runs three sequential specialist
tasks through the same configured local provider:

1. Data Intelligence receives bounded selected-file metadata, the stored
   profile summary, source fingerprint, user objective, and inspection
   evidence. Its schema-validated observed fields must exist in that profile,
   and its recommendation must match the user's requested operation.
2. Analytics and Modelling receives that accepted handoff and the exact tool
   arguments. It must approve the unchanged allowlisted operation before the
   deterministic engine is invoked.
3. Insights and Visualization receives the validated handoff, actual engine
   result, and evidence references. Its response is schema-validated, must cite
   only supplied references, and cannot add numeric claims or an unsupported
   chart specification. On failure, the deterministic verified engine summary
   remains and the overall outcome is partial.

The three specialist task records use the existing workflow task and
dependency tables; no second task store is used. Each stores the agent/stage,
bounded arguments, source-state fingerprint, input fingerprint, dependency
task keys and output fingerprints, lifecycle timestamps, and a typed handoff
containing the requested, recommended, and executed operation states. Recovery
compares recorded dependency-output fingerprints with the current completed
dependency results and invalidates downstream work when those outputs change.
The Data Intelligence handoff carries a bounded verified schema/profile
summary. Analytics & Modelling records its exact operation approval; the
actual deterministic metrics/results remain in the separate engine-task
record and are passed to Insights rather than being attributed to the model.
The Insights handoff references the actual deterministic operation, execution
fingerprint, warnings, and evidence artifacts. Handoffs bind dataset/file
identifiers and the source fingerprint; outputs and evidence
references are size-bounded and validated before downstream use. Analytics
depends on successful Data Intelligence; engine execution depends on Analytics;
Insights depends on the completed specialist handoffs and deterministic engine
task.

Explicit recovery may retry an eligible failed or stale specialist task once
when its source and input fingerprints still match and all dependencies are
valid. Completed tasks are reused only while their source and dependencies
remain current; stale upstream output blocks downstream tasks. Specialist
inference is read-only and can be replayed under this bounded policy. A
data-changing engine task is non-idempotent and is not replayed when its
completion is ambiguous; it requires reconciliation. Recovery requires the
workflow to be stale, holds no database transaction while a specialist waits
for the shared inference slot, and never runs automatically at startup.

The router uses the existing provider and model by default. Optional local
model overrides are `INSIGHTOS_LLM_MODEL_ORCHESTRATOR`,
`INSIGHTOS_LLM_MODEL_DATA_INTELLIGENCE`, `INSIGHTOS_LLM_MODEL_ANALYTICS`, and
`INSIGHTOS_LLM_MODEL_PRESENTATION`. Configure these only with model identifiers
already available in the local Ollama runtime. Calls through this router share
a process-wide single-slot inference gate. Waiting for the slot is bounded by
the configured provider timeout; failure to acquire it returns a retryable
`provider_timeout`, and the slot is released when provider calls raise errors.
This is bounded waiting, not a durable model-call queue or automatic
resumption. A claimed specialist waiting on the slot remains visible as a
running workflow task.

## Current limits

- These tools require the selected dataset context. They do not discover or
  infer a dataset on the user's behalf.
- Quality analysis and EDA require normalized output; chat does not silently
  normalize first. Ask explicitly for normalization as a separate operation.
- EDA and grouped business aggregations use their existing deterministic
  engines. Forecasting and prediction are not implemented.
- The runtime permits one engine operation per chat request; explicitly
  requested analytics/report tools are bounded and separately scheduled.
- The specialist stages are durable workflow tasks, not independent model
  processes; each makes a sequential call through the existing provider. Data
  understanding is limited to the existing file/readiness check and stored
  profile metadata for this path.
- Registered tool calls are persisted as workflow tasks before execution;
  specialist stages are also separately persisted with explicit dependencies,
  source/input fingerprints and bounded handoff output. Recovery can reuse
  valid completed stages and retry eligible specialist model tasks explicitly.
  Engine operations and other ambiguous data-changing work are not blindly
  replayed, and no model call runs automatically at startup.
- Multi-dataset relationship recommendations remain advisory. Explicitly
  supplied compatible keys can be used for bounded inner/left joins and
  deterministic grouped aggregations; joins are never auto-approved.
- Analytics and stored EDA results can produce measured findings, validated
  bar/line/scatter/pie or histogram chart specifications, source references,
  and provenance. Owner-scoped dashboard CRUD stores bounded definitions and
  layouts only; reports refresh on demand and are not persisted as live data.
- The orchestration call sequence increases local model generations and may
  consume additional CPU/RAM. Model routing defaults to the configured model;
  role overrides are optional and are not automatically checked against
  Ollama's installed-model inventory.
- Engine invocation does not add new authentication or tenant-isolation
  guarantees to the underlying HTTP endpoints. The chat tool path performs an
  owner-checked file lookup before dispatch, but direct API authorization and
  broader tenant isolation remain separate work.
