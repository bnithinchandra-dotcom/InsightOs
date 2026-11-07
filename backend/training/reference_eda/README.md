# Reference EDA Qwen3 specialization

This directory contains an isolated fine-tuning and evaluation path for synthetic
business analytics behavior. It does not modify production APIs, agents, providers,
analytics engines, orchestration, or lifecycle code.

## Corpus and review status

`dataset_v1/` contains 250 original synthetic training examples across five
industries: marketing, predictive maintenance, e-commerce, fraud review, and
healthcare readmission. Examples use stakeholder-led conversations, explicit
synthetic dataset/file identifiers, registered tool schemas, complete serialized
`ToolResult` objects, and schema-validated inspection handoffs. SQL, Python, and
DAX examples calculate scenario-specific measures and include denominator,
grain, leakage, attribution, label, and human-review controls where relevant.

The corpus has 50 topic groups, kept intact across a 200/25/25 train/validation/
held-out split. Each industry is represented in every split. The split design is
approved; the individual records are not.

**The current corpus is draft until humans explicitly review and approve its
individual records.** The 250 training examples and 20 evaluation cases are
reviewed independently. No review decision is written into source examples or
cases; the append-only review history is `review_history.jsonl`.

The reviewed ZIP (`reference_eda_reviewed.zip`) was used as the input review
record. Its findings about missing domains, single-turn templates, ungrounded
tool identifiers, incomplete tool results, generic KPI code, and an underspecified
evaluation suite have been applied to the generated blueprint. The source archive
review did not approve any record or authorize fine-tuning. See
`HUMAN_REVIEW_REPORT.md` and `review_manifest.json` for the current disposition.

## Build and validate

From `backend/`:

```text
python -m training.reference_eda.build_dataset
python -m training.reference_eda.validate_dataset --check-source
```

Validation checks the SFT schema, tool arguments against the registered tool
definitions, identifier presence in preceding conversation context, complete
serialized tool-result fields, `InspectionHandoff` and engine-inspection
contracts, group integrity, fixture determinism, executable read-only SQL,
Python syntax, KPI arithmetic tests, DAX field coverage, and the evaluation
rubric. The human-review suite contains 20 non-tool held-out cases with scenario
profiles, mandatory facts, incorrect behaviors, and a 100-point weighted rubric.
Each case also has domain-specific acceptance and rejection criteria and a
case-level rubric totaling 100 points. Held-out tool calls remain in
`held_out.jsonl` for a future workflow-aware runner; they are not covered by the
text-only benchmark.

Fixture outputs are synthetic and descriptive. They are not production
measurements, clinical advice, confirmed fraud, confirmed failures, or causal
estimates. The marketing fixture has no impressions or touch-level attribution
records, and the e-commerce fixture omits tax and shipping. Zero-spend and
fully-refunded groups exercise undefined-denominator handling. Definitions and
limitations are included in each fixture and example.

## Review gates

From `backend/`, generate reviewer files and inspect individual records:

```text
python -m training.reference_eda.review_workflow queue --output-dir training/reference_eda/review_queue
python -m training.reference_eda.review_workflow inspect --category training --id eda-marketing-qualified-leads-schema
python -m training.reference_eda.review_workflow inspect --category evaluation --id <held-out-case-id>
```

The queue creates separate `training.json` and `evaluation.json` files. Each
record includes the unchanged source record, a readable inspection view,
scenario checklist, current status, source/content hashes, and a decision
template. Fill the template with `reviewer_id`, `decision`, timezone-aware
`timestamp_utc`, comments, issues, and requested corrections. Import the completed
record in a JSON artifact with the queue version and category:

```json
{
  "queue_version": "insightos-human-review-queue-v1",
  "record_category": "training",
  "decisions": [
    {
      "record_id": "eda-marketing-qualified-leads-schema",
      "record_category": "training",
      "scenario": "marketing",
      "source_sha256": "<copy from queue>",
      "content_sha256": "<copy from queue>",
      "reviewer_id": "reviewer-handle",
      "decision": "approved",
      "timestamp_utc": "2026-11-04T10:00:00+05:30",
      "comments": "Checked response, tool contracts, and fixture evidence.",
      "issues": [],
      "requested_corrections": [],
      "reason_for_change": "",
      "correction_reference": "",
      "corrected_record": null
    }
  ]
}
```

Use a stable non-sensitive reviewer handle; do not include unnecessary personal
information in reviewer IDs or comments.

Import, validate, and report progress:

```text
python -m training.reference_eda.review_workflow import --file reviewer_decisions.json
python -m training.reference_eda.review_workflow validate
python -m training.reference_eda.review_workflow report --output training/reference_eda/review_report.json
```

Rejected and `needs_revision` decisions require comments. Corrections are stored
in the review history, never written over source records. A changed correction
must first receive a `needs_revision` decision and then a separate review before
approval. New source hashes invalidate earlier approvals; re-review is appended
with a reason and does not erase prior events. Regenerating the source dataset
resets legacy embedded status fields to draft while leaving the separate history
available for hash-based validation.

Create a separate approved-only view:

```text
python -m training.reference_eda.review_workflow approved
```

Only explicitly approved records appear in the generated files under
`dataset_v1/approved_only/`. The manifest is ready only when every training
record and every evaluation case has a current explicit approval. Partial
exports remain visibly incomplete and cannot pass fine-tuning preflight.
Preflight verifies the approval history, source hashes, output hashes, and
record-by-record correspondence before training or evaluation; merely creating
review files does not confer approval.

Claim validation remains fail-closed. Metric, comparative, statistical, forecast,
prediction, anomaly, causal, and preprocessing claims are not enabled merely by
this training corpus; they require current, verified, evidence-specific
provenance resolvers in the production claim-validation path.

## Isolated training CLI and container

This training path is separate from production dependencies. From `backend/`:

```text
python -m training.reference_eda.cli environment-check
python -m training.reference_eda.cli model-preflight --model qwen3-0.6b
python -m training.reference_eda.cli dataset-preflight
python -m training.reference_eda.cli dry-run --model qwen3-0.6b
python -m training.reference_eda.cli inspect-adapter <adapter-directory>
```

The dry-run validates configuration and synthetic fixtures without loading a base
model or using draft records for training. `train` is separate and requires the
explicit `--model` selection plus a ready review-gated preflight:

```text
python -m training.reference_eda.cli train --model qwen3-0.6b
```

From the repository root, build and start only the Linux GPU training container
(it defaults to environment-check and does not train):

```text
docker compose -f backend/training/reference_eda/docker-compose.gpu.yml build
docker compose -f backend/training/reference_eda/docker-compose.gpu.yml run --rm qwen-training environment-check
docker compose -f backend/training/reference_eda/docker-compose.gpu.yml run --rm qwen-training dry-run --model qwen3-0.6b
docker compose -f backend/training/reference_eda/docker-compose.gpu.yml run --rm qwen-training model-preflight --model qwen3-0.6b
```

Run real fine-tuning only after explicit approval and an override-free successful
preflight:

```text
docker compose -f backend/training/reference_eda/docker-compose.gpu.yml run --rm qwen-training train --model qwen3-0.6b
```

## Reproducible LoRA/QLoRA runs

The three model configs pin immutable Hugging Face revisions. Training is opt-in:
the default command only runs preflight and never downloads model weights.

The supported target is Linux x86_64 with an NVIDIA CUDA GPU and bitsandbytes.
Use Python 3.10-3.12. Install the pinned PyTorch 2.6.0 wheel matched to the CUDA
runtime from the official PyTorch index, then install
`requirements-training.txt`. Minimum GPU memory is conservative and
model-specific: 8 GiB for 0.6B, 24 GiB for 4B, and 32 GiB for 8B. These limits
are preflight gates, not claims of a measured training run. CPU-only and Windows
training are deliberately disabled until validated on those platforms.

From `backend/`:

```text
python -m training.reference_eda.train_lora --model qwen3-0.6b
python -m training.reference_eda.train_lora --model qwen3-0.6b --train
```

The first command reports dataset, approval, dependency, Python, platform, CUDA,
and VRAM blockers. The second explicitly authorizes checkpoint download and
training, and proceeds only when every gate passes. Training uses 4-bit NF4 with
LoRA, a fixed seed, pinned packages/checkpoint, validation loss, no external
reporting, and a run manifest with dataset hashes, environment, and Git revision.

The pinned Qwen3 tokenizer template was checked and does not expose
`{% generation %}` assistant-mask markers. The trainer therefore constructs
assistant-only completion labels from the tokenizer's actual chat template, masks
system/user/tool context with `-100`, and fails if prompt/target token boundaries
do not match. Tool-call assistant turns and post-tool assistant responses are
separate supervised completions. No model is downloaded or trained by corpus
generation or validation.

## Evaluation

The group-held-out human-review cases are in
`dataset_v1/human_review_suite.json`. Use the same suite before and after tuning,
retain the generated case outputs, and complete human review with the existing
training evaluation workflow. The text-only adapter evaluator does not simulate
tool execution; held-out tool cases need a workflow-aware runner.
