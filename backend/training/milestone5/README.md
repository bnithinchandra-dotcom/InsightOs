# InsightOS Milestone 5 — Qwen3-8B Baseline Handoff

This milestone prepares a manual Kaggle evaluation of the **original,
un-fine-tuned** `Qwen/Qwen3-8B` model. The notebook has not been executed.
There are no inference outputs or benchmark scores in this repository. The
baseline remains `pending`, and training is not authorized.

## What is pinned and measured

- Base model: `Qwen/Qwen3-8B`, revision
  `b968826d9c46dd6066d109eabc6255188de91218`.
- Tokenizer: same repository and immutable revision as the base model;
  notebook verifies the loaded snapshot and records the embedded chat-template
  SHA-256. `enable_thinking=False`.
- Inference: 4-bit NF4 with double quantization and float16 compute, one
  request at a time, deterministic decoding (`do_sample=False`), input limit
  6,144 tokens, output limit 512 tokens, seed `20261105`, and explicit
  `enable_thinking=False`. This is a **non-thinking-mode baseline**, not a
  complete evaluation of Qwen3 reasoning-mode performance.
- Kaggle inference packages are pinned to Transformers 4.51.3, Accelerate
  1.6.0, bitsandbytes 0.48.2, safetensors 0.5.3, and jsonschema 4.26.0.
  PyTorch is supplied by Kaggle and must be `>=2.6.0,<3.0.0` with CUDA.
  Preflight checks the actual GPU name and free/total VRAM, validates package
  versions and NF4 config, verifies the loaded bitsandbytes native library is
  CUDA-capable and matches the detected CUDA binary, then runs a real
  bitsandbytes NF4 CUDA quantize/dequantize/synchronize smoke check before
  model loading. Failure categories distinguish no CUDA PyTorch, no NVIDIA
  GPU, CPU-only fallback, missing CUDA binary, incompatible GPU architecture,
  and NF4 kernel execution failure. PEFT and TRL are intentionally not
  installed or used for this inference-only baseline.
- Model and tokenizer are resolved together from the exact pinned commit.
  The notebook verifies the Hub `model_info(...).sha`, the revision-specific
  snapshot directory and its resolved path, required tokenizer/model files,
  weight-index shards, and snapshot symlink targets. Mutable cache refs are
  not used to resolve the pinned full commit. Both loaders receive the same
  verified local snapshot with `local_files_only=True`.
- Benchmark: v4 preserves the 12 stable v2 case IDs and v3 expected outcomes,
  with corrected metric definitions. It includes expected
  decisions/tool proposals/specialist route, failure checks, the actual
  specialist-input contract, and synthetic-only context fixtures. No tool is
  dispatched. The case prompts and synthetic fixtures are included in the
  bundle; expected outcomes are not included in prompts.
- Benchmark independence: duplicate/near-duplicate analysis was run against
  the Qwen3-8B SFT v3 corpus and within the benchmark at the existing
  character-trigram Jaccard threshold. Counts and limitation are stored in the
  benchmark. This lexical audit is not proof of semantic independence.
- Tool schemas were regenerated from `app.agent.tools.TOOL_DEFINITIONS` and
  compared with the Milestone 4 bundle. The Kaggle notebook imports no
  InsightOS backend, database, registry, or application tool executor.

The orchestration output is a **benchmark evaluation envelope**, not a new
production API contract. Tool argument JSON Schemas and the
`SpecialistInputContract` are the actual InsightOS contracts. Automated
unsupported-claim and hallucinated-reference checks are heuristics; semantic
evaluation requires owner review.

## Kaggle execution instructions

1. In Kaggle, choose **File → Import Notebook** and upload:
   `notebooks/qwen3_8b_baseline_v6.ipynb`.
2. Create a private Kaggle dataset containing exactly the supplied
   `qwen3-8b-baseline-bundle-v6.json` file. Attach it as an input named
   `insightos-qwen3-8b-baseline-v6`. The notebook verifies the bundle SHA-256;
   do not edit or reserialize the JSON file.
3. In notebook settings, enable a GPU and internet access so the public,
   revision-pinned Hugging Face model and tokenizer can be downloaded. No
   Kaggle token or private model credentials are required.
4. Use one NVIDIA T4 16 GB GPU as the recommended starting configuration, or
   a GPU with more VRAM. This is a conservative recommendation for the
   8.19-billion-parameter model under the pinned 4-bit NF4, float16-compute,
   batch-one setup; it is **not a measured Kaggle result or a guarantee of
   fit**. The theoretical 4-bit weight payload alone is about 4.1 GB before
   quantization metadata, runtime allocations, and the 6,144-token KV cache.
   The notebook records actual GPU/VRAM and fails explicitly on load/OOM
   errors; do not silently change quantization or context settings.
5. Run the package-install cell first. It upgrades only the pinned inference
   packages and does not request a PyTorch replacement. If it says a restart
   is required, or if the runtime had already imported bitsandbytes, use
   **Kernel → Restart Session** and then **Run All** from the first cell. Do
   not continue to    preflight with the old imported module. Revision verification failure
   diagnostics record the expected Hub commit, observed Hub commit, snapshot
   directory, resolved snapshot directory, and metadata source in the run
   report and runtime metadata. The export remains explicitly `incomplete`,
   has no generated case IDs, and is rejected by the local importer. The install cell
   fails closed and prints this restart instruction if bitsandbytes was
   already loaded. The NF4 GPU preflight runs before model loading; failed
   preflight details are checkpointed in the export without loading the model.
   The inference cell
   checkpoints each case in `/kaggle/working`; re-running with the same
   bundle resumes completed cases and records interrupted/failed attempts
   without filling in missing results.
6. Wait for the final cell. It verifies the ZIP member hashes and prints the
   unique ZIP filename, byte size, execution status, member list, and whole-ZIP
   SHA-256. Download
   that ZIP from Kaggle's notebook output/files panel.
7. Copy the downloaded file directly into:

   ```text
   backend/training/milestone5/kaggle_results/incoming/
   ```

8. From `backend/`, validate and import it with the project's Python 3.14.3
   environment:

   ```text
   python -m training.milestone5.import_kaggle_results training/milestone5/kaggle_results/incoming/<downloaded-zip-name>.zip
   ```

   The importer checks execution status, exact model/tokenizer revision,
   benchmark identity and coverage, every artifact SHA-256/size, raw/per-case
   result schemas, tool argument schemas, and aggregate pass-rate arithmetic.
   Accepted artifacts are extracted to a new run-specific directory under
   `kaggle_results/validated/`; existing imports are never overwritten.
   Import validation means the run artifact is structurally verified, not
   that the model passed the benchmark or received semantic approval.

## Output ZIP and runtime expectations

Expected filename pattern:

```text
insightos-qwen3-8b-baseline-v1-<UTC-run-id>-export-001.zip
```

The export contains the baseline bundle and benchmark, raw generated output
for every attempted case, per-case results, aggregate metrics, model/tokenizer
metadata, runtime/hardware/package metadata, progress checkpoint, a run
report, and an execution manifest with file hashes and sizes. Re-exporting a
resumed run creates a new `export-NNN` archive; previous ZIPs are not
overwritten.

Runtime depends on Kaggle GPU allocation, model download bandwidth/cache,
dependency setup, model load, and 12 sequential generations (each with up to
6,144 input and 512 output tokens). No duration is asserted because no Kaggle
run has been performed.

## Error reporting

- If setup, download, model load, or inference fails, preserve the complete
  Kaggle error output and report the cell name, exception type/message, Kaggle
  GPU model/VRAM, Python/PyTorch/CUDA and package versions printed by the
  notebook. Do not include credentials or Kaggle/Hugging Face tokens.
- A caught initialization or case failure produces an incomplete diagnostic
  ZIP when the bundle itself is valid. The local importer deliberately rejects
  incomplete archives as accepted baselines.
- If the kernel is forcibly terminated, download the latest
  `progress_checkpoint.json` and any available files under the run's
  `raw_outputs/` directory, along with the notebook error log. A case marked
  `in_progress` has no fabricated output; rerun the notebook to record it as
  interrupted and retry it.
- If the pinned 4-bit configuration runs out of GPU memory, send the exact
  error and hardware details. Do not change precision, model revision, context
  length, or settings and call the result comparable without review.

## Metrics and limitations

The report defines numerator and denominator for automated pass rate,
tool-selection accuracy, argument-schema validity, structured-output
validity, `expected_decision_accuracy`, `required_clarification_accuracy`,
specialist routing, and per-category pass rate. Numeric grounding is reported
separately as a literal-membership heuristic: it does not verify calculations,
units, context, causal meaning, or qualitative factual claims. It cannot
establish that all unsupported claims or hallucinations were detected.
Latency is measured around `model.generate` with CUDA
synchronization; input/output token counts come from tokenizer/model tensors.
Missing or inapplicable observations are explicitly unavailable. Semantic
unsupported-claim and hallucination rates remain unavailable until human
review; automated text/number checks are reported separately as heuristics.

One case per capability is a smoke baseline only. It is not statistically
powered and must not be used alone to claim general capability or to authorize
fine-tuning.

## Training-readiness decision

**NOT READY — BASELINE INCOMPLETE.** The SFT v3 release hash and benchmark
leakage audit are pinned; human-validation scope is documented; training
authorization remains false. Kaggle execution, reproducibility evidence,
human semantic review, and Kaggle QLoRA runtime compatibility are still
pending. Do not train or change production model routing as part of this
milestone.
