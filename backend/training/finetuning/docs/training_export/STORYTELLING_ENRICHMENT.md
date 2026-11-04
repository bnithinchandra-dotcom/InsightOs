# First-Party Power BI Storytelling Enrichment

This is a separate D2.5.2 knowledge-enrichment artifact. It does not replace or modify the
D2.5.1 bundle, registry, corpus, adapters, production code, or source files.

## Provenance

All seven projects are recorded as:

- source authority: `first_party_owner_authored`
- author attestation: `user_confirmed`
- owner-estimated accuracy: `0.95`
- accuracy estimate basis: `owner_attestation`
- independent verification: `not_established`

The owner attestation establishes first-party provenance. It does not establish independent
accuracy, approve extracted candidates, or bypass training readiness gates.

## Enrichment

The bundle adds:

- project-level source inventories with original filenames and checksums
- 609 configuration recoverability records
- derived, explicitly labeled dashboard prompts
- separate future-model destination mappings
- 647 selection-record reconciliation metadata
- a fail-closed review queue
- empty train, validation, and evaluation splits

No business field mapping, aggregation, measure definition, filter context, or complete
visualization target is fabricated. Missing information is preserved explicitly.

## The 197 transition

The current records show an aggregate exclusion-register increase from 1,054 to 1,251.
That numeric difference alone is not proof that exactly 197 named records changed state.
An authoritative pre-D2.5.1 archive or candidate register is required for exact record-level
pending-to-excluded reconciliation.

## Archive

Build:

```bash
uv run python -m insightos_training.training_export.storytelling_enrichment \
  --archive ~/.cache/insightos/training_exports/insightos_qwen3_0_6b_training_bundle.zip \
  --extraction-root ~/.cache/insightos/powerbi_reference \
  --destination ~/.cache/insightos/training_exports
```

Verify the external archive checksum with the adjacent `.zip.sha256` sidecar:

```bash
sha256sum ~/.cache/insightos/training_exports/insightos_qwen3_0_6b_storytelling_enriched_bundle.zip
```
