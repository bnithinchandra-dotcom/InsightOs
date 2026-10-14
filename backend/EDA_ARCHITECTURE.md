# Exploratory Data Analysis

## Scope and API

EDA Stage 1 provides a service-level, deterministic analysis of a single table
in a verified normalized Parquet artifact. It does not accept a caller-supplied
path. The entry point is `app.eda.analyze_normalized_file`; callers provide the
storage adapter, bucket, dataset and file identity, current file and
normalization statuses, source metadata, parser result, normalization result,
and an `EDAConfiguration`.

Use `eda_configuration_from_dict` to parse configuration data. Configuration
schema version 1 supports `analysis_type="univariate"`, `"bivariate"`, and
`"multivariate"`, explicit column-name selection (up to 32 unique columns), an
explicit normalized-table index, 2–100 numeric histogram bins, up to 16 sorted
percentiles from 0–100, a 1–100-category output limit, a bounded pair count,
a bounded multivariate feature count, and a bounded serialized result limit. The
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
   normalization metadata, then scan only columns required by the selected
   relationship pairs/features in bounded Arrow batches. Relationship value
   and work budgets are consumed during that scan; analysis does not
   materialize selected relationship columns as a whole-table Arrow table.
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

`EDAResult` uses result schema version 1 and the analysis-specific algorithm
version (`univariate-1`, `bivariate-1`, `multivariate-1`). It records source
identity and artifact checksum, configuration and its canonical SHA-256, UTC
execution time, requested and analyzed columns, row count, exact/no-sampling
status, per-column type and status, per-pair summaries, multivariate matrix
summaries, warnings, an additive versioned provenance envelope, and an
optional sanitized failure. Successful result provenance links the original
source and selected normalized table to the normalization operation and records
the artifact checksum, schema, and row-count checks. Returned storage/Parquet
failure results carry failed operation status and `unverifiable` verification
when the required checks did not complete; unavailable source identity remains
null rather than being represented by a placeholder hash. The provenance is
returned to the caller, not persisted as analysis history.

Supported Arrow values are integers, floating point, decimals, booleans,
strings, dates and timestamps. Other Arrow types are reported as
`unsupported`; their presence makes the univariate result
`partially_completed`. Numeric values are represented as IEEE-754 binary64. NaN
and infinities are counted separately and excluded from finite-value summaries.
Percentiles use linear interpolation at `h=(n-1)p/100`; variance, standard
deviation and bivariate covariance are sample statistics (`ddof=1`).
Correlations use scaled centering to avoid avoidable intermediate overflow.
A statistic whose final value cannot be represented as a finite binary64
number is emitted as null, with a warning where applicable. Numeric-categorical
group summaries preserve the established singleton convention of zero sample
variance and standard deviation. Histograms use equal-width bins, and box-plot
whiskers are the observed extremes within 1.5 IQR fences. If the
finite numeric range cannot be represented safely, histogram boundaries are
omitted with a warning.

Categorical/boolean frequencies are ordered by descending count and then
display value. Per-column frequency output is capped by `max_categories`.
Univariate category strings longer than the existing 256-character display
limit are not retained for exact category tracking. In relationship analysis,
long string category identities are represented internally by SHA-256 digests
and displayed with a bounded, digest-suffixed label, so distinct long values do
not collapse merely because their display prefixes match. Displayed
categorical strings are limited to 256 characters.

Bivariate analysis is bounded to the selected columns or a user-specified
`column_pairs` set, limited to `max_pairs` pair combinations and to 64 pairs per
request. Explicit pairs can reference at most 32 distinct columns. Explicit
pair sets larger than `max_pairs` are rejected; automatically
generated pairs stop at that limit. Numeric-numeric comparisons report Pearson
and Spearman correlation plus covariance. Numeric-categorical group summaries
work for either requested column order, exclude missing categories and
non-finite numeric values, sort categories deterministically, and return at
most `max_categories` groups. Categorical-
categorical comparisons produce a deterministic contingency table bounded to
`max_categories` row and column categories. When categories are omitted,
`total_observations`, row/column totals, and Cramer's V cover only the displayed
table after zero-margin dimensions are removed; `excluded_observations` reports
valid pairs omitted by that bound. Cramer's V is null when fewer than two
represented categories remain on either axis.

Multivariate analysis is limited to the numeric features in the selected
columns, capped by `max_features`, and emits a symmetric correlation matrix and
pairwise valid row counts. Matrix diagonals use the same Pearson definition as
off-diagonal entries: they are `1.0` for varying features and null when the
self-correlation is undefined because there are fewer than two valid values or
zero variance. Relationship analysis enforces the selected-column
uncompressed scan-size and table row limits before reading values, then applies
finite-numeric, distinct-category, grouped-value, contingency-cell,
pairwise-work and serialized-result budgets while consuming batches.
The bivariate summary reports eligible and analyzed pair counts and indicates
when `max_pairs` omits eligible automatic pairs. Multivariate summaries report
eligible numeric features, selected numeric features, and feature count;
insufficient-feature results retain that metadata and indicate when
`max_features` omits eligible features. Failure summaries keep the same
analysis-specific fields; feature-selection values are null when a failed
analysis did not determine them.
Unsupported type combinations are reported as explicit pair entries rather
than silently dropped.

## Resource and privacy boundaries

- Normalized output and each selected Parquet table must remain within the
  existing normalization output limit (`DEFAULT_MAX_OUTPUT_BYTES`, 600 MiB).
- The combined uncompressed size of selected Parquet columns is also limited
  to 600 MiB, based on Parquet row-group metadata, before Arrow batches are read.
- Exact analysis is limited to 1,000,000 rows per table and scans in batches of
  4,096 rows.
- Normalization and EDA relationship analysis use a shared incremental
  `ExecutionBudget` accounting primitive with separate operation-specific
  resource profiles; the primitive does not make their limits or execution
  semantics identical.
- Relationship analysis retains at most 250,000 finite numeric values,
  50,000 distinct category entries, 250,000 numeric-categorical grouped
  observations and 50,000 non-empty contingency cells per request.
- Before converting a categorical string batch to Python objects in univariate
  or relationship analysis, EDA uses Arrow-native string byte lengths
  (including dictionary indices) to estimate the decoded Python-list footprint,
  including per-entry/list overhead. The estimate is cumulative across batches
  and selected string columns for that operation, capped at 128 MiB; an
  over-budget batch is rejected before Python-list materialization. This is a
  decoded-value estimate, not a process-wide Python heap limit.
- Relationship calculations are capped at 4,000,000 pairwise-work units,
  bounding repeated work across pairs independently of retained-value budgets.
- Normalization checks its cumulative temporary Parquet output budget after
  each written batch and after writer close. A temporary output can exceed the
  remaining budget by one written batch plus final Parquet metadata before it
  is rejected and cleaned up.
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

This stage intentionally does not provide time-series analysis, automatic
column selection, AI-driven planning, an HTTP endpoint, persistent execution
history or lineage, model artifacts, or cross-session state. Its returned
provenance is a result-level trace, not an independently audited or persisted
history. Profile
statistics are not treated as normalized-artifact statistics.

Deferred or intentionally not yet implemented capabilities include advanced
pairwise hypothesis testing, non-pearson dependence metrics beyond the
bounded Spearman/Cramer's V summaries, automatic outlier detection,
multilayer categorical encoding, and sampled execution or support for datasets
that exceed the fixed row, scan, value and work budgets. The implemented
bivariate and multivariate paths are therefore
limited to the deterministic, bounded logic described above.

Analysis is limited to the data types and exact resource bounds described
above.
