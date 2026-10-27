# InsightOS

InsightOS is a local-first data analysis application under active development.
Its current backend provides file ingestion, deterministic parsing and
profiling, normalized Parquet artifacts, quality analysis, an in-memory
preprocessing library, and a service-level EDA library.

The roadmap below distinguishes what exists in this repository from the
broader product vision. The authoritative system contracts and status are in
the [system architecture](docs/architecture/system-design.md).

## Current implementation

- **File ingestion:** CSV, TSV, XLSX, JSON, Parquet, and XML. ZIP, folder
  uploads, database connectors, and REST-source connectors are not implemented.
- **Profiling and quality:** deterministic stored profiles and versioned,
  advisory quality reports.
- **Normalization:** synchronous conversion to verified, content-addressed
  Parquet tables in object storage.
- **Preprocessing:** explicit Arrow-table transformations with optional
  approval gates and bounded in-memory lineage metadata; no API or persisted
  transformation output.
- **EDA:** deterministic univariate, bivariate, and multivariate analysis of
  verified normalized Parquet, exposed through FastAPI endpoints (`POST /api/v1/datasets/{dataset_id}/files/{file_id}/eda`,
  `GET .../eda`, `GET .../eda/{analysis_id}`) with execution persistence in PostgreSQL;
  the Data workspace can normalize an uploaded file and run univariate EDA
  through the Phase 5B.2 backend execution boundary.
- **Schema Architect (Phase 4, partial):** deterministic cross-file and
  normalized-table schema profiles, candidate key and relationship evidence,
  conditional star/snowflake/hybrid recommendations, conservative normalized
  preservation when evidence is insufficient, clarification context, and
  persisted provenance through `/api/v1/datasets/{dataset_id}/schema-analyses`;
  business semantics remain unconfirmed. The React workflow lets users select
  a project, dataset, and normalized tables; submit or recompute an analysis;
  and review paginated history, relationships, recommendations, warnings, and
  provenance. Frontend workflow tests and a disposable PostgreSQL/MinIO
  browser-to-service run cover selection, analysis, recomputation, retrieval,
  and stale-result display.
- **Frontend:** a React application shell with health/system status, local
  workspace-profile bootstrap, project and dataset creation, supported-file
  upload, ingestion status, normalization, EDA, conversation workflow, and the
  Phase 4 Schema Architect workflow, plus owner-scoped dashboard definitions
  with explicit refresh.
- **Assistant (bounded workflow integration; model-specific behavior unverified):**
  persisted conversations, bounded Ollama/OpenAI-compatible provider adapters,
  deterministic profile inspection, typed discovery/retrieval tools, and
  explicitly requested normalization, quality-analysis, and EDA engine tools.
  It also exposes deterministic multi-dataset analysis and evidence-report
  tools with explicit sources, join keys, and metrics. Specialist stages run
  sequentially through the configured provider for engine operations; durable
  workflow records preserve task outcomes and support explicit recovery of
  eligible stale read-only tasks; engine operations and model reasoning are not
  replayed. Monthly forecasting is available through the deterministic
  analytics API, but is not registered as an assistant tool. Arbitrary code
  execution and automatic analysis selection are not supported. The application
  default provider is `none`; live model generation and autonomous tool
  selection remain unverified.
- **Worker:** a Redis connectivity process only. It does not consume or execute
  jobs.

The Compose development environment is supported for access from the local
machine. Published backend, PostgreSQL, Redis, and MinIO ports bind to
`127.0.0.1`. Some project, dataset, file, and conversation routes enforce ownership using
the single local workspace profile. This is a local-resource guard, not
authenticated user identity or complete authorization across every route;
normalization, quality, EDA, schema-analysis, preprocessing, and dataset
deletion routes do not all apply the same guard. Full tenant isolation is not
established. Remote or multi-user deployment is unsupported. The single-profile
bootstrap is a local-development convenience, not an account or authentication
system.

## First-time data workflow

In the local development UI, open **Projects** and create the one-time local
workspace profile (display name and email identity), then create a project.
Open **Datasets** to create a dataset and upload CSV, TSV, XLSX, JSON, Parquet,
or XML using the existing ingestion endpoint. Ingestion validates the file,
stores original bytes, and returns parser/profile metadata; unsupported files
and operational errors are shown without marking a failed upload as complete.
Select the resulting file to normalize it, then run univariate EDA on a chosen
column. The EDA response reports backend placement and the results/provenance
come from the existing deterministic engine. In Assistant, select the dataset and ask for stored profile facts (counts,
missing values, observed types, or exact duplicate counts); this inspection
path requires exactly one ready file and declines unsupported measurements.
Explicit requests can also invoke the registered deterministic engines and
evidence-backed reporting tools; joins require explicit keys and analytics
arguments. This workflow is local-only; the local profile ownership guard is
not authentication or complete tenant isolation. In particular, analysis,
preprocessing, and dataset-deletion routes do not all apply the same ownership
guard.

## Evidence-backed reports and dashboards

`POST /api/v1/analytics/forecasts/monthly` trains a bounded CPU linear-trend
model with annual calendar features from one authorized normalized Parquet
table. The request names the date and numeric target columns and a 1–24 month
horizon; monthly target values are summed, gaps are rejected, and a
chronological holdout is compared with naive baselines. The workflow and
bounded JSON model artifact are stored in the existing workflow-task tables.
`GET /api/v1/analytics/forecasts/monthly/{workflow_id}` retrieves the
owner-scoped persisted run. This does not yet integrate forecasts into the
assistant or dashboard APIs, provide prediction intervals, or establish
end-to-end dashboard acceptance.

`POST /api/v1/analytics/reports` runs the existing deterministic grouped
multi-dataset engine and returns measured findings, validated charts,
source/file references, warnings, and provenance. Join keys must be supplied
explicitly; supported visualizations are bar, line, scatter, and pie, with
bounded result points and series. `POST /api/v1/analytics/eda-reports` reports
an existing completed EDA result, verifies its persisted provenance against
the current normalized artifact, and does not rerun EDA.

Dashboard definitions are owner-scoped through `GET`, `POST`, `PUT`, and
`DELETE /api/v1/dashboards`. They store a bounded analytics request, chart
configuration, and layout—not source data or computed report results.
Refreshing is explicit and recomputes against current inputs; dashboards do
not refresh automatically. The additive dashboard migration
`7a94c21b6e3f` has been created but not applied. As elsewhere in the local
workspace, the owner guard is not authenticated multi-tenant access.

## Architecture and development contracts

Read the [canonical system architecture](docs/architecture/system-design.md)
for implemented/partial/unverified/planned status, data flow, execution-budget
semantics, error and result contracts, provenance, artifact lifecycle, and
deployment limits. The [architecture overview](docs/architecture/overview.md)
is a short navigation document. Operation contracts remain in:

- [Upload and normalization policy](backend/UPLOAD_FORMAT_POLICY.md)
- [Quality rule engine](backend/QUALITY_RULE_ENGINE.md)
- [Quality rule catalog](backend/QUALITY_RULE_CATALOG.md)
- [Preprocessing operations](backend/PREPROCESSING_ARCHITECTURE.md)
- [EDA](backend/EDA_ARCHITECTURE.md)
- [Open model foundation and evaluation](backend/OPEN_MODEL_FOUNDATION.md)
- [Qwen3 evaluation and fine-tuning preparation](backend/QWEN3_FINE_TUNING_PREPARATION.md)

## Development roadmap

| Phase | Status | Current scope |
| --- | --- | --- |
| 0. Project definition | Implemented | Repository and initial project documentation. |
| 1. Foundation | Partial | FastAPI, SQLAlchemy/Alembic, PostgreSQL, Redis, MinIO, Compose, and frontend shell; authentication and a functioning job worker remain planned. |
| 2. Ingestion and storage | Partial | Six file formats and raw-object lifecycle; ZIP, folders, database sources, and asynchronous ingestion are not implemented. |
| 3. Profiling and quality | Partial | Profiles, normalization, and deterministic quality rules/reports exist; broader interactive workflows remain planned. |
| 3.6. Preprocessing | Partial | Arrow-table transformation registry and orchestration exist as a library; no persisted or API workflow. |
| 3.7. EDA API and persistence | Partial | Deterministic univariate, bivariate, and multivariate EDA API routes with PostgreSQL execution records, idempotency, and version 1 provenance; the Data Workspace supports normalization and univariate EDA, while broader interactive workflows remain planned. |
| 4. AI Schema Architect | Partial | Deterministic schema discovery, relationship evidence, and conditional dimensional-model recommendations with PostgreSQL persistence and versioned provenance; the Schema Architect UI and its disposable-service workflow validation are implemented. |
| 5A. Conversational assistant foundation | Foundation accepted; live local-model inference unverified | Bounded model adapters, allowlisted deterministic tools, persisted conversations, profile inspection, deterministic multi-dataset analytics, and evidence-backed EDA/report tools. The local workspace-owner guard is not authentication or full tenant isolation; approval-gated preprocessing remains unavailable. |
| 5B. Capability-aware execution foundation | Partial | Typed target/task contracts, privacy/locality-aware placement, registered agent tools routed through the backend target, and a bounded browser Web Worker SHA-256 demonstration. No BI analysis has moved to browser/local execution; no cross-environment scheduler or queue exists. |
| 5C. Open model foundation | Provider health and synthetic evaluation implemented; live generation unverified | Provider-neutral completion, honest health/capability status, and evaluation harnesses exist. Ollama 0.40.11 listed `qwen3:8b`, `qwen3:4b`, `qwen3:4b-instruct`, and `qwen3:0.6b` locally on 2026-10-26; health is not generation verification. No Qwen3-8B adapter has been trained. |
| 5-13. Remaining agents, BI, ML, monitoring, governance, production | Partial / planned | Deterministic multi-dataset analytics, evidence-backed reports, on-demand dashboards, and a bounded monthly forecasting API exist; assistant/dashboard forecast integration, prediction intervals, broad governance, and production deployment remain planned. Do not treat roadmap descriptions as implemented capabilities. |

## Assistant configuration

The assistant is disabled by default (`INSIGHTOS_LLM_PROVIDER=none`). To use
the local Qwen model, set `INSIGHTOS_LLM_PROVIDER=ollama`,
`OLLAMA_MODEL=qwen3:4b-instruct`, and an `OLLAMA_BASE_URL` reachable by the
backend. The Ollama provider needs no API key and does not fall back to a
remote service. When the backend runs in Docker on Windows, use
`http://host.docker.internal:11434` instead of `localhost`; when it runs on
the host, use `http://127.0.0.1:11434`. The `.env.example` model tag is a
convenience only; the provider remains disabled until explicitly selected.
Alternatively, configure
`INSIGHTOS_LLM_PROVIDER=openai_compatible` with the server-side base URL, model,
and API key for a local or self-hosted compatible service. The generic
compatible adapter rejects OpenAI-hosted domains; it can send configured
prompts and tool results to other explicitly configured endpoints, so do not
enable it with data unless that transfer is acceptable. The provider-status
endpoint checks whether the configured runtime lists the selected model; this
does not verify generation quality. Model capabilities remain `unverified`
until tested with the selected model. Unsupported adapter features fail
explicitly. There is no automatic provider fallback. Timeout, output-token,
model-turn, tool-call, and history limits are listed in `.env.example`. See the
[open model foundation](backend/OPEN_MODEL_FOUNDATION.md) for the local model
recommendation, resource constraints, evaluation command, and approval-gated
setup instructions. The [Qwen3 preparation report](backend/QWEN3_FINE_TUNING_PREPARATION.md)
records prior baseline outcomes, the token-limit diagnostic, the draft SFT
dataset, and why local Qwen3-8B training has not run.

The tool registry also exposes explicit deterministic engine and reporting
operations as described above; each has strict typed arguments and bounded
results. It does not expose arbitrary code, SQL, shell, or filesystem actions.
Conversations and workflow traces persist in PostgreSQL. Some
project/dataset/file and conversation paths enforce ownership against the
single local profile, but this is not authenticated authorization or a complete
tenant-isolation boundary; coverage is not uniform across every data route.
Normalization, quality, EDA, schema-analysis, preprocessing, and
dataset-deletion endpoints are not all protected by the same owner guard. The
application is supported only in a trusted local development environment.
Stored EDA results may include data-derived values needed to inspect an
analysis; file storage keys are omitted.

**Local model configuration observed 2026-10-27:** the ignored root `.env`
declares Ollama with `qwen3:8b`, a 300-second timeout, and a 512-token output
limit. No role-specific model overrides are set, so when those values are
passed to the backend all four routed roles inherit `qwen3:8b`; the installed
`qwen3:4b-instruct`, `qwen3:4b`, and `qwen3:0.6b` tags are not assigned to
specialist roles.
Ollama listed all four tags, but available physical memory was 1.72 GiB and no
model was loaded. Generation and model-selected tool use were not tested.
Role assignments were not changed because this resource check does not
establish that loading the specialist models is safe. The application reads
process environment variables rather than loading `.env` itself.

### Phase 5A acceptance and remaining gaps

The following acceptance matrix records the earlier 2026-10-20 foundation
snapshot; it predates the engine-tool, multi-dataset report, and durable
workflow integration described above.

The conversation API/UI foundation is accepted for local development. This
does not mean the autonomous BI platform is complete or that a real model has
been accepted.

| Acceptance criterion | Result |
| --- | --- |
| Live HTTP conversation create, send, and history | Passed against the running FastAPI service and existing PostgreSQL database. Create returned `201`; send returned and persisted the user/assistant pair; history returned both messages. |
| Live HTTP error handling | Passed. With no provider configured, send returned `provider_unavailable` / `provider_not_configured` and persisted an explicit assistant failure. Invalid conversation creation returned `422`; missing conversation history returned `404`. |
| Browser conversation workspace | Passed. The Vite UI loaded, listed saved conversations, created a conversation from the composer, displayed the persisted provider-unavailable response, and retained the message history. |
| Schema Architect remains intact | Passed for UI rendering and focused regression tests. The page loaded at `#analysis`; this workspace has no projects, so a data-backed analysis was not available to exercise. |
| No paid API dependency | Passed. The effective provider is `none`; no model request or paid-provider call was made. |
| Real local Ollama/model compatibility | **Not verified in that acceptance run.** Adapter behavior was covered by mocked-transport tests; compatibility with an installed model's tool-calling behavior remained an acceptance gate. The 2026-10-27 runtime snapshot above confirms installed tags, but does not verify generation or tool selection. |

At the time of that acceptance run, the backend environment resolved to the
safe `none` default. Ollama is called via its `/api/chat` endpoint without an
API key; an installed model and a backend-reachable `OLLAMA_BASE_URL` are
required. On Docker, `localhost` refers to the backend container, so a
host-installed Ollama must be addressed through a URL reachable from that
container. Do not switch to a paid provider to satisfy this gate.

Focused verification passed: 15 backend agent API/provider tests and 5
conversation-workspace/Schema-Architect frontend tests. The existing Compose
services and their ports/volumes were left unchanged. Two clearly named live
acceptance conversations were created in the existing PostgreSQL database and
left intact; no existing conversation or dataset was deleted or modified.

**Phase 5A closeout:** accept the persisted conversation/API/UI foundation and
its explicit failure behavior. Keep live generation and model-specific
tool-selection compatibility open as separate gates. The assistant now has
bounded engine and analytics/report tools; this does not establish a completed
autonomous BI platform. Authentication, complete ownership authorization, and
approval-gated preprocessing remain open. Explicit stale-workflow recovery is
limited to eligible idempotent read-only tasks after source validation; it
does not replay engine operations or specialist model calls and does not run
automatically at startup. Selected project/dataset/file and conversation
routes check ownership using the single local profile; this is not
authenticated access and does not protect every data route.

### Local data-workspace acceptance and remaining gaps

Live browser and HTTP acceptance used the existing local services and a
non-sensitive synthetic fixture. No migration, reset, new container, port, or
volume change was made; the existing backend container was restarted to load
the API changes. The acceptance profile, project, dataset, files, and analysis
history remain in the existing PostgreSQL and MinIO stores.

| Acceptance criterion | Result |
| --- | --- |
| First-use profile and project setup | Passed through the UI using the explicitly approved local-only profile. This is not authentication or multi-user authorization. |
| File upload and ingestion | Passed for an 8-row CSV and an 8-row typed JSON file; both appear as `Ready`. The JSON source checksum returned by the API matched the local fixture. |
| Normalization integrity | Passed for both files. The JSON normalization record reports verified source checksum, normalized output checksum/size, and schema/row fidelity. |
| Deterministic numeric EDA | Passed on the normalized JSON `sales` column: 8 rows, `int64`, mean 117.5. The live response selected the `backend` execution target; the completed result and EDA provenance are retrievable from history. |
| Unsupported-file handling | Passed through the UI and live HTTP: an `.exe` upload returned `415` with an actionable supported-format message and did not appear in the dataset file list. |
| Schema Architect | Passed for live UI continuity. It still loads and lists both normalized files from the acceptance dataset; no schema analysis was started. |
| Frontend regression | Passed: 20 tests, lint, and production build. The backend full suite was not rerun in this final acceptance because `pytest` is unavailable in both the host Python and the existing backend container; the focused workspace API tests had passed in earlier validation. |

**Remaining gaps:** numeric-looking CSV columns are preserved as strings in the
normalized schema, even when profiling can summarize their values
numerically. Numeric EDA was therefore verified with typed JSON, not CSV.
Changing CSV inference needs an explicit policy that protects identifiers and
leading-zero values. EDA execution placement is returned on the immediate
analysis response but is not persisted as execution history. Authentication
and complete route-level ownership authorization are not implemented;
selected local-profile checks are not tenant isolation. Keep this workflow
confined to a trusted local development environment.

### Phase 5B capability-aware execution foundation

Backend execution contracts in `backend/app/task_execution.py` describe target
capabilities, task requirements, privacy/locality constraints, placement
rationale, progress, cancellation, timeout support, result validation, and
sanitized failures. Registered read-only discovery tools and explicit
dataset-engine tools pass through this boundary and record the selected
backend target/rationale in their result references.

The System page feature-probes a Web Worker and Web Crypto SHA-256, then can
execute a fixed, bounded sample task locally in that worker. Worker support
and SHA-256 are reported only after the worker initializes and successfully
tests the digest API. The actual task is capped at 16 MiB; timeout and
cancellation terminate the worker, and local-only data is never eligible for
backend fallback. No device fingerprint, RAM estimate, or GPU claim is made.

This is an execution-foundation demonstration, not hybrid BI execution. Browser
execution is not yet wired to ingestion, profiling, quality, EDA, or Schema
Architect, and no task handoff spans browser, local runtime, and backend.
Backend synchronous operations expose cooperative checkpoints only; they are
not forcibly interrupted or given a new execution deadline by this phase.
Optional local runtimes, a queue/scheduler, persisted execution provenance,
approval workflows, and execution of real BI tasks on browser/local targets
remain future work.

### Phase 5B.2: execute one existing BI operation through placement

The existing EDA POST route now sends its deterministic computation through
the capability-aware task boundary, after the route's existing source and
normalized-artifact checks, locks, and idempotency lookup. EDA requires the
backend `deterministic_eda` capability, sensitive-data permission, and backend
locality; transfer to another target is not allowed. The existing EDA engine,
result and provenance contracts, configured limits, and `eda_analyses`
persistence remain in place. The immediate compute response reports the
selected target and placement rationale; cached reads and later history/retrieval
responses do not claim a new execution. Typed EDA errors retain their API
status and persisted failure code, while incompatible placement fails closed.

This is one synchronous backend integration, not a general scheduler: it does
not add browser/local-runtime execution, backend preemption or hard timeout,
cancellation, a queue, persisted execution-placement history, or a new
database migration. No container, port, volume, or shared dataset was changed.

**Phase 5B.2 verification:** all 384 backend tests ran; 383 passed and the
gated PostgreSQL/MinIO integration test was skipped because it requires
dedicated services. Backend compilation passed. The database revision and
migration head both remained `da7304d9fe22`; no migration was applied.

**Phase 5B verification:** backend compilation passed; 378 backend tests
passed and one gated PostgreSQL/MinIO integration test was skipped because its
dedicated-service requirement cannot safely be met with the shared database.
All 17 frontend tests passed, and frontend lint and production build passed.
The live browser initialized its Worker/Web Crypto capability and completed
the sample hash. The current database revision and migration head were
inspected read-only and already matched; no migration was run. Existing
Compose services and persistent data remained in place.

## Python versions

Python 3.14.3 is the sole supported Python version for InsightOS. Backend unit
and PostgreSQL/MinIO integration CI jobs use Python 3.14.3; the backend and
worker containers also use Python 3.14.3.

## Local backend checks

Install Python 3.14.3 and make its executable the active `python`, then run
these commands from `backend`:

```powershell
python --version
python -m venv .venv
.venv\Scripts\Activate.ps1
python --version
python -m pip install -r requirements.txt
python -m compileall -q app tests
python -m unittest discover -s tests -v
```

Both version checks must report `3.14.3`.
The unit suite uses in-memory SQLite and mocked storage for lifecycle tests.
CI also defines an isolated PostgreSQL/MinIO integration job covering migrations
and upload-to-quality processing; it remains unverified until that job succeeds.
Unit-test success is not evidence of live service behavior.
