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
to Redis and idles; it does not consume jobs. Upload, normalization, and quality
analysis execute synchronously in API requests. Preprocessing and EDA are
Python-library/service entry points and are not wired into the API or UI.

Compose-published API and infrastructure ports bind to `127.0.0.1`. The API
does not implement authentication, authorization, or project ownership checks.
Accordingly, the supported boundary is a trusted local development machine;
remote, shared, and multi-user deployments are unsupported. A localhost port
binding is a development exposure reduction, not API authentication and not a
security boundary against local processes or a differently configured
deployment.
Compose port-binding changes apply when containers are created or recreated;
already-running containers may retain their previous published interfaces.
Check the active container bindings when applying this change to an existing
development stack.

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

Library callers
  -> preprocessing on caller-supplied Arrow tables; output remains in memory
  -> EDA on selected, verified normalized Parquet; typed result returned to caller
```

The uploaded object remains the source of truth. Normalization does not
overwrite it. Quality analysis and EDA use normalized output only after
normalization metadata and artifact checksums have been verified. EDA recomputes
statistics from the normalized artifact rather than treating the earlier
profile as artifact-bound statistics. EDA results and preprocessing outputs
currently have no persisted execution-history lifecycle.

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
| Foundation/API | **Partial** | FastAPI in `backend/app/main.py`; SQLAlchemy metadata models and Alembic migrations; health and CRUD/upload/normalize/quality routes. | Local Compose only. No authentication or ownership enforcement. Requests perform significant work synchronously. |
| Ingestion/parsing | **Partial** | `parse_dataset_file` handles CSV, TSV, XLSX, JSON, Parquet, and XML; validates parsing metadata; stores original bytes and deterministic profile. | Upload size defaults to 150 MiB; row/column and format-specific limits exist. CSV/TSV and Parquet are scanned in bounded records/batches. XML uses secure bounded event passes and materializes at most one record tree at a time; parser profiling rereads the source rather than retaining all parsed rows. JSON uses stdlib whole-document decoding with source-byte and pre-decode depth checks (see JSON boundary below). ZIP/folder/database/API sources are not implemented. |
| Profiling | **Implemented with documented limits** | `DatasetProfiler` computes missing/empty, exact distinct/duplicate counts and numeric/category summaries within operation-specific budgets. | Distinct values default 100,000; numeric values 250,000; duplicate-row fingerprints 250,000. Per-value estimates and a cumulative 16 MiB estimated-retention budget stop hashing/retaining oversized values and mark exact fields unavailable with warnings. Estimates are not measured heap usage. Profile data does not replace artifact-bound analysis. |
| Normalization | **Partial** | `normalize_file` hashes and reparses the source, builds normalized Parquet per logical table, validates rows/schema/nulls and stores checksummed objects. | Output defaults to 600 MiB, written in batches. A raw Arrow batch-size estimate is checked before each batch; a write-through reservation enforces the exact resulting temporary Parquet file length, including footer bytes, before writes. Result metadata includes versioned source/configuration/output provenance. Temporary files are removed on success and failure. This is an output-file length cap, not a process-memory or filesystem-allocation quota. |
| Quality analysis | **Partial** | API validates normalized artifacts, downloads verified local copies, runs deterministic registered rules, and persists versioned provenance-bound reports. | 600 MiB normalized-input cap, 4 MiB rule-tracking memory cap, shared comparison budget, bounded findings/examples/report serialization. `RuleBudget` delegates its counters to `ExecutionBudget` while preserving quality-specific units and exhaustion semantics. Additive report provenance preserves legacy report validation and the existing deterministic analysis key. Real PostgreSQL advisory locking and MinIO integration are not established by unit tests in this checkout. |
| Preprocessing | **Partial** | `DEFAULT_TRANSFORMATION_REGISTRY` and `orchestrate_preprocessing` run explicit DA/BA transforms on Arrow tables; business-semantic operations require explicit approvals. | Input cap 64 MiB Arrow buffers, 1,000,000 rows, 256 columns. Python-string estimates are checked before whole-column conversions and share the byte ceiling across selected columns/chunks. Input/output Arrow IPC fingerprints and ordered per-step provenance are returned in memory. This adds bounded full-table passes; it is not streaming or a process-wide memory bound. Output is not persisted; no API. |
| EDA | **Partial** | `analyze_normalized_file` performs univariate, bivariate and multivariate deterministic analysis on a selected verified normalized table. | Defaults/caps include 1,000,000 rows, 600 MiB selected-column scan metadata, 250,000 numeric values, 50,000 categories, 250,000 grouped values, 50,000 contingency cells, 4,000,000 pairwise work units, 128 MiB estimated decoded strings, and configured result serialization up to 1 MiB. Result provenance is returned but not persisted; no API/history/UI. |
| Frontend | **Partial** | React shell and system-health view. | API client currently calls only health and system-info; dataset and analysis workflows are not connected. |
| Worker/queue | **Planned** | Worker starts, checks Redis, and idles. | No queue consumer, task contract, retries, or execution-budget propagation. |
| Schema modeling, agents, semantic layer, NL analytics, ML, monitoring, governance | **Planned** | Product roadmap only. | No claims of implementation until code and tests establish them. |

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
| EDA | **Partial; service-level library** |
| Model/schema design, semantic layer, agents, NL-BI, ML, monitoring, governance, production deployment | **Planned** |

The historical phase sequence is retained in the root README. Stage completion
must be updated only after its stated user-facing and integration contracts
are implemented and verified.
