# Exploratory Data Analysis

## Scope and API

EDA Stage 1 provides a service-level, deterministic univariate analysis of a
single table in a verified normalized Parquet artifact. It does not accept a
caller-supplied path. The entry point is
`app.eda.analyze_normalized_file`; callers provide the storage adapter, bucket,
dataset and file identity, current file and normalization statuses, source
metadata, parser result, normalization result, and an `EDAConfiguration`.

Use `eda_configuration_from_dict` to parse configuration data. Configuration
schema version 1 supports `analysis_type="univariate"`, explicit column-name
selection (up to 32 unique columns), an explicit normalized-table index,
2–100 numeric histogram bins, up to 16 sorted percentiles from 0–100, a
1–100-category output limit, and a bounded serialized result limit. The
selected column list is required; the empty default is not an executable
configuration. Unsupported fields and invalid values raise `EDAError` with a
stable code and an actionable, non-data-bearing message.

## Provenance and lifecycle

1. Validate configuration, source identity and readiness states.
2. Validate the normalization result against the parser result, source
   filename, source size and checksum, dataset ID and file ID using the
   existing normalization contract.
3. Check that the selected table is within the managed object-key prefix and
   declared artifact limits.
4. Download the object into a managed temporary file while enforcing the
   declared byte limit and verifying its SHA-256 checksum.
5. Check Parquet schema, Arrow types and row count against validated
   normalization metadata, then scan only requested supported columns in
   bounded Arrow batches.
6. Build the typed result, enforce its configured serialized-size limit, and
   remove the temporary file.

The service reuses stored profiling data only as a compatibility reference:
table identity, row and column counts, selected column names and positions,
and null/empty counts must match the verified Parquet artifact. EDA statistics
are recomputed from the artifact because a profile does not bind its statistic
values directly to that normalized artifact's checksum.

An optional quality report is included only after the existing quality-analysis
validator confirms that its analysis key, source checksum, normalized-output
provenance and summary match the current normalization result. EDA returns only
bounded finding references (finding ID, rule ID, classification and severity),
not finding descriptions or evidence. Stale or incompatible optional profile
and quality data are ignored with a warning.

## Results and calculation behavior

`EDAResult` uses result schema version 1 and algorithm version `univariate-1`.
It records source identity and artifact checksum, configuration and its
canonical SHA-256, UTC execution time, requested and analyzed columns, row
count, exact/no-sampling status, per-column type and status, summaries,
warnings, and an optional sanitized failure.

Supported Arrow values are integers, floating point, decimals, booleans,
strings, dates and timestamps. Other Arrow types are reported as
`unsupported`; their presence makes the result `partially_completed`. Numeric
values are represented as IEEE-754 binary64. NaN and infinities are counted
separately and excluded from finite-value summaries. Percentiles use linear
interpolation at `h=(n-1)p/100`; variance and standard deviation are sample
statistics (`ddof=1`). Histograms use equal-width bins, and box-plot whiskers
are the observed extremes within 1.5 IQR fences. If the finite numeric range
cannot be represented safely, histogram boundaries are omitted with a
warning.

Categorical/boolean frequencies are ordered by descending count and then
display value. Per-column frequency output is capped by `max_categories`.
Category tracking is capped across the request; category strings longer than
the existing 256-character display limit are not retained for exact category
tracking. When either bound prevents complete tracking, the result marks the
distinct count unavailable and the tracked frequencies as truncated. Displayed
categorical strings are limited to 256 characters.

## Resource and privacy boundaries

- Normalized output and each selected Parquet table must remain within the
  existing normalization output limit (`DEFAULT_MAX_OUTPUT_BYTES`, 600 MiB).
- The combined uncompressed size of selected Parquet columns is also limited
  to 600 MiB, based on Parquet row-group metadata, before Arrow batches are read.
- Exact analysis is limited to 1,000,000 rows per table and scans in batches of
  4,096 rows.
- A request may retain at most 250,000 finite numeric values and 50,000
  distinct category entries across its selected columns.
- At most 32 columns are analyzed, histogram bin count is at most 100, and at
  most 16 percentiles are returned.
- Result serialization is capped at 1 MiB, with a 256 KiB default and a
  per-request lower bound of 1 KiB.
- Input is read-only. The service does not write transformed data or persist
  analysis history. Temporary downloaded artifacts are removed after success
  or failure.
- Failure messages do not include row contents. Bounded categorical summaries
  can include displayed category values; callers should apply their own access
  controls to EDA results.

## Failure behavior and limitations

Invalid requests, invalid source/normalization metadata, checksum mismatches,
unknown tables or columns, and resource-limit violations raise `EDAError`
instead of returning a success-shaped result. Storage and Parquet read errors
that are not integrity or request-validation errors return a typed result with
`status="failed"` and a sanitized failure code/message. No partial dataset or
failed intermediate analysis is returned as a completed result. Unsupported
columns are explicit per-column results rather than silently omitted.

This stage intentionally does not provide bivariate or time-series analysis,
sampling, automatic column selection, AI-driven planning, an HTTP endpoint,
persistent execution history or lineage, model artifacts, or cross-session
state. Profile statistics are not treated as normalized-artifact statistics.
Analysis is limited to the data types and exact resource bounds described
above.
