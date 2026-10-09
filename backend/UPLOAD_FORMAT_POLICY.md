# Upload format policy

The dataset file upload endpoint accepts `.csv`, `.tsv`, `.xlsx`, `.json`,
`.parquet`, and `.xml`. Other extensions, including legacy `.xls` workbooks and
`.zip` packages, are rejected.

For these extensions, the endpoint accepts the corresponding MIME types:

| Extension | Accepted supplied MIME types |
| --- | --- |
| `.csv` | `text/csv`, `application/csv`, `text/plain` |
| `.tsv` | `text/tab-separated-values`, `text/tsv`, `text/plain` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `.json` | `application/json`, `text/json` |
| `.parquet` | `application/vnd.apache.parquet`, `application/x-parquet` |
| `.xml` | `application/xml`, `text/xml` |

An omitted MIME type or `application/octet-stream` is also accepted for any of
the listed extensions. Extension and MIME checks validate the upload labels;
the parser independently inspects content and rejects malformed or mismatched
files.

Parsing is synchronous during upload. On success, the original bytes are stored
unchanged and the file record contains detected format, column names and
physical types, row count, and format-specific metadata. The dataset returns to
the existing `Uploaded` state only after the parsing metadata has been validated,
the object size and SHA-256 metadata have been verified, and the file record is
marked `Ready`. A file record is created in `Processing` before parsing/storage
so interrupted and failed attempts are visible and recoverable; parse failures
leave a `Failed` file record with a safe error code and message, without storing
an object. Successful retries of identical filename/content reuse the same
record and object key. Clients may also supply an `Idempotency-Key` header; a
key reused for different filename/content returns a conflict. If a storage or
database failure leaves object cleanup uncertain, the record remains
`Processing` and the same upload can be retried to verify or complete it.
PostgreSQL advisory locks serialize requests for the same dataset/upload key
across backend workers and remain held through object verification and cleanup.
A simultaneous retry receives an in-progress conflict; after a worker exits,
the connection releases its lock and a later retry can recover the persisted
`Processing` record.
The connection is pinned outside the SQLAlchemy Session transaction lifecycle
until lock release has been confirmed. Upload concurrency tests use mocked lock
helpers and do not establish behavior against a live PostgreSQL server; a
PostgreSQL integration test remains necessary to verify backend lock and
connection-termination behavior in deployment. If SQLAlchemy invalidation and
detachment both fail, the backend attempts to close the underlying DBAPI
connection directly. If that also fails, the checked-out connection is kept in
process quarantine and is not returned to the pool; safe server-side lock
release cannot be guaranteed until the connection or backend process terminates.
`MAX_DATASET_SIZE_MB` limits upload size;
`MAX_DATASET_ROWS`, `MAX_DATASET_COLUMNS`, `MAX_XLSX_UNCOMPRESSED_MB`,
`MAX_XLSX_ENTRIES`, `MAX_PARQUET_ROW_GROUPS`, `MAX_XML_SIZE_MB`,
`MAX_XML_DEPTH`, `MAX_XML_ELEMENTS`, and `MAX_JSON_DEPTH` configure parser
resource limits. `MAX_PROFILE_DISTINCT_VALUES` (default 100,000),
`MAX_PROFILE_NUMERIC_VALUES` (default 250,000), and
`MAX_PROFILE_DUPLICATE_ROWS` (default 250,000) bound additional profiling
memory use.

CSV and TSV use UTF-8 (an optional UTF-8 BOM is accepted), with the first record
as the header and strict row widths. Their physical type is text; values are
not coerced. A well-formed XML document or recognized binary signature is
rejected as mismatched delimited content; ordinary single-column text remains
valid. JSON supports an array of objects, a flat object with scalar values, or
a wrapper object whose only property is an array of record objects. Sibling
fields in wrapper objects and duplicate object keys are rejected rather than
discarded. Nested JSON values inside records remain object/array values.
XLSX uses each worksheet's first row as its header; normalization rejects
values after an empty first row rather than silently omitting them. Formulas
are identified but not evaluated. Parquet schema types come from its Arrow
schema, and the full data stream is scanned to verify rows.

XML support is intentionally limited to a well-formed document with exactly
one unambiguous group of repeated same-name sibling record elements. The record
container cannot mix unrelated element siblings. Namespace names use expanded
Clark notation (`{uri}local`); nested leaf names are joined with `/`, and
attributes use `/@name`. Record attributes and root/container attributes are
preserved; root/container attribute columns use
`@container:<JSON-encoded ancestor path>/@<attribute>` and repeat their
constant source value on each record row. Missing elements and empty values
are counted separately, and mixed text/element content or repeated nested
fields are rejected as unsupported rather than silently flattened.
All DTD declarations, including internal subsets, and external entities are
forbidden. XML is parsed with a bounded tree builder that enforces depth and
element-count limits as start elements arrive, before those nodes are added to
the tree. The configured XML file-size limit is also applied before parsing.

Parsing metadata describes file structure and physical types. Neither parsing
nor profiling infers semantic meaning, modifies source values, or performs
preprocessing. An uploaded object remains the source of truth.

Successful uploads also store a separate, versioned deterministic profile in
`dataset_files.profile_result`. The file response and
`GET /api/v1/datasets/{dataset_id}/files/{file_id}/profile` expose it separately
from `parsing_result`. Profiles contain row/column counts, per-column physical
types, missing/empty counts, distinct counts when within the fingerprint budget,
duplicate rows when within the row-fingerprint budget, numeric min/max/mean and
an exact median when its tracking budget allows, and up to ten categorical
values. CSV, TSV, and XML numeric-looking text is parsed as decimal for numeric
statistics; JSON string values are not coerced. Mixed numeric and non-numeric
columns omit numeric statistics and include an explanation. XLSX values are
profiled as stored; formulas are not evaluated.

Distinct and duplicate counts use SHA-256 fingerprints. If configured tracking
budgets are exceeded, affected exact counts or medians are reported unavailable
with warnings rather than presented as partial results. Profile output never
includes source rows; categorical display values are truncated at 256
characters. Historical `Ready` records created before profiling may not have a
profile; their profile endpoint returns `profile_not_available`. Profile
generation is synchronous as part of upload parsing.

## Parquet normalization

Normalization is a distinct synchronous operation on an ingested `Ready` file:

- `POST /api/v1/datasets/{dataset_id}/files/{file_id}/normalize` requests
  normalization and returns its current state and result.
- `GET /api/v1/datasets/{dataset_id}/files/{file_id}/normalization` retrieves
  normalization state, result, or safe failure details.

Normalization state (`NotStarted`, `Processing`, `Ready`, or `Failed`) is stored
separately from ingestion state. Normalization requires PostgreSQL advisory-lock
support. Concurrent requests for the same file receive an
`normalization_in_progress` conflict. Requests for a completed result with the
same source checksum and configuration verify the stored objects and reuse the
result. Failures do not change the uploaded file's `Ready` ingestion state.
When a retry starts, its prior result is cleared so a later failure cannot
present stale output as the current result. Previously written content-addressed
objects are retained and can be verified and reused on a subsequent retry.

Outputs are written as Parquet objects to `MINIO_PROCESSED_BUCKET` (default
`insightos-processed`). Each XLSX worksheet remains a separate logical table and
output. A wholly empty worksheet is retained in result metadata without
creating a zero-column Parquet object. Keys include the dataset/file identity,
source checksum, normalization configuration hash, table position, and output
checksum. Original objects are never overwritten or deleted. Outputs are
validated locally and after upload using Parquet schema, row count, null/empty
counts, content fingerprints, object size, and SHA-256 before normalization is
marked `Ready`. If metadata persistence fails after an output is written, that
content-addressed object is retained; a retry verifies and reuses it rather
than risking deletion of a valid output.
Deleting a dataset removes only objects under that dataset's prefix from both
the raw and processed buckets. Deletion acquires the same per-file advisory
locks as normalization and returns a conflict while any file is being
normalized. Object-store deletion is not transactional across buckets; a
storage failure returns an error and leaves the database records for a retry,
although objects successfully removed before the failure are not restored.

CSV and TSV columns remain strings, including numeric-looking identifiers and
leading zeros. XML columns remain strings, with the parser's expanded namespace
names and nested paths preserved; missing values remain null and empty values
remain empty strings. Parquet inputs retain their Arrow logical schema and
values. JSON integers that fit `int64` remain integers; JSON integer/decimal
columns use Parquet Decimal when precision permits. Nested JSON values,
high-precision numeric values that exceed Parquet Decimal limits, mixed JSON
types, and incompatible mixed XLSX cell types use a reversible tagged JSON text
encoding, accompanied by warnings. XLSX formulas are preserved as formula text
and never evaluated. XLSX formula columns mixed with other values and XLSX error
cells fail normalization explicitly. Date-formatted XLSX cells with midnight
values remain dates; non-midnight or time-formatted datetime cells remain
timestamps. A column mixing dates and datetimes uses timestamps, promoting
date-only values to midnight without truncating datetime values. No values are
imputed, removed, or deduplicated. JSON missing fields and explicit nulls are
distinguishable in the tagged representation, including columns whose only
present values are null.

`NORMALIZATION_COMPRESSION` selects an installed Parquet codec (default `zstd`;
`none` disables compression). `NORMALIZATION_BATCH_SIZE` bounds row batches
(default 8,192; maximum 65,536), and `MAX_NORMALIZED_OUTPUT_MB` caps total generated output
(default 600 MiB). CSV/TSV, XLSX, and Parquet values are processed in batches.
The existing JSON parser materializes a JSON document, and XML normalization
uses the parser's bounded XML tree; those formats are therefore bounded by the
configured upload, row, nesting, and XML limits but are not fully streaming.
Normalization is synchronous; the current worker does not run background jobs.
The output can be larger than its source. The API does not implement
application-level authentication or project ownership checks; deployments must
place it behind a trusted network boundary or an authenticating gateway.
Automated tests use mocked object storage and isolated SQLite databases; live
PostgreSQL advisory-lock and MinIO behavior has not been exercised.

Database sources such as PostgreSQL and MySQL, live servers, streaming, polling,
and cloud object storage require separate connector-based ingestion paths.
They are not file formats and are not handled by this endpoint.
