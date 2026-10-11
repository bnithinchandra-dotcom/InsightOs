# Preprocessing operations

The transformation registry is separate from the quality-rule executor:
quality rules detect and report findings, while transformations return a new
working table. Transformations do not write normalized Parquet files or modify
their input. Stage 3.6.1 established the `TransformationOperation` metadata
contract and `TransformationRegistry`; Stage 3.6.2 registers the first
implemented DA operations in that same registry; Stage 3.6.3 adds explicitly
configured BA operations alongside them.

An operation records its stable ID and version, category and implementation
status, input/output requirements, prerequisites, validated configuration
schema and defaults, whether fitting is required, which data dimensions may
change, required approvals, resource limits, and an optional quality-catalog
reference. Registry discovery sorts by operation ID. Registration rejects
duplicate IDs and invalid metadata. Dispatch only resolves implemented
executors and checks configuration, prerequisites, and approvals.

## Supported DA operations

All operations accept and return `pyarrow.Table` values. Each has an explicit
`columns` configuration; its default is an empty list, which selects no
columns and therefore performs no change. Unselected columns retain their
values. Nulls are preserved unless a selected missing-value strategy explicitly
handles them. Non-finite floating-point inputs are rejected by numeric
operations. Column names must be unique and non-empty.

| Operation ID | Configuration | Behavior |
| --- | --- | --- |
| `da.string_normalization` | `columns`, `trim` (default `false`), `case` (`none`, `lower`, `upper`; default `none`), `empty_strings_as_missing` (default `false`) | Trims surrounding whitespace and/or changes case only on explicitly selected string columns. Empty strings become null only when opted in. It does not remove punctuation or merge semantic labels. Does not require fitting. |
| `da.missing_value_handling` | `columns`, `strategy` (`leave`, `drop_rows`, `fill_constant`, `mean`, `median`, `most_frequent`; default `leave`), `empty_strings_as_missing` (default `false`), and exactly one typed constant field for `fill_constant` | Leaves values, drops rows missing in any selected column, fills an explicitly configured constant, or learns mean/median/mode on fit data. Mean/median support integer and floating columns and output `float64`; most-frequent supports string and boolean columns, with lexical/false-first tie-breaking. Empty strings are treated as missing only when configured. |
| `da.numeric_outlier_handling` | `columns`, `strategy` (`leave`, `clip_bounds`, `iqr_clip`; default `leave`); explicit clipping needs finite `lower_bound` and `upper_bound`; IQR clipping needs `iqr_multiplier` from 0 to 100 | Leaves values unchanged or clips explicitly selected numeric columns. IQR bounds use linear 25th/75th percentiles learned from fit data and the configured multiplier. At least four observed values are required. Clipped output columns are `float64`; nulls remain null. Outliers are never removed by default. |
| `da.numeric_scaling` | `columns`, `strategy` (`standard`, `min_max`; default `standard`), `feature_min`/`feature_max` (defaults 0 and 1) | Learns population mean/standard deviation or minimum/maximum from fit data and reuses those values unchanged. Selected output columns are `float64`. Constant columns map to zero for standard scaling or `feature_min` for min-max scaling; nulls remain null. |

## Supported BA operations

BA operations are also registered in `DEFAULT_TRANSFORMATION_REGISTRY` and
accept and return `pyarrow.Table`. Their aggregate summaries and trace data are
stored in the bounded `insightos.transformation_lineage` schema-metadata entry;
existing schema metadata is preserved. The lineage records operation ID,
version, configuration SHA-256, and a summary. It does not include mapping
source/target values. At most 32 operations and 16 KiB of lineage metadata are
accepted. A metadata-only change is represented by the `changes_metadata`
operation-contract flag, so read-only validation need not pretend that values,
rows, or columns changed.

| Operation ID | Configuration | Behavior |
| --- | --- | --- |
| `ba.business_rule_filter` | `predicates` (1–64); `composition` (`all` or `any`); `null_policy` (`exclude` or `include`); non-empty `reason` | Predicates require `column` and `operator`. Operators are `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `is_null`, and `is_not_null`. Equality accepts one typed `value_string`, `value_number`, or `value_boolean`; membership accepts one non-empty typed values list. Numeric comparisons require numeric columns and values. Null comparison results are counted as review rows and included/excluded only by `null_policy`. |
| `ba.category_mapping` | `column`, explicit `mappings` (`from`/`to` string pairs), `unmapped_policy` (`preserve`, `constant`, `error`; default `preserve`), `null_policy` (`preserve`, `constant`, `error`; default `preserve`), and corresponding `unknown_value`/`null_value` for constant policies | Requires a string column. Duplicate source mappings are rejected. Null values are handled separately; unmapped and null categories are preserved by default. Coverage, unmapped, and null row counts are recorded without category values. The registry requires both `approved_category_mapping` and `approved_category_merging` before dispatch, conservatively gating any mapping that could consolidate business categories. |
| `ba.derived_business_metric` | `left_column`, `operator` (`add`, `subtract`, `multiply`, `divide`), `right_column`, `output_column`; `divide_by_zero` (`error` or `null`, default `error`) | Evaluates only a two-column numeric expression using Arrow compute kernels; no `eval` or arbitrary expressions. Nulls propagate. Divide-by-zero errors or produces null only according to the explicit policy; non-finite results fail. Adds a `float64` output column, never overwrites an existing column, and records the formula in lineage metadata. |
| `ba.business_rule_validation` | `rules` (1–64); `empty_strings_are_missing` (default `false`) | Read-only checks: `required`; string `allowed_values` with explicit `values`; numeric `numeric_range` with at least one finite `minimum`/`maximum`; and numeric cross-column `less_than`, `less_or_equal`, `greater_than`, `greater_or_equal`, or `equals_column` with `other_column`. Aggregate violations, null-review counts, and row denominators are recorded; no rows or values are removed or changed. |

Filtering requires the `approved_business_scope_filter` registry approval.
Derived metrics require `approved_business_metric_definition`; validation
requires `approved_business_validation_rules`. All BA operations also require
the `pyarrow_table` and `explicit_business_rule_definition` prerequisites.
Registry resolution checks these gates before returning the executor. The
approval tokens are not automatically granted by configuration; callers must
obtain them explicitly.

Example configurations (approval is deliberately passed separately to
registry resolution):

```python
filter_configuration = {
    "predicates": [
        {
            "column": "status",
            "operator": "eq",
            "value_string": "completed",
        }
    ],
    "composition": "all",
    "null_policy": "exclude",
    "reason": "This analysis explicitly includes completed orders only.",
}
metric_configuration = {
    "left_column": "gross_revenue",
    "operator": "subtract",
    "right_column": "discount",
    "output_column": "net_revenue",
}
mapping_configuration = {
    "column": "category",
    "mappings": [{"from": "Retail", "to": "Consumer"}],
    "unmapped_policy": "preserve",
    "null_policy": "preserve",
}
```

No business semantics are inferred: zero-revenue, cancelled, pending, or
otherwise unusual records remain unless the caller supplies and obtains
approval for an explicit filter. Missing discounts are not treated as zero.
Category labels are compared exactly; no fuzzy, case-folded, or spelling-based
merges are performed. Formula column names carry no built-in business meaning.
Filter output metadata records rows before/retained/filtered/requiring review
and the supplied reason. Category mapping records matched/unmapped/null
counts. Validation records per-rule affected/review counts and denominators.
These summaries omit row values and mapped category labels.

Mean/median/mode imputation, IQR clipping, and both scaling strategies require
fitting. Statistics are returned as a fitted context from `fit()` and supplied
to `transform(..., context=...)`; no executor retains fitted state. The context
is bound to operation ID/version, configuration, and full input schema, so it
includes a checksum of its learned parameters and cannot accidentally be reused
with a different operation, configuration, or schema. Fit on evaluation data is
not performed by `transform()`. The contract does not define `fit_transform()`.

Example:

```python
from app.preprocessing import DEFAULT_TRANSFORMATION_REGISTRY

operation = next(
    op for op in DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
    if op.operation_id == "da.numeric_scaling"
)
configuration = {"columns": ["amount"], "strategy": "standard"}
scaler = DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
    operation.operation_id,
    configuration=configuration,
    available_prerequisites=("pyarrow_table", "explicit_column_selection"),
    approved_approvals=("approved_scaling_parameters",),
)
fit_context = scaler.fit(training_table, configuration=configuration)
training_scaled = scaler.transform(
    training_table, configuration=configuration, context=fit_context
)
evaluation_scaled = scaler.transform(
    evaluation_table, configuration=configuration, context=fit_context
)
```

Supply an approval token only after obtaining the corresponding explicit
approval; the registry metadata describes the approval required, not whether it
has been granted.

## Safety, boundedness, and limitations

Dispatch requires explicit operation approvals in addition to the `pyarrow_table`
and `explicit_column_selection` prerequisites. Inputs are bounded to 64 MiB,
1,000,000 rows, and 256 columns; a configuration is bounded to 64 KiB and at
most 64 selected columns. Invalid configurations, missing columns, unsupported
types, non-finite numeric values, empty/all-null statistic-training columns,
and insufficient IQR training data fail with `TransformationRegistryError`.
Constant columns are handled deterministically and missing values do not imply
zero.

These components do not infer business meaning, determine whether an outlier
is an error, join datasets, or run a complete pipeline. Lineage is attached to
the returned in-memory PyArrow table; it is not persisted to storage by these
operations. Callers must retain the operation version, configuration,
approval, fitted context, and input/output provenance when assembling a later
pipeline. No API, database migration, or new dependency is introduced.

The quality catalog remains unchanged: its transformation descriptions are
catalog metadata for the quality-rule surface, while executable transformation
discovery uses `TransformationRegistry`. The quality-report contract and its
versions are not changed. Do not mark a catalog capability executable merely
because it shares a concept with a DA operation.

Future work includes fiscal calendars, KPI/status semantics, reporting grain,
business anomaly detection, approved master-dimension joins, and broader
validation rules. The four operations above are not an orchestrator and do not
provide these capabilities.

To add an operation, define complete `TransformationOperation` metadata,
implement `fit`/`transform` only for supported behavior, and register the pair.
Add tests for schema/configuration validity, prerequisites and approvals,
deterministic discovery, fit/evaluation separation, input immutability,
resource limits, and output validation. Planned or partial operations must
remain executor-free until their behavior and tests exist.
