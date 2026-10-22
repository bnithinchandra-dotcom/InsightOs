# Open model foundation (Phase 5C)

**Audit date:** 2026-10-21
**Scope:** local inference provider contract, provider health, and a synthetic
model evaluation harness. This is not the agent-engine or autonomous-BI phase.

> **Environment follow-up:** The environment observations below are an earlier
> snapshot and are superseded by the [Qwen3 fine-tuning preparation report](./QWEN3_FINE_TUNING_PREPARATION.md).
> A follow-up on 2026-10-21 found Ollama 0.40.7 and `qwen3:0.6b` already
> installed, but no Hugging Face Qwen3 checkpoint. After memory was freed, the
> synthetic-only Qwen baseline ran; free physical RAM ranged from 1.24 GiB
> during generation to 2.31 GiB after the final run. The report records model
> identities, baseline results, resources, and remaining approval gates.

## Environment audit

The inspected checkout was on `main` at `5c16c8e` with an existing untracked
`.vscode/` directory. That directory was not read or changed. The machine
reported Windows 11, an Intel Core i7-13620H (10 cores / 16 logical
processors), Intel UHD integrated graphics, 15.7 GiB visible RAM, about
1.45 GiB free physical memory at inspection time, and about 183.6 GiB free on
`C:`. The free-memory reading is a point-in-time observation and should be
rechecked before any model is loaded.

Python 3.14.3 and the existing `httpx2`, FastAPI, and Pydantic dependencies
were available. The frontend dependencies were already present. No Ollama
executable, Ollama process/service, listener on port 11434, default Ollama
model directory, or cached Qwen model directory was found. No model weights
were downloaded. The runtime and weights required for real inference are
therefore absent from the inspected locations.

The backend already had Ollama and OpenAI-compatible adapters, a `ModelProvider`
protocol, bounded response parsing, conversation persistence, and a bounded
read-only tool boundary. The prior provider-status route reported configuration
only; it did not establish that a runtime or model was reachable.

## Candidate comparison and recommendation

Qwen3 is a short-list candidate, not a quality claim. The official Hugging Face
model metadata lists the Qwen3-0.6B and Qwen3-1.7B models as Apache-2.0, with a
32,768-token advertised context. Ollama's registry manifests currently list
the following model-layer sizes:

| Candidate | Parameters / advertised context | Ollama model layer | Assessment |
| --- | --- | ---: | --- |
| `qwen3:0.6b` | 0.6B / 32,768 tokens | 522,640,096 bytes (about 0.49 GiB) | **Primary smoke-test candidate.** Lowest storage and memory burden in this shortlist, but capability and useful latency on this CPU are unmeasured. |
| `qwen3:1.7b` | 1.7B / 32,768 tokens | 1,359,279,776 bytes (about 1.27 GiB) | **Fallback for later comparison.** More parameters, but this model layer alone is close to the machine's currently free physical memory; do not try it until more memory is available. |

Sizes are for the Ollama model layer, not total peak RAM. The runtime, context
cache, operating system, and other processes add memory use. The machine has no
discrete GPU reported, so the recommendation assumes CPU execution. No
throughput, latency, reasoning, JSON, or tool-call behavior has been benchmarked
on this laptop. Although both model cards advertise 32K context, that is not a
safe working context-size recommendation for this machine; the effective
context limit remains unverified. Start with short prompts and evaluate measured
latency and memory before increasing context.

**Recommendation:** after explicit approval to install a runtime and download
weights, start with `qwen3:0.6b` only as a smoke-test candidate. It is not
accepted as sufficiently capable for business analysis. Test `qwen3:1.7b` only
if memory headroom is first confirmed. Select a production model only after
real evaluation results justify it.

Sources checked 2026-10-21:

- [Qwen3-0.6B model card and license metadata](https://huggingface.co/Qwen/Qwen3-0.6B)
- [Qwen3-1.7B model card and license metadata](https://huggingface.co/Qwen/Qwen3-1.7B)
- [Ollama qwen3:0.6b registry manifest](https://registry.ollama.ai/v2/library/qwen3/manifests/0.6b)
- [Ollama qwen3:1.7b registry manifest](https://registry.ollama.ai/v2/library/qwen3/manifests/1.7b)
- [Ollama Qwen3 model library](https://ollama.com/library/qwen3)

The source model license is Apache-2.0; review the model and runtime licenses
again when selecting a specific version for redistribution or deployment.
The software/API spend for this phase and the proposed local setup is ₹0;
hardware, electricity, and any hosted infrastructure remain separate costs.

## Provider contract and status semantics

`ModelProvider` exposes provider-neutral completion and a health check.
Provider-specific HTTP payloads and response parsing remain inside the Ollama
and OpenAI-compatible adapters. Requests support role-based messages, bounded
output tokens, tool-call representation, and an optional JSON response mode.
The JSON mode requests a JSON object; it is not general JSON Schema-constrained
generation. Provider requests use configured timeouts, disallow redirects, cap
response bodies, and return sanitized errors. OpenAI-compatible authentication
uses the server-side configured API key; it is not returned by the API.

`GET /api/v1/chat/provider-status` now distinguishes:

- `not_configured`: provider selection, model, or required credentials are
  missing.
- `unavailable`: the runtime cannot be reached or the selected model is not
  listed by it.
- `failed`: invalid provider responses, rejected requests, or authentication
  failures prevented status verification.
- `healthy`: the runtime is reachable and reports the configured model.
- `invalid_configuration`: provider settings fail validation.

`healthy` is a runtime/model-presence check, **not** proof that text generation
or any model capability works. The response reports capabilities as
`supported`, `unsupported`, or `unverified`. Text generation, structured
output, tool calling, and local execution remain unverified until tested for
the actual model/runtime. Streaming is unsupported by these adapters.
Configured output-token limits are reported; context-window size is unknown.
No remote fallback occurs when a local provider is unavailable.

## Synthetic evaluation harness

Run from the `backend` directory after configuring an approved, reachable
provider:

```powershell
python -m app.agent.evaluate
```

The harness first checks runtime/model availability, then sends only synthetic
prompts. It tests exact instruction following, synthetic fact extraction,
business-metric definitions, schema interpretation, JSON object
shape/content, evidence abstention, clarification, exact line formatting,
synthetic tool selection and arguments, unsupported actions, and three-run
consistency. Each case reports latency and a sanitized failure category.
Truncation and malformed structured outputs are explicitly classified. Tool
calls are scored but never executed, and the harness never reads datasets.

At the initial audit, the real-model harness was **not run** because the
runtime and model were absent from the inspected locations. A later follow-up
found an already-installed local runtime and model and ran the 13-case
synthetic baseline; its results and limitations are recorded in the
[Qwen3 preparation report](./QWEN3_FINE_TUNING_PREPARATION.md). Mocked unit
tests are not real inference or model evaluation.

An earlier evaluation CLI run, before the harness was expanded to 13 cases,
used the provider explicitly set to `none`: 7 cases skipped with
`provider_not_configured`, 0 executed, 0 passed, 0 failed, and no latency
samples. Its exit code was 1, as expected when there is no provider; this is
not a model-evaluation pass.

## Approval-required local setup

For a machine without the local runtime and model, the following commands are
proposed only; they were **not executed** during the follow-up because the
existing Ollama runtime and Qwen model were already available:

```powershell
winget install --id Ollama.Ollama --exact
ollama pull qwen3:0.6b
ollama --version
ollama list
```

The model layer requires about 523 MB of download/storage; runtime installation
and model/runtime overhead require additional disk space. RAM use includes
model loading, context cache, and the runtime, and cannot be asserted precisely
without a live run. With only about 1.45 GiB free at audit time, first close
other memory-heavy programs and recheck available RAM. If the backend runs
inside Docker, `localhost` refers to that container; configure an address
reachable from it rather than changing Docker services or ports.

After approval and setup, configure the existing variable names in the backend
process (do not commit credentials):

```powershell
$env:INSIGHTOS_LLM_PROVIDER = 'ollama'
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
$env:OLLAMA_MODEL = 'qwen3:0.6b'
python -m app.agent.evaluate
```

Do not set the OpenAI-compatible provider unless intentionally choosing a
remote endpoint and accepting its data-transfer implications.

## Verification and limitations

Backend unit tests use fake providers and transports. They cover adapter
parsing, authentication handling, model-list health, sanitized provider
status, JSON-mode request shape, and evaluation criteria. These tests verify
application behavior only; they do not verify that Qwen3 runs, that the
provider's chat template makes tool calls, or that model responses are
analytically correct.

Validation on 2026-10-21: backend unittest discovery ran 404 tests (403
passed, 1 skipped); frontend Vitest ran 20 tests (all passed); frontend lint
and production build passed; Python `compileall` and `git diff --check` passed.
The Python `pytest` runner is not installed, so the repository's
`unittest`-compatible backend tests were run with `unittest` discovery rather
than installing test dependencies.

The existing conversation and deterministic analysis APIs remain in place.
The assistant must not be described as a completed autonomous agent, RAG or
GraphRAG system, fine-tuned model, or production-grade BI intelligence. Future
work should compare real model evaluations on synthetic fixtures and keep
model responses separate from deterministic result verification.
