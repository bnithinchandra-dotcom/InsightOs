# InsightOS Qwen3 Milestone 4

This package contains additive, provenance-bound dataset releases and an
independent Qwen3-8B orchestration benchmark. It does not start training or
modify production routing.

## What this milestone establishes

- The source EDA corpus contains 250 examples, 50 scenario groups, and 50
  SQL/Python/DAX examples validated against synthetic fixtures. A near-duplicate
  audit found 959 similar pairs, including 364 across its original splits;
  scenario-group separation alone did not prevent template leakage.
- `reference_eda/dataset_v2` is a byte-preserving audit snapshot of the original
  splits, not a training-ready release. The additive `dataset_v3` release
  preserves prompts and targets and groups entire domains into splits:
  150/50/50 train/validation/test, with marketing, maintenance, and ecommerce
  in train, fraud in validation, and healthcare in test. Its domain holdout
  removes the observed cross-split near duplicates, but the single-domain
  validation and test splits limit broad performance claims. The v3 quality
  report is `qwen3_4b_quality_report_v3.json`; the earlier
  `qwen3_4b_quality_report.json` records the original split audit.
- Reference EDA model configs now point to v3. The schema remains
  `insightos-agent-sft-v1`; that is a schema-contract version, not the corpus
  release version.
- The orchestration SFT v2 source preserves the original 12 examples and adds
  12 contract-grounded synthetic drafts. The final v3 release is an additive,
  byte-identical copy of v2: 24 records split 13/5/6 across
  train/validation/test, with 24 scenario groups, no detected exact or
  cross-split near duplicates, and all records still draft. The 12 synthetic
  additions are outside the owner-reported human-review scope.
- The versioned orchestration quality report documents tool-contract
  validation, coverage, duplicate/leakage checks, provenance scope, and
  remaining blockers. The independent 12-case benchmark is evaluation-only;
  tool calls are proposals and are never dispatched.
- `dataset_validation_attestations_v1.json` records owner-reported validation
  for exact dataset versions and SHA-256 hashes, including senior Business
  Analyst validation as owner-reported provenance. It does not invent reviewer
  identities, historical review dates, or per-example approvals.
- The Qwen3-8B Kaggle baseline notebook evaluates the untouched pinned base
  model with 4-bit NF4 inference and mocked tool proposals. Its baseline is
  pending owner execution; no inference or training was run as part of this
  milestone.

Generate these additive artifacts from the backend directory with the
existing training-governance environment:

```text
python -m training.milestone4.prepare_milestone4
```

The generator refuses to overwrite existing versioned releases. To generate
only the corrected EDA release from the already prepared Milestone 4 outputs:

```text
python -m training.milestone4.prepare_milestone4 --generate-eda-v3
```

Finalize the additive v3 orchestration release, dataset-level attestation,
reports, benchmark bundle, and separate QLoRA candidate configs with:

```text
python -m training.milestone4.finalize_milestone4
```

Finalization uses exclusive-create writes and refuses to overwrite existing
versioned outputs.

## Human review and training gates

The 2026-11-03 `reference_eda/review_manifest.json` is preserved as a
historical record. Separately, the project owner reports that the project work
was human-validated by the owner and a senior Business Analyst. The newer
statement is bound to the released dataset versions and hashes in
`dataset_validation_attestations_v1.json`; it is owner-reported provenance,
not a direct attestation from an identified Business Analyst. No reviewer
identities, historical review dates, or record-to-review mapping were
provided. Human validation, automated validation, per-example approval, and
training authorization remain distinct states; no row was converted to
`approved`.

The reference EDA training gate requires complete, current, record-level
review events. The orchestration v2 dataset also contains only draft rows.
Neither dataset is training-ready. Review the new release explicitly:

```text
python -m training.reference_eda.validate_dataset --dataset-dir training/reference_eda/dataset_v3
python -m training.reference_eda.review_workflow --dataset-dir training/reference_eda/dataset_v3 queue
```

Do not fill reviewer names or timestamps from assumptions. Preserve review
history as append-only events and retain the existing manifest as historical
evidence.

## Qwen3-8B baseline

The installed base model was identified as:

- Ollama tag: `qwen3:8b`
- Digest: `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`
- Architecture/size: Qwen3, 8.19B parameters
- Quantization/artifact size: Q4_K_M, 5,225,388,164 bytes
- Ollama: 0.40.14
- Chat template: Ollama's embedded Qwen3 Go template; tokenizer metadata reports
  `tokenizer.ggml.pre=qwen2`

Baseline inference was **not run**. At the preflight snapshot the host had
1.76 GiB free RAM, below the existing local live-benchmark estimate of 7 GiB
for loading this model. The model was not loaded or called; there are no
per-case or aggregate scores. See `qwen3_8b_baseline_preflight.json`.

Run the independent benchmark on an isolated Kaggle inference kernel or another
host with adequate free memory. Record the exact model digest, environment,
tool-schema hash, suite hash, prompts, outputs, token/latency telemetry, and
failures in a new, non-overwriting report. Keep tool execution mocked.

The benchmark has one case per capability and is a smoke baseline only, not a
statistically powered certification set. Before tuning, expand it with
independent scenario families and human-reviewed semantic rubrics. Do not tune
prompts against its held-out cases.

## Pre-registered acceptance thresholds

These are proposed release gates to review before any fine-tuning:

| Measure | Minimum acceptance |
|---|---:|
| Strict contract/schema validity | 100% |
| Registered tool name and argument correctness | 95% overall; 100% for safety-critical argument constraints |
| Task accuracy | 85% overall and at least 80% in each capability category |
| Evidence-supported factual claims | 95% or higher |
| Fabricated numeric results or false execution claims | 0 critical failures |
| Required clarification/abstention | 90% or higher; unsupported claims must not be used to avoid abstention |
| Regression against base model | No more than 5 percentage points worse on any critical existing contract/safety suite |
| Primary specialist score after tuning | At least 5 percentage points above its pinned base-model baseline |
| Latency and memory | Report p50/p95, cold/warm load, throughput, peak RAM/VRAM; satisfy the deployment host's measured budget |

Use paired base-versus-adapter prompts and fixed decoding, but keep validation
for model selection and never use the test split for optimization. Human
semantic review remains required even when deterministic checks pass. Training
loss is not an acceptance metric.

## Review-gated roadmap

1. Review the exact release hashes, owner-reported validation scopes, split
   limitations, and the 12-case benchmark. Complete record-level review and
   authorization as a separate governance action.
2. Run the untouched Qwen3-8B baseline notebook on Kaggle, review every case,
   and preserve its environment and result report. No baseline metrics exist
   until that run completes.
3. Perform no-training Kaggle compatibility smoke checks for the pinned 4B
   and 8B models, tokenizers, QLoRA libraries, target modules, and artifact
   export. Keep the 4B held-out domain limits explicit.
4. Only after owner approval, run each QLoRA experiment separately, preserving
   the base revision, dataset hashes, adapter, environment, and run metadata.
5. Compare each adapter with its pinned base using fixed evaluation inputs,
   regression gates, human semantic review, latency, and memory measurements.
6. Treat GGUF conversion and non-production Ollama integration as a later
   gated validation; do not change production routing during these steps.

## Kaggle compatibility

The tracked 0.6B notebook has no stored execution outputs and its package
installation is unversioned, so its successful runtime package versions cannot
be verified from the checked-in notebook. The local governance venv is Python 3.11.18 with
jsonschema 4.26.0, PyYAML 6.0.3, and safetensors 0.8.0; it lacks PyTorch,
Transformers, PEFT, TRL, Accelerate, bitsandbytes, and datasets. No Kaggle
kernel was available for verification.

The reference EDA stack pins Python `>=3.10,<3.13`, PyTorch 2.6.0/CUDA 12.4,
Transformers 4.51.3, PEFT 0.15.2, TRL 0.16.1, Accelerate 1.6.0,
bitsandbytes 0.45.5, datasets 3.5.0, and safetensors 0.5.3. Treat this as a
candidate environment, not verified compatibility for 4B/8B on Kaggle. Verify
model revisions, tokenizer/chat template, target modules, assistant-only loss,
CUDA kernels, and adapter export in a no-training smoke environment first.

The Qwen3-8B baseline bundle pins `Qwen/Qwen3-8B` revision
`b968826d9c46dd6066d109eabc6255188de91218`. Its notebook captures package,
CUDA, GPU, latency, memory, model/tokenizer, generation, and input-hash
metadata in the owner-generated run report. Quantization and package pins are
candidate evaluation settings; compatibility must be confirmed in Kaggle.

The backend remains Python 3.14.3. Do not install the training stack into the
backend environment or change its runtime.

## Integration boundary

The existing runtime has a static model registry, role-specific overrides,
strict Pydantic tool contracts, LangGraph task state, and a serialized Ollama
model lifecycle. No registry or routing change is part of this milestone.
For a future approved run, retain the original adapter weights, tokenizer,
training configuration, and hashes as the auditable source artifact. Test a
merged Transformers checkpoint separately; for the Ollama deployment path,
GGUF is the likely packaging format, but conversion is conditional on the
selected converter's support for the exact Qwen3 architecture and revision.
Record merge/conversion and quantization settings, then compare the converted
model with the pinned base on the same benchmark. First import only under a
non-production Ollama tag. Validate structured responses, registered tool
arguments, role eligibility, model switching, cold/warm latency, and memory
release before proposing any static-registry entry. Never assume a LoRA
adapter can be loaded directly by Ollama or register an unvalidated artifact.

The attached supervised ML engine remains deterministic application logic;
fine-tuning must not replace its feature validation, leakage checks, split
handling, or metric computation.
