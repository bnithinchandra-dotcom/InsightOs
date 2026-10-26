# Chat dataset-engine integration

The chat agent can invoke three existing backend engines through its allowlisted
tool registry:

| Tool | Existing engine | Effect |
| --- | --- | --- |
| `normalize_file` | Dataset normalization | Writes normalized artifacts and metadata. Available only after an explicit normalization request. |
| `run_quality_analysis` | Deterministic quality analysis | Persists a quality report for a ready, normalized file. |
| `run_eda_analysis` | Deterministic EDA | Persists univariate, bivariate, or multivariate results for explicitly selected columns. |

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
remain additive-compatible.

The current chat integration materializes its workflow task records from the
validated trace after request execution. `app.workflow_execution` separately
provides dependency-cycle validation, a bounded task scheduler, completed-task
reuse, bounded retries for explicitly idempotent tasks, and conservative
interruption handling. Per-task timeout budgets are stored, but the scheduler
does not forcibly interrupt a blocking synchronous handler; handlers must use
cooperative or provider/engine timeouts. The scheduler is not yet the dispatcher for the chat
specialist/tool calls: a process interruption before the completed trace is
persisted leaves a running workflow without resumable task checkpoints, and
automated chat recovery is not implemented. Do not treat this persistence as
full crash recovery.

## Sequential model-backed specialists

For explicit engine requests, the existing provider-backed orchestrator chooses
the registered tool. It then runs three additional, sequential specialist
calls through the same configured local provider:

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

The router uses the existing provider and model by default. Optional local
model overrides are `INSIGHTOS_LLM_MODEL_ORCHESTRATOR`,
`INSIGHTOS_LLM_MODEL_DATA_INTELLIGENCE`, `INSIGHTOS_LLM_MODEL_ANALYTICS`, and
`INSIGHTOS_LLM_MODEL_PRESENTATION`. Configure these only with model identifiers
already available in the local Ollama runtime. Calls through this router are
serialized and use the configured timeout and output limits; no model was
invoked to test this routing change.

## Current limits

- These tools require the selected dataset context. They do not discover or
  infer a dataset on the user's behalf.
- Quality analysis and EDA require normalized output; chat does not silently
  normalize first. Ask explicitly for normalization as a separate operation.
- EDA and grouped business aggregations use their existing deterministic
  engines. Forecasting and prediction are not implemented.
- The runtime permits one engine operation per chat request; explicitly
  requested read-only analytics/report tools are bounded and separately
  scheduled.
- The specialist stages are logical workflow records, not independent model
  processes; each makes a sequential call through the existing provider. Data
  understanding is limited to the existing file/readiness check and stored
  profile metadata for this path.
- Registered tool calls are persisted as workflow tasks before execution;
  dependencies and actual task outcomes determine their recorded state.
  Recovery is limited to eligible read-only/idempotent work. Ambiguous or
  potentially mutating work is not blindly replayed, and no live model call
  runs automatically at startup.
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
