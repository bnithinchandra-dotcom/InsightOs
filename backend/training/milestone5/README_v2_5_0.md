# InsightOS Qwen3-8B Runtime Evaluation v2.5.0

## Deliverables

- [Kaggle evaluation notebook](./notebooks/InsightOS_Qwen3_8B_Runtime_Evaluation_v2_5_0.ipynb)
- [Kaggle input ZIP](./InsightOS_Qwen3_8B_Runtime_Evaluation_v2_5_0_Kaggle_Inputs.zip)
- [Evaluation report](./Runtime_Evaluation_Report_v2_5_0.md)

## What changed

The v2.5.0 notebook retains v2.4.0's constrained-decoding and generation-loop hardening and expands the source-grounded runtime simulation. It probes persisted scheduler attempts and retryability, operation-specific retry budgets, completed-operation replay protection, final-response enforcement, clarification continuation identity, checkpoint boundaries, and specialist handoffs that remain undispatched.

The ZIP contains 28 SHA-256-pinned inputs, including the current runtime/scheduler/graph source and regression tests. The canonical v4 benchmark, gold responses, baseline bundle, and Qwen3-8B model revision are unchanged. The notebook verifies every input and the input manifest before evaluation.

## Running on Kaggle

1. Extract the ZIP contents into a Kaggle Dataset without changing the directory structure.
2. Attach that Dataset to the notebook; the expected mounted slug is `insightos-qwen3-8b-runtime-evaluation-2-5-0`.
3. Enable a supported Kaggle GPU and run the notebook from the first cell.
4. Review all 12 cases and complete the exported human-review CSV.

No pinned-model inference was run while preparing these files. The notebook performs no production tool, engine, database, checkpoint-task, or specialist dispatch. Its automated runtime probes are simulations, not production execution evidence.

No existing work was overwritten and no commits were created.
