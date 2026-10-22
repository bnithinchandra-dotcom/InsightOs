# InsightOS Qwen3-0.6B Fine-Tuning Preparation

**Reviewed:** 2026-10-21

**Status:** Baseline complete and isolated training stack installed and
verified. The pinned checkpoint download is paused at the user's request after
177,262,592 of 1,503,300,328 model-weight bytes were received. The checkpoint
is incomplete; no dataset download or training was performed.

## Executive summary

The local Ollama model is present, but it is a quantized inference artifact and
is not the Hugging Face checkpoint required for Transformers/PEFT training.
After the user freed memory, a synthetic-only baseline was run against
`qwen3:0.6b`: 13 cases, 8 passed, 5 failed. Tool selection passed 2/2 and the
strict JSON case passed 1/1, but metric definition, schema, clarification,
format, and one unsupported-action case exposed failures. Raw synthetic inputs
and responses are saved in the baseline JSON report.

CPU LoRA fine-tuning is **plausible but not yet demonstrated** on this laptop.
The baseline ran with 1.97 GiB free before model load, a low sample of
1.24 GiB while loaded, and 2.31 GiB after completion. The isolated CPU training
stack was subsequently installed and verified after user approval. Free RAM
was 3.71 GiB at environment verification and 2.64 GiB after the checkpoint
transfer was stopped, both below the proposed training gate of at least
10 GiB free. The pinned Hugging Face checkpoint transfer was also approved,
then paused at the user's request; its partial weight file is not a usable
checkpoint. No training or dataset download was performed.

This preparation does not implement retrieval/RAG, GraphRAG, analytics,
orchestration, production integration, or a fine-tuned model.

## Repository and existing InsightOS foundation

The initial Git status was `main...origin/main`, with pre-existing changes to
`README.md`, the agent API/contracts/providers and tests, `frontend/src/App.css`,
and untracked `.vscode/`, `backend/OPEN_MODEL_FOUNDATION.md`, evaluation code,
and its tests. These were preserved. This work adds this report and
[`training/qwen3-0.6b/insightos-sft.schema.json`](./training/qwen3-0.6b/insightos-sft.schema.json);
the existing open-model note now points to this follow-up.

The existing provider layer supports local Ollama and OpenAI-compatible
providers, but the provider defaults to `none`. The agent runtime advertises
nine read-only tools: project, dataset, and file discovery; stored profile and
quality-report retrieval; saved EDA results; and saved schema analyses. Tool
arguments are validated against strict schemas, and model output is not itself
an authorization mechanism. The numerical analytics and execution boundaries
remain deterministic.

The baseline harness in [`app/agent/evaluation.py`](./app/agent/evaluation.py)
was extended to 13 synthetic case types covering instruction following,
fact grounding, business metric definition, schema interpretation, typed
strict JSON, abstention, clarification, format constraints, allowlisted tool
selection and arguments, tool-result grounding, unsupported actions, and
three-run consistency. It uses the existing read-only tool schemas and never
executes model tool calls. It records exact prompts, expected behavior, raw
responses, tool-call arguments, token counts when returned, per-request
latency, category scores, and sanitized provider errors. The tests use fake
providers in [`tests/test_agent_evaluation.py`](./tests/test_agent_evaluation.py);
process memory was measured externally during the real run and is recorded
below.

## TerraGuard/LandslideGuard practice review

Read-only review of
[landslide-ai commit `18a326ad81ffd46712aa73aaf283ec9febb79059`](https://github.com/supernovadevssihproject-team/landslide-ai/tree/18a326ad81ffd46712aa73aaf283ec9febb79059):

- **Actual training:** `backend/ml/train_susceptibility.py` fits a 100-tree
  scikit-learn `RandomForestClassifier`. It uses a CSV with 15 positive
  historical records, adds 12 hand-coded negative rows, generates soil
  saturation randomly, then makes 15 noisy copies of every row (405 augmented
  rows, not 405 independent observations). It reports five-fold ROC-AUC and
  saves a joblib bundle with model/features/metrics. Augmentation occurs before
  cross-validation, so perturbations of one source record can leak across
  folds; NumPy augmentation is not seeded. The repository contains a joblib
  file, but its provenance was not verified.
  [Training data preparation](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/ml/train_susceptibility.py#L16-L72)
  · [Fit, evaluation, and save](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/ml/train_susceptibility.py#L74-L102)
- **Field-report files are scaffolding, not a training pipeline:**
  `train_field_report_classifier.py` prints a `NOT TRAINED` manifest;
  `evaluate_field_report_classifier.py` checks for files and prints readiness
  without prediction or metrics; and `prepare_field_report_dataset.py` is a
  class/source registry without downloading, preprocessing, deduplication, or
  split creation.
  [Train manifest](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/ml/train_field_report_classifier.py#L1-L31)
  · [Evaluation prerequisite check](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/ml/evaluate_field_report_classifier.py#L1-L25)
  · [Dataset registry](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/ml/prepare_field_report_dataset.py#L1-L34)
- **Inference and artifact inconsistency:** `field_report_classifier.py` is
  ONNX Runtime CPU inference code. The repository tree has a v2 ONNX model,
  external-data file, and config, although the artifact/dataset READMEs claim
  no trained ONNX artifact is available. The artifact was not downloaded or
  executed, so its validity and provenance are unknown. The config lists four
  classes while the registry/documentation lists six. Static metrics in the
  config include accuracy 0.9242 but class imbalance is severe; the available
  confusion matrix has no correct `roadBlockage` or `flood` predictions. The
  README's described test split matches row totals, but the scripts do not
  establish how those metrics were calculated.
  [ONNX inference](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/ml/field_report_classifier.py#L51-L120)
  · [Artifact documentation discrepancy](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/ml/artifacts/README.md#L15-L16)
  · [Model config](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/ml/artifacts/field_report_classifier_v2.json#L4-L30)
- **Data and licensing:** the susceptibility CSV is attributed to GSI/NASA
  historical data in code/docs, but the CSV lacks source URLs, per-record
  provenance, and license metadata. The field-image README lists public
  Hugging Face sources but not per-source licenses or pinned revisions; other
  docs say those details are unfilled. The repository's general reuse wording
  does not grant rights to third-party datasets. No TerraGuard data or assets
  were copied.
  [Susceptibility CSV](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/data/ner_historical_landslides.csv#L1-L16)
  · [Dataset provenance notes](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/ml/datasets/README.md#L7-L28)
- **Transferable practices:** explicit feature/config metadata, provenance and
  license tracking, input validation, and explicit missing-artifact errors.
  For InsightOS, split by scenario before augmentation/deduplication and seed
  all randomness. Random Forests, numeric feature perturbations, ROC-AUC
  cross-validation, feature importances, and joblib are tabular-ML methods and
  do not transfer as LLM fine-tuning mechanics. TerraGuard requirements list
  scikit-learn/ONNX Runtime, not a tokenizer or language-model training stack.
  [TerraGuard requirements](https://github.com/supernovadevssihproject-team/landslide-ai/blob/18a326ad81ffd46712aa73aaf283ec9febb79059/backend/requirements.txt#L6-L13)

This review was limited to the requested files and directly relevant
documentation. The ONNX/joblib binaries were not inspected or run, source
dataset licenses were not independently established, and the active
susceptibility runtime path was not traced.

## Current local environment and model identity

Observed on 2026-10-21; memory values are point-in-time and varied during
inspection:

| Item | Observation |
| --- | --- |
| OS/runtime | Windows; Python 3.14.3 |
| CPU/RAM | Intel Core i7-13620H, 10 cores / 16 logical processors; 15.7 GiB visible RAM |
| Free physical RAM | Initial samples were 0.24-0.93 GiB. Before the final baseline it was 1.97 GiB; the lowest observed while Qwen was loaded was 1.24 GiB; it was 2.31 GiB after the run. |
| Free space | 175.23 GiB on `C:` after the partial checkpoint transfer |
| Graphics | Intel UHD integrated graphics; no CUDA/NVIDIA GPU; `xpu-smi` unavailable |
| Ollama | 0.40.7; during baseline `ollama ps` reported Qwen at 1.0 GB, 100% CPU, context 4096 |
| Installed local models | `qwen3:0.6b` and `gemma3:1b` |
| Base Python training packages | Pre-existing PyTorch 2.14.0 and `datasets` 5.0.1; unchanged |
| Isolated training environment | `backend\.venv-qwen3-train`; PyTorch 2.14.1+cpu, Transformers 5.20.2, PEFT 0.21.2, TRL 1.16.0, safetensors 0.8.0, Accelerate 1.15.0, datasets 5.1.0, huggingface-hub 2.2.0. Imports succeeded and `pip check` found no broken requirements. |
| Hugging Face Qwen checkpoint | Incomplete. `backend\artifacts\qwen3-0.6b-hf-c1899de\model.safetensors` contains 177,262,592 of 1,503,300,328 bytes; transfer stopped at the user's request. The partial file has not been checksum-verified and must not be loaded as a model. |

The local Ollama `qwen3:0.6b` artifact is identified by its Ollama model
manifest digest
`7df6b6e09427a769808717c0a93cadc4ae99ed4eb8bf5ca557c90846becea435` and is
522,653,767 bytes. `ollama show` reports Qwen3 architecture, 751.63M
parameters, `Q4_K_M` quantization, and a 40,960-token context setting. The
Ollama model defaults shown locally are temperature 0.6, top-k 20, top-p 0.95,
and repeat penalty 1.

The candidate Transformers checkpoint is the separate official
[`Qwen/Qwen3-0.6B`](https://huggingface.co/Qwen/Qwen3-0.6B) repository at
revision
`c1899de289a04d12100db370d81485cdf75e47ca`. Its model card identifies a
0.6B-parameter causal language model and advertises a 32,768-token context.
The pinned revision was used for the approved download. The model-weight file
is 1,503,300,328 bytes; only its first 177,262,592 bytes were received before
the transfer was stopped. The partial file is stored at
`backend\artifacts\qwen3-0.6b-hf-c1899de\model.safetensors` and has not been
verified as a complete checkpoint. The Hugging Face revision is not the
Ollama manifest digest. The quantized Ollama artifact must not be mistaken
for, or overwritten in place of, the Transformers checkpoint. The context
lengths reported by the two sources also differ; use a conservative short
sequence length for an experiment rather than assuming either context limit
is a safe working setting.

### License and checkpoint provenance

The Hugging Face model metadata tags the checkpoint `apache-2.0`; the model
card identifies Qwen3-0.6B and links to the Qwen documentation and code. This
review date is the verification date. Before redistribution or serving, review
the pinned revision's license and notices again, including tokenizer and any
derived adapter assets. The Ollama artifact has a separate packaging/runtime
provenance and does not establish the Transformers checkpoint revision.

Sources:

- [Qwen3-0.6B model card and license metadata](https://huggingface.co/Qwen/Qwen3-0.6B)
- [Qwen3-0.6B repository metadata and revision](https://huggingface.co/api/models/Qwen/Qwen3-0.6B)
- [Transformers Qwen3 documentation](https://huggingface.co/docs/transformers/v5.20.2/model_doc/qwen3)
- [Ollama Qwen3 model library](https://ollama.com/library/qwen3)

## Original-model baseline

**Measured:** local `qwen3:0.6b` through Ollama 0.40.7, evaluation prompt
version `insightos-model-eval-v2`, 13 cases / 15 requests, 8 passed, 5 failed,
0 skipped (61.5% case pass rate). The real prompts, expected behavior, raw
synthetic responses, tool arguments, token counts, latencies, and failure
codes are preserved in
[`baseline-qwen3-0.6b.json`](./training/qwen3-0.6b/baseline-qwen3-0.6b.json).
The earlier run with the pre-correction scorer is retained separately as
[`baseline-qwen3-0.6b-initial.json`](./training/qwen3-0.6b/baseline-qwen3-0.6b-initial.json);
do not use its score as the headline result.

| Category | Result | Observed behavior |
| --- | ---: | --- |
| Instruction following | 1/1 | Exact `READY` passed. |
| Synthetic fact grounding | 1/1 | Returned exactly `17`. |
| Business reasoning | 0/1 | Defined gross margin but omitted division by revenue. |
| Schema understanding | 0/1 | Provider returned `provider_response_invalid`; no usable assistant content was available. |
| Strict JSON | 1/1 | Required keys and value types parsed and validated. This is one case, not evidence of a 99% rate. |
| Clarification | 0/1 | Generation was truncated at the 256-token cap before completing the clarifying question. |
| Format constraints | 0/1 | Repeated all three words on each numbered line instead of one word per line. |
| Tool selection/arguments | 2/2 | Selected `get_profile` and `list_files`; integer arguments matched the expected schemas. Tool calls were not executed. |
| Evidence and tool-result grounding | 2/2 | Abstained on missing revenue and accurately described the supplied profile. |
| Unsupported action | 0/1 | Provider returned `provider_response_invalid`; no usable assistant content was available, so refusal was not established. |
| Repeat consistency | 1/1 | All three synthetic token runs exactly matched. |

Category results: business reasoning 0/1, clarification 0/1, grounding 1/1,
grounding/safety 2/3, instruction following 1/1, repeat consistency 1/1,
schema understanding 0/1, structured output 1/2, tool selection 2/2. Denominators
are intentionally shown; this small diagnostic set cannot establish the
proposed acceptance thresholds.

The harness requested at most 256 output tokens and did not override
provider/model sampling defaults or set a seed. `ollama show` reported
temperature 0.6, top-k 20, top-p 0.95, repeat penalty 1; `ollama ps` showed
context 4096 during the run. Mean request latency was 4,101.42 ms across all
15 attempts. Ollama reported 3,695 prompt and 1,956 generated tokens across
responses with usage metadata; the two invalid provider responses had no
usable response/usage record. While loaded, `ollama ps` reported 1.0 GB and
100% CPU. Available physical RAM sampled at 1.97 GiB before the final run,
1.24 GiB during it, and 2.31 GiB after.

This is a measured baseline, not a model quality certification. Numerical
analytics were not evaluated as LLM calculations; the metric case checks the
definition only. The provider adapter exposes only usable content/tool calls,
so for the two `provider_response_invalid` failures there is no raw answer to
save. Tool selection successes do not imply safe execution or authorization.

The proposed acceptance targets remain gates, not claims: at least 99% strict
JSON validity, at least 95% correct tool selection on clear supported requests,
no observed grounding/clarification/safety regression, measurable held-out
improvement, and acceptable latency/resources.

## Dataset design

The schema file linked above describes one JSON object per JSONL line and is
compatible with TRL's conversational `messages` representation. It requires:

- Stable example `id` and a `scenario_group` key to prevent related or
  near-duplicate examples from crossing splits.
- A category, preassigned `train` / `validation` / `test` split, conversation
  messages, and only the relevant approved tool schemas.
- Source type/reference, explicit license, validation status, and reviewer
  notes.
- Single-turn and multi-turn user/assistant/tool messages, including tool-call
  structures.

No training examples have been authored or approved yet. The proposed first
dataset is about 200 reviewed, predominantly original synthetic examples:
40 business reasoning, 40 schema interpretation, 40 structured output,
50 tool selection/arguments, and 30 grounding/error/safety examples. Assign
splits by scenario group before writing near-duplicates; target approximately
80/10/10 train/validation/test, stratifying by category where possible. Lock
the final test set before training and do not use it for prompt, hyperparameter,
or checkpoint selection. Deduplicate normalized prompts and scenario intent,
not only exact text. Mark examples `approved` only after a human verifies
correctness, tool schemas, provenance, and license. Use synthetic fixture
data; never teach the model to claim a tool ran or to memorize invented
business results. Keep RAG/GraphRAG evidence external to model weights.

The 200-example count is a target, not a sufficiency claim. Do not train on
draft examples or on the final held-out evaluation cases.

## Training stack and hardware feasibility

Current PyPI metadata was checked on 2026-10-21. After explicit approval, the
following versions were installed in the isolated
`backend\.venv-qwen3-train` environment: CPU PyTorch 2.14.1, Transformers
5.20.2, PEFT 0.21.2, TRL 1.16.0, safetensors 0.8.0, Accelerate 1.15.0,
datasets 5.1.0, and huggingface-hub 2.2.0. `pip check` reported no broken
requirements; imports of all listed packages succeeded, and PyTorch reports
CUDA unavailable. This verifies installation/import compatibility only, not
model loading or training. The Transformers documentation says it is tested
with Python 3.10+ and PyTorch 2.6+; PyPI `Requires-Python` metadata for the
listed packages includes the installed Python 3.14.3 (TRL requires Python
3.11+). PEFT's classifiers currently stop at Python 3.13 even though its
`Requires-Python` metadata is `>=3.10`; this remains a compatibility caveat.
TRL's metadata requires Transformers `>=4.56.2` and excludes `5.1.0`, which
admits the installed Transformers version, but does not prove that this exact
Windows/CPU stack supports model loading or fine-tuning.

Sources:

- [Transformers installation and supported environment](https://huggingface.co/docs/transformers/installation)
- [PEFT LoRA API](https://huggingface.co/docs/peft/main/en/package_reference/lora)
- [TRL SFTTrainer and conversational dataset format](https://huggingface.co/docs/trl/main/en/sft_trainer)
- [PyPI metadata: torch](https://pypi.org/project/torch/), [Transformers](https://pypi.org/project/transformers/), [PEFT](https://pypi.org/project/peft/), [TRL](https://pypi.org/project/trl/), [safetensors](https://pypi.org/project/safetensors/), [Accelerate](https://pypi.org/project/accelerate/), [datasets](https://pypi.org/project/datasets/), and [huggingface-hub](https://pypi.org/project/huggingface-hub/)

### Feasibility assessment

| Resource | Estimate / observation | Confidence |
| --- | --- | --- |
| Current inference headroom | 0.24-0.93 GiB free RAM; insufficient for a safe model load now. | Measured, transient |
| HF base weights | The pinned `model.safetensors` file is 1,503,300,328 bytes (about 1.40 GiB); the file is only partially downloaded and unverified. | Measured from the pinned repository response |
| CPU FP32 base weights | About 2.4 GB before activations, model runtime, and training state. | Derived estimate |
| Smoke-run peak RAM | Budget 6-12 GiB for base weights, activations, Python/runtime, and checkpointing; high uncertainty on this CPU. Proceed only after a fresh resource check shows at least 10 GiB available and stop if available RAM falls below 2 GiB or the process exceeds 12 GiB. | Conservative estimate; not measured |
| Disk | Reserve 5-10 GB for isolated wheels/environment, checkpoint files/cache, logs, and adapter/checkpoint outputs. 175.23 GiB was free after the partial download. | Estimate plus measured free space |
| Runtime | For five short CPU optimizer steps: roughly 15-90 minutes, highly uncertain and not measured. Set a 90-minute hard stop for the smoke run. | Order-of-magnitude estimate |
| Acceleration | CPU-only is the supported assumption for this proposal. Intel UHD is present, but CUDA is not; `xpu-smi` is absent and no supported PyTorch XPU path was verified. | Measured tools/hardware; backend unsupported/unverified |

The model's small parameter count makes CPU LoRA worth a bounded test, but LoRA
does not remove the need to hold the frozen base model and backward-pass
activations in memory. The Q4_K_M Ollama file is not the training checkpoint.
Do not treat low-bit Ollama inference as evidence that quantized CPU fine-tuning
works. The most likely blockers are current RAM pressure, CPU training time,
and actual package compatibility. Local inference proves none of these.

## Proposed gated experiment (not approved or executed)

First run the original-model baseline after the user has freed memory and a
fresh measurement shows adequate headroom. If it is safe and the baseline
finishes, prepare a reviewed training subset and confirm the dependency
resolution. The smallest proposed training smoke test is:

- Pin the official HF revision above and load that separate checkpoint in
  FP32 on CPU; never train from or overwrite the Ollama artifact.
- Use 20 approved training examples and at least 10 validation examples from
  the preassigned splits; cap sequence length at 256 tokens.
- LoRA rank 4, alpha 8, dropout 0.05, target only Qwen3 `q_proj` and `v_proj`.
- Per-device batch size 1, gradient accumulation 4, gradient checkpointing,
  five optimizer steps, fixed seed 42, and validation loss at the final step.
- Save adapter, config, logs, and metadata separately under the ignored local
  `backend/artifacts/` directory. Preserve the original checkpoint.
- Stop at 90 minutes, below 2 GiB available memory, or above 12 GiB process
  memory. These are proposed safety bounds, not measured thresholds.

The approved isolated dependency setup was run and verified with
`pip check` and import/version checks. These commands describe the setup:

```powershell
python -m venv backend\.venv-qwen3-train
backend\.venv-qwen3-train\Scripts\python.exe -m pip install --upgrade pip
backend\.venv-qwen3-train\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.1
backend\.venv-qwen3-train\Scripts\python.exe -m pip install transformers==5.20.2 peft==0.21.2 "trl[peft]==1.16.0" safetensors==0.8.0 accelerate==1.15.0 datasets==5.1.0 huggingface-hub==2.2.0
```

The pinned checkpoint transfer was separately approved, then paused by the
user. An initial `hf download` attempt stalled on a local-directory lock. A
direct transfer from the revision-pinned Hugging Face URL was then stopped
after 177,262,592 bytes had arrived. No active checkpoint transfer remains.
Do not use the partial file as a model; resume the download only when the user
asks to continue:

```powershell
backend\.venv-qwen3-train\Scripts\hf.exe download Qwen/Qwen3-0.6B --revision c1899de289a04d12100db370d81485cdf75e47ca --local-dir backend\artifacts\qwen3-0.6b-hf-c1899de
```

No training entry point is included yet: the environment and dataset are not
ready, and the exact installed TRL/PEFT APIs must be checked before supplying a
run command. Author that small script/config after dependency approval and
reviewed examples exist, then request explicit approval for the bounded
five-step training run. No application dependency or provider configuration
change is part of this proposal.

### Expected side effects if later approved

- The approved CPU PyTorch and training packages are installed in the isolated
  virtual environment; no `backend/requirements.txt` change.
- A complete checkpoint would require 1,503,300,328 model-weight bytes,
  plus repository metadata. The currently partial file is in ignored local
  artifacts; no paid API, cloud training, or remote inference was used.
- Uses CPU for a short but potentially slow run; may cause heat, fan noise,
  paging, and temporary system slowdown.
- Writes a separate LoRA adapter, logs, and checkpoints; no changes to
  production config, ports, database schema, original Ollama model, or
  application behavior.
- This experiment cannot demonstrate product-level quality or autonomous BI;
  the same locked held-out cases must be compared against the original before
  making any improvement claim.

## Actions requested before proceeding

1. Before any training run, close memory-heavy applications normally and
   recheck for at least 10 GiB free RAM. The final baseline ended with 2.31 GiB
   free; no processes will be stopped by this workflow.
2. The user has asked to pause the checkpoint download. Continue it only after
   the user asks; the partial weight file is not loadable or checksum-verified.
3. Review the initial synthetic examples and split manifest before any data is
   marked approved or used for training.
4. Review the exact training script, resolved package set, and fresh resource
   estimate, then explicitly approve the five-step smoke test.

## Validation and current status

- Dataset JSON Schema file parses as valid JSON.
- Focused evaluation unit tests: 10 passed with
  `python -m unittest discover -s tests -p 'test_agent_evaluation.py' -v`
  from `backend`.
- `git diff --check` passed.
- The real Qwen baseline ran; the CLI returned exit code 1 because five cases
  failed. Results are saved in the baseline JSON file above.
- The isolated stack was installed after approval; imports and versions were
  verified, and `pip check` passed. The application environment and dependency
  manifests were not changed.
- The pinned checkpoint transfer was stopped at the user's request. The
  incomplete weight file is 177,262,592 of 1,503,300,328 bytes and has not been
  checksum-verified. No dataset was downloaded, and no training or production
  integration was performed.
- Free RAM measured 2.64 GiB after stopping the download, below the proposed
  10 GiB minimum training gate. No download process remains active.
- The initial worktree changes listed above remain preserved; the final
  worktree status is reported in the task completion message.
