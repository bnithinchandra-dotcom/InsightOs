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
  UI integration remains planned.
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
- **Frontend:** a React application shell with health/system status and the
  Phase 4 Schema Architect workflow. Other data workflows remain unconnected.
- **Worker:** a Redis connectivity process only. It does not consume or execute
  jobs.

The Compose development environment is supported for access from the local
machine. Published backend, PostgreSQL, Redis, and MinIO ports bind to
`127.0.0.1`. The API does not yet implement authentication or project ownership
authorization; remote or multi-user deployment is unsupported.

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

## Development roadmap

| Phase | Status | Current scope |
| --- | --- | --- |
| 0. Project definition | Implemented | Repository and initial project documentation. |
| 1. Foundation | Partial | FastAPI, SQLAlchemy/Alembic, PostgreSQL, Redis, MinIO, Compose, and frontend shell; authentication and a functioning job worker remain planned. |
| 2. Ingestion and storage | Partial | Six file formats and raw-object lifecycle; ZIP, folders, database sources, and asynchronous ingestion are not implemented. |
| 3. Profiling and quality | Partial | Profiles, normalization, and deterministic quality rules/reports exist; broader interactive workflows remain planned. |
| 3.6. Preprocessing | Partial | Arrow-table transformation registry and orchestration exist as a library; no persisted or API workflow. |
| 3.7. EDA API and persistence | Partial | Deterministic univariate, bivariate, and multivariate EDA API routes with PostgreSQL execution records, idempotency, and version 1 provenance; frontend integration remains planned. |
| 4. AI Schema Architect | Partial | Deterministic schema discovery, relationship evidence, and conditional dimensional-model recommendations with PostgreSQL persistence and versioned provenance; the Schema Architect UI and its disposable-service workflow validation are implemented. |
| 5-13. Agents, BI, ML, monitoring, governance, production | Planned | Do not treat roadmap descriptions as implemented capabilities. |

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
