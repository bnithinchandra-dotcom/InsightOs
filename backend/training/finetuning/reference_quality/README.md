# Verified Reference Quality and Training Readiness

This isolated D2.4 implementation audits existing D1.1 and Power BI reference material. It does
not approve examples, construct positive training targets, execute training, modify adapters, or
change shared governance interfaces.

## Audit

```bash
uv run python -m insightos_training.reference_quality.audit audit \
  --source /mnt/c/Users/NITHIN/Desktop/data \
  --extraction-root ~/.cache/insightos/powerbi_reference \
  --output ~/.cache/insightos/reference_quality
```

The audit publishes a versioned review artifact outside the Git repository. It reconciles:

- the latest D2.3 extraction manifest with actual chart candidates and visual objects;
- the JSON visualization reference count and source checksum;
- the ZIP record count and manifest declaration;
- exact duplicate and normalized business-question overlap;
- marketing-funnel and customer-churn metric discrepancies; and
- the formal fine-tuning readiness gate.

## Approval and training boundary

All extracted records begin and remain pending human review. Technical validation is not approval.
The default approved-training and approved-evaluation counts are zero. The audit therefore reports
`TRAINING_BLOCKED_PENDING_DATA_APPROVAL` unless real human-approved records are supplied through a
separate authorized review integration.

No base-model identifier, revision, or baseline result is claimed unless independently verified.
The training configuration documents thresholds and boundaries; it does not run training.
