# InsightOS Training Framework

This package is isolated from production InsightOS runtime, lifecycle, APIs, frontend, and analytical engines.

## WSL2 setup

```bash
cd backend/training/finetuning
./scripts/setup.sh
./scripts/doctor.sh
./scripts/verify.sh
```

## Visualization governance

Candidates follow: source ingestion -> normalization -> candidate generation -> validation -> human review -> approval -> export.
No record is approved automatically.

```bash
uv run python -m insightos_training.cli visualization validate-candidate --candidate candidate.json
uv run python -m insightos_training.cli visualization add-candidate --candidate candidate.json
uv run python -m insightos_training.cli visualization list-pending
uv run python -m insightos_training.cli visualization inspect-provenance --example example-id
uv run python -m insightos_training.cli visualization review --example example-id --decision approved --reviewer reviewer-name
uv run python -m insightos_training.cli visualization export-approved
uv run python -m insightos_training.cli visualization inspect-export --export-dir ~/.cache/insightos/visualization/approved-export
```

Approved exports fail closed when zero approved examples exist. They require complete train, validation, and evaluation coverage and reject split leakage and duplicate content.

## Kaggle artifact import

```bash
uv run python -m insightos_training.cli artifacts import-kaggle \
  --archive ~/.cache/insightos/incoming/insightos_qwen3_0_6b_validated.zip \
  --expected-sha256 8af718140e78857afe0ffd6ed22abf556778bf0ccbe8355c4b3fb0b1d1d309f4
uv run python -m insightos_training.cli artifacts list
uv run python -m insightos_training.cli artifacts inspect --experiment insightos_qwen3_0_6b_smoke
```

Experiments are stored outside Git under `~/.cache/insightos/experiments/`. Synthetic artifacts are not production-ready.

## Visualization foundation

The visualization package contains a 17-entry machine-readable registry,
versioned structured output schema, complete decision rules, a 24-example
development corpus, and separate evaluation cases. All chart selections and
structured output targets are validated against supplied field names and types.
The language model selects and describes configurations; it does not calculate
metrics or render charts. The registry and D1 corpus are development assets and
are not automatically approved training data.
