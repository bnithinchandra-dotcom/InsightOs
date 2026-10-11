# BA + DA preprocessing rule catalog

The versioned catalog is the read-only discovery surface for preprocessing
rules. It is available at `GET /api/v1/quality-rules` and
`GET /api/v1/quality-rules/{rule_id}`. List results are sorted by stable rule
ID; optional `category` and `status` filters are exact matches. The response
includes `catalog_version` (`2`) separately from each rule's semantic `version`.

The context-aware planning endpoint is
`GET /api/v1/datasets/{dataset_id}/files/{file_id}/quality-rule-plan`. It reads
the existing dataset-file status, validated profile metadata, validated
normalization metadata, and only a current validated quality report. Optional
category/status filters use the same exact catalog values. Each recommendation
reports the catalog status, planning status, rationale, prerequisites, missing
context, execution support, required user decisions, and any prior current
quality-check status. A `ready` planning status means an implemented executor's
catalog prerequisites appear available; the endpoint does not run the rule.
Execution rechecks normalized object integrity and rule-specific prerequisites
through the existing quality-analysis flow.

When no valid normalized context exists, implemented rules appear as
`needs_context`. Partial and planned entries remain visible as `partial` and
`planned`, with execution explicitly unsupported. The planner does not infer
business semantics from column names or values, accept guessed definitions,
or execute transformations. Business-key checks still require an explicit
key configuration, which this planning endpoint does not collect.

Catalog metadata is deterministic and independent of LLM output. Entries
describe purpose, use cases, inputs, prerequisites, configuration schema and
defaults (when executable), method, supported operations, required approvals,
risks, limitations, status, and implementation/test references. The current
catalog includes the implemented DA/statistical checks and planned BA, DA,
temporal, relational, and governance capabilities. Planned entries are
discoverable only; they do not register an executor or enable an engine rule.
`supported_operations` lists only operations available now; non-implemented
entries instead declare future operation types in `planned_operations`.

## Implementation statuses

- **implemented** — a matching versioned executor is registered and covered by
  tests. This status describes only the documented scope and limitations.
- **partial** — related foundations exist, but the cataloged capability is
  incomplete and has no dispatchable executor.
- **planned** — no executable implementation exists yet.
- **unsupported** — explicitly not supported; no executor is registered.

The implemented inventory is missingness reporting, source/normalized type
consistency, exact duplicate rows, explicitly configured business-key
duplicate candidates, string whitespace candidates, numeric validity
candidates, and configurable IQR or population Z-score outlier candidates.
All findings retain the existing quality engine's status, severity,
classification, evidence, denominator, and resource-limit behavior. The
business-key rule requires an explicit key configuration and is not enabled by
default. Statistical and formatting candidates do not establish errors.
Approval metadata describes necessary future planner gates; this stage does not
add an approval workflow. For the implemented business-key rule, execution
requires an explicit key definition through its rule configuration.

BA business/KPI definitions, business scope, reporting grain and
reconciliation, contextual missingness, business gaps and anomalies,
categories and master dimensions, fiscal calendars, and timezone handling
remain planned. DA imputation, string normalization, categorical encoding,
numeric scaling, skewness transformations, logical constraints, and
referential-integrity checks also remain planned. No transformation, business
interpretation, join, or repair is performed by this catalog.

## Executor integration and versioning

The quality engine resolves its built-in executor registry from catalog
entries whose status is `implemented`. Startup validation rejects missing or
extra executors and mismatches in rule version, scope, severity, prerequisites,
or default configuration. Planned and partial catalog entries cannot be
registered through the engine's normal registry. Catalog configuration
schemas validate the accepted structure before the existing rule-specific
validators enforce semantic constraints.
The existing engine continues to handle rule selection, input prerequisites,
and shared execution budgets; this increment does not add a separate planner
or dataset-objective interface.

Increment the **catalog version** when the catalog's public structure or
cross-entry semantics change. Increment a **rule version** when that rule's
meaning, algorithm, configuration, or execution semantics change. Add a rule
by documenting its stable ID, inputs, prerequisites, schema, defaults,
operations, approval needs, risks, and limitations; implement and test its
executor; then mark it `implemented` and bind it into the engine registry.
Never mark a planned capability executable merely because it has a catalog
entry. Preserve the separate detection and transformation boundaries and the
existing report contract and ruleset compatibility.

Catalog version 2 declares verified normalized Parquet as a prerequisite for
exact-duplicate detection, matching the existing executor's actual input
requirements; the rule algorithm and semantic version are unchanged.

## Current category coverage

| Category | Catalog coverage |
| --- | --- |
| BA | Business definitions and KPI/status mapping, scope filters, reporting grain and reconciliation, contextual missingness, business gaps/anomalies, category grouping, and master dimensions |
| DA | Missingness, schema/type consistency, exact and configured-key duplicate candidates, string consistency/normalization, numeric validity, encoding, scaling, skewness transformation, and logical constraints |
| statistical | Configurable IQR and population Z-score outlier candidates |
| temporal | Timezone handling and fiscal-calendar mapping |
| relational | Configured business-key duplicate candidates and referential integrity |
| governance | Approved missing-value imputation as a future transformation capability |

Category overlap is intentional: an entry has one primary category and can
refer to related entries through its prerequisites and documentation.
