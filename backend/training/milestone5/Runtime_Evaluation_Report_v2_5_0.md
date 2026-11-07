# Runtime Evaluation Package Report — v2.5.0

## Outcome

Prepared the v2.5.0 Kaggle notebook and input package from the supplied v2.4.0 archive. This is a reproducible evaluation package, not a completed Qwen3-8B model run: Kaggle GPU inference and human review remain pending.

## Runtime and evaluation coverage

- Preserved the canonical 12-case orchestration benchmark v4, baseline bundle v6, gold responses v1, and pinned `Qwen/Qwen3-8B` revision `b968826d9c46dd6066d109eabc6255188de91218`.
- Added source-derived notebook probes for persisted retry states, input-specific retry identity, unknown retry eligibility, clarification continuation protection, completed engine-operation replay rejection, final-response shortening, and specialist handoff non-dispatch.
- The notebook retains the v2.4.0 constrained-decoding and generation-loop hardening. It keeps gold and benchmark examples out of model prompts and records that no production tool, engine, database mutation, checkpoint task, or specialist was dispatched.
- Packaged 28 SHA-256-pinned inputs, including runtime, scheduler, graph, checkpointer, and related regression-test snapshots. The manifest also records the unchanged hashes for all canonical benchmark artifacts.

## Verification

- Focused runtime, scheduler, API, and graph tests: **177 passed**, 2 existing Pydantic deprecation warnings, and 10 subtests.
- The test process used a one-line runtime bootstrap for the pre-existing `_XmlNode` forward-annotation import error in [parsing.py](../../app/parsing.py). That unrelated source file was not changed.
- Notebook code cells passed Python syntax validation.
- All **8/8** notebook runtime simulation probes passed when executed locally against the packaged source snapshot.
- All **28** input hashes and the manifest pin matched; ZIP CRC passed; the benchmark/gold coverage remained exactly 12 cases.
- No Kaggle inference or model-quality result is claimed.

Relevant regression coverage includes [test_runtime_corrections.py](../../tests/test_runtime_corrections.py), [test_workflow_execution.py](../../tests/test_workflow_execution.py), [test_agent_api.py](../../tests/test_agent_api.py), [test_agent_runtime.py](../../tests/test_agent_runtime.py), [test_langgraph_orchestrator.py](../../tests/test_langgraph_orchestrator.py), and [test_langgraph_milestone1.py](../../tests/test_langgraph_milestone1.py). PostgreSQL integration tests requiring a live database were not run.

## Worktree handling

The repository already contained unrelated modified and untracked files. They were preserved. No commits were created.
