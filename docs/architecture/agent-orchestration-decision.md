# Agent orchestration and model architecture decision

**Status:** Accepted direction; implementation planned  
**Decision date:** 2026-11-05  
**Scope:** InsightOS agent orchestration and local Qwen model responsibilities

## Context

InsightOS already has a bounded agent runtime with a provider-neutral model adapter, model router and registry, specialist stages, a bounded tool registry, typed handoffs, workflow execution persistence, and model lifecycle management. The current assistant executes its specialist stages sequentially through the configured provider for supported engine operations. This is an existing foundation, not yet the target multi-model orchestration architecture.

The local Ollama installation contains these model tags:

- `qwen3:8b`
- `qwen3:4b`
- `qwen3:4b-instruct`
- `qwen3:0.6b`

On 2026-11-05, connectivity to the Windows Ollama service at `http://host.docker.internal:11434` was verified from both the InsightOS backend and worker containers. This verifies network reachability and model listing only; it does not establish end-to-end generation through the InsightOS agent runtime. The worker currently idles and is not a job consumer, so this decision does not assign it model-execution responsibilities.

## Decision 1: primary and supporting models

Qwen3-8B is the **primary model** and the central reasoning model for InsightOS.

Its intended responsibilities are:

- Interpret user requests and business context.
- Plan analytical workflows and decide which capabilities are needed.
- Coordinate specialist agents and registered tools.
- Review specialist outputs and reconcile their results.
- Synthesize the final user-facing explanation.

Qwen3-4B is a **supporting specialist model** for data intelligence and analytics.

Its intended responsibilities include:

- Assist with understanding datasets, schemas, and business context.
- Support analytical reasoning and interpretation.
- Assist with modelling-related explanations and structured specialist handoffs.

Qwen3-0.6B is a **supporting presentation and visualization model**.

Its intended responsibilities include:

- Assist with chart and visualization selection.
- Produce bounded dashboard/presentation configuration proposals.
- Assist with layout and presentation formatting.

The supporting models do not replace the primary model. They perform delegated specialist work when the request benefits from it. Not every request needs every model; routing should avoid unnecessary model calls.

The installed `qwen3:4b` and `qwen3:4b-instruct` tags are both available locally. The exact 4B variant to assign to the data-intelligence/analytics role remains an implementation choice to validate before activation. Model role assignments are intended responsibilities, not claims that all model-specific behavior has passed InsightOS acceptance tests.

## Decision 2: LangGraph orchestration

LangGraph is the intended orchestration/control-flow layer for coordinating the primary model, specialist agents, and registered tools.

The target flow is:

1. Receive and validate the user request and its available project/dataset context.
2. Let Qwen3-8B interpret the request and create a bounded execution plan.
3. Route only the required work to the relevant specialist or deterministic tool.
4. Validate specialist handoffs, tool arguments, source scope, and evidence references.
5. Return control to Qwen3-8B to review the results and synthesize the response.
6. Persist execution state, task outcomes, and provenance through InsightOS-owned contracts.

Conditional routing is preferred over invoking every specialist for every request. Deterministic data operations remain authoritative for computed measurements; model-generated text must not replace verified engine outputs or provenance.

## Decision 3: preserve InsightOS domain capabilities

LangGraph should coordinate existing InsightOS capabilities rather than duplicate them.

The integration must preserve and reuse, where appropriate:

- Existing specialist-agent responsibilities and typed handoff contracts.
- The bounded tool registry and its explicit argument schemas.
- Deterministic analytics, EDA, forecasting, schema, and reporting engines.
- Evidence and claim validation, source freshness checks, and provenance.
- Workflow execution persistence, idempotency, and explicit recovery behavior.
- Provider adapters, model registry/router, and model lifecycle management.
- Existing API contracts and frontend conversation workflow.

The current custom orchestration flow must first be mapped against the proposed graph. The exact replacement/migration boundary is not yet finalized. Do not remove the existing orchestrator or rewrite workflow persistence merely because LangGraph is being introduced. Prefer a staged integration with compatibility tests and a reversible migration.

LangGraph checkpointing must be designed to work with InsightOS workflow identity, ownership, idempotency, and recovery semantics. Do not introduce a second independent source of truth for workflow status without an explicit migration design.

## Decision 4: local resource and execution constraints

The initial deployment remains local-first and uses Ollama on the Windows host, reachable from Docker services through `host.docker.internal:11434`.

Preserve the existing model lifecycle manager and its memory-conscious loading behavior. Do not assume all three models can remain resident simultaneously. Model loading, unloading, timeouts, token limits, and concurrency must remain bounded and observable.

The worker is not currently a job-processing service. LangGraph integration must not silently assume that it can execute background jobs. Initial graph execution should use an explicitly supported execution boundary; asynchronous/background execution is a separate future capability.

## Implementation sequence

1. **Map the current runtime:** document the current request path, orchestrator, specialist stages, tool dispatch, workflow persistence, recovery, and lifecycle management.
2. **Integrate LangGraph incrementally:** add a small graph around the existing contracts, with bounded state and explicit conditional routing. Keep existing API behavior compatible.
3. **Prove one end-to-end path:** run a representative request through Qwen3-8B, one existing specialist/tool, validation, persistence, and the final response.
4. **Integrate supporting models:** assign Qwen3-4B to data intelligence/analytics and Qwen3-0.6B to presentation/visualization after their role-specific integration tests.
5. **Test whole-application workflows:** cover success, partial results, tool errors, invalid handoffs, timeouts, retries/recovery, persistence, and model lifecycle transitions.
6. **Improve models after integration testing:** use observed application failures to guide later evaluation and fine-tuning. Personal-project training data and private artifacts must not be published in the open-source repository.
7. **Then optimize:** improve UI/UX, performance, ML capabilities, tools, security, and deployment based on measured application behavior.

## Acceptance expectations

The LangGraph integration is not considered complete merely because the package is installed or a graph compiles. Acceptance requires:

- A real InsightOS request traverses the graph.
- Qwen3-8B acts as the primary planner/reasoner.
- Supporting specialists are invoked only when appropriate.
- Deterministic tools remain the source of computed facts.
- Typed state and handoffs are validated at graph boundaries.
- Failures and partial outcomes remain explicit.
- Workflow persistence, idempotency, ownership, and recovery remain correct.
- Model lifecycle and resource limits remain enforced.
- Existing API and frontend workflows continue to work.

## Current status and non-goals

This document records the agreed architecture direction; it does not claim LangGraph is installed or integrated. The current backend requirements file does not yet declare LangGraph. No runtime code, model assignment, Docker configuration, database schema, or training dataset is changed by this decision record.

This decision does not authorize publishing personal training data, enabling arbitrary code/SQL/shell tools, moving source datasets to a remote model provider, or treating model output as verified analytical evidence.
