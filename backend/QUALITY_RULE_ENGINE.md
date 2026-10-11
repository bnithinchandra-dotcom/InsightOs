# Deterministic quality-rule engine

The engine consumes validated normalization metadata and, for value-scanning
rules, a verified, read-only local copy of normalized Parquet. It never edits
the source object or normalized output. Temporary scan files are removed when
the analysis request ends. Quality findings are advisory; this stage does not
clean, impute, reject, deduplicate, or rewrite records.

## Registered rules

Rules have stable dotted IDs, independent versions, declared scope and
prerequisites, explicit configuration defaults, and a configuration validator.
The registry sorts rules by ID, and the engine sorts results by rule and
finding scope. A rule semantic change requires incrementing that rule's
version. Ruleset version 3 accepts prior version-1 and version-2 reports,
preserving their validation and provenance; current analysis keys bind the new
ruleset and canonical rule configuration.

The [BA + DA preprocessing rule catalog](./QUALITY_RULE_CATALOG.md) is the
versioned discovery surface for implemented and planned capabilities.
Implemented catalog entries are validated against the executor registry;
planned entries are not dispatchable.

| Rule | Evidence and classification |
| --- | --- |
| `generic.missing_value_candidates` | Counts nulls and exact empty strings from validated normalization metadata. Reports an `unusual_candidate`, with row denominator; these values are not presumed invalid. |
| `generic.type_consistency` | Compares validated parsing physical-type metadata with normalized column types. Mixed or incompatible source representations are `suspected_issue` candidates; the metadata does not establish an affected-row count. |
| `generic.exact_duplicate_rows` | Scans normalized rows and counts repeated complete rows. Reports `unusual_candidate`; repeated rows can be intentional. |
| `generic.string_whitespace_candidates` | Scans string columns for leading/trailing or repeated whitespace. Reports `unusual_candidate` counts and bounded row references without changing values. |
| `generic.numeric_validity_candidates` | Detects non-finite values or values inconsistent with a normalized numeric type, where available. Reports `suspected_issue`; negative numbers and statistical outliers are not flagged. |
| `generic.numeric_outlier_candidates` | Applies configured IQR or population Z-score bounds to finite normalized numeric values. Defaults are IQR, 1.5 multiplier, and 4 minimum samples. Reports `unusual_candidate`; statistical thresholds never establish a business error. |
| `configured.business_key_duplicate_candidates` | Available only with an explicit table/column key configuration. It compares exact typed values, excludes rows with null key values, and reports `unusual_candidate`; uniqueness is never inferred. It is not enabled by default. |

No rule emits a `confirmed_violation` without an explicit business definition.
In particular, missing values, repeated values, whitespace, negative numbers,
and unusual categories are not presumed to be errors.

## Configuration, provenance, and bounds

Configuration selects registered rules and supplies each selected rule's
version and complete configuration. Invalid or unknown options are rejected
before execution. The canonical configuration, source checksum, normalized
output checksums, analyzer version, and ruleset version bind the analysis key.
Exact-row and business-key comparisons preserve deterministic typed value
semantics. configured business keys must name existing columns and have unique column
names. Rows with null key values are excluded; empty strings are compared as
literal values because the engine has no business definition for them.

Value scans read normalized Parquet in one-row batches and share a bounded
comparison budget. The engine also caps total normalized input at 600 MiB,
tracked fingerprint and IQR-value memory at 4 MiB, business-key value size at
64 KiB, exact row and outlier numeric-value size at 128 KiB, findings, examples,
evidence, and serialized result size. Z-score uses a deterministic one-pass
population mean/variance followed by a bounded second scan. IQR uses exact
quartiles with linear interpolation and stores finite values only within the
shared memory budget. If a scan exceeds a limit, that rule is marked
`BudgetExhausted` and its partial findings are discarded. Oversized input makes
value-scanning rules explicitly `NotApplicable`; metadata-backed rules can
still run.

Unsupported prerequisites are visible as `NotApplicable`; rule execution
failures are visible as `Failed`. Either state remains visible in report
coverage. A failure or exhausted budget prevents the required aggregate rule
check from being reported as completed. A report may complete when a
non-applicable optional rule has no relevant input, with that rule's own
coverage state preserved.

No SQL connector, live data fetching, frontend, migration, or additional
dependency is introduced for this stage.
