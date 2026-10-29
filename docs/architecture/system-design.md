# InsightOS system architecture

## Authority and status language

This is the canonical current-state design. Operation-specific limits and
algorithms remain in their module documents; this page defines the shared
semantics and system boundaries.

- **Implemented:** present in source and exercised by tests.
- **Partial:** some paths or lifecycle steps exist, while the stated capability
  is not end-to-end or has known limits.
- **Unverified:** implementation exists, but the stated external/runtime
  guarantee has not been verified against the real dependency.
- **Planned:** not implemented; product vision and roadmap text do not imply
  current availability.

These labels describe the evidence presently available in the repository.
Passing unit tests do not establish deployment-scale or live-service behavior.

## Runtime and supported deployment boundary

Python 3.14.3 is the sole supported Python version. CI, the backend image, and
the worker image are pinned to this version; Python 3.12 and 3.13 are not
supported targets.

The current Compose development stack contains FastAPI, PostgreSQL, Redis,
MinIO, a React shell outside Compose, and a worker process. The worker connects
to Redis and idles; it does not consume jobs. Upload, normalization, quality
analysis, EDA, and schema analysis execute synchronously in API requests.
Preprocessing is a Python-library entry point and is not wired into the API or
UI. The local Data Workspace supports profile/project setup, upload,
normalization, univariate EDA, monthly forecasting, and explicit dashboard
saving for verified results. EDA evidence reporting uses the stored EDA
analysis reference and the existing durable analytics-plan task path.
Multi-dataset reports remain an explicitly sourced dashboard-builder path;
they do not consume EDA or forecast results. A general-purpose BI workspace
remains unimplemented.

Compose-published API and infrastructure ports bind to `127.0.0.1`. Selected
project, dataset, file, and conversation routes enforce ownership against the
single local workspace profile. This is not authenticated user identity, and
the checks do not establish complete authorization across every data route or
full tenant isolation. Accordingly, the supported boundary remains a trusted
local development machine; remote, shared, and multi-user deployments are
unsupported. A localhost port binding is a development exposure reduction,
not API authentication and not a security boundary against local processes or
a differently configured deployment.

The local owner guard is applied to selected profile, project, dataset/file
discovery and upload, and conversation operations. Normalization, quality,
EDA, schema-analysis, preprocessing, and dataset deletion do not all apply
that same guard. Do not treat the selected checks as protection for those
routes or as an authorization boundary for shared deployments.
Compose port-binding changes apply when containers are created or recreated;
already-running containers may retain their previous published interfaces.
Check the active container bindings when applying this change to an existing
development stack. **Observed during Phase 5A verification (2026-10-20):**
effective Compose configuration and active container bindings used the
requested loopback interfaces: backend `127.0.0.1:8000`, PostgreSQL
`127.0.0.1:55438 -> 5432`, Redis `127.0.0.1:6379`, and MinIO
`127.0.0.1:9000-9001`.

Compose runs a one-shot MinIO initialization service after MinIO becomes
healthy. It provisions the configured raw, processed, and exports buckets
before starting the backend. Storage health remains read-only; missing buckets
are reported rather than created by health requests. Ingestion and
normalization retain their own idempotent bucket checks for non-Compose
deployments.

## Data flow and ownership

```text
Client upload
  -> size/type checks, SHA-256, parsing, deterministic profile
  -> original bytes in raw MinIO; metadata and lifecycle state in PostgreSQL
  -> verified source download and re-parse
  -> normalized Parquet tables in processed MinIO
  -> checksum/schema/count validation and normalization metadata in PostgreSQL
  -> quality rules scan verified temporary Parquet copies; report in PostgreSQL

API callers
  -> EDA on selected, verified normalized Parquet; execution records and bounded
     results persisted in PostgreSQL
  -> Schema Architect profiles and relationships across selected verified
     normalized Parquet; dataset-level execution records and bounded results
     persisted in PostgreSQL

Library callers
  -> preprocessing on caller-supplied Arrow tables; output remains in memory
```

The uploaded object remains the source of truth. Normalization does not
overwrite it. Quality analysis and EDA use normalized output only after
normalization metadata and artifact checksums have been verified. EDA recomputes
statistics from the normalized artifact rather than treating the earlier
profile as artifact-bound statistics. EDA results are persisted in `eda_analyses`
execution records with version 1 provenance envelopes. Preprocessing outputs
currently have no persisted execution-history lifecycle.

The Phase 4 Schema Architect reads verified normalized Parquet without changing
the uploaded source or normalized artifacts. Its deterministic profiles reuse
the stored parser profile for source physical-type evidence and the existing
quality report for advisory findings, while distinctness and relationship
coverage are recomputed from normalized values. Column roles are exposed only
as heuristic candidates; when evidence does not support dimensional modeling,
the result recommends conservatively preserving the selected tables. Fact,
dimension, grain, and measure semantics remain unconfirmed. Analyses are
persisted in `schema_analyses` with source, profile, quality-report,
normalized-object, and schema fingerprints. Results are explicitly marked
stale when the referenced inputs change. The analysis retrieval endpoint
returns the complete result, including schema, relationships, and model
recommendations, with its freshness state.

The schema endpoints are:

- `POST /api/v1/datasets/{dataset_id}/schema-analyses` — analyze all ready
  normalized files by default, or explicitly selected files/table indices.
  Optional `business_context` is recorded as user context, not treated as
  verified evidence. `recompute: true` explicitly reruns an equivalent input.
- `GET /api/v1/datasets/{dataset_id}/schema-analyses?limit=&offset=` — bounded
  history listing (`limit` 1-100).
- `GET /api/v1/datasets/{dataset_id}/schema-analyses/{analysis_id}` — execution,
  result, provenance, and freshness state.

An analysis key is the canonical SHA-256 of the engine version, selection,
input manifest, and business context. Equivalent requests replay the same
persisted record unless recomputation is explicit. Failed results remain
visible; retrying a failed analysis requires `recompute: true`. Synchronous
execution states are `Requested`, `Running`, `Completed`, and `Failed`.

For JSON columns that require reversible tagged text to distinguish a missing
field from explicit null, normalization metadata retains source-level null and
empty-string counts separately from physical Parquet null/empty counts. The
missing-value quality rule combines these non-overlapping counts. Exact
duplicate-row quality analysis scans verified normalized Parquet directly and
does not require parser-profile duplicate statistics.

## Phase 5A conversational assistant (partial)

The assistant uses a provider-neutral request/response contract with Ollama
and OpenAI-compatible chat-completions adapters. Provider selection defaults
to `none`; reading settings does not make a network request. The provider
status endpoint checks runtime/model availability, while generation occurs
only when a user sends a message. Timeout, streamed provider response bytes,
output tokens, model turns, tool calls, conversation history, tool output, and
persisted response size have explicit bounds. A request runs synchronously in
the API process; these limits are not process-wide memory or availability
guarantees.

The API persists conversations and ordered user/assistant messages in
`conversations` and `conversation_messages`. The endpoints are:

- `GET /api/v1/chat/provider-status` — checks provider configuration and
  runtime/model availability; it does not test generation or model capability.
- `POST /api/v1/chat/conversations` — create a conversation with an optional
  title and optional existing project/dataset context.
- `GET /api/v1/chat/conversations?limit=&offset=` — list conversations.
- `GET /api/v1/chat/conversations/{conversation_id}?limit=&offset=` — retrieve
  a conversation and a bounded, paginated message page.
- `POST /api/v1/chat/conversations/{conversation_id}/messages` — synchronously
  execute one bounded assistant turn and persist the resulting assistant
  message, tool activity, structured results, warnings, and sanitized status.
- `GET /api/v1/chat/workflows/{workflow_id}` — retrieve the persisted workflow
  objective, status, selected source scope, task trace, evidence references,
  and final response after checking workspace ownership.
- `POST /api/v1/chat/workflows/{workflow_id}/recover` — explicitly recover a
  stale workflow by reusing valid completed read-only tasks and retrying
  eligible idempotent read-only tasks.
- `GET /api/v1/chat/workflows/{workflow_id}/tasks/{task_key}/analytics-result`
  — retrieve a completed Assistant evidence-report task after rechecking
  workflow/task ownership, task input identity, normalized source references,
  report provenance, and the persisted output digest.
- `GET /api/v1/chat/analytics/capabilities` — return machine-readable analytics
  capability metadata and parameter schemas derived from registered tool
  definitions, including which operations support the structured report
  workflow and bounded-plan execution.
- `POST /api/v1/chat/analytics/executions` — validate and execute one
  registered evidence-report operation through the existing durable workflow
  task scheduler. Identical operation, parameters, and source-state requests
  resolve to the same persisted workflow.
- `POST /api/v1/chat/analytics/plans` — validate and persist a plan of one to
  five registered evidence-report operations, with dependency depth limited
  to four operations. Dependencies define execution ordering and failure
  blocking only: report tools do not accept upstream output references, so
  result handoffs are rejected. The existing durable workflow task runner
  executes the plan in dependency order, continues independent operations
  after failures, and returns verified per-operation results and provenance.
  Successful report tasks remain individually saveable to dashboards even
  when the overall workflow is partial; dashboard reload uses the existing
  task-result verifier and does not rerun work.

Concurrent sends for the same conversation are rejected while it is being
processed. An unexpected execution failure produces a sanitized failed
outcome rather than a success. If persisting the outcome fails, the API returns
an error and does not claim the response was saved. Conversation and selected
project/dataset operations use the local workspace-owner checks described
above. This is not authenticated user identity or complete authorization;
database foreign keys remain lifecycle constraints only.

The typed tool registry includes existing read-only project/dataset/file
discovery, stored profile and quality-report retrieval, and EDA/schema
result-history retrieval. It also exposes explicit normalization,
quality-analysis, and EDA engine operations, deterministic multi-dataset join
recommendations and analysis, and evidence-backed reports from analytics or
completed EDA results. Each operation delegates to the existing backend
handler or deterministic engine; it does not duplicate numerical logic.
Engine operations require explicit scope and validated arguments, and at most
one engine operation is permitted per chat request. Multi-dataset joins
require explicit keys; many-to-many joins require explicit approval and remain
row-limited. File results omit storage keys; profile and quality summaries
omit sample values. Stored EDA results may include data-derived values needed
to inspect an analysis. Dataset-derived text is untrusted model input, and
schema/key/relationship/model outputs remain hypotheses. No arbitrary code,
SQL, shell, or filesystem tool is registered. Project creation, file upload,
dashboard CRUD, and approval-gated preprocessing are not exposed as assistant
capabilities.

For explicit engine requests, the orchestrator validates a tool selection,
then sequentially runs Data Intelligence and Analytics & Modelling against
the selected-file handoff and exact arguments before deterministic execution.
Insights & Visualization receives the actual engine result and can only return
validated claims and supported chart specifications. Failures remain explicit;
model output cannot substitute for engine results. These are bounded logical
specialist stages using the shared configured provider, not independent
services. Their model identities and the execution trace are recorded with
the workflow.

Chat requests create a durable `WorkflowExecution` linked to the owning
workspace user and conversation. Registered tool tasks and their validated
arguments are persisted before execution and dispatched through the bounded
dependency-aware scheduler. The explicit recovery endpoint validates source
freshness, reuses completed idempotent read-only results, and can retry
eligible stale idempotent read-only tasks. It does not replay engine
operations or specialist model calls, does not run automatically at startup,
and does not forcibly interrupt synchronous handlers. It is limited recovery,
not full crash recovery.

The bounded inspection orchestrator runs before provider setup for supported
stored-profile questions. It requires a selected dataset and exactly one ready
file, resolves only row/column counts, missing-value counts, observed types,
and exact duplicate-row counts, validates the versioned handoff and claims,
and returns explicit unsupported or blocked outcomes for unavailable
measurements. This fast path does not run new analyses; separately, explicit
registered tools can invoke the bounded engines and evidence-backed reporting
described above. Exact duplicate counts do not establish semantic validity;
semantic invalid/suspicious-value analysis remains unsupported.

The conversation-first React workflow retrieves actual project and dataset
choices, supports saved conversations and bounded history, preserves draft
text on request errors, and displays provider status and persisted tool
results. Schema Architect remains independently available. This is a
local-development feature; authentication, authorization, asynchronous job
execution, and deployment-scale provider behavior remain unimplemented or
unverified.

### Structured final-claim validation (partial)

Data-oriented requests and turns that execute an allowlisted tool must return
a bounded JSON claims envelope. The runtime resolves its handoff identifier
only against a successful `get_inspection_handoff` result produced by that
runtime turn, revalidates the handoff schema and content hash, checks dataset
and file scope, and requires a resolved persisted reference with legacy
`verified` status for profile, quality, or schema evidence. `UNKNOWN`, `FAILED`,
the evidence classifications (`OBSERVED`, `COMPUTED`, `INFERRED`, and
`ASSUMED`), stale, and unverifiable references cannot satisfy this requirement.
Supported claims are resolved to values already present in the validated
handoff; the backend constructs the final user-facing wording from those
values instead of returning the model's prose. A failed or malformed
envelope produces a sanitized non-success response.

This is not general natural-language or claim-level verification. The current
claim resolver supports profile-backed table and column facts, verified
quality findings and schema relationship hypotheses, and preprocessing
recommendations (not execution claims). EDA statistics, arbitrary metrics,
predictions, forecasts, causal claims, and other unsupported categories fail
closed. The request-intent detector is a heuristic, and validated-tool
execution also triggers the structured envelope requirement; unsupported
claims therefore may safely degrade to a generic response. Evidence-reference
resolution is limited to the current turn's registered handoff result. JEV
syntax and provenance checks do not resolve arbitrary references against a
general evidence registry. There is no authenticated user principal in the
chat path. The single-local-profile ownership guard on selected API routes is
not tenant authorization, and its coverage is not uniform across all data
operations.
Profile results are linked to the current persisted file record and its
checksum, but the profile payload has no independent source fingerprint;
in-place source replacement therefore cannot be independently ruled out by
the claim resolver alone.

**Phase 5A integration evidence (2026-10-20):** With explicit authorization,
the existing `insightos` PostgreSQL database was upgraded from
`8eb5df4b59e5` to `da7304d9fe22`; no database was created, downgraded, or
reset. `alembic check` detected no metadata drift. The FastAPI
conversation create/send/history and provider-error paths were exercised by
`TestClient` against this existing database; uniquely prefixed fixture
records were removed. The configured provider is `none`; no external provider
was called. Provider adapters and runtime behavior were tested using mocked
transports/providers.

The existing backend container’s live HTTP endpoint
`127.0.0.1:8000/api/v1/chat/provider-status` initially returned 404 because
its running process had not loaded the current chat router. After approval, the
existing backend service was restarted without configuration changes; the
endpoint then returned HTTP 200 with `provider: none` and
`status: not_configured`. Conversation create/send/history passed through
`TestClient` against the existing database, not through live HTTP. Live
model-provider behavior remains unverified.

## Shared execution contract

### Operation scope and accounting

1. Each operation creates or receives a budget with named resources and
   operation-specific limits.
2. Consumption is cumulative for the lifetime of that operation. A new
   independent operation gets a new budget; budgets are never module-global.
   A child budget can only use parent-declared resources and its consumption
   is charged atomically to every ancestor.
3. Resource units must be explicit (for example bytes, rows, comparisons,
   decoded Python strings, result bytes). Similar units do not make two
   operation limits interchangeable.
4. `consume` records completed work. `reserve` makes capacity unavailable
   before an operation starts; callers must commit it after success or cancel
   it after failure. Reservations and consumption are synchronized within a
   shared budget instance.
5. Check a limit before the corresponding expensive allocation or work where
   practical. If an implementation can only check after a bounded batch, its
   maximum overshoot must be documented.
6. Exhaustion must be visible as a typed rejection/failure or an explicitly
   marked partial result. It must not silently return incomplete statistics as
   complete.
7. An estimate is not a hard process-memory limit. Report the estimate and
   its scope accurately; do not call it a process-wide memory guarantee.

**Implemented and tested:** `ExecutionBudget` provides validated named limits,
atomic cumulative counters, hierarchical child budgets, explicit reservations,
and structured exhaustion. Quality's `RuleBudget` preserves its comparison and
tracking-memory API while delegating accounting to this primitive. Preprocessing
and EDA use it for estimated string materialization; normalization reserves
exact temporary-output file growth before each local file write.

**Operation-specific by design:** profiling retains its separate exactness and
cardinality policies, now with bounded per-value and cumulative estimated
tracking bytes. Parser row/depth/element limits and quality-rule comparison
units remain phase-specific. These do not share a process-global budget.
The HTTP upload, normalization, and quality routes are separate request
operations; there is no request-to-worker execution context that propagates
one parent budget across them. Child propagation is available to nested work
that explicitly receives a parent budget, not an implicit cross-request quota.

## Phase 5B capability-aware execution (foundation)

`backend/app/task_execution.py` defines typed execution targets, task
requirements, placement decisions, progress, cancellation, timeout support,
and structured results. Its in-memory capability registry and deterministic
placement policy reject unavailable/incompatible targets, unmeasured memory
requirements, disallowed data classifications, and cross-locality placement
without explicit transfer permission. Local-only data cannot select a backend.
The backend agent registry advertises registered read-only tools and the
explicit dataset-engine capability. A separate backend EDA registry advertises
deterministic EDA for sensitive data; neither registry claims a local-runtime
or browser target exists on the server.

Registered read-only tools and explicit dataset-engine tools pass through the
task boundary and include the selected backend target and placement rationale
in tool result references. Existing discovery-tool behavior, argument/result
bounds, and underlying handlers remain intact; explicitly registered
normalization, quality, and EDA operations use their deterministic handlers.
There is no browser execution of BI operations and no backend job queue.

The React System page feature-probes a real module Web Worker and the Web
Crypto SHA-256 digest API before reporting those browser capabilities. A
bounded, fixed-sample SHA-256 task runs in that Worker; its byte input is capped
at 16 MiB. The worker is terminated on completion, timeout, or caller
cancellation, and results are shape-validated. The placement helper prefers
local execution only when target capability, locality, privacy, permission,
and measurable resource requirements allow it. It never falls back to the
backend for local-only data. Browser memory and GPU are not inferred or
advertised.

### Phase 5B.2: one backend BI operation

The existing EDA POST route passes deterministic univariate, bivariate, or
multivariate computation through the task boundary after its source/artifact
integrity checks, concurrency locks, and idempotency lookup. Placement requires
the backend `deterministic_eda` capability, sensitive-data classification,
backend locality, and no data transfer. EDA's existing validation, resource
limits, persisted result/provenance contract, and typed failure behavior are
preserved. The immediate compute response includes the target and rationale;
cached results and later reads do not claim a new execution. Result
serialization is bounded by the EDA 1 MiB ceiling and the existing configured
EDA result limit.

**Execution boundaries:** this is not a cross-environment scheduler or hybrid
BI execution. The browser sample task remains unconnected to ingestion,
profiling, quality, EDA, or Schema Architect. The new EDA path runs only on the
backend; no BI operation runs in the browser or local runtime. Backend
synchronous operations expose cooperative checkpoints only; they are not
forcibly interrupted or given a new execution deadline. Backend cancellation
and timeout are not advertised as supported. Browser task cancellation and
timeout are enforceable because the worker can be terminated. The
local-runtime target is a contract option only and is not registered or
available. Execution-placement history is not persisted.

### Phase 5B.3: ML suitability and robust anomaly screening

The metadata-only `POST /api/v1/analytics/ml/suitability` endpoint requires an
authorized dataset file, successful ingestion, and verified normalized
artifact. It reuses stored schema and bounded profiling metadata to report
candidate task categories, explicit prerequisites, missingness, constant
columns, and likely identifier warnings. It does not infer a business target
or execute a predictive model. Existing monthly forecasting remains on its
separate chronological evaluation and persistence contract.

EDA also supports persisted `anomaly_detection` results for one explicitly
selected numeric column. It scans at most 250,000 rows from the verified
Parquet artifact and uses a median/MAD robust score, with IQR and zero-
dispersion fallbacks. At most 100 scored rows are retained. The screen is
univariate and its threshold is a screening convention, not a calibrated
false-positive rate; flags are not proof of errors or fraud. Grouped/time-aware
anomaly detection remains unimplemented.

### Phase 5B.4: supervised evaluation

`POST /api/v1/analytics/ml/supervised` requires an authorized ready source,
explicit target and feature columns, and explicit confirmation for
identifier-like or suspicious features. It reads only selected columns from a
verified Parquet artifact, with 20,000-row, 30-feature, 32 MiB artifact,
64 MiB decoded-column, 20-class, and 32-category-per-feature bounds. The
deterministic candidates are Ridge/regularized logistic regression and bounded
random forests, compared against a dummy baseline.

Preprocessing lives inside each training pipeline. Candidate selection uses a
separate training-only validation split; final metrics use a held-out test
split. The request may explicitly select chronological or group-aware
evaluation, which keeps temporal boundaries ordered or groups disjoint across
partitions. Only versioned JSON evaluation evidence and a digest are stored in
the existing workflow task result; executable models are not persisted.
`GET /api/v1/analytics/ml/supervised/{workflow_id}` and the latest-by-file
route verify result integrity and current source identity before returning a
saved result. This is split-specific evaluation evidence, not a production
model, causal conclusion, or guarantee of future performance.

### Resource units and enforcement points

| Dimension | Unit and scope | Accounting and enforcement | Status/limitation |
| --- | --- | --- | --- |
| Uploaded source | Exact bytes per upload | API reads bounded chunks before parsing/storage. | Implemented; does not cap reverse-proxy buffering. |
| Parser records and structure | Rows/columns/elements/depth per parse | Checked during CSV/XLSX/Parquet/XML reading or before JSON result processing; XML source bytes are counted while read. | Implemented and tested, with format-specific paths. |
| JSON expansion | Estimated bytes per parse | Source is limited to 1/64 of configured decoded budget; source depth is scanned before object decoding. | Estimate only; stdlib parsing still materializes the document and decoded string. |
| XML row expansion | Current record and bounded parser chunk | Structural pass builds no tree; later passes build/flatten one record at a time. | Implemented; a single record can approach the configured file-size limit. |
| Profile retention | Exact counts plus estimated bytes per profile operation | Per-value and cumulative retained-profile estimates; disables only affected exact summaries and warns. | Implemented with 64 KiB per-value and 16 MiB aggregate estimates; not RSS measurement. |
| Preprocessing/EDA decoded strings | Estimated Python bytes per transform/analysis | Arrow string payload and conservative object/list overhead estimated before conversion; cumulative within the operation budget. | Implemented for covered conversions; not a process-wide cap. |
| Quality work | Comparisons and retained tracking bytes per quality analysis | `RuleBudget` uses the shared counter/reservation primitive; partial results from an exhausted rule are discarded. | Implemented with operation-specific units and limits. |
| Normalized output | Exact resulting temporary Parquet file bytes, cumulative across tables | Batch payload estimate checked before `write_batch`; sink reserves actual file growth before each write, including footer/finalization; successful file length must equal charged bytes. | Implemented and tested as a file-length cap; not a filesystem-block or total-disk quota. |
| EDA pair/result work | Pairwise comparisons, scan bytes, estimated decoded strings, serialized bytes | Checked before/while selecting and analyzing pairs and before returning bounded JSON-safe output. | Implemented with deterministic operation-specific limits. |
| Temporary storage | Temporary file length/lifecycle | Normalization removes output on all exit paths; callers use scoped temporary files/directories for artifact copies. | Implemented in covered paths; process crash cleanup is OS/temp-directory dependent. |

### Errors and result states

- **Completed:** the requested bounded operation finished.
- **Truncated:** partial output is returned only by an operation that explicitly
  supports truncation and includes counts/warnings; EDA uses this for configured
  pair/category/feature caps.
- **Insufficient data:** an analysis does not meet its minimum input
  requirements and returns its existing insufficient-data result.
- **Rejected:** typed validation, provenance, configuration, or resource-limit
  errors stop execution without a success-shaped complete result.
- **Failed:** unexpected operation failures use the existing typed error or
  failure-result contract for that module.

These are canonical meanings, not a new universal serialized enum. Parsers and
normalization continue to raise typed errors; quality reports per-rule
budget-exhausted states; EDA retains its existing status/result schema.
Consumers must not infer one phase's status field names apply to another phase.

- Invalid configuration, invalid schema/input, provenance mismatch and
  exhausted hard limits are rejected with stable, sanitized typed errors.
- Storage/integrity and execution failures retain operation-specific codes;
  internal data values are not included in public failure messages.
- `failed` results are used only where that API already returns a result object;
  raised typed errors remain raised.
- Failure, truncation, and insufficient-data metadata must remain
  distinguishable and JSON-safe.

These semantics are implemented in the EDA and quality contracts, but are not
yet a common cross-module schema.

### Provenance and schema

Persisted artifacts and derived analyses must retain or validate:

- source dataset/file identity and original SHA-256;
- parser and normalization versions/configuration;
- normalized table index/name, schema, row/column counts, object key, size and
  SHA-256;
- analysis/ruleset version and canonical configuration identity where a
  result is persisted.

`app.provenance` defines a shared, versioned provenance envelope. Its stable
operation ID binds the operation/version, canonical configuration hash,
ordered input/output references, and parent operation IDs; completion time and
verification status are recorded separately. Normalization independently
hashes the local source before parsing, and its result metadata includes source,
configuration, output-object and schema fingerprints. Quality reports include
the same envelope additively; the established quality-analysis key is
unchanged, and reports without the optional provenance member remain valid.
The quality API marks a report verified only after checking the source and
normalized stored bytes. EDA returns provenance with successful and failed
results; successful results follow its artifact checksum, schema and row-count
checks, while failures without completed checks are marked unverifiable.
Preprocessing returns bounded Arrow IPC fingerprints and ordered step
provenance in memory. These records are not database history or an
independently audited lineage log, and in-memory preprocessing outputs have no
artifact read-back verification. No migration or public API schema change is
required for these additive fields.

### Internal verification policy

`app.jev` provides bounded, type-specific comparison policies and evidence
binding. The quality-analysis request path uses it only as a best-effort,
internal cross-check that a validated report's input references match the
source and normalized artifacts whose stored bytes were just verified.
Assessment status and reason codes are server-side log details only; they are
not added to API responses, reports, provenance, or persisted history. The
check does not independently verify quality-rule findings or EDA statistics,
and an unavailable assessment does not invalidate an otherwise validated
primary result. Corrections default off; the tested correction-policy utility
has no production caller. Forecasts and semantic interpretations remain
unverifiable until an approved independent oracle exists.

### Artifact lifecycle

- Original uploads are retained in raw storage and are not changed by analysis.
- Normalized artifacts are written to the processed bucket using
  content-addressed keys and verified before a result is marked ready.
- Analysis copies use scoped temporary storage and must be cleaned on success
  and failure.
- Failed operations must not expose stale results as current success.
- Cleanup uncertainty must be surfaced and retryable; it must not be silently
  represented as confirmed cleanup.

Normalization and quality API paths use scoped temporary directories;
EDA uses a managed temporary file. The modules have unit coverage for their
cleanup/failure paths. Compose provisions MinIO buckets before starting the
backend, and the health check does not mutate storage.

## Current module inventory

| Module | Status | Entry point and actual responsibility | Limits, failure, provenance, and remaining boundary |
| --- | --- | --- | --- |
| Foundation/API | **Partial** | FastAPI in `backend/app/main.py`; SQLAlchemy metadata models and Alembic migrations; health and CRUD/upload/normalize/quality routes. Selected project, dataset, file, and conversation operations check ownership against the single local profile. | Local Compose only. No authenticated principal or complete route-level ownership enforcement; normalization, quality, EDA, schema, preprocessing, and deletion routes do not all apply the same guard. Requests perform significant work synchronously. |
| Ingestion/parsing | **Partial** | `parse_dataset_file` handles CSV, TSV, XLSX, JSON, Parquet, and XML; validates parsing metadata; stores original bytes and deterministic profile. | Upload size defaults to 150 MiB; row/column and format-specific limits exist. CSV/TSV and Parquet are scanned in bounded records/batches. XML uses secure bounded event passes and materializes at most one record tree at a time; parser profiling rereads the source rather than retaining all parsed rows. JSON uses stdlib whole-document decoding with source-byte and pre-decode depth checks (see JSON boundary below). ZIP/folder/database/API sources are not implemented. |
| Profiling | **Implemented with documented limits** | `DatasetProfiler` computes missing/empty, exact distinct/duplicate counts and numeric/category summaries within operation-specific budgets. | Distinct values default 100,000; numeric values 250,000; duplicate-row fingerprints 250,000. Per-value estimates and a cumulative 16 MiB estimated-retention budget stop hashing/retaining oversized values and mark exact fields unavailable with warnings. Estimates are not measured heap usage. Profile data does not replace artifact-bound analysis. |
| Normalization | **Partial** | `normalize_file` hashes and reparses the source, builds normalized Parquet per logical table, validates rows/schema/nulls and stores checksummed objects. | Output defaults to 600 MiB, written in batches. A raw Arrow batch-size estimate is checked before each batch; a write-through reservation enforces the exact resulting temporary Parquet file length, including footer bytes, before writes. Result metadata includes versioned source/configuration/output provenance. Temporary files are removed on success and failure. This is an output-file length cap, not a process-memory or filesystem-allocation quota. |
| Quality analysis | **Partial** | API validates normalized artifacts, downloads verified local copies, runs deterministic registered rules, and persists versioned provenance-bound reports. | 600 MiB normalized-input cap, 4 MiB rule-tracking memory cap, shared comparison budget, bounded findings/examples/report serialization. `RuleBudget` delegates its counters to `ExecutionBudget` while preserving quality-specific units and exhaustion semantics. Additive report provenance preserves legacy report validation and the existing deterministic analysis key. Real PostgreSQL advisory locking and MinIO integration are not established by unit tests in this checkout. |
| Preprocessing | **Partial** | `DEFAULT_TRANSFORMATION_REGISTRY` and `orchestrate_preprocessing` run explicit DA/BA transforms on Arrow tables; business-semantic operations require explicit approvals. | Input cap 64 MiB Arrow buffers, 1,000,000 rows, 256 columns. Python-string estimates are checked before whole-column conversions and share the byte ceiling across selected columns/chunks. Input/output Arrow IPC fingerprints and ordered per-step provenance are returned in memory. This adds bounded full-table passes; it is not streaming or a process-wide memory bound. Output is not persisted; no API. |
| EDA | **Partial** | FastAPI routes in `backend/app/main.py` (`POST /api/v1/datasets/{dataset_id}/files/{file_id}/eda`, `GET .../eda`, `GET .../eda/{analysis_id}`); deterministic univariate, bivariate, and multivariate analysis of verified normalized Parquet; the POST computation uses the capability-aware backend task boundary, requiring sensitive-data permission and backend locality without transfer; PostgreSQL persistence in `eda_analyses` retains idempotency, version 1 provenance envelope, and backend-only JEV lineage verification. The Data Workspace runs univariate analysis and passes its persisted analysis ID to the durable EDA evidence-report task, which can be explicitly pinned to an existing or new dashboard. | Defaults/caps include 1,000,000 rows, 600 MiB selected-column scan metadata, 250,000 numeric values, 50,000 categories, 250,000 grouped values, 50,000 contingency cells, 4,000,000 pairwise work units, 128 MiB estimated decoded strings, and configured result serialization up to 1 MiB. Result records are persisted with bounded pagination (limit 1-100). Placement is reported only on the immediate computation response, not persisted as execution history. |
| Monthly forecasting | **Partial** | `POST /api/v1/analytics/forecasts/monthly` loads an owner-authorized, integrity-checked normalized Parquet table, validates explicit time/target columns, sum aggregation, and a 1–24 month horizon, then executes the existing deterministic engine through persisted workflow/task claims. Evaluation is a chronological holdout plus a bounded expanding-window backtest of up to 12 origins. Model MAE comparison uses a 1% relative tolerance; WAPE is unavailable when the absolute-actual denominator is at most `1e-12`. Structured findings report short history, small holdouts, near-constant targets, long horizons relative to history, and duplicate-month aggregation. A descriptive 90th-percentile absolute-error envelope is emitted only with at least eight out-of-sample origins per lead; it is not a calibrated prediction/confidence interval. Request + source fingerprints provide idempotent reuse; `GET /api/v1/analytics/forecasts/monthly/{workflow_id}` verifies bounded model/output integrity and rejects a changed or unavailable source. The registered Assistant tool uses the same API and explicit-intent gate; the Data workspace renders persisted observed and forecast series, measured holdout metrics, warnings, and provenance using the existing chart component. A completed run can be explicitly appended to an owner-scoped dashboard through `POST /api/v1/dashboards/{dashboard_id}/forecasts`; the dashboard JSON stores the run ID, source fingerprint, parameters, chart specification, timestamps, and provenance references. Dashboard reload and refresh resolve the same verified persisted run and display stale/unavailable states without retraining. Schema-v1 artifacts remain readable with suitability/uncertainty explicitly unavailable. | Monthly gaps are rejected; only sum aggregation and the existing linear-trend/annual-seasonality algorithm are supported. Result/model artifacts are bounded and serialized into the existing task summary; public API responses contain an artifact hash/reference, not the serialized model. Dashboard references are bounded to eight per dashboard and do not duplicate model artifacts or series data. No calibrated prediction intervals, independent accuracy oracle, background queue, or automatic replay is implemented. No schema migration was needed. |
| Assistant report dashboard delivery | **Partial** | Existing dashboard JSON can explicitly pin a completed `generate_evidence_report` or `generate_eda_evidence_report` task through `POST /api/v1/dashboards/{dashboard_id}/analytics-results`. The dashboard stores only the workflow/task reference, result digest, source fingerprints/artifact hashes, and save time; `GET /api/v1/chat/workflows/{workflow_id}/tasks/{task_key}/analytics-result` resolves the bounded persisted task output and revalidates its inputs and provenance. Dashboard reload renders stored charts/findings and unavailable/stale states. Refresh re-resolves the original task and does not rerun its report operation. | At most eight report references are stored per dashboard; report data remains in the existing workflow task result summary. New saves reject incomplete, unauthorized, stale-source, or integrity-invalid results. Dashboard edits preserve saved forecast and report references. No parallel report/dashboard store or schema migration was added. |
| Unified structured analytics journey | **Partial** | `GET /api/v1/chat/analytics/capabilities` describes registered analytics operations directly from `ToolDefinition` parameter models and execution metadata. The Assistant workspace supports bounded single-operation execution and multi-operation plans through `POST /api/v1/chat/analytics/executions` and `/plans`; report tasks are source-validated, persisted through the existing workflow/task scheduler, and returned with verified evidence. The Data Workspace can pass a stored EDA analysis ID into an EDA evidence-report plan and explicitly pin its completed task result to a dashboard. Completed monthly forecast runs are saved through their separate forecast contract. Multi-dataset reports use explicit source, join, dimension, and metric parameters in the dashboard builder; they do not consume EDA or forecast task outputs. | Plans currently accept only registered evidence-report operations, with dependency ordering/blocking but no result-data handoffs. EDA, multi-dataset reporting, and forecasting remain separate operation paths. Execution is bounded and synchronous under existing task, output-size, and workflow limits; there is no new background queue or automatic recovery. |
| Schema Architect (Phase 4) | **Partial** | `backend/app/schema_architect.py` profiles selected normalized tables, identifies candidate keys, infers or rejects relationship hypotheses with overlap/cardinality/fan-out evidence, and produces conditional star, snowflake, and hybrid recommendations; when evidence does not support those structures, it recommends conservatively preserving the selected tables. Column roles are heuristic candidates only. FastAPI persists results in `schema_analyses` with source and artifact provenance. The React workflow selects projects, datasets, and normalized tables; creates or recomputes analyses; and displays history, relationship evidence, conditional recommendations, warnings, and provenance. | Synchronous and deterministic; accepts at most 20 files/tables and 200 columns per table, scans at most 1,000,000 rows and 1 GiB of selected normalized artifacts, retains at most 100,000 typed values (32 MiB canonical-value bytes; 10,000 values per column; 16 KiB per value), evaluates at most 10,000 relationship comparisons/500 candidates, and returns at most 10 MiB. It streams object downloads in 1 MiB chunks and scans Parquet batches. Exact distinct/overlap statistics become unavailable when retention limits are exceeded. Keys and relationships are never called verified constraints; models, grain, and measure semantics remain conditional. A per-file schema-analysis advisory lock prevents duplicate concurrent runs; existing normalization, quality, EDA, preprocessing, and deletion locks also prevent cross-operation overlap. No LLM, source rewrite, or warehouse materialization is used. Isolated PostgreSQL/MinIO integration tests cover project/dataset/file listing, analysis creation/retrieval/recomputation, history pagination, stale detection, and missing-resource errors; a browser run against the same isolated service flow verified frontend selection, creation, recomputation, retrieval, and stale display. |
| Frontend | **Partial** | React shell, system-health view, local workspace profile bootstrap, project/dataset creation, supported-file multipart upload, ingestion and normalization status/error display, retryable normalization, univariate EDA with durable evidence-report and dashboard-save flow, monthly forecasting of verified normalized files, explicit-source dashboard report builder, Phase 4 Schema Architect, conversation workspace, and a feature-probed browser Web Worker SHA-256 demonstration. File mutations refresh only the selected source list; forecast and saved analytics results resolve their original persisted references and display provenance/warnings. | The local profile bootstrap is one-time and unauthenticated for local development only; it is not account/authentication support. Selected API routes check ownership, but route coverage is incomplete and not tenant isolation; remote/multi-user deployment is unsupported. Browser worker is not integrated with ingestion or BI analysis; no cross-target scheduling. |
| Capability-aware execution (Phase 5B) | **Foundation partial** | `backend/app/task_execution.py` provides typed capabilities, deterministic policy, typed failures, and bounded result validation; registered agent tools and deterministic EDA computation use backend targets with their respective classifications/capabilities. `frontend/src/execution.js` discovers browser worker/Web Crypto support and enforces task timeout/cancellation by terminating the worker. | Backend task execution remains synchronous/cooperative; no backend hard timeout/cancellation, local-runtime target, cross-environment scheduler, persisted execution-placement history, or BI operation on a browser worker. |
| Worker/queue | **Planned** | Worker starts, checks Redis, and idles. | No queue consumer, retry lifecycle, or execution-budget propagation. The frontend Web Worker is a separate browser-local execution target, not this service. |
| Bounded assistant specialists and deterministic analytics/report tools | **Partial** | Sequential logical specialists validate explicit registered operations; deterministic multi-dataset analytics, evidence-backed reports, and explicitly requested monthly forecasting are available through typed chat tools. | Not a general autonomous BI system: arbitrary forecasting/modeling, broad NL analytics, semantic layer, monitoring, governance, and production deployment remain planned or unverified. |

### JSON expansion boundary

`MAX_JSON_ESTIMATED_DECODED_MB` defaults to 256 MiB. The parser limits source
bytes to 1/64 of that ceiling, checks nesting depth from the raw bytes before
`json.loads`, and bounds the read even when declared file size is inaccurate.
It releases the decoded text after constructing the Python document.

The multiplier is an estimate, not a Python-version-independent upper bound on
allocator/RSS usage. JSON still uses whole-document decoding below the cap and
temporarily holds the source bytes and decoded text during decoding.
Large JSON datasets may be rejected earlier than other formats. A true
incremental JSON parser is a future option if the format contract and additional
complexity are approved; this implementation does not claim streaming JSON.

### XML record memory boundary

**Implemented and tested:** XML uses `defusedxml` event callbacks with DTD,
entity, and external access disabled. Source bytes, depth, elements, repeated
record rows, and flattened columns are checked while scanning. One pass finds
the single supported record group without constructing a document tree; later
passes build and flatten one record at a time. Profiling uses another bounded
pass. Normalization applies the same streaming row reader.

This bounds retained XML parse structures by one record plus parser chunks,
not total process memory. A single large record and its flattened fields can
still be large up to the source-file limit; callers also retain their own
inputs and profiles.

## Contracts retained by operation

Shared semantics do not impose identical algorithms or numerical limits:

- **Parser:** format/type identification, source size, row/column, XML/XLSX and
  JSON-specific bounds; safe parse error codes.
- **Profiler:** fingerprint and exact-statistic tracking budgets; warns and
  marks exactness unavailable when a tracking budget is exceeded.
- **Normalization:** deterministic type conversion, table boundaries, row
  fidelity, output bytes, checksum and object-store verification.
- **Quality:** rule-specific prerequisites, report/version validation and
  budget-exhausted rule states with partial findings discarded.
- **Preprocessing:** explicit column/operation selection, fit-context binding,
  approval gates, Arrow schemas and non-mutating input/output semantics.
- **EDA:** explicit table/column selection, deterministic statistics,
  pair/feature/category truncation metadata, JSON-safe results and sanitized
  failure behavior.
- **Schema Architect:** selected, checksum-verified normalized table scans,
  deterministic source-profile/quality-report reuse, exact tracked-value
  statistics with explicit unavailable states, bounded relationship candidates,
  persisted freshness/provenance, and conditional dimensional-model hypotheses.
  It never creates database constraints or materializes warehouse tables.

## Milestone 18: Agent-First UX & End-to-End Workflow Integration

### Assistant-First User Journey

InsightOS centers user interaction on the Assistant workspace:
1. **Initial View:** Users are presented with a natural goal prompt ("What would you like to understand about your data?"), verified example goals reflecting real backend capabilities, an active composer, and compact provider/system status indicators.
2. **Intent & Data Context:** Optional project and dataset context selectors scope the conversational session. If no workspace profile exists, a clear actionable notice guides users to initialize their profile rather than showing cryptic API failures.
3. **Execution Transparency:** Live tool activities (`get_profile`, `generate_evidence_report`, etc.) render as an honest progress timeline reflecting real backend states. Failed steps are reported explicitly; simulated percentages or synthetic completion states are strictly forbidden.
4. **Structured Results:** Verified outcomes display human-readable summaries, key findings, and interactive visual charts (`ChartCard`), while raw JSON payloads are tucked behind expandable technical-details disclosures.
5. **Durable Dashboard Persistence:** Verified reports produced by analytical workflows can be saved directly to existing or new dashboards by immutable reference without recomputing data.
6. **Advanced Workflows:** Direct parameter JSON execution and multi-operation dependency plans remain fully accessible inside dedicated collapsible disclosure panels, preserving advanced engineering controls without overwhelming general users.

### API Architecture & Route Alignment

- **Analytics Capabilities:** Registered tool capabilities are exposed at `/api/v1/chat/analytics/capabilities` (defined in `backend/app/agent/api.py` under the router prefix `/api/v1/chat`).
- **Profile Requirement vs 404:** When no local workspace profile exists, `_require_workspace_owner` raises HTTP 409 (`workspace_profile_required`), which is surfaced by the frontend API helper as actionable workspace setup guidance rather than an unhandled 404.

## Test and verification status

Backend CI runs compile checks and `unittest` discovery on Python 3.14.3. Unit
tests use in-memory SQLite and mocked object storage for API lifecycle tests.
They establish deterministic local code behavior, but not PostgreSQL advisory
locks, migration upgrades on a live database, MinIO permissions/consistency,
deployment networking, or peak process memory.

CI also defines a separate integration job that provisions a fresh PostgreSQL
service, builds an isolated MinIO service, applies migrations, and exercises
upload, source-object verification, normalization/Parquet verification, and
quality analysis. The job passed on baseline commit
`3ba1bdce665574fff37259cb2b88d5b2ee1558db`; uncommitted changes still require
their own CI run before their GitHub Actions status is verified. The current
working-tree changes also passed the migration and data-flow integration test
against fresh, isolated local PostgreSQL and MinIO containers; that local run
is not a GitHub Actions result and does not establish deployment-scale behavior.
That integration coverage did not include the Phase 4 Schema Architect; its
PostgreSQL/MinIO migration and lifecycle behavior remains unverified in live
services.
Do not run destructive database/storage integration tests against developer
data or shared services.
The repository does not currently provide a verified production worker,
authentication boundary, or remote deployment profile.

## Roadmap state

| Stage | Status |
| --- | --- |
| Project definition | **Implemented** |
| Foundation | **Partial** |
| Ingestion and storage | **Partial** |
| Profiling, normalization, quality | **Partial** |
| Preprocessing | **Partial; in-memory library** |
| EDA (Stage 3.7 API & persistence) | **Partial; API and PostgreSQL persistence, Data Workspace EDA/anomaly screening, durable evidence reporting, and explicit dashboard save implemented; broader analysis workflows remain** |
| ML task suitability, anomaly screening and supervised evaluation | **Partial; verified-source metadata assessment, persisted bounded univariate anomaly screening, and held-out regression/classification evaluation; executable model persistence and dashboard integration are not implemented** |
| Schema Architect (Phase 4) | **Partial; deterministic API, persistence, frontend workflow, and disposable PostgreSQL/MinIO browser verification implemented** |
| Agent-First UX & Assistant Integration (Milestone 18) | **Implemented; natural language intent routing, registered tool discovery, honest timeline progress, verified result cards with expandable technical details, and advanced structured/plan execution panels** |
| Semantic layer, agents, NL-BI, ML, monitoring, governance, production deployment | **Planned** |

The historical phase sequence is retained in the root README. Stage completion
must be updated only after its stated user-facing and integration contracts
are implemented and verified.
