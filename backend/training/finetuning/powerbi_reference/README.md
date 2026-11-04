# Power BI Reference Extraction

This directory documents the isolated D2.3 Power BI reference extraction pipeline. The
implementation is intentionally separate from D1.1, D2 governance, D2.2 intake, the shared
CLI, and all production contracts.

## Source intake

The default source directory is `/mnt/c/Users/NITHIN/Desktop/data`. The inventory is read-only:

```bash
uv run python -m insightos_training.powerbi_reference inventory \
  --source /mnt/c/Users/NITHIN/Desktop/data
```

Create an external review collection:

```bash
uv run python -m insightos_training.powerbi_reference extract \
  --source /mnt/c/Users/NITHIN/Desktop/data \
  --output ~/.cache/insightos/powerbi_reference
```

## Safety

- Original files are never copied into the output and are never modified.
- PBIX archives reject traversal, symlinks, member size limits, total size limits, and
  compression-ratio limits before metadata is read.
- Only `Report/Layout` and bounded member inventory metadata are decoded.
- DAX and binary model decoding are reported as unavailable when safe support is absent.
- PDF extraction is conservative and never upgrades OCR-like text without human review.
- Every candidate record has `review_status: pending_review` and
  `approval_status: not_approved`.
- Successful publication uses an operation-local staging directory followed by atomic rename.
- The registry is read-only; unsupported visuals are reported as review extensions only.

## Integration

The module does not edit or import the shared governance/export APIs. If later integration is
authorized, an adapter must explicitly translate the external reference manifests, candidates,
and review states without changing the existing contracts.
