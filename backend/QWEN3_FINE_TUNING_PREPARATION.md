# InsightOS Qwen3-0.6B Fine-Tuning Preparation

**Reviewed:** 2026-10-22

**Status:** The isolated stack is installed and verified. The local
Hugging Face Qwen3 baseline ran on 2026-10-22; no fine-tuning or dataset
download was performed. The earlier checkpoint transfer was paused while
partial, but a later read-only inspection found the complete checkpoint, and
the inference run loaded it successfully. The original raw run report remains
unchanged; an offline rescore using a later scoring version is recorded
separately below.

## Executive summary

The local Ollama model is present, but it is a quantized inference artifact and
is not the Hugging Face checkpoint required for Transformers/PEFT training.
An earlier synthetic-only baseline was run against the separate Ollama
`qwen3:0.6b` artifact: 13 cases, 8 passed, 5 failed. This is not the HF
checkpoint baseline. The HF model was subsequently evaluated locally with the
separate seven-case suite: 1 passed, 3 failed, and 3 pending human review in
the original scorer run. Offline rescoring later identified one scorer false
negative and a likely token-limit truncation; it did not regenerate model
outputs. The two HF reports and their scoring-version distinction are
documented below.

CPU LoRA fine-tuning is **plausible but not yet demonstrated** on this laptop.
During the earlier Ollama baseline, free RAM sampled 1.97 GiB before model
load, 1.24 GiB while loaded, and 2.31 GiB after completion. During the HF
baseline, it was 3.28 GiB before and 4.16 GiB after; peak use was not
measured. The isolated CPU training stack was installed and verified after
user approval. No training or dataset download was performed.

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

The application-provider evaluator in
[`app/agent/evaluation.py`](./app/agent/evaluation.py) has 13 synthetic case
types covering instruction following, fact grounding, business metric
definition, schema interpretation, typed strict JSON, abstention,
clarification, format constraints, allowlisted tool selection and arguments,
tool-result grounding, unsupported actions, and three-run consistency. It
uses the existing read-only tool schemas and never executes model tool calls.
It evaluates the configured Ollama or OpenAI-compatible provider, not a local
Transformers checkpoint.

The separate local Hugging Face baseline runner is
[`training/evaluation/hf_runner.py`](./training/evaluation/hf_runner.py), with
the versioned synthetic suite in
[`training/evaluation/suite_v1.json`](./training/evaluation/suite_v1.json).
It loads only the pinned local checkpoint, uses greedy CPU inference with the
Qwen chat template and thinking disabled, and does not call application
providers or execute tools. Machine-checkable dimensions and pending human
review are reported separately. Runtime errors are retained per case and
later cases continue. Reports are written with exclusive-create semantics
under ignored `backend/artifacts/evaluation-results/`, so an existing report
cannot be overwritten. The old Ollama baseline script and application
evaluator remain unchanged. Unit tests use synthetic fake generations.
Checkpoint inference was performed once; no training occurred.

After explicit approval for a local inference run, invoke it from the
repository root with the isolated training interpreter:

```powershell
backend\.venv-qwen3-train\Scripts\python.exe backend\training\evaluation\hf_runner.py
```

This loads the local checkpoint once and writes a uniquely named JSON report
under ignored `backend\artifacts\evaluation-results\`. Use `--output` only
with a new path; the runner refuses to overwrite an existing report. To
rescore a saved report without loading the model or running inference:

```powershell
backend\.venv-qwen3-train\Scripts\python.exe backend\training\evaluation\hf_runner.py `
  --rescore-report backend\artifacts\evaluation-results\hf-qwen3-0.6b-5ebfc2a5dc484f3ea8dc69ddb759f235.json
```

The original HF run and its pre-correction scores remain immutable; the
offline rescore writes a new report containing the source checksum, original
statuses, corrected dimensions, and the explicit `inference_performed: false`
marker.

### First HF run and offline rescore

The immutable first run is
`artifacts/evaluation-results/hf-qwen3-0.6b-5ebfc2a5dc484f3ea8dc69ddb759f235.json`.
Its SHA-256 is
`2E8EC217C46A24886E193C49A596A5BAFDD901E3C1C942DCC4D3640E4CA04EB7`.
The complete, authoritative corrected-score report is
`artifacts/evaluation-results/hf-qwen3-0.6b-rescored-47bf322d051541fa8c4fa87c4b8e350f.json`.
An earlier offline rescore was also written without overwriting it; it is
retained as an intermediate, while the report named here additionally carries
the source run timestamp, model-load duration, and explicit tokenizer
metadata.
It records source SHA-256
`2e8ec217c46a24886e193c49a596a5bafdd901e3c1c942dcc4d3640e4ca04eb7`,
the pinned checkpoint revision, Python/PyTorch/Transformers/tokenizer
versions, model-load duration, tokenizer package/revision, generation
settings, original status for each case, per-generation raw output and
timing, and the `insightos-hf-scoring-v2` scoring version.
The raw generations in the new report were verified to match the original
report identically as parsed JSON values.

| Result | Original scoring | Offline scoring v2 |
| --- | ---: | ---: |
| Passed | 1 | 1 |
| Failed | 3 | 2 |
| Needs human review | 3 | 4 |
| Errored / skipped | 0 / 0 | 0 / 0 |

The `missing_churn_inputs` output already said the information was incomplete
and churn was impossible to calculate. The original phrase matcher missed
“impossible to calculate” and incorrectly failed that response. Scoring v2
recognizes that paraphrase and marks the deterministic abstention checks as
passed, while leaving semantic confirmation at `needs_review`; it does not
award an invented churn percentage. This false negative matters because it
misattributes an evaluator defect to the model and would distort a before /
after comparison.

For `json_profit_margin`, scoring v2 separately records parse validity,
embedded JSON validity, required/exact keys, field types/schema, metric,
numeric tolerance, unit, and JSON-only formatting. The generated object has
the right fields and calculation inside Markdown fences, so its calculation
passes while strict JSON-only compliance fails. Syntactically valid JSON and
semantically correct content are separate properties: a parseable object can
still contain a wrong answer, and a right answer can violate the requested
format.

The exact-two-sentence check splits on terminal `.`, `!`, or `?` followed by
whitespace and checks that two non-empty sentences end in terminal
punctuation. The sampled answer is one sentence, so it fails that
machine-checkable instruction. This heuristic can miscount abbreviations,
decimals, ellipses, or unconventional punctuation; it does not judge whether
the explanation is true.

The planning response ended at 192 generated tokens with “Sub”, an
unfinished section heading. The original report predates termination metadata,
so the rescore labels this **likely** truncated at the token cap, not
confirmed. A later inference run records generated token IDs and EOS versus
`max_new_tokens`; EOS is a model stop token, while reaching the configured
token cap is independent evidence of a length-limited stop. Text length alone
cannot reliably establish truncation.

Model quality and scorer quality must be tracked independently: deterministic
checks catch format/count conditions, while semantic cases remain for human
review. These fixed synthetic prompts, pinned model revision, generation
settings, and scoring version provide a repeatable comparison protocol for a
future approved fine-tuning evaluation. Rescoring stored generations corrects
the evaluator's classification; it is not a model rerun and cannot establish
improved model quality.

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

Environment details were observed on 2026-10-21 and 2026-10-22; memory values
are point-in-time and varied during inspection:

| Item | Observation |
| --- | --- |
| OS/runtime | Windows; Python 3.14.3 |
| CPU/RAM | Intel Core i7-13620H, 10 cores / 16 logical processors; 15.7 GiB visible RAM |
| Free physical RAM | Earlier Ollama baseline samples were 1.97 GiB before, 1.24 GiB while loaded, and 2.31 GiB after. Before the HF baseline it was 3.28 GiB and 4.16 GiB after; peak HF-run memory was not measured. |
| Free space | 175.23 GiB on `C:` after the partial checkpoint transfer |
| Graphics | Intel UHD integrated graphics; no CUDA/NVIDIA GPU; `xpu-smi` unavailable |
| Ollama | 0.40.7; during baseline `ollama ps` reported Qwen at 1.0 GB, 100% CPU, context 4096 |
| Installed local models | `qwen3:0.6b` and `gemma3:1b` |
| Base Python training packages | Pre-existing PyTorch 2.14.0 and `datasets` 5.0.1; unchanged |
| Isolated training environment | `backend\.venv-qwen3-train`; PyTorch 2.14.1+cpu, Transformers 5.20.2, PEFT 0.21.2, TRL 1.16.0, safetensors 0.8.0, Accelerate 1.15.0, datasets 5.1.0, huggingface-hub 2.2.0. Imports succeeded and `pip check` found no broken requirements. |
| Hugging Face Qwen checkpoint | Complete file present at 1,503,300,328 bytes and loaded successfully by the local HF evaluator. SHA-256 observed during the later audit: `F47F71177F32BCD101B7573EC9171E6A57F4F4D31148D38E382306F42996874B`. |

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
is 1,503,300,328 bytes and is now complete; a later local HF inference run
loaded it successfully. The Hugging Face revision is not the
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
| Current inference headroom | At the earlier 2026-10-21 assessment, 0.24-0.93 GiB free RAM was insufficient for a safe model load. Later, the baseline ran with 3.28 GiB free before load; no peak-memory measurement is available. | Measured, transient |
| HF base weights | The pinned `model.safetensors` file is complete at 1,503,300,328 bytes (about 1.40 GiB) and was loaded by the HF baseline. | Measured locally |
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
user. At that time an initial `hf download` attempt stalled on a
local-directory lock, and a direct transfer was stopped after 177,262,592
bytes. A later inspection found the checkpoint complete and the HF baseline
loaded it successfully. No further download is needed:

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
- The checkpoint is in ignored local artifacts; no paid API, cloud training,
  or remote inference was used.
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
   recheck for at least 10 GiB free RAM. The HF baseline ended with 4.16 GiB
   free; no processes will be stopped by this workflow.
2. The checkpoint is complete and loaded for evaluation; do not download it
   again unless a later task requires it.
3. Review the initial synthetic examples and split manifest before any data is
   marked approved or used for training.
4. Review the exact training script, resolved package set, and fresh resource
   estimate, then explicitly approve the five-step smoke test.

## Validation and current status

- Dataset JSON Schema file parses as valid JSON.
- Application evaluator regression tests: 10 passed with
  `python -m unittest discover -s tests -p 'test_agent_evaluation.py' -v`
  from `backend`.
- Focused HF scorer/rescore tests: 13 passed with
  `python -m unittest discover -s tests -p 'test_hf_evaluation.py' -v`
  from `backend` using the isolated training Python.
- `git diff --check` passed.
- The HF Qwen baseline generated nine responses for seven synthetic cases
  (the repeatability case ran three times). The original report's scores are
  immutable; a separate offline rescore uses the stored generations and does
  not imply new inference.
- The isolated stack was installed after approval; imports and versions were
  verified, and `pip check` passed. The application environment and dependency
  manifests were not changed.
- The original checkpoint transfer was stopped at the user's request while
  partial; the checkpoint was later found complete and loaded by the local
  evaluator. Its observed SHA-256 is recorded in the environment table. No
  dataset was downloaded, and no training or production integration occurred.
- Free RAM after the HF baseline measured 4.16 GiB, below the proposed 10 GiB
  minimum training gate.
- The initial worktree changes listed above remain preserved; the final
  worktree status is reported in the task completion message.

## Reliability follow-up (offline changes only)

The local HF runner now supplies a concise, versioned analytical system prompt
that reinforces the financial definitions, evidence limits, clarification
behavior, exact output constraints, and the distinction between a proposed
plan and an executed tool. Its version and SHA-256 are recorded in future
generation reports. The seven-case suite and the historical raw report are
unchanged; rescoring that historical report does not retroactively apply this
new prompt.

The scorer now rejects duplicate-key and non-standard-constant JSON, keeps
JSON-only compliance distinct from embedded-value diagnostics, checks the
churn prompt's named count and period inputs, and flags only explicit
financial contradictions and required analysis-plan omissions. These checks
are deliberately narrow heuristics: a clean result stays in `needs_review`
when semantic review is required and is not a claim of general natural-language
understanding. Sentence counting recognizes a small set of common
abbreviations and decimal points, but remains a punctuation-boundary heuristic
that can miscount ellipses, initials, and unusual punctuation.

The application agent's system instructions now reinforce financial
definitions, clarification when definitions or inputs are missing, and
truthful separation of plans from observed tool results. Existing allowlisted
tool execution and authorization boundaries are unchanged.

No inference, training, fine-tuning, model or dataset download, or package
installation was performed as part of this reliability follow-up. A future
diagnostic must be explicitly authorized and should be treated as a new run
under the versioned prompt, not compared as if its generations came from the
historical baseline prompt.

Follow-up validation: `test_hf_evaluation.py` passed 18 tests,
`test_agent_runtime.py` passed 15 tests, and `test_agent_evaluation.py` passed
10 tests; the changed Python modules and tests compiled successfully. Offline
rescoring the immutable raw report produced 1 pass, 4 failures, and 2 cases
requiring human review. The rescore command returned its documented
non-success exit status because the stored generations contain failures; the
new report was still written successfully. Its source SHA-256 is
`b18b133c8056dd28bb38a566c006861c2d51b24dc366bd32a8b5bcc72d9107b1`.
The historical generations predate the system prompt above, so this rescore
evaluates them under the updated checks only and cannot measure the new prompt's
effect.

The follow-up prompt/scorer revisions are versioned as
`insightos-analytical-agent-v2` and `insightos-hf-scoring-v4`. The scorer keeps
the original generation text for acceptance checks; strict JSON parsing,
exact-key/type validation, and canonical JSON are diagnostics only. A fenced
or surrounded JSON object remains a JSON-only failure even when its parsed
object is valid. Profit and margin arithmetic is checked with decimal
calculations, and missing or zero revenue returns no margin rather than a
division result. Churn inputs inside JSON responses are reviewed independently
of their presentation format. Analysis-plan checks require date-column
validation, monthly aggregation, the profit and margin formulas, disclosure
that the schema was not supplied, and no unsupported execution claim.

The `repeat_exact_token` contract remains a complete-string requirement: each
of its three responses must be exactly `ORBIT-72`; matching wrappers or merely
containing the token do not pass. The HF runner already uses greedy decoding
(`do_sample=False`). The regular application runtime still returns provider
assistant text as text and does not accept a caller-supplied response schema;
provider JSON mode is available only to callers that explicitly set
`ModelRequest.response_format`. This offline change does not add a generic
application JSON contract without a caller-provided schema.

## Offline repair for the 2026-10-22 21:53 diagnostic

The seven-case suite and the diagnostic report
`artifacts/evaluation-results/hf-qwen3-0.6b-diagnostic-20261022-215347.json`
were left unchanged. The HF scorer now uses scoring version 4 and the system
prompt is version 2. Its JSON-only check parses duplicate-key-free standard
JSON, validates the exact expected object shape and types, and records a
sorted compact canonical representation separately; canonicalization does
not change the raw answer or make fences compliant. The expected margin is
cross-checked against the prompt inputs with decimal arithmetic. Missing or
zero revenue produces no margin. Input extraction for the churn case recognizes
JSON keys and values, but its status remains `needs_review` after successful
machine checks because semantic review is still required.

An in-memory rescore of the 21:53 report (no model loading, no report write)
still identifies four failures: fenced JSON, an analysis plan omitting date
column validation and disclosure that the source schema is unavailable, a
tool plan using revenue divided by total revenue, and three identical JSON
wrappers instead of the exact requested token. Business explanation,
two-sentence format, and missing-input cases remain `needs_review`; their
machine checks do not certify semantic correctness. This is not a fresh model
result and does not establish improvement.

Offline validation for this repair: 22 HF evaluator tests passed, 15 agent
runtime tests passed, and 10 fake-provider agent evaluator tests passed. AST
syntax checks passed for all changed Python files. No inference, model loading,
training, fine-tuning, package installation, external API call, or change to
the suite, historical report, checkpoint, or training environment occurred.

## Local Ollama Qwen3:4B diagnostic

The separate runner `training/evaluation/ollama_runner.py` reuses the seven-case
suite and current scorer, restricts requests to a local Ollama HTTP endpoint,
sets `think=false`, uses temperature 0 and a 192-token output cap, and enables
JSON mode only for `json_profit_margin`. It requires the model to already be
installed and does not pull models. Run it from `backend` with:

```powershell
.\.venv-qwen3-train\Scripts\python.exe -m training.evaluation.ollama_runner `
  --model qwen3:4b `
  --base-url http://localhost:11434 `
  --timeout-seconds 300 `
  --max-output-tokens 192 `
  --baseline-report artifacts\evaluation-results\hf-qwen3-0.6b-diagnostic-20261022-2203-reliability-v2.json
```

The 2026-10-22 run is saved separately at
`artifacts/evaluation-results/ollama-qwen3-4b-99554a4803e44a31987f103fa6de81d4.json`.
It produced 1 pass and 6 failures. JSON profit margin passed under constrained
JSON mode. The other six generations exhausted the 192-token cap; their output
included extensive prompt restatement/self-narration, and the exact-token
repetitions did not return the requested token. The analysis plan also omitted
date-column validation and schema-unavailable disclosure. Machine checks on
the churn response passed before truncation, but the full response did not
complete. The tool-plan execution-claim check was a false positive on the
phrase “without claiming I opened a file”; this false positive is corrected in
scoring version 6. The saved Ollama report faithfully records scoring version 5
as used during its run; its failed status is also caused by token-limit
truncation. Scoring version 6 also detects a percentage-scale error: the
truncated tool-plan output used `* 10` instead of `* 100`.

The prior saved HF diagnostic was generated with system-prompt version 2 and
scoring version 4. Rescoring its stored outputs during the run under scoring
version 5 gave 1 pass, 3 failures, and 3 needs-review cases; the negated-claim
false positive is corrected in version 6. The prompts/configurations
are not identical. In addition, this diagnostic used the dedicated evaluation
system prompt; the application runtime has its own system instructions, and
the existing `OllamaProvider` does not currently set `think=false` or
temperature 0. This is therefore a useful diagnostic, not a controlled claim
about production behavior or that model size alone caused the difference.
The available Ollama model was 2.5 GB and free physical memory was about
680 MiB before loading; inference completed with high CPU use and the model
was unloaded afterward. No production provider/default was changed. The
existing Ollama provider remains configurable via
`INSIGHTOS_LLM_PROVIDER=ollama` and `OLLAMA_MODEL`; these results do not
justify selecting Qwen3:4B as the application default.

After that run, the Ollama evaluation runner was changed offline to use a
shorter final-answer-only system instruction. It records only whether Ollama
returned a separate `message.thinking` field and its character count; it does
not persist that field's contents. A follow-up controlled run used this prompt
with the original seven prompts and scoring criteria, JSON mode only for the
JSON case, and a 192-token cap. It again produced 1 pass and 6 failures, all
marked truncated; the model still narrated its reasoning in `message.content`
and did not provide a separate `message.thinking` field.

The Ollama-only runner default is now 512 output tokens to address this
observed truncation. This does not change the HF runner or application
provider. It has unit-test coverage but has not yet been evaluated against the
model; the preceding report remains the 192-token run and is not evidence for
the new cap.

### Targeted analysis-plan follow-up (2026-10-24)

The targeted `analysis_plan_profit_margin` run is saved separately as
`artifacts/evaluation-results/ollama-qwen3-4b-instruct-analysis-plan-targeted-v10-20261024-0009.json`.
It used evaluation prompt v5, scoring v9, suite v2, temperature 0, and one
attempt. All 11 machine checks passed, but its status remains `needs_review`.
The response appropriately flags negative revenue for policy review and does
not automatically exclude it; however, it incorrectly calls division by
negative revenue mathematically undefined. Human review is still required.

The subsequent evaluation prompt v6 clarifies that zero revenue makes margin
undefined, while negative-revenue handling should be deferred to the relevant
business policy and division by a negative number is not itself undefined.
Deterministic prompt tests cover this wording, but prompt v6 has not been
validated by another model inference. The v10 response must not be attributed
to prompt v6.
