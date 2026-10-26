# InsightOS BA/DA Diagnostic Benchmark v2

## Purpose and boundaries

This 60-case synthetic benchmark is for diagnosing Business Analyst and Data
Analyst workflow capabilities before fine-tuning. It is separate from the
seven-case local Hugging Face smoke suite and the provider-based application
evaluator. The benchmark helpers validate data, create mode-specific model
inputs, and score only deterministic observations they can verify. They do
not load a model, perform inference, connect to an application provider, or
execute model-generated SQL against any real database or invoke model tools.
The established `*_v1.json` filenames are retained for compatibility; the
benchmark document declares v2 and the reference document declares v3.

Prompt/input records are in `ba_da_diagnostic_v1.json`. Gold references and
review rubrics are separately stored in `ba_da_references_v1.json`. The
projection helper reads only the prompt document; it does not load the
reference document when constructing model-visible content.

## Categories and stable case IDs

There are exactly ten cases in each category:

| Category | Cases |
| --- | --- |
| Business Analyst knowledge and requirements reasoning | `ba_requirements_vs_solution`, `ba_stakeholder_authority`, `ba_retention_kpi`, `ba_order_bottleneck`, `ba_revenue_vs_profit`, `ba_churn_definition`, `ba_root_cause_correlation`, `ba_acceptance_criteria`, `ba_evidence_recommendation`, `ba_ambiguous_request` |
| Data Analyst knowledge and analytical workflows | `da_profile_quality`, `da_groupby_aggregation`, `da_one_to_many_join`, `da_eda_order`, `da_zero_vs_missing`, `da_timeseries_chart`, `da_segment_rates`, `da_weighted_vs_unweighted`, `da_target_leakage`, `da_insight_communication` |
| Arithmetic and business calculations | `calc_pct_change`, `calc_profit`, `calc_margin`, `calc_conversion_denominator`, `calc_weighted_avg`, `calc_region_ratio`, `calc_cagr`, `calc_units`, `calc_multistep_profit_margin`, `calc_zero_denominator` |
| Statistics and probability | `stat_mean_median_outlier`, `stat_population_sample`, `stat_iqr`, `stat_conditional_probability`, `stat_correlation_causation`, `stat_sampling_bias`, `stat_ci_interpretation`, `stat_pvalue`, `stat_test_selection`, `stat_multiple_comparisons` |
| Dataset reasoning and interpretation | `data_monthly_margin`, `data_sales_vs_margin`, `data_missing_required_column`, `data_unequal_group_sizes`, `data_outlier_handling`, `data_trend_vs_cause`, `data_missingness_impact`, `data_simpsons_paradox`, `data_misleading_aggregation_chart`, `data_forecast_uncertainty` |
| Tool use, instructions, and output reliability | `tool_json_only`, `tool_exact_sentence_count`, `tool_sql_group_query`, `tool_calculator_choice`, `tool_statistical_tool_choice`, `tool_arguments_valid`, `tool_trace_result_grounding`, `tool_failed_execution`, `tool_clarify_ambiguous_definition`, `tool_no_execution_claim` |

The cases cover missing and ambiguous inputs, incorrect denominators, weighted
averages, one-to-many joins, missing-versus-zero semantics, population and
sample variance, correlation and causation, confidence intervals, p-values,
multiple comparisons, SQL aggregation, invalid arguments, failed execution,
JSON-only output, and unsupported execution claims.

## Diagnostic modes

Each case declares the modes in which it may be presented:

1. **Base:** prompt and case inputs only; no added formula card or tools.
2. **Context-assisted:** prompt and inputs plus only the explicitly allowed
   controlled definition/formula cards. Cards provide general methods, not
   the case's numeric result or answer.
3. **Tool-assisted:** prompt and inputs plus only the listed tool contracts.
   Where appropriate, a case can include an explicitly visible observed tool
   trace, such as a failed SQL call. This benchmark contains no running tool
   adapter; the mode is a specification, not an instruction to execute a tool.

`model_input(case_id, mode)` enforces the case's mode permission. It returns
only the prompt, inputs, and material allowed for that mode. It does not return
case/family IDs, split labels, expected answers, reference calculations,
rubrics, scoring decisions, or hidden expected traces. The only trace exposed
is a case's declared visible trace in tool-assisted mode.

Paired comparisons can suggest where help came from: formula context may
improve a base response, or an observable tool trace may help isolate argument
or interpretation errors. They cannot prove an internal model capability or
diagnose a retrieval system. A correct tool-assisted calculation is not
evidence that the model can calculate unaided. A tool call or result shown in
these fixtures is not a real execution performed by this benchmark.

The `diagnostic_pairs` metadata names same-case comparisons for formula
context versus base calculation, population/sample definitions, statistical
interpretation context, SQL/calculator contract assistance, and visible
successful/failed execution traces. Improved performance with a formula card
may suggest a method-selection or context-use gap; it does not establish
retrieval quality. Correct tool-assisted results do not demonstrate unaided
arithmetic. Tool-trace pairs test use of observed state, not real execution.
These comparisons support diagnostic hypotheses, not causal conclusions.

## References and scoring

The separate reference file records an answer/rubric, any calculation,
units, tolerances, dimension rubric, scorer type, and review status for every
case. The current lifecycle is 25 `approved`, 35
`proposed_reference`, and 0 `pending`. The 35 project-selected proposals
preserve their previous status as provenance: 19 were `revised`, and 16 were
`not_required`. This transition makes the records usable for an offline
diagnostic; it does **not** convert them to human-approved references.
`human_review_required` remains an informational flag, not a baseline
readiness gate.

Each proposed reference has an `operational_scoring` disposition for every
declared dimension. `deterministic_component` identifies an existing scorer
that can verify a structured component; it does not validate the reference's
semantics or score unobserved response content.
`not_assessed_by_deterministic_scorer` explicitly means the baseline must
receive no automatic credit for that dimension. Structured clarification,
tool, and trace checks require correctly extracted observations and do not
read or authenticate natural-language claims. `validate_baseline_readiness()`
checks the 60-case structure, all 25 numeric references, the isolated SQL
fixture, and proposal-policy coverage. It permits explicitly unassessed
dimensions and makes no human-approval claim.

Scoring results separate `reference_validation` (lifecycle status and whether
the reference is human-certified) from `model_response_score` (deterministic
dimension scores and a `dimension_dispositions` entry for every applicable
dimension, using `not_assessed_by_deterministic_scorer` when appropriate). A
partial model score is not a full pass. Existing top-level `status` and `human_review_status`
fields are retained for compatibility; they are not a gate to running the
offline diagnostic.

Arithmetic uses `Decimal` values and explicit denominators/units.
`validate_numeric_references()` recomputes 25 deterministic numeric
references from their benchmark inputs and checks the documented tolerance.
SQL reference validation runs only the fixed reference SELECT against the
provided tiny fixture in an in-memory SQLite database. Generated SQL is run
only against a separate, in-memory copy of that synthetic fixture. An
authorizer restricts reads to the fixture's two columns and allows only
`SUM`; one statement, a progress limit, and a result-row cap are enforced.
The checker compares column names, typed values, row order, and SQLite
authorization. It never connects to application or user data, and an exact
fixture result still cannot prove that query intent is correct in general.

JSON checks separately parse the complete output, require the exact object
keys and expected value types/values, and reject extra prose or Markdown
fences. A passing JSON-format check does not establish semantic correctness.
The sentence-count scorer treats `.`, `!`, and `?` as terminators when
followed by whitespace or end-of-output, collapses repeated terminal
punctuation, and does not split decimal points or recognized abbreviations.
It reports abbreviation use separately. This heuristic is limited: unusual
abbreviations, quotations, lists, and language-specific punctuation can still
be miscounted, so semantic correctness remains for human review.
Sentence-count and abbreviation dimensions are scored independently.
`output_instruction_compliance` receives full credit only when both explicit
format constraints pass; a violation is not counted as multiple additive
penalties because the benchmark defines no aggregate score.

Score applicable dimensions independently:

| Score | Meaning |
| --- | --- |
| `0` | Incorrect or unsupported |
| `1` | Partially correct |
| `2` | Complete and correct |
| `NA` | Not tested; excluded from that dimension's denominator |

Dimensions are conceptual correctness, formula/method selection, numerical
correctness, statistical interpretation, business relevance, evidence and
uncertainty, tool-use correctness, output/instruction compliance, abstention
accuracy, unsupported claims, sentence-count and abbreviation compliance,
SQL safety and result correctness, clarification completeness, execution
claim integrity, query intent, and result interpretation. `NA` is not
applicable; `needs_review` remains separate from the 0/1/2 scale. There is no
aggregate score. Unassessed dimensions remain outside automatic pass/fail
counts.
Manual dimension scoring requires an explicit valid disposition for every
dimension declared by the case. Empty and partial score maps are reported as
`unscored` and `partially_scored`, respectively, with missing dimension names;
`NA` is an explicit disposition and is not inferred for omitted dimensions.
Completeness does not resolve a reference's semantic-review requirement:
`proposed_reference` remains a project-selected, non-human-certified status
even when every machine-checkable component passes.

Tool-trace scoring preserves the observed `execution_status`. A matching
trace only scores the trace-related check; the overall response stays
`needs_review` because the available fixture cannot authenticate execution or
fully grade the final interpretation. A failed execution is not rewritten as
a successful runtime event. A supplied claim that contradicts the observed
success/failure status is explicitly failed as `unsupported_execution_claim`.
The helper compares caller-supplied trace objects; it does not authenticate
their origin.

Failure attribution labels are `concept_gap`, `formula_selection_error`,
`arithmetic_error`, `statistical_interpretation_error`,
`missing_context_error`, `retrieval_error`, `tool_selection_error`,
`invalid_tool_arguments`, `execution_failure`,
`result_interpretation_error`, `output_format_failure`,
`unsupported_claim`, `unsupported_execution_claim`, `reference_ambiguity`,
and `scorer_error`.
These are candidate diagnostic explanations, not automatic causal findings.
In particular, a wrong numerical result can arise from misunderstanding the
question, selecting the wrong formula, or arithmetic. Paired base/context
cases and separately scored formula/method and numerical dimensions help
distinguish these possibilities. Context
and tool modes similarly provide suggestive comparisons, not proof of cause.

Free-form semantic answers are not scored by keyword matching. They remain
explicitly unassessed by the deterministic scorer, with their human rubrics
retained for optional review. Numeric helpers accept structured,
independently extracted observations; JSON helpers parse the complete output;
tool helpers compare explicit tool/argument/trace observations. A matching
tool trace scores that trace dimension only, not the complete final answer.
A deterministic match against a proposed reference does not make the
reference human-certified. Passing format, numeric, or tool checks do not
score any semantic dimension listed as unassessed.

Every reference now has a rubric entry for exactly the dimensions declared by
its case. Tool-argument checks separately verify tool/schema/formula/value,
but the prompt also asks for an explanation of why the original arguments are
invalid; that explanation remains human-reviewed, so a structurally correct
call does not produce a final pass.
For the percentage calculator contract, multiplicative/divisive rearrangements
of the requested numerator, denominator, and percentage multiplier are
accepted only when they preserve those operands and evaluate to the expected
result. A coincidental expression such as `1 + 2` is not accepted as the
requested formula.

Synthetic outputs used in tests are test fixtures for scorer behavior, not
model evaluation results.

## Split and leakage policy

The case set has 20 development, 20 validation, and 20 hidden-test cases.
Each category uses a rotated 4/3/3 allocation. Related variants must share a
`family_id` and remain within one split; validation rejects a family spanning
splits. The 60 cases belong to 51 families. Related prompts covering
percentage denominators, weighted averages, causal inference, missing
information, and profit/margin calculations are grouped. A prompt-only
similarity diagnostic (normalized prompt text, `SequenceMatcher`, threshold
0.55) rejects detected near-duplicates if they cross splits or do not share a
family. This inexpensive heuristic is not a semantic proof of independence;
review its findings when prompts are added or changed.

The hidden split is distinguishable by the evaluator but is not protected by
repository access controls or cryptographic separation. Do not describe it as
secret from anyone who can read this repository. Avoid selecting training
examples or tuning decisions based on hidden-test outcomes.

## Human review and known limits

Semantic business, statistical, and interpretation dimensions remain
unassessed by deterministic scoring even when a reference lifecycle status is
`approved` or `proposed_reference`.
Unresolved business definitions, such as the meaning of “active customer” or
“retained,” must not be silently treated as gold. In particular, structured
clarification scoring consumes explicitly extracted required fields and
invented values; it does not infer those fields from raw prose. Deterministic
references are independently recomputed where supported. No inference run is
blocked by semantic review, but any complete interpretation of unassessed
semantic dimensions still requires human evaluation; the automated result
cannot be treated as a full score.

This is a small diagnostic set, not a statistically powered capability
certification. Synthetic tables do not measure data ingestion, schema
retrieval, orchestration, production authorization, or tool reliability under
real infrastructure. A future retrieval evaluation needs separate controlled
documents, evidence labels, and citation checks. The repository-readable
hidden split is not access-controlled.

## Offline validation

From the repository root, run:

```powershell
Push-Location backend
.\.venv-qwen3-train\Scripts\python.exe -m unittest discover -s tests -p "test_ba_da_benchmark.py" -v
Pop-Location
```

The tests validate case/category/split/reference and lifecycle counts, proposal
provenance and operational scoring dispositions, response-score separation,
the offline readiness gate, complete dimension-rubric coverage, schema
contract fields, family and prompt-similarity isolation, safe projections,
deterministic Decimal references and tolerance boundaries, constrained
in-memory SQL behavior, malformed/fenced JSON, sentence count, decimals,
abbreviations and ellipses, clarification observations, tool arguments and
execution-claim consistency, `NA` handling, and the rule that unassessed
semantic dimensions do not receive automatic credit.
`validate_benchmark()` and
`validate_numeric_references()` can also be invoked from the helper module.
These checks are offline and do not import or load Qwen.
