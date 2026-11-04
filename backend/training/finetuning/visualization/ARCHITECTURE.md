# Visualization intelligence architecture

## Responsibility boundary

The language model selects a chart identifier and produces a structured
visualization specification. It does not discover schemas, execute aggregations,
fabricate values, access data, or render charts.

The deterministic analytical/rendering stack:
- supplies available field names and types;
- validates mappings against the supplied fields;
- rejects unsupported registry identifiers;
- validates configuration with the output JSON Schema;
- performs aggregation and rendering independently;
- supplies layout and accessibility-safe defaults.

## Registry extension

1. Add one stable entry to `registry.json`.
2. Define category, purposes, encodings, accepted types, options, limitations,
   fallback and accessibility guidance.
3. Add schema validation or decision-rule support when configuration changes
   require it.
4. Add positive, negative and fallback examples.
5. Add registry, split and benchmark tests before use.
## Registry-aware validation

The JSON Schema validates the common output structure. The Python registry-aware
validator additionally checks chart identifiers, required encodings, supplied
field names and types, allowed configuration values, aggregation references,
axis and series mappings, layout dimensions, and fallback identifiers.

## Training example format

Each D1 development JSONL record preserves its stable ID, scenario/template
groups, business question, analytical profile, supplied fields, expected chart
identifier, and complete `expected.visualization_spec`. The expected
specification is schema-valid and registry-valid. Insufficient-data records use
an explicit table fallback and contain no invented mappings.

## Governance and training

D1 development assets are development-only. D2 provenance and review controls
must not convert them into approved records automatically. Only an explicitly
approved candidate can enter an approved export.

Training exports are deterministic JSONL chat records generated through the
audited prompt template. Evaluation benchmark cases remain separate from
approved export data.
