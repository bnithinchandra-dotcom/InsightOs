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
2. Counters are cumulative for the lifetime of that operation and are passed
   to helpers that contribute to the same resource. A new independent operation
   gets a new budget.
3. Resource units must be explicit (for example bytes, rows, comparisons,
   decoded Python strings, result bytes). Similar units do not make two
   operation limits interchangeable.
4. Check a limit before the corresponding expensive allocation or work where
   practical. If an implementation can only check after a bounded batch, its
   maximum overshoot must be documented.
5. Exhaustion must be visible as a typed rejection/failure or an explicitly
   marked partial result. It must not silently return incomplete statistics as
   complete.
6. An estimate is not a hard process-memory limit. Report the estimate and
   its scope accurately; do not call it a process-wide memory guarantee.

The shared `ExecutionBudget` primitive currently provides monotonic counters
and structured exhaustion for normalization and EDA. Quality uses a separate
`RuleBudget`; profiling and preprocessing use operation-specific policies.
These implementations are not yet unified under one shared accounting
interface.

### Errors and result states

- Invalid configuration, invalid schema/input, provenance mismatch and
  exhausted hard limits are rejected with stable, sanitized typed errors.
- Storage/integrity and execution failures retain operation-specific codes;
  internal data values are not included in public failure messages.
- `completed` means the requested bounded operation finished.
- `partially_completed` is allowed only where the contract explicitly reports
  what was omitted or unsupported (such as EDA pair/category truncation).
- `insufficient data` is an analysis outcome, not a fabricated statistic.
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

Normalization validates source metadata and checks local and stored artifact
integrity. Quality analysis keys bind source checksum, normalized-output
checksums, ruleset/analyzer versions and canonical configuration. EDA validates
source and normalized provenance before reading the selected table. Preprocessing
is in-memory and records bounded operation/configuration lineage metadata where
the transformation changes the table; it does not persist source artifact
provenance.

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
| Ingestion/parsing | **Partial** | `parse_dataset_file` handles CSV, TSV, XLSX, JSON, Parquet, and XML; validates parsing metadata; stores original bytes and deterministic profile. | Upload size defaults to 150 MiB; row/column and format-specific limits exist. CSV/TSV and Parquet are scanned in bounded records/batches. XML has file/depth/element caps but materializes a tree and flattened rows. JSON uses stdlib whole-document decoding and now applies a pre-read source-size estimate (see JSON boundary below). ZIP/folder/database/API sources are not implemented. |
| Profiling | **Implemented for current parsers** | `DatasetProfiler` computes missing/empty, exact distinct/duplicate counts and numeric/category summaries within its own tracking budgets. | Distinct values default 100,000; numeric values 250,000; duplicate-row fingerprints 250,000. Exact fields become unavailable with warnings when tracking budgets are exceeded. Profile data does not replace artifact-bound analysis. |
| Normalization | **Partial** | `normalize_file` reparses and validates source, builds normalized Parquet per logical table, validates rows/schema/nulls and stores checksummed objects. | Output defaults to 600 MiB, written in batches. Output-size enforcement occurs after each written batch/close, so a documented batch/footer overshoot may occur before cleanup. Uses `ExecutionBudget` for the cumulative temporary-output bytes. |
| Quality analysis | **Partial** | API validates normalized artifacts, downloads verified local copies, runs deterministic registered rules, and persists versioned provenance-bound reports. | 600 MiB normalized-input cap, 4 MiB rule-tracking memory cap, shared comparison budget, bounded findings/examples/report serialization. `RuleBudget` is separate from `ExecutionBudget`. Real PostgreSQL advisory locking and MinIO integration are not established by unit tests. |
| Preprocessing | **Partial** | `DEFAULT_TRANSFORMATION_REGISTRY` and `orchestrate_preprocessing` run explicit DA/BA transforms on Arrow tables; business-semantic operations require explicit approvals. | Input cap 64 MiB Arrow buffers, 1,000,000 rows, 256 columns. Python-string estimates are checked before the two whole-column `to_pylist` paths and share the byte ceiling across selected columns/chunks. This is not streaming or a process-wide memory bound. Output is in memory; no API/persistence. |
| EDA | **Partial** | `analyze_normalized_file` performs univariate, bivariate and multivariate deterministic analysis on a selected verified normalized table. | Defaults/caps include 1,000,000 rows, 600 MiB selected-column scan metadata, 250,000 numeric values, 50,000 categories, 250,000 grouped values, 50,000 contingency cells, 4,000,000 pairwise work units, 128 MiB estimated decoded strings, and configured result serialization up to 1 MiB. EDA remains service-level; no API/history/UI. |
| Frontend | **Partial** | React shell and system-health view. | API client currently calls only health and system-info; dataset and analysis workflows are not connected. |
| Worker/queue | **Planned** | Worker starts, checks Redis, and idles. | No queue consumer, task contract, retries, or execution-budget propagation. |
| Schema modeling, agents, semantic layer, NL analytics, ML, monitoring, governance | **Planned** | Product roadmap only. | No claims of implementation until code and tests establish them. |

### JSON expansion boundary

`MAX_JSON_ESTIMATED_DECODED_MB` defaults to 256 MiB. The parser estimates
decoded working memory conservatively as 64 times source bytes and rejects a
document whose declared or observed source bytes exceed that budget before
calling `json.loads`. This prevents an unbounded whole-document decode from
proceeding beyond the configured estimate and ensures the read itself is
bounded.

The multiplier is an estimate, not a Python-version-independent upper bound on
allocator/RSS usage. JSON still uses whole-document decoding below the cap.
Large JSON datasets may be rejected earlier than other formats. A true
incremental JSON parser is a future option if the format contract and additional
complexity are approved; this implementation does not claim streaming JSON.

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
quality analysis. This path is implemented but remains unverified until that
job has run successfully. Do not run destructive database/storage integration
tests against developer data or shared services.
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
