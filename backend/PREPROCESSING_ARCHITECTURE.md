# Preprocessing operation contracts

Stage 3.6.1 introduces `app.preprocessing.TransformationOperation` and
`TransformationRegistry` as the contract and discovery foundation for future
BA and DA transformations. This is separate from the deterministic quality
rule executor: quality rules detect and report findings; transformation
operations will later alter a working dataset only through explicit execution
stages.

An operation contract records a stable ID and semantic version, category and
implementation status, input/output requirements, prerequisites, configuration
schema/defaults when executable, whether fitting is required, which data
dimensions can change, required approvals, resource limits, and an optional
reference to an existing quality-catalog capability. Categories are BA, DA,
and structural. Statuses and configuration-schema matching reuse the quality
catalog conventions.

Registry discovery is sorted by operation ID and supports exact category and
status filters. Duplicate IDs and invalid contracts are rejected. Implemented
operations must provide both `fit` and `transform` methods plus a closed
object configuration schema and explicit defaults. `resolve_executor`
validates configuration, prerequisites, and approvals before returning an
implemented executor. Partial, planned, and unsupported operations cannot
register an executor or be resolved for dispatch.

The default transformation registry is intentionally empty. Existing catalog
entries describe future transformation capabilities but do not yet provide
complete transformation contracts or executable implementations. Where an
operation references a catalog entry, its status must match and the referenced
entry must declare transformation as supported or planned. This avoids
duplicating or overstating the existing quality catalog.

This stage defines contracts and gated discovery only. It does not execute
`fit` or `transform`, mutate data, add an API, collect approvals, or coordinate
pipelines. Those responsibilities belong to later stages after concrete
operations, validation, and lineage contracts are implemented.

To add an operation, define its complete `TransformationOperation` metadata,
implement the `TransformationExecutor` protocol only when its status is
`implemented`, and register the pair in an explicitly constructed registry.
Add tests for schema/configuration validity, prerequisites, approvals,
deterministic discovery, safety of data changes, and output validation before
enabling execution in a later stage.
