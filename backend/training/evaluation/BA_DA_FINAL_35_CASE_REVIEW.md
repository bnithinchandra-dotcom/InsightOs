# BA/DA Benchmark Final Review: 35 Operationally Proposed References

## Scope, provenance, and lifecycle

This source-aligned review covers the exact 35 records whose current reference lifecycle is `proposed_reference`; it derives IDs and content from the current benchmark and reference JSON. The source has 60 unique cases and 60 one-to-one references, six categories with 10 cases each, three 20-case splits, and 51 families. No prompt/input, category, family, split, mode, or approved reference answer was changed for this lifecycle update.

Current lifecycle: **25 approved / 35 proposed_reference / 0 pending**. Provenance for proposals: **19 originated as revised / 16 originated as not_required**. `proposed_reference` means project-selected for operational use, not human-semantic approval. The prior case-by-case review is preserved inside each case as historical evidence; its old labels describe the prior lifecycle snapshot only. The `human_review_required` flag remains informational and does not block an offline diagnostic.

Each proposal has an `operational_scoring` entry for every declared dimension. Existing deterministic scorers receive credit only for structured components they actually check. All unsupported semantic dimensions are explicitly `not_assessed_by_deterministic_scorer`, not silently passed or failed. `validate_baseline_readiness()` allows this explicit partial assessment, does not certify reference semantics, and does not run inference.

## Current proposal inventory

| Case ID | Category | Family | Split | Modes | Prior status | Scorer | Dimensions |
|---|---|---|---|---|---|---|---|
| `ba_order_bottleneck` | `business_analyst` | `ba_order_flow` | `hidden_test` | base, context_assisted | `revised` | `numeric_values` | conceptual_correctness, business_relevance, evidence_uncertainty |
| `ba_acceptance_criteria` | `business_analyst` | `ba_acceptance` | `development` | base, context_assisted | `revised` | `human_review` | conceptual_correctness, business_relevance, evidence_uncertainty |
| `ba_evidence_recommendation` | `business_analyst` | `ba_recommendation` | `hidden_test` | base, context_assisted | `revised` | `numeric_values` | business_relevance, evidence_uncertainty, unsupported_claims |
| `da_groupby_aggregation` | `data_analyst` | `da_aggregation` | `development` | base, context_assisted, tool_assisted | `revised` | `human_review` | formula_method_selection, tool_use_correctness, output_instruction_compliance |
| `da_one_to_many_join` | `data_analyst` | `da_join_cardinality` | `development` | base, context_assisted | `revised` | `numeric_values` | conceptual_correctness, numerical_correctness, business_relevance |
| `calc_pct_change` | `arithmetic_calculations` | `calc_percent_change` | `development` | base, context_assisted, tool_assisted | `revised` | `numeric_values` | formula_method_selection, numerical_correctness, tool_use_correctness |
| `calc_profit` | `arithmetic_calculations` | `profit_margin_calculations` | `development` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, business_relevance |
| `calc_margin` | `arithmetic_calculations` | `profit_margin_calculations` | `development` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, business_relevance |
| `calc_conversion_denominator` | `arithmetic_calculations` | `calc_conversion` | `validation` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness |
| `calc_weighted_avg` | `arithmetic_calculations` | `weighted_average_variants` | `validation` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness |
| `calc_region_ratio` | `arithmetic_calculations` | `percentage_rate_denominator` | `validation` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, business_relevance |
| `calc_cagr` | `arithmetic_calculations` | `calc_compounded_growth` | `hidden_test` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness |
| `calc_units` | `arithmetic_calculations` | `calc_unit_conversion` | `hidden_test` | base, context_assisted, tool_assisted | `revised` | `numeric_values` | formula_method_selection, numerical_correctness, output_instruction_compliance |
| `calc_multistep_profit_margin` | `arithmetic_calculations` | `calc_multistep_margin` | `hidden_test` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, business_relevance |
| `stat_mean_median_outlier` | `statistics_probability` | `stat_location_outlier` | `development` | base, context_assisted, tool_assisted | `revised` | `numeric_values` | formula_method_selection, numerical_correctness, statistical_interpretation |
| `stat_population_sample` | `statistics_probability` | `stat_variance_definition` | `development` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, statistical_interpretation |
| `stat_iqr` | `statistics_probability` | `stat_iqr_convention` | `development` | base, context_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, statistical_interpretation |
| `stat_conditional_probability` | `statistics_probability` | `stat_conditional_probability` | `development` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, statistical_interpretation |
| `stat_correlation_causation` | `statistics_probability` | `causal_inference_variants` | `validation` | base, context_assisted | `revised` | `human_review` | conceptual_correctness, statistical_interpretation, evidence_uncertainty |
| `stat_sampling_bias` | `statistics_probability` | `stat_sampling` | `validation` | base, context_assisted | `revised` | `human_review` | conceptual_correctness, statistical_interpretation, evidence_uncertainty |
| `stat_ci_interpretation` | `statistics_probability` | `stat_confidence_interval` | `validation` | base, context_assisted | `revised` | `human_review` | statistical_interpretation, evidence_uncertainty, business_relevance |
| `stat_pvalue` | `statistics_probability` | `stat_pvalue_meaning` | `hidden_test` | base, context_assisted | `revised` | `human_review` | numerical_correctness, statistical_interpretation, evidence_uncertainty |
| `stat_test_selection` | `statistics_probability` | `stat_test_choice` | `hidden_test` | base, context_assisted | `revised` | `human_review` | formula_method_selection, statistical_interpretation, evidence_uncertainty |
| `stat_multiple_comparisons` | `statistics_probability` | `stat_multiple_testing` | `hidden_test` | base, context_assisted | `revised` | `numeric_values` | statistical_interpretation, evidence_uncertainty |
| `data_monthly_margin` | `dataset_reasoning` | `data_monthly_margin` | `development` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, business_relevance |
| `data_sales_vs_margin` | `dataset_reasoning` | `data_sales_margin_comparison` | `development` | base, context_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, business_relevance |
| `data_missing_required_column` | `dataset_reasoning` | `data_required_columns` | `development` | base, context_assisted | `revised` | `human_review` | formula_method_selection, evidence_uncertainty, abstention_accuracy, unsupported_claims |
| `data_unequal_group_sizes` | `dataset_reasoning` | `percentage_rate_denominator` | `validation` | base, context_assisted, tool_assisted | `not_required` | `numeric_values` | formula_method_selection, numerical_correctness, business_relevance |
| `data_missingness_impact` | `dataset_reasoning` | `data_missingness_impact` | `validation` | base, context_assisted | `revised` | `human_review` | statistical_interpretation, evidence_uncertainty, business_relevance |
| `data_simpsons_paradox` | `dataset_reasoning` | `data_simpsons` | `hidden_test` | base, context_assisted | `revised` | `numeric_values` | numerical_correctness, statistical_interpretation, business_relevance, evidence_uncertainty |
| `data_forecast_uncertainty` | `dataset_reasoning` | `data_forecast` | `hidden_test` | base, context_assisted | `revised` | `human_review` | statistical_interpretation, evidence_uncertainty, business_relevance |
| `tool_json_only` | `tools_reliability` | `tool_json_format` | `development` | base, context_assisted | `not_required` | `json_object` | output_instruction_compliance |
| `tool_exact_sentence_count` | `tools_reliability` | `tool_sentence_count` | `hidden_test` | base, context_assisted | `revised` | `sentence_count` | abbreviation_compliance, conceptual_correctness, output_instruction_compliance, sentence_count_compliance |
| `tool_arguments_valid` | `tools_reliability` | `percentage_rate_denominator` | `validation` | base, context_assisted, tool_assisted | `not_required` | `tool_arguments` | numerical_correctness, tool_use_correctness, formula_method_selection, conceptual_correctness |
| `tool_trace_result_grounding` | `tools_reliability` | `tool_result_grounding` | `hidden_test` | base, context_assisted, tool_assisted | `not_required` | `tool_trace` | numerical_correctness, tool_use_correctness, unsupported_claims, execution_claim_integrity, result_interpretation_error |

## Case-by-case A-K operational review

The 0/1/2 scale is applied independently: 0 = incorrect or unsupported; 1 = partially correct/materially incomplete; 2 = complete and correct under the stated criterion; `NA` only when explicitly justified. There is no aggregate score.

### ba_order_bottleneck - proposed reference

**A. Identity.** Category `business_analyst`; family `ba_order_flow`; split `hidden_test`; modes `base, context_assisted`; reference ID `ba_order_bottleneck`; current status `proposed_reference` (prior status `revised`); scorer `numeric_values`.

**B. Real-world skill.** Business Analyst; process analysis, distinguishing a longest observed wait from a true throughput constraint. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a long review queue may be harmless if it has spare capacity.

**D. Actual benchmark prompt and inputs.** Orders spend 1 hour in intake, 18 hours awaiting approval, and 2 hours in fulfillment. Identify the observed bottleneck and one follow-up question before recommending a change.

```json
{
  "stage_hours": {
    "intake": 1,
    "approval": 18,
    "fulfillment": 2
  }
}
```

Task adequacy and assumptions from the prior case-specific analysis: Stage waits are supplied (intake 1 h, approval 18 h, fulfillment 2 h). They support identifying the longest listed wait but do not establish capacity, queue volume, or throughput impact.

**E. Exact recommended reference answer.** Approval has the longest listed wait at 18 hours. This identifies the longest observed delay, not necessarily the operational bottleneck. Ask: does approval constrain throughput or capacity compared with the other stages? Do not recommend a change from duration alone.

**F. Reasoning.** max(1, 18, 2) = 18 hours at approval; duration alone does not establish throughput constraint Units: hours. Tolerances: `{"hours": "0"}`. Treat these as limits of the evidence: Stage waits are supplied (intake 1 h, approval 18 h, fulfillment 2 h). They support identifying the longest listed wait but do not establish capacity, queue volume, or throughput impact.

**G. Relevant failure modes.** Declared labels: `concept_gap`, `unsupported_execution_claim`. Prior case-specific note: `concept_gap`, `unsupported_claim`; family `ba_order_flow`, hidden-test split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `conceptual_correctness` | Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. |
| `business_relevance` | Distinguishes longest observed wait from a proven throughput bottleneck and proposes a relevant follow-up. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Distinguishes longest observed wait from a proven throughput bottleneck and proposes a relevant follow-up. |
| `evidence_uncertainty` | Does not infer a capacity constraint from duration alone. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Does not infer a capacity constraint from duration alone. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: bottleneck_hours = 18. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Business Analyst; process analysis, distinguishing a longest observed wait from a true throughput constraint.
- **Task adequacy and assumptions:** Stage waits are supplied (intake 1 h, approval 18 h, fulfillment 2 h). They support identifying the longest listed wait but do not establish capacity, queue volume, or throughput impact.
- **Reference and example:** Approval is the longest listed wait at 18 hours, not necessarily the operational bottleneck. The corrected answer asks whether approval constrains throughput or capacity versus other stages. Synthetic example: a long review queue may be harmless if it has spare capacity.
- **Scoring and evidence:** Dimensions are `conceptual_correctness`, `business_relevance`, `evidence_uncertainty`. The 18-hour maximum is deterministic; the requested follow-up and restraint before recommending a change need human review. Existing numeric scorer checks only the maximum.
- **Failure and isolation:** `concept_gap`, `unsupported_claim`; family `ba_order_flow`, hidden-test split.
- **Disposition:** **Revised; retain review requirement.** The prior wording implied a follow-up investigation but did not actually pose the requested question. Corrected without asserting the delay is a proven bottleneck.


### ba_acceptance_criteria - proposed reference

**A. Identity.** Category `business_analyst`; family `ba_acceptance`; split `development`; modes `base, context_assisted`; reference ID `ba_acceptance_criteria`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Business Analyst; writing measurable, implementation-neutral acceptance criteria. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a placeholder might later be replaced with the team’s agreed target after user research; no target is supplied by the benchmark.

**D. Actual benchmark prompt and inputs.** Draft two implementation-neutral measurable acceptance-criteria templates for 'make the monthly report easier to use'. Use explicit placeholders where the target user group, task, baseline, or threshold is missing, and state what must be confirmed before either criterion can be tested.

```json
{
  "context": "No baseline usability measure or target user group is given."
}
```

Task adequacy and assumptions from the prior case-specific analysis: The request “make the monthly report easier to use” lacks target users, priority task, baseline, and thresholds. The prompt explicitly calls for placeholders and confirmation.

**E. Exact recommended reference answer.** Offer two implementation-neutral templates: (1) at least [agreed percentage] of [target users] complete [priority report task] within [agreed time]; (2) the median time for [target users] to complete [priority report task] is at most [agreed time]. Agree on user/task definitions, any baseline, percentage threshold, and time threshold before treating either criterion as testable; do not invent numeric targets.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: not separately specified. Tolerances: `{}`. Treat these as limits of the evidence: The request “make the monthly report easier to use” lacks target users, priority task, baseline, and thresholds. The prompt explicitly calls for placeholders and confirmation.

**G. Relevant failure modes.** Declared labels: `concept_gap`, `missing_context_error`. Prior case-specific note: `concept_gap`, `missing_context_error`, `unsupported_claim`; family `ba_acceptance`, development split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `conceptual_correctness` | Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. |
| `business_relevance` | Provides two measurable, implementation-neutral templates with explicit placeholders. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Provides two measurable, implementation-neutral templates with explicit placeholders. |
| `evidence_uncertainty` | Requires users, tasks, baselines, and thresholds to be agreed rather than fabricated. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Requires users, tasks, baselines, and thresholds to be agreed rather than fabricated. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Business Analyst; writing measurable, implementation-neutral acceptance criteria.
- **Task adequacy and assumptions:** The request “make the monthly report easier to use” lacks target users, priority task, baseline, and thresholds. The prompt explicitly calls for placeholders and confirmation.
- **Reference and example:** Two templates use an agreed completion percentage/time and an agreed median task time. Synthetic example: a placeholder might later be replaced with the team’s agreed target after user research; no target is supplied by the benchmark.
- **Scoring and evidence:** `conceptual_correctness`, `business_relevance`, `evidence_uncertainty`; all require human rubric scoring. There is no deterministic semantic scorer.
- **Failure and isolation:** `concept_gap`, `missing_context_error`, `unsupported_claim`; family `ba_acceptance`, development split.
- **Disposition:** **Defensible, retain revised.** Placeholders avoid inventing targets; multiple valid measurable templates are accepted by the rubric. Human review must check both criteria and confirmation requirements.


### ba_evidence_recommendation - proposed reference

**A. Identity.** Category `business_analyst`; family `ba_recommendation`; split `hidden_test`; modes `base, context_assisted`; reference ID `ba_evidence_recommendation`; current status `proposed_reference` (prior status `revised`); scorer `numeric_values`.

**B. Real-world skill.** Business Analyst; evidence-calibrated recommendation. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a follow-up pilot with a comparison group could test whether the change explains the difference.

**D. Actual benchmark prompt and inputs.** A pilot reduced average processing time from 10 days to 8 days for 12 cases, with no control group and no cost data. Give a cautious recommendation and identify key limitations.

```json
{
  "pilot_cases": 12,
  "before_mean_days": 10,
  "after_mean_days": 8,
  "control_group": false,
  "cost_data": false
}
```

Task adequacy and assumptions from the prior case-specific analysis: Before/after means are 10 and 8 days for 12 pilot cases; no control or cost data. These establish an observed difference, not causal or net-benefit effects.

**E. Exact recommended reference answer.** In the 12 pilot cases, the observed mean processing time was 2 days lower (20% relative to the 10-day baseline). This is preliminary descriptive evidence. The uncontrolled pilot and missing cost data do not establish causation, generalizability, or net benefit; use a suitable comparison and cost assessment before considering broad rollout.

**F. Reasoning.** 10 - 8 = 2 days; (2 / 10) * 100 = 20% relative decrease Units: days; percent. Tolerances: `{"days": "0", "percent": "0"}`. Treat these as limits of the evidence: Before/after means are 10 and 8 days for 12 pilot cases; no control or cost data. These establish an observed difference, not causal or net-benefit effects.

**G. Relevant failure modes.** Declared labels: `statistical_interpretation_error`, `unsupported_execution_claim`. Prior case-specific note: `arithmetic_error`, `unsupported_claim`, `missing_context_error`; family `ba_recommendation`, hidden-test split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `business_relevance` | Recommends a bounded next step rather than deployment certainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Recommends a bounded next step rather than deployment certainty. |
| `evidence_uncertainty` | Describes an observed pilot difference and states that no control, small sample, and missing cost data limit causal, generalization, and net-benefit claims. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Describes an observed pilot difference and states that no control, small sample, and missing cost data limit causal, generalization, and net-benefit claims. |
| `unsupported_claims` | Makes no claim beyond the supplied evidence or observable trace. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Makes no claim beyond the supplied evidence or observable trace. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: difference_days = 2; relative_reduction_percent = 2E+1. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Business Analyst; evidence-calibrated recommendation.
- **Task adequacy and assumptions:** Before/after means are 10 and 8 days for 12 pilot cases; no control or cost data. These establish an observed difference, not causal or net-benefit effects.
- **Reference and example:** The observed mean is 2 days lower, a 20% relative decrease from 10 days. Recommend a suitable comparison and cost assessment before broad rollout. Synthetic example: a follow-up pilot with a comparison group could test whether the change explains the difference.
- **Scoring and evidence:** `business_relevance`, `evidence_uncertainty`, `unsupported_claims`; numeric difference and relative change are independently recalculated, while causal restraint and recommendation need human review.
- **Failure and isolation:** `arithmetic_error`, `unsupported_claim`, `missing_context_error`; family `ba_recommendation`, hidden-test split.
- **Disposition:** **Defensible, retain revised.** The reference does not infer causation, generalizability, or net benefit from an uncontrolled pilot.


### da_groupby_aggregation - proposed reference

**A. Identity.** Category `data_analyst`; family `da_aggregation`; split `development`; modes `base, context_assisted, tool_assisted`; reference ID `da_groupby_aggregation`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Data Analyst; SQL aggregation by calendar period. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** January 2024 and January 2025 must remain separate groups.

**D. Actual benchmark prompt and inputs.** Assume SQLite and that order_date already represents the intended business timezone. Given orders(order_id, order_date, revenue), describe the SQL grouping needed for total revenue by calendar month. Group by year-month, not month name alone.

```json
{
  "schema": {
    "orders": {
      "order_id": "INTEGER",
      "order_date": "DATE",
      "revenue": "DECIMAL"
    }
  }
}
```

Task adequacy and assumptions from the prior case-specific analysis: SQLite dialect and intended business timezone normalization are explicit; `orders` provides `order_date` and `revenue`. The request distinguishes year-month from month name.

**E. Exact recommended reference answer.** Assuming SQLite and that order_date already represents the intended business timezone, group by strftime('%Y-%m', order_date) and calculate SUM(revenue). The year-month key keeps the same month from different years separate.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: revenue. Tolerances: `{}`. Treat these as limits of the evidence: SQLite dialect and intended business timezone normalization are explicit; `orders` provides `order_date` and `revenue`. The request distinguishes year-month from month name.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `invalid_tool_arguments`. Prior case-specific note: `concept_gap`, `invalid_tool_arguments`, `output_format_failure`; family `da_aggregation`, development split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Uses SQLite year-month grouping with SUM(revenue), not month name alone. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Uses SQLite year-month grouping with SUM(revenue), not month name alone. |
| `tool_use_correctness` | Selects and structures the requested tool use correctly; do not claim execution unless supported by the supplied trace. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Selects and structures the requested tool use correctly; do not claim execution unless supported by the supplied trace. |
| `output_instruction_compliance` | States the SQLite and pre-normalized business-timezone assumptions; query syntax must match that dialect if provided. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: States the SQLite and pre-normalized business-timezone assumptions; query syntax must match that dialect if provided. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Data Analyst; SQL aggregation by calendar period.
- **Task adequacy and assumptions:** SQLite dialect and intended business timezone normalization are explicit; `orders` provides `order_date` and `revenue`. The request distinguishes year-month from month name.
- **Reference and example:** Use `strftime('%Y-%m', order_date)` and `SUM(revenue)`. Synthetic example: January 2024 and January 2025 must remain separate groups.
- **Scoring and evidence:** `formula_method_selection`, `tool_use_correctness`, `output_instruction_compliance`. No generated SQL result is executed for this case; dialect and grouping correctness need human review. Do not claim execution.
- **Failure and isolation:** `concept_gap`, `invalid_tool_arguments`, `output_format_failure`; family `da_aggregation`, development split.
- **Disposition:** **Defensible, retain revised.** The timezone and dialect assumptions are explicit. The reference describes grouping rather than requiring a fully executable query, matching “describe the SQL grouping.”


### da_one_to_many_join - proposed reference

**A. Identity.** Category `data_analyst`; family `da_join_cardinality`; split `development`; modes `base, context_assisted`; reference ID `da_one_to_many_join`; current status `proposed_reference` (prior status `revised`); scorer `numeric_values`.

**B. Real-world skill.** Data Analyst; join cardinality and grain-safe aggregation. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a customer balance of 10 appears on each of three order rows.

**D. Actual benchmark prompt and inputs.** Customers has one row for each of C1 and C2. Orders has three rows for C1 and one for C2. Predict the number of rows after an inner join on customer_id and explain what happens to a customer-level amount repeated across joined rows.

```json
{
  "customers": [
    {
      "customer_id": "C1",
      "amount": 10
    },
    {
      "customer_id": "C2",
      "amount": 20
    }
  ],
  "orders_per_customer": {
    "C1": 3,
    "C2": 1
  }
}
```

Task adequacy and assumptions from the prior case-specific analysis: Customer rows are one per C1/C2; orders match three rows for C1 and one for C2; inner join key is specified.

**E. Exact recommended reference answer.** The inner join has 4 rows: three for C1 and one for C2. Customer-level values repeat on matching order rows; aggregate at the intended grain and do not blindly sum repeated customer-level values at order grain.

**F. Reasoning.** 3 matching C1 orders + 1 matching C2 order = 4 joined rows; customer amount repeats at order grain Units: rows. Tolerances: `{"rows": "0"}`. Treat these as limits of the evidence: Customer rows are one per C1/C2; orders match three rows for C1 and one for C2; inner join key is specified.

**G. Relevant failure modes.** Declared labels: `concept_gap`, `arithmetic_error`. Prior case-specific note: `concept_gap`, `arithmetic_error`, `result_interpretation_error`; family `da_join_cardinality`, development split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `conceptual_correctness` | Explains join multiplicity, repeated one-side attributes, and aggregation at the intended grain. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Explains join multiplicity, repeated one-side attributes, and aggregation at the intended grain. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: joined_rows = 4. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Data Analyst; join cardinality and grain-safe aggregation.
- **Task adequacy and assumptions:** Customer rows are one per C1/C2; orders match three rows for C1 and one for C2; inner join key is specified.
- **Reference and example:** Four joined rows. Customer-level amount repeats on matching order rows; aggregate at the intended grain rather than summing repeated customer values. Synthetic example: a customer balance of 10 appears on each of three order rows.
- **Scoring and evidence:** `conceptual_correctness`, `numerical_correctness`, `business_relevance`. The four-row count is deterministic; repeated-value implications and correct grain require human scoring.
- **Failure and isolation:** `concept_gap`, `arithmetic_error`, `result_interpretation_error`; family `da_join_cardinality`, development split.
- **Disposition:** **Defensible, retain revised.** The table cardinalities make the row count unique; the reference correctly warns against double-counting one-side measures.


### calc_pct_change - proposed reference

**A. Identity.** Category `arithmetic_calculations`; family `calc_percent_change`; split `development`; modes `base, context_assisted, tool_assisted`; reference ID `calc_pct_change`; current status `proposed_reference` (prior status `revised`); scorer `numeric_values`.

**B. Real-world skill.** Arithmetic/business calculation; percentage change and denominator selection. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** changing from 50 to 60 is a 20% increase relative to 50.

**D. Actual benchmark prompt and inputs.** Sales increased from 80,000 to 100,000. Calculate the percentage change relative to the original value and show the formula.

```json
{
  "original": "80000",
  "new": "100000",
  "unit": "currency"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Original 80,000 and new 100,000 are explicit; “relative to the original” fixes the denominator.

**E. Exact recommended reference answer.** Percentage change = (100,000 - 80,000) / 80,000 × 100 = 25% increase.

**F. Reasoning.** (100000 - 80000) / 80000 * 100 = 25 Units: percent. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Original 80,000 and new 100,000 are explicit; “relative to the original” fixes the denominator.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`, `tool_selection_error`. Prior case-specific note: `formula_selection_error`, `arithmetic_error`, `invalid_tool_arguments`; family `calc_percent_change`, development split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Uses original value as denominator. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Uses original value as denominator. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `tool_use_correctness` | Selects and structures the requested tool use correctly; do not claim execution unless supported by the supplied trace. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Selects and structures the requested tool use correctly; do not claim execution unless supported by the supplied trace. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: percent_change = 25. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Arithmetic/business calculation; percentage change and denominator selection.
- **Task adequacy and assumptions:** Original 80,000 and new 100,000 are explicit; “relative to the original” fixes the denominator.
- **Reference and example:** Corrected answer: `(100,000 - 80,000) / 80,000 × 100 = 25% increase`. Synthetic example: changing from 50 to 60 is a 20% increase relative to 50.
- **Scoring and evidence:** `formula_method_selection`, `numerical_correctness`, `tool_use_correctness`. The numeric result is deterministic; the reference-value helper alone does not score the displayed formula or tool use, so those dimensions require manual/structured scoring.
- **Failure and isolation:** `formula_selection_error`, `arithmetic_error`, `invalid_tool_arguments`; family `calc_percent_change`, development split.
- **Disposition:** **Rewritten; remains revised pending human review.** The old answer omitted the formula explicitly requested. The expected numeric value remains 25.


### calc_profit - proposed reference

**A. Identity.** Category `arithmetic_calculations`; family `profit_margin_calculations`; split `development`; modes `base, context_assisted, tool_assisted`; reference ID `calc_profit`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Profit amount; revenue ₹500,000 and costs ₹375,000, both INR, make the subtraction unique. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** revenue 100 less costs 70 gives profit 30.

**D. Actual benchmark prompt and inputs.** Revenue is ₹500,000 and total costs are ₹375,000. Calculate profit in rupees.

```json
{
  "revenue": "500000",
  "costs": "375000",
  "unit": "INR"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** ₹125,000 profit.

**F. Reasoning.** 500000 - 375000 = 125000 Units: INR. Tolerances: `{"value": "0"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Subtracts costs from revenue. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Subtracts costs from revenue. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: profit = 1.25E+5. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Profit amount; revenue ₹500,000 and costs ₹375,000, both INR, make the subtraction unique.
- **Reference / example:** ₹125,000 profit. Synthetic example: revenue 100 less costs 70 gives profit 30.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `business_relevance`; numeric result is deterministic, formula/explanation is not automatically scored.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`; family `profit_margin_calculations`, development.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** The stored answer and calculation agree; manual scoring must cover rubric dimensions not emitted by the numeric helper.


### calc_margin - proposed reference

**A. Identity.** Category `arithmetic_calculations`; family `profit_margin_calculations`; split `development`; modes `base, context_assisted, tool_assisted`; reference ID `calc_margin`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Profit margin; profit, revenue, unit, and requested denominator are explicit. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** 20 profit on 100 revenue is 20%.

**D. Actual benchmark prompt and inputs.** Revenue is ₹240,000 and profit is ₹36,000. Calculate profit margin as a percentage and state the denominator.

```json
{
  "revenue": "240000",
  "profit": "36000",
  "unit": "percent"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** 15%, using revenue as the denominator.

**F. Reasoning.** 36000 / 240000 * 100 = 15 Units: percent. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Uses profit divided by revenue. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Uses profit divided by revenue. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: margin_percent = 15. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Profit margin; profit, revenue, unit, and requested denominator are explicit.
- **Reference / example:** `36,000 / 240,000 × 100 = 15%`, revenue denominator. Synthetic example: 20 profit on 100 revenue is 20%.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `business_relevance`; value check is deterministic, denominator statement and explanation require response-level scoring.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`; family `profit_margin_calculations`, development.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Formula and value are consistent.


### calc_conversion_denominator - proposed reference

**A. Identity.** Category `arithmetic_calculations`; family `calc_conversion`; split `validation`; modes `base, context_assisted, tool_assisted`; reference ID `calc_conversion_denominator`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Conversion rate and denominator; 240 converted from 3,000 eligible leads. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** 5 conversions from 100 eligible leads is 5%.

**D. Actual benchmark prompt and inputs.** 240 of 3,000 eligible leads converted. Calculate the conversion rate and identify the denominator.

```json
{
  "conversions": "240",
  "eligible_leads": "3000"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** 8%; eligible leads are the denominator.

**F. Reasoning.** 240 / 3000 * 100 = 8 Units: percent. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Uses eligible leads rather than conversions as denominator. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Uses eligible leads rather than conversions as denominator. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: conversion_percent = 8. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Conversion rate and denominator; 240 converted from 3,000 eligible leads.
- **Reference / example:** 8%, denominator eligible leads. Synthetic example: 5 conversions from 100 eligible leads is 5%.
- **Scoring:** `formula_method_selection`, `numerical_correctness`; numeric result is deterministic, explicit denominator must be checked in the response.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`; family `calc_conversion`, validation.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Denominator is explicit and correctly specified.


### calc_weighted_avg - proposed reference

**A. Identity.** Category `arithmetic_calculations`; family `weighted_average_variants`; split `validation`; modes `base, context_assisted, tool_assisted`; reference ID `calc_weighted_avg`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Weighted mean; group sizes and means are supplied, with positive counts. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** group counts, not groups, determine the weights.

**D. Actual benchmark prompt and inputs.** Group A has 20 units averaging 4. Group B has 80 units averaging 9. Calculate the combined weighted average.

```json
{
  "groups": [
    {
      "count": "20",
      "mean": "4"
    },
    {
      "count": "80",
      "mean": "9"
    }
  ]
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** Combined weighted average is 8.

**F. Reasoning.** (20*4 + 80*9) / (20+80) = 8 Units: same as group means. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Weights group means by group sizes. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Weights group means by group sizes. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: weighted_average = 8. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Weighted mean; group sizes and means are supplied, with positive counts.
- **Reference / example:** `(20×4 + 80×9)/(20+80) = 8`. Synthetic example: group counts, not groups, determine the weights.
- **Scoring:** `formula_method_selection`, `numerical_correctness`; result is deterministic, but formula selection is not covered by numeric comparison alone.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`; family `weighted_average_variants`, validation.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Values and weighting are correct; related DA variant is in the same split/family.


### calc_region_ratio - proposed reference

**A. Identity.** Category `arithmetic_calculations`; family `percentage_rate_denominator`; split `validation`; modes `base, context_assisted, tool_assisted`; reference ID `calc_region_ratio`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Comparable rate denominators; both regions provide returns and orders. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** compare returns per order rather than raw counts.

**D. Actual benchmark prompt and inputs.** North has 18 returns from 600 orders and South has 12 returns from 200 orders. Calculate each return rate and state which is higher.

```json
{
  "North": {
    "numerator": "18",
    "denominator": "600"
  },
  "South": {
    "numerator": "12",
    "denominator": "200"
  }
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** North is 3%; South is 6%; South is higher.

**F. Reasoning.** 18/600*100=3; 12/200*100=6 Units: percent. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Applies the same numerator/denominator definition to both regions. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Applies the same numerator/denominator definition to both regions. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: North_percent = 3; South_percent = 6. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Comparable rate denominators; both regions provide returns and orders.
- **Reference / example:** North 3%; South 6%; South higher. Synthetic example: compare returns per order rather than raw counts.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `business_relevance`; rates are deterministic; ranking follows from them, but response wording is not extracted by the numeric helper.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`; family `percentage_rate_denominator`, validation.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Denominators are consistent and unique; related DA/tool cases are co-located in validation.


### calc_cagr - proposed reference

**A. Identity.** Category `arithmetic_calculations`; family `calc_compounded_growth`; split `hidden_test`; modes `base, context_assisted, tool_assisted`; reference ID `calc_cagr`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Compound annual growth; beginning 100, ending 121, and exactly two years are explicit. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** annualization depends on elapsed years.

**D. Actual benchmark prompt and inputs.** Revenue grew from 100 to 121 over exactly two years. Calculate the compound annual growth rate as a percentage, not the total growth rate.

```json
{
  "beginning": "100",
  "ending": "121",
  "years": 2
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** 10% compound annual growth rate.

**F. Reasoning.** (121 / 100) ** (1 / 2) - 1 = 0.10 Units: percent per year. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Annualizes compounded growth over two years, not total growth. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Annualizes compounded growth over two years, not total growth. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: cagr_percent = 1E+1. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Compound annual growth; beginning 100, ending 121, and exactly two years are explicit.
- **Reference / example:** `((121/100)^(1/2)-1)×100 = 10%`, not 21% total growth. Synthetic example: annualization depends on elapsed years.
- **Scoring:** `formula_method_selection`, `numerical_correctness`; deterministic check verifies the expected percentage, not the displayed method.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`; family `calc_compounded_growth`, hidden_test.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Formula is appropriate for positive beginning/ending values and the stated interval.


### calc_units - proposed reference

**A. Identity.** Category `arithmetic_calculations`; family `calc_unit_conversion`; split `hidden_test`; modes `base, context_assisted, tool_assisted`; reference ID `calc_units`; current status `proposed_reference` (prior status `revised`); scorer `numeric_values`.

**B. Real-world skill.** Arithmetic/business calculation; metric unit conversion with units. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** 1.2 metric tonnes equals 1,200 kilograms using the same supplied factor.

**D. Actual benchmark prompt and inputs.** A shipment weighs 2.5 metric tonnes. Convert it to kilograms and state the conversion used.

```json
{
  "tonnes": "2.5",
  "kilograms_per_tonne": "1000"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Input is 2.5 metric tonnes and supplies 1,000 kilograms per tonne.

**E. Exact recommended reference answer.** 2.5 metric tonnes × 1,000 kilograms per tonne = 2,500 kilograms.

**F. Reasoning.** 2.5 metric tonnes * 1000 kilograms/tonne = 2500 kilograms Units: kilograms. Tolerances: `{"value": "0"}`. Treat these as limits of the evidence: Input is 2.5 metric tonnes and supplies 1,000 kilograms per tonne.

**G. Relevant failure modes.** Declared labels: `arithmetic_error`, `concept_gap`. Prior case-specific note: `formula_selection_error`, `arithmetic_error`, `output_format_failure`; family `calc_unit_conversion`, hidden-test split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Uses the supplied metric-tonne conversion factor and labels the result. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Uses the supplied metric-tonne conversion factor and labels the result. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `output_instruction_compliance` | Follows the requested format, scope, assumptions, and output constraints. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Follows the requested format, scope, assumptions, and output constraints. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: kilograms = 2.5E+3. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Arithmetic/business calculation; metric unit conversion with units.
- **Task adequacy and assumptions:** Input is 2.5 metric tonnes and supplies 1,000 kilograms per tonne.
- **Reference and example:** Corrected answer: `2.5 metric tonnes × 1,000 kilograms per tonne = 2,500 kilograms`. Synthetic example: 1.2 metric tonnes equals 1,200 kilograms using the same supplied factor.
- **Scoring and evidence:** `formula_method_selection`, `numerical_correctness`, `output_instruction_compliance`. The numeric result is deterministic; stating the conversion and labeling units require response-level review.
- **Failure and isolation:** `formula_selection_error`, `arithmetic_error`, `output_format_failure`; family `calc_unit_conversion`, hidden-test split.
- **Disposition:** **Rewritten; remains revised pending human review.** The old answer omitted the requested conversion used. The numeric result remains 2,500 kilograms.


### calc_multistep_profit_margin - proposed reference

**A. Identity.** Category `arithmetic_calculations`; family `calc_multistep_margin`; split `hidden_test`; modes `base, context_assisted, tool_assisted`; reference ID `calc_multistep_profit_margin`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Multi-step business calculation; units sold, price, variable and fixed costs are explicit. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** margin uses profit divided by revenue, not cost.

**D. Actual benchmark prompt and inputs.** A business sells 80 items for ₹125 each. Variable costs are ₹6,400 and fixed costs are ₹1,600. Calculate revenue, total costs, profit, and profit margin.

```json
{
  "units": "80",
  "price_per_unit": "125",
  "variable_costs": "6400",
  "fixed_costs": "1600"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** Revenue ₹10,000; total costs ₹8,000; profit ₹2,000; margin 20%.

**F. Reasoning.** 80*125=10000; 6400+1600=8000; 10000-8000=2000; 2000/10000*100=20 Units: INR; percent. Tolerances: `{"INR": "0", "percent": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Uses the correct order of revenue, cost, profit, and margin computations. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Uses the correct order of revenue, cost, profit, and margin computations. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: revenue = 1E+4; costs = 8E+3; profit = 2E+3; margin_percent = 2E+1. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Multi-step business calculation; units sold, price, variable and fixed costs are explicit.
- **Reference / example:** Revenue ₹10,000; total cost ₹8,000; profit ₹2,000; margin 20%. Synthetic example: margin uses profit divided by revenue, not cost.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `business_relevance`; all four outputs are checked numerically; sequencing and explanation need manual scoring.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`; family `calc_multistep_margin`, hidden_test.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Calculations and units are consistent; no business facts beyond the prompt are assumed.


### stat_mean_median_outlier - proposed reference

**A. Identity.** Category `statistics_probability`; family `stat_location_outlier`; split `development`; modes `base, context_assisted, tool_assisted`; reference ID `stat_mean_median_outlier`; current status `proposed_reference` (prior status `revised`); scorer `numeric_values`.

**B. Real-world skill.** Statistics; mean, median, and sensitivity to a large observation. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** adding a very large value generally pulls the mean more than the median.

**D. Actual benchmark prompt and inputs.** For values 2, 4, 4, and 10, calculate the mean and median. Briefly explain how the largest value affects each.

```json
{
  "values": [
    "2",
    "4",
    "4",
    "10"
  ]
}
```

Task adequacy and assumptions from the prior case-specific analysis: Four values are fully supplied; the prompt asks for both statistics and a short effect explanation.

**E. Exact recommended reference answer.** Mean = 5; median = 4. In this sample, the value 10 raises the mean above the median. The median can also change if values or central ranks change.

**F. Reasoning.** (2+4+4+10)/4=5; median=(4+4)/2=4 Units: same as observations. Tolerances: `{"value": "0"}`. Treat these as limits of the evidence: Four values are fully supplied; the prompt asks for both statistics and a short effect explanation.

**G. Relevant failure modes.** Declared labels: `arithmetic_error`, `statistical_interpretation_error`. Prior case-specific note: `arithmetic_error`, `statistical_interpretation_error`; family `stat_location_outlier`, development split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Selects the requested method and applies the stated population, denominator, grain, and assumptions. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Selects the requested method and applies the stated population, denominator, grain, and assumptions. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `statistical_interpretation` | Explains this sample's mean/median relationship without claiming that medians cannot change. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Explains this sample's mean/median relationship without claiming that medians cannot change. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: mean = 5; median = 4. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Statistics; mean, median, and sensitivity to a large observation.
- **Task adequacy and assumptions:** Four values are fully supplied; the prompt asks for both statistics and a short effect explanation.
- **Reference and example:** Mean 5; median 4. In this sample, 10 raises the mean above the median; the median can also change when values or central ranks change. Synthetic example: adding a very large value generally pulls the mean more than the median.
- **Scoring and evidence:** `formula_method_selection`, `numerical_correctness`, `statistical_interpretation`. Values are deterministic; interpretation requires human review. Numeric-only matching is insufficient.
- **Failure and isolation:** `arithmetic_error`, `statistical_interpretation_error`; family `stat_location_outlier`, development split.
- **Disposition:** **Defensible, retain revised.** The wording avoids the false universal claim that a median cannot change.


### stat_population_sample - proposed reference

**A. Identity.** Category `statistics_probability`; family `stat_variance_definition`; split `development`; modes `base, context_assisted, tool_assisted`; reference ID `stat_population_sample`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Population versus sample variance; prompt explicitly supplies all three values as the population, then as a sample. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** denominators are N and n−1 respectively.

**D. Actual benchmark prompt and inputs.** Treat 2, 4, 6 as the entire population. Calculate the population variance. Then calculate the sample variance if the same three values are instead a sample.

```json
{
  "values": [
    "2",
    "4",
    "6"
  ],
  "population_denominator": 3,
  "sample_denominator": 2
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** Mean is 4; population variance is 8/3; sample variance is 4.

**F. Reasoning.** Squared deviations are 4,0,4; population variance=8/3; sample variance=8/(3-1)=4 Units: squared input units. Tolerances: `{"population_variance": "0.0001", "sample_variance": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Uses N for population and n-1 for sample variance. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Uses N for population and n-1 for sample variance. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `statistical_interpretation` | Interprets the result only to the extent supported by the design, assumptions, and stated population. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Interprets the result only to the extent supported by the design, assumptions, and stated population. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: population_variance = 2.666666666666666666666666667; sample_variance = 4. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Population versus sample variance; prompt explicitly supplies all three values as the population, then as a sample.
- **Reference / example:** Mean 4; population variance 8/3; sample variance 4. Synthetic example: denominators are N and n−1 respectively.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `statistical_interpretation`; variance numbers are deterministic, denominator explanation is not covered by value matching.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`, `statistical_interpretation_error`; family `stat_variance_definition`, development.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Explicit population/sample framing removes denominator ambiguity.


### stat_iqr - proposed reference

**A. Identity.** Category `statistics_probability`; family `stat_iqr_convention`; split `development`; modes `base, context_assisted`; reference ID `stat_iqr`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Quartiles and IQR; values are sorted and the median-of-halves convention is explicit. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a different quartile convention can produce different quartiles, hence the prompt’s explicit convention matters.

**D. Actual benchmark prompt and inputs.** For sorted values 1, 2, 3, 4, 5, 6, 7, 8, use the median-of-halves convention. Calculate Q1, Q3, and the interquartile range.

```json
{
  "sorted_values": [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8
  ],
  "quartile_convention": "median_of_halves"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** Under the stated median-of-halves convention, Q1=2.5, Q3=6.5, and IQR=4.

**F. Reasoning.** Lower half median=(2+3)/2=2.5; upper half median=(6+7)/2=6.5; IQR=4 Units: same as observations. Tolerances: `{"value": "0"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Applies the stated quartile convention. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Applies the stated quartile convention. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `statistical_interpretation` | Interprets the result only to the extent supported by the design, assumptions, and stated population. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Interprets the result only to the extent supported by the design, assumptions, and stated population. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: Q1 = 2.5; Q3 = 6.5; IQR = 4. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Quartiles and IQR; values are sorted and the median-of-halves convention is explicit.
- **Reference / example:** Q1=2.5, Q3=6.5, IQR=4. Synthetic example: a different quartile convention can produce different quartiles, hence the prompt’s explicit convention matters.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `statistical_interpretation`; numeric values are deterministic; interpretation dimension is not machine-scored.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`, `statistical_interpretation_error`; family `stat_iqr_convention`, development.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Reference exactly follows the specified convention.


### stat_conditional_probability - proposed reference

**A. Identity.** Category `statistics_probability`; family `stat_conditional_probability`; split `development`; modes `base, context_assisted, tool_assisted`; reference ID `stat_conditional_probability`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Conditional probability; 40 mobile users and 10 renewals among them define the event and denominator. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** conditioning on mobile means excluding non-mobile customers from the denominator.

**D. Actual benchmark prompt and inputs.** In a group of 100 customers, 40 use mobile and 10 of those 40 renew. What is the probability of renewal given mobile use? State the denominator.

```json
{
  "customers": 100,
  "mobile_users": 40,
  "mobile_users_who_renew": 10
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** 25%; denominator is the 40 mobile users.

**F. Reasoning.** 10 / 40 * 100 = 25 Units: percent. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Conditions on mobile use and uses 40 as denominator. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Conditions on mobile use and uses 40 as denominator. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `statistical_interpretation` | Interprets the result only to the extent supported by the design, assumptions, and stated population. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Interprets the result only to the extent supported by the design, assumptions, and stated population. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: conditional_probability_percent = 25. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Conditional probability; 40 mobile users and 10 renewals among them define the event and denominator.
- **Reference / example:** 10/40=25%, denominator 40 mobile users. Synthetic example: conditioning on mobile means excluding non-mobile customers from the denominator.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `statistical_interpretation`; numeric value is deterministic; denominator explanation is a response-level criterion.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`, `statistical_interpretation_error`; family `stat_conditional_probability`, development.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** The conditional event is sufficiently specified.


### stat_correlation_causation - proposed reference

**A. Identity.** Category `statistics_probability`; family `causal_inference_variants`; split `validation`; modes `base, context_assisted`; reference ID `stat_correlation_causation`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Statistics; causal inference and confounding. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** hotter weather could influence both swimming and ice-cream sales, but that hypothesis needs evidence.

**D. Actual benchmark prompt and inputs.** Ice-cream sales and swimming incidents rise together in summer. Does this establish that ice-cream sales cause incidents? Explain a plausible confounder and what evidence would strengthen a causal claim.

```json
{
  "observed": "Seasonal correlation only; no intervention or adjustment is supplied."
}
```

Task adequacy and assumptions from the prior case-specific analysis: Only seasonal co-movement is supplied; no intervention, adjustment, or causal design is provided.

**E. Exact recommended reference answer.** Seasonal co-movement does not establish that ice-cream sales cause swimming incidents. Season or temperature could influence both, but neither is proven to be a confounder here; a suitable designed intervention or defensible causal analysis would provide stronger evidence.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: not separately specified. Tolerances: `{}`. Treat these as limits of the evidence: Only seasonal co-movement is supplied; no intervention, adjustment, or causal design is provided.

**G. Relevant failure modes.** Declared labels: `statistical_interpretation_error`, `concept_gap`. Prior case-specific note: `statistical_interpretation_error`, `unsupported_claim`; family `causal_inference_variants`, validation split. Related BA and dataset cases are in validation and hidden-test respectively; family/similarity checks prevent leakage.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `conceptual_correctness` | Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. |
| `statistical_interpretation` | Rejects causal inference from co-movement and presents season/temperature only as possible, unproven confounders. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Rejects causal inference from co-movement and presents season/temperature only as possible, unproven confounders. |
| `evidence_uncertainty` | Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Statistics; causal inference and confounding.
- **Task adequacy and assumptions:** Only seasonal co-movement is supplied; no intervention, adjustment, or causal design is provided.
- **Reference and example:** Co-movement does not establish ice-cream sales cause incidents; season or temperature are possible, not proven, common causes. Synthetic example: hotter weather could influence both swimming and ice-cream sales, but that hypothesis needs evidence.
- **Scoring and evidence:** `conceptual_correctness`, `statistical_interpretation`, `evidence_uncertainty`; all require semantic human review.
- **Failure and isolation:** `statistical_interpretation_error`, `unsupported_claim`; family `causal_inference_variants`, validation split. Related BA and dataset cases are in validation and hidden-test respectively; family/similarity checks prevent leakage.
- **Disposition:** **Defensible, retain revised.** A proposed confounder is clearly presented as a hypothesis, not a proven mechanism.


### stat_sampling_bias - proposed reference

**A. Identity.** Category `statistics_probability`; family `stat_sampling`; split `validation`; modes `base, context_assisted`; reference ID `stat_sampling_bias`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Statistics; sampling-frame coverage and selection bias. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a survey of app users who opened the app this week cannot automatically represent all registered users.

**D. Actual benchmark prompt and inputs.** A customer satisfaction survey is sent only to users who logged in during the last week. Identify a likely sampling bias and one way to assess or reduce it.

```json
{
  "sampling_frame": "recently active users only"
}
```

Task adequacy and assumptions from the prior case-specific analysis: The frame is recent logins only; the target population is deliberately unspecified.

**E. Exact recommended reference answer.** If the target population includes users who did not log in during the last week, this survey excludes them and may overrepresent recent users. State the target population and sampling frame, compare respondents with that population, and consider broader sampling or justified adjustment; stratification alone does not guarantee bias removal.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: not separately specified. Tolerances: `{}`. Treat these as limits of the evidence: The frame is recent logins only; the target population is deliberately unspecified.

**G. Relevant failure modes.** Declared labels: `statistical_interpretation_error`, `concept_gap`. Prior case-specific note: `statistical_interpretation_error`, `missing_context_error`, `unsupported_claim`; family `stat_sampling`, validation split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `conceptual_correctness` | Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. |
| `statistical_interpretation` | Explains sampling-frame coverage relative to a stated or clarified target population; does not claim that stratification automatically removes bias. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Explains sampling-frame coverage relative to a stated or clarified target population; does not claim that stratification automatically removes bias. |
| `evidence_uncertainty` | Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Statistics; sampling-frame coverage and selection bias.
- **Task adequacy and assumptions:** The frame is recent logins only; the target population is deliberately unspecified.
- **Reference and example:** Conditionally identify undercoverage if the target includes inactive users, state the target population, compare respondents with it, and consider broader sampling or justified adjustment. Stratification alone does not guarantee bias removal. Synthetic example: a survey of app users who opened the app this week cannot automatically represent all registered users.
- **Scoring and evidence:** `conceptual_correctness`, `statistical_interpretation`, `evidence_uncertainty`; human review needed for conditional reasoning and feasibility.
- **Failure and isolation:** `statistical_interpretation_error`, `missing_context_error`, `unsupported_claim`; family `stat_sampling`, validation split.
- **Disposition:** **Defensible, retain revised.** The answer does not invent the target population or claim a specific bias magnitude.


### stat_ci_interpretation - proposed reference

**A. Identity.** Category `statistics_probability`; family `stat_confidence_interval`; split `validation`; modes `base, context_assisted`; reference ID `stat_ci_interpretation`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Statistics; confidence-interval coverage versus individual outcomes. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a confidence interval for an average is not a range containing 95% of individual observations.

**D. Actual benchmark prompt and inputs.** A study estimates mean delivery delay as 4 days with a 95% confidence interval of 2 to 6 days. Interpret the interval without claiming that 95% of individual deliveries fall in it.

```json
{
  "estimate_days": 4,
  "confidence_level_percent": 95,
  "lower_days": 2,
  "upper_days": 6
}
```

Task adequacy and assumptions from the prior case-specific analysis: Estimate, units, interval, and nominal confidence level are provided; the interval procedure is not, so coverage is appropriately conditional.

**E. Exact recommended reference answer.** Under the assumptions of the stated 95% confidence procedure, intervals produced by that procedure would contain the fixed population mean about 95% of the time over repeated samples. The reported interval is 2 to 6 days; it does not mean that 95% of individual delivery times fall there.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: days. Tolerances: `{}`. Treat these as limits of the evidence: Estimate, units, interval, and nominal confidence level are provided; the interval procedure is not, so coverage is appropriately conditional.

**G. Relevant failure modes.** Declared labels: `statistical_interpretation_error`. Prior case-specific note: `statistical_interpretation_error`, `unsupported_claim`; family `stat_confidence_interval`, validation split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `statistical_interpretation` | Gives the repeated-sampling coverage interpretation without assuming a specific interval method or confusing parameter coverage with individual outcomes. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Gives the repeated-sampling coverage interpretation without assuming a specific interval method or confusing parameter coverage with individual outcomes. |
| `evidence_uncertainty` | Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Statistics; confidence-interval coverage versus individual outcomes.
- **Task adequacy and assumptions:** Estimate, units, interval, and nominal confidence level are provided; the interval procedure is not, so coverage is appropriately conditional.
- **Reference and example:** Under the stated procedure’s assumptions, repeated intervals cover the fixed mean about 95% of the time; the interval does not mean 95% of deliveries lie between 2 and 6 days. Synthetic example: a confidence interval for an average is not a range containing 95% of individual observations.
- **Scoring and evidence:** `statistical_interpretation`, `evidence_uncertainty`, `business_relevance`; semantic human review required.
- **Failure and isolation:** `statistical_interpretation_error`, `unsupported_claim`; family `stat_confidence_interval`, validation split.
- **Disposition:** **Defensible, retain revised.** It neither assumes an interval construction nor confuses parameter coverage with individual variation.


### stat_pvalue - proposed reference

**A. Identity.** Category `statistics_probability`; family `stat_pvalue_meaning`; split `hidden_test`; modes `base, context_assisted`; reference ID `stat_pvalue`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Statistics; conditional p-value meaning, threshold, effect size, and null probability. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a small p-value alone does not quantify practical impact.

**D. Actual benchmark prompt and inputs.** A test reports p = 0.03 under a preselected 0.05 threshold. State what this says under the null model and what it does not establish about effect size or the probability the null is true.

```json
{
  "p_value": 0.03,
  "threshold": 0.05,
  "test_design": "not otherwise specified"
}
```

Task adequacy and assumptions from the prior case-specific analysis: p=0.03 and preselected alpha=0.05 are given; test statistic/extremeness rule and test design are not.

**E. Exact recommended reference answer.** For a specified test and its extremeness rule, if the null model and assumptions hold, p=0.03 is the probability of a result at least as extreme under that null. It is below the prespecified 0.05 threshold, but is not the probability that the null is true and does not measure effect size. The test design is not supplied, so the exact extremeness rule cannot be made more specific.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: not separately specified. Tolerances: `{}`. Treat these as limits of the evidence: p=0.03 and preselected alpha=0.05 are given; test statistic/extremeness rule and test design are not.

**G. Relevant failure modes.** Declared labels: `statistical_interpretation_error`, `arithmetic_error`. Prior case-specific note: `statistical_interpretation_error`, `unsupported_claim`; family `stat_pvalue_meaning`, hidden-test split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `statistical_interpretation` | Defines p conditionally on the specified test's statistic/extremeness rule; rejects null-probability and effect-size interpretations. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Defines p conditionally on the specified test's statistic/extremeness rule; rejects null-probability and effect-size interpretations. |
| `evidence_uncertainty` | Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Statistics; conditional p-value meaning, threshold, effect size, and null probability.
- **Task adequacy and assumptions:** p=0.03 and preselected alpha=0.05 are given; test statistic/extremeness rule and test design are not.
- **Reference and example:** Conditional on the specified test and null assumptions, p is the probability of a result at least as extreme under that null. It is below 0.05 but is neither the probability the null is true nor an effect-size measure. Synthetic example: a small p-value alone does not quantify practical impact.
- **Scoring and evidence:** `numerical_correctness`, `statistical_interpretation`, `evidence_uncertainty`; threshold comparison is deterministic, but interpretation needs a human rubric.
- **Failure and isolation:** `statistical_interpretation_error`, `unsupported_claim`; family `stat_pvalue_meaning`, hidden-test split.
- **Disposition:** **Defensible, retain revised.** The answer explicitly limits specificity because the test design is absent.


### stat_test_selection - proposed reference

**A. Identity.** Category `statistics_probability`; family `stat_test_choice`; split `hidden_test`; modes `base, context_assisted`; reference ID `stat_test_selection`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Statistics; selecting a test conditional on estimand and design. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** comparing medians is not automatically an equivalent test of mean difference.

**D. Actual benchmark prompt and inputs.** You have two independent groups and a continuous outcome. The target estimand is the difference in population means. Before choosing a test, name the sample-size, distribution, outlier, and design details you need; give a conditional method choice without assuming those details.

```json
{
  "groups": 2,
  "independent": true,
  "outcome": "continuous",
  "sample_sizes_and_distribution": null,
  "estimand": "difference in population means"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Two independent groups, continuous outcome, and difference in population means are explicit. Sample sizes, distribution, outliers, sampling, and design details are requested rather than assumed.

**E. Exact recommended reference answer.** For the stated difference in population means, a Welch two-sample t-test is a conditional option for independent groups. Confirm sample sizes, distribution/outliers, and whether the sampling/design assumptions are reasonable. A robust or nonparametric method may target a different estimand and is not automatically interchangeable.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: not separately specified. Tolerances: `{}`. Treat these as limits of the evidence: Two independent groups, continuous outcome, and difference in population means are explicit. Sample sizes, distribution, outliers, sampling, and design details are requested rather than assumed.

**G. Relevant failure modes.** Declared labels: `statistical_interpretation_error`, `missing_context_error`. Prior case-specific note: `formula_selection_error`, `statistical_interpretation_error`, `missing_context_error`; family `stat_test_choice`, hidden-test split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Gives Welch's test only as a conditional method for the stated independent-group mean difference and distinguishes alternatives with different estimands. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Gives Welch's test only as a conditional method for the stated independent-group mean difference and distinguishes alternatives with different estimands. |
| `statistical_interpretation` | Interprets the result only to the extent supported by the design, assumptions, and stated population. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Interprets the result only to the extent supported by the design, assumptions, and stated population. |
| `evidence_uncertainty` | Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Statistics; selecting a test conditional on estimand and design.
- **Task adequacy and assumptions:** Two independent groups, continuous outcome, and difference in population means are explicit. Sample sizes, distribution, outliers, sampling, and design details are requested rather than assumed.
- **Reference and example:** Welch’s two-sample t-test is a conditional option; confirm the requested details and assumptions. Robust/nonparametric alternatives may target a different estimand. Synthetic example: comparing medians is not automatically an equivalent test of mean difference.
- **Scoring and evidence:** `formula_method_selection`, `statistical_interpretation`, `evidence_uncertainty`; all require domain review.
- **Failure and isolation:** `formula_selection_error`, `statistical_interpretation_error`, `missing_context_error`; family `stat_test_choice`, hidden-test split.
- **Disposition:** **Defensible, retain revised.** Welch is not asserted as universally correct; the condition and estimand are explicit.


### stat_multiple_comparisons - proposed reference

**A. Identity.** Category `statistics_probability`; family `stat_multiple_testing`; split `hidden_test`; modes `base, context_assisted`; reference ID `stat_multiple_comparisons`; current status `proposed_reference` (prior status `revised`); scorer `numeric_values`.

**B. Real-world skill.** Statistics; family-wise testing context and expected false positives. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** expected count is an expectation over repeated testing, not a verdict on one result.

**D. Actual benchmark prompt and inputs.** An analyst tests 20 independent null outcomes at alpha 0.05 and highlights one with p = 0.04. Calculate the expected number of false positives under the global null, explain why the finding needs caution, and name a suitable follow-up.

```json
{
  "tests": 20,
  "per_test_alpha": 0.05,
  "smallest_p_value": 0.04
}
```

Task adequacy and assumptions from the prior case-specific analysis: Twenty tests, per-test alpha 0.05, global null, and observed p=0.04 are specified. Independence is stated but is not needed for the expected count.

**E. Exact recommended reference answer.** Under the global null and correctly calibrated tests at alpha 0.05, the expected number of false positives across 20 tests is 1. A p-value of 0.04 is not thereby known to be false and does not establish effect size; report the tested family and use a prespecified multiplicity procedure or independent confirmation.

**F. Reasoning.** Expected false positives = sum of per-test alpha under the global null = 20 * 0.05 = 1; independence is not required for this expectation Units: expected false positives. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Twenty tests, per-test alpha 0.05, global null, and observed p=0.04 are specified. Independence is stated but is not needed for the expected count.

**G. Relevant failure modes.** Declared labels: `statistical_interpretation_error`. Prior case-specific note: `arithmetic_error`, `statistical_interpretation_error`, `unsupported_claim`; family `stat_multiple_testing`, hidden-test split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `statistical_interpretation` | Explains expected false-positive count under the global null and does not classify the p=0.04 finding as false. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Explains expected false-positive count under the global null and does not classify the p=0.04 finding as false. |
| `evidence_uncertainty` | Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: expected_false_positives = 1. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Statistics; family-wise testing context and expected false positives.
- **Task adequacy and assumptions:** Twenty tests, per-test alpha 0.05, global null, and observed p=0.04 are specified. Independence is stated but is not needed for the expected count.
- **Reference and example:** Expected false positives under the global null equal `20 × 0.05 = 1`; this does not prove the p=0.04 finding is false or provide its effect size. Report the tested family and use a prespecified multiplicity procedure or independent confirmation. Synthetic example: expected count is an expectation over repeated testing, not a verdict on one result.
- **Scoring and evidence:** `statistical_interpretation`, `evidence_uncertainty`; numeric expected count is deterministic, cautions and follow-up need human review.
- **Failure and isolation:** `arithmetic_error`, `statistical_interpretation_error`, `unsupported_claim`; family `stat_multiple_testing`, hidden-test split.
- **Disposition:** **Defensible, retain revised.** The expectation is valid under the global null and calibrated tests; independence is not falsely treated as necessary.


### data_monthly_margin - proposed reference

**A. Identity.** Category `dataset_reasoning`; family `data_monthly_margin`; split `development`; modes `base, context_assisted, tool_assisted`; reference ID `data_monthly_margin`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Row-wise calculation of profit and margin; prompt supplies the formula and all monthly revenue/cost values. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** compute each row’s profit before dividing by that same row’s revenue.

**D. Actual benchmark prompt and inputs.** For each month, calculate profit and profit margin from this table. Use profit = revenue - costs and margin = profit / revenue × 100. January: revenue 100, costs 70. February: revenue 120, costs 90. March: revenue 150, costs 105.

```json
{
  "rows": [
    {
      "month": "January",
      "revenue": "100",
      "costs": "70"
    },
    {
      "month": "February",
      "revenue": "120",
      "costs": "90"
    },
    {
      "month": "March",
      "revenue": "150",
      "costs": "105"
    }
  ]
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** January profit 30 and margin 30%; February profit 30 and margin 25%; March profit 45 and margin 30%.

**F. Reasoning.** Jan:100-70=30;30/100*100=30. Feb:120-90=30;30/120*100=25. Mar:150-105=45;45/150*100=30 Units: same currency units; percent. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `arithmetic_error`, `formula_selection_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Uses revenue as denominator for each month's margin. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Uses revenue as denominator for each month's margin. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: January_profit = 3E+1; January_margin_percent = 3E+1; February_profit = 3E+1; February_margin_percent = 25; March_profit = 45; March_margin_percent = 3E+1. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Row-wise calculation of profit and margin; prompt supplies the formula and all monthly revenue/cost values.
- **Reference / example:** January 30/30%; February 30/25%; March 45/30%. Synthetic example: compute each row’s profit before dividing by that same row’s revenue.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `business_relevance`; six values are deterministic, but row labels and formula interpretation should be checked in the response.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`; family `data_monthly_margin`, development.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Formula and per-month denominators are explicit.


### data_sales_vs_margin - proposed reference

**A. Identity.** Category `dataset_reasoning`; family `data_sales_margin_comparison`; split `development`; modes `base, context_assisted`; reference ID `data_sales_vs_margin`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Distinguishing revenue scale from margin ranking; both region revenue and profit are explicit. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** higher sales do not guarantee higher percentage margin.

**D. Actual benchmark prompt and inputs.** North has revenue 200 and profit 40; South has revenue 100 and profit 30. Identify which has higher sales and which has higher profit margin.

```json
{
  "North": {
    "revenue": "200",
    "profit": "40"
  },
  "South": {
    "revenue": "100",
    "profit": "30"
  }
}
```

Task adequacy and assumptions from the prior case-specific analysis: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**E. Exact recommended reference answer.** North has higher sales (200 versus 100); South has higher margin (30% versus 20%).

**F. Reasoning.** North:40/200*100=20; South:30/100*100=30 Units: currency; percent. Tolerances: `{"percent": "0.0001"}`. Treat these as limits of the evidence: Use only the actual prompt and inputs below; do not infer unstated definitions or assumptions.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: See the source failure labels; do not invent additional labels.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Selects the requested method and applies the stated population, denominator, grain, and assumptions. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Selects the requested method and applies the stated population, denominator, grain, and assumptions. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `business_relevance` | Distinguishes sales volume from margin ranking. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Distinguishes sales volume from margin ranking. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: North_margin_percent = 2E+1; South_margin_percent = 3E+1. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Competency and adequacy:** Distinguishing revenue scale from margin ranking; both region revenue and profit are explicit.
- **Reference / example:** North has greater revenue (200 vs 100); South has greater margin (30% vs 20%). Synthetic example: higher sales do not guarantee higher percentage margin.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `business_relevance`; numeric scorer verifies margin values only, so sales ranking and distinction require manual scoring.
- **Failure / split:** `formula_selection_error`, `arithmetic_error`, `result_interpretation_error`; family `data_sales_margin_comparison`, development.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Reference is correct, but the human rubric is essential because numeric checking alone does not score the sales comparison.


### data_missing_required_column - proposed reference

**A. Identity.** Category `dataset_reasoning`; family `data_required_columns`; split `development`; modes `base, context_assisted`; reference ID `data_missing_required_column`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Dataset reasoning; abstention when profit-margin inputs are missing. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** an order value column may be gross order amount and not net revenue.

**D. Actual benchmark prompt and inputs.** A table contains order_date and order_value, but no cost or profit field. Can you calculate profit margin? State what you would need to confirm about order_value, its currency and grain, what information is missing, and any assumptions you would need.

```json
{
  "columns": [
    "order_date",
    "order_value"
  ]
}
```

Task adequacy and assumptions from the prior case-specific analysis: Only `order_date` and `order_value` are named. Revenue semantics, currency, grain, matching cost/profit, population, and period are intentionally unresolved.

**E. Exact recommended reference answer.** The listed columns alone are insufficient. Confirm whether order_value represents revenue, its currency and grain, and obtain compatible cost data or profit for the same population and period. Define profit consistently; when revenue is nonzero, margin is profit divided by revenue, multiplied by 100 if expressed as a percentage. Do not infer missing cost or profit.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: not separately specified. Tolerances: `{}`. Treat these as limits of the evidence: Only `order_date` and `order_value` are named. Revenue semantics, currency, grain, matching cost/profit, population, and period are intentionally unresolved.

**G. Relevant failure modes.** Declared labels: `missing_context_error`, `unsupported_execution_claim`. Prior case-specific note: `missing_context_error`, `unsupported_claim`, `formula_selection_error`; family `data_required_columns`, development split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Selects the requested method and applies the stated population, denominator, grain, and assumptions. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Selects the requested method and applies the stated population, denominator, grain, and assumptions. |
| `evidence_uncertainty` | Does not assume order_value is revenue; requests compatible cost/profit and matching population/period definitions. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Does not assume order_value is revenue; requests compatible cost/profit and matching population/period definitions. |
| `abstention_accuracy` | Does not infer unobserved costs or profit. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Does not infer unobserved costs or profit. |
| `unsupported_claims` | Makes no claim beyond the supplied evidence or observable trace. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Makes no claim beyond the supplied evidence or observable trace. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Dataset reasoning; abstention when profit-margin inputs are missing.
- **Task adequacy and assumptions:** Only `order_date` and `order_value` are named. Revenue semantics, currency, grain, matching cost/profit, population, and period are intentionally unresolved.
- **Reference and example:** Do not infer that order value is revenue or invent costs. Confirm its meaning/currency/grain and obtain compatible costs or profit; define profit and use profit/revenue for nonzero revenue. Synthetic example: an order value column may be gross order amount and not net revenue.
- **Scoring and evidence:** `formula_method_selection`, `evidence_uncertainty`, `abstention_accuracy`, `unsupported_claims`; all require semantic review.
- **Failure and isolation:** `missing_context_error`, `unsupported_claim`, `formula_selection_error`; family `data_required_columns`, development split.
- **Disposition:** **Defensible, retain revised.** It names the missing information without silently resolving business definitions.


### data_unequal_group_sizes - proposed reference

**A. Identity.** Category `dataset_reasoning`; family `percentage_rate_denominator`; split `validation`; modes `base, context_assisted, tool_assisted`; reference ID `data_unequal_group_sizes`; current status `proposed_reference` (prior status `not_required`); scorer `numeric_values`.

**B. Real-world skill.** Dataset reasoning; compare rates rather than raw successes across unequal denominators. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** counts alone conflate group size and performance.

**D. Actual benchmark prompt and inputs.** Segment A has 9 successes in 10 trials; Segment B has 90 successes in 200 trials. Compare the rates and explain why comparing raw success counts would mislead.

```json
{
  "A": {
    "successes": 9,
    "trials": 10
  },
  "B": {
    "successes": 90,
    "trials": 200
  }
}
```

Task adequacy and assumptions from the prior case-specific analysis: Segment A has 9/10 and B 90/200; success and trial definitions are shared.

**E. Exact recommended reference answer.** A rate is 90%; B rate is 45%. A has the higher rate despite fewer total successes; compare successes divided by each segment's trials.

**F. Reasoning.** 9/10*100=90;90/200*100=45 Units: percent. Tolerances: `{"percent": "0.0001"}`. Treat these as limits of the evidence: Segment A has 9/10 and B 90/200; success and trial definitions are shared.

**G. Relevant failure modes.** Declared labels: `formula_selection_error`, `arithmetic_error`. Prior case-specific note: `formula_selection_error`, `arithmetic_error`; family `percentage_rate_denominator`, validation, shared with related rate variants in the same split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `formula_method_selection` | Uses within-group denominator and explains why counts alone mislead. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Uses within-group denominator and explains why counts alone mislead. |
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: A_percent = 9E+1; B_percent = 45. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Dataset reasoning; compare rates rather than raw successes across unequal denominators.
- **Task adequacy and assumptions:** Segment A has 9/10 and B 90/200; success and trial definitions are shared.
- **Reference / example:** A=90%, B=45%; A has the higher rate despite fewer successes. Synthetic example: counts alone conflate group size and performance.
- **Scoring:** `formula_method_selection`, `numerical_correctness`, `business_relevance`; rates are deterministic, explanation is not scored by numeric helper.
- **Failure and isolation:** `formula_selection_error`, `arithmetic_error`; family `percentage_rate_denominator`, validation, shared with related rate variants in the same split.
- **Disposition:** **Retain not_required for reference approval; not an automatic response pass.** Denominators and interpretation are clear.


### data_missingness_impact - proposed reference

**A. Identity.** Category `dataset_reasoning`; family `data_missingness_impact`; split `validation`; modes `base, context_assisted`; reference ID `data_missingness_impact`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Dataset reasoning; missing-data mechanisms and comparison bias. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** if high-income records are disproportionately absent in one region, complete-case comparisons may shift.

**D. Actual benchmark prompt and inputs.** Income is missing for 35% of records, and missingness is more common in one region. Explain why dropping all incomplete rows could bias a regional comparison and what diagnostics to perform.

```json
{
  "missing_percent": 35,
  "missingness_pattern": "more frequent in one region"
}
```

Task adequacy and assumptions from the prior case-specific analysis: 35% missingness and a regional imbalance are stated. Missingness mechanism and target estimand are not.

**E. Exact recommended reference answer.** Because income is missing more often in one region, dropping incomplete records could change regional composition and bias the comparison, depending on the target estimand and missingness mechanism. Profile missingness by region and relevant variables; investigate plausible mechanisms and compare justified sensitivity approaches.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: not separately specified. Tolerances: `{}`. Treat these as limits of the evidence: 35% missingness and a regional imbalance are stated. Missingness mechanism and target estimand are not.

**G. Relevant failure modes.** Declared labels: `statistical_interpretation_error`, `concept_gap`. Prior case-specific note: `statistical_interpretation_error`, `missing_context_error`, `unsupported_claim`; family `data_missingness_impact`, validation split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `statistical_interpretation` | Describes possible composition change and bias without asserting a specific missingness mechanism. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Describes possible composition change and bias without asserting a specific missingness mechanism. |
| `evidence_uncertainty` | Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Dataset reasoning; missing-data mechanisms and comparison bias.
- **Task adequacy and assumptions:** 35% missingness and a regional imbalance are stated. Missingness mechanism and target estimand are not.
- **Reference and example:** Dropping incomplete rows could change regional composition and bias a comparison depending on estimand/mechanism; profile missingness by region and relevant variables and compare justified sensitivity approaches. Synthetic example: if high-income records are disproportionately absent in one region, complete-case comparisons may shift.
- **Scoring and evidence:** `statistical_interpretation`, `evidence_uncertainty`, `business_relevance`; human review must ensure it does not claim MCAR/MAR/MNAR without evidence.
- **Failure and isolation:** `statistical_interpretation_error`, `missing_context_error`, `unsupported_claim`; family `data_missingness_impact`, validation split.
- **Disposition:** **Defensible, retain revised.** The reference describes possible bias and requests diagnostics without fabricating a mechanism.


### data_simpsons_paradox - proposed reference

**A. Identity.** Category `dataset_reasoning`; family `data_simpsons`; split `hidden_test`; modes `base, context_assisted`; reference ID `data_simpsons_paradox`; current status `proposed_reference` (prior status `revised`); scorer `numeric_values`.

**B. Real-world skill.** Dataset reasoning; stratified rates, aggregation, and target-population comparison. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** strata with different prevalence can reverse aggregate rankings.

**D. Actual benchmark prompt and inputs.** Treatment A succeeds for 9/10 high-risk and 1/100 low-risk cases. Treatment B succeeds for 80/100 high-risk and 0/10 low-risk cases. Explain how A can have a higher success rate within both risk groups but a lower overall rate, and what comparison is needed before recommending treatment.

```json
{
  "high_risk": {
    "A": {
      "successes": 9,
      "trials": 10
    },
    "B": {
      "successes": 80,
      "trials": 100
    }
  },
  "low_risk": {
    "A": {
      "successes": 1,
      "trials": 100
    },
    "B": {
      "successes": 0,
      "trials": 10
    }
  }
}
```

Task adequacy and assumptions from the prior case-specific analysis: Counts and risk strata are explicit. Both within-stratum and overall rates have unique denominators.

**E. Exact recommended reference answer.** A's rates are 90% high-risk and 1% low-risk; B's are 80% high-risk and 0% low-risk. Overall A is 10/110 = 9.09%, while B is 80/110 = 72.73%, because group mixes differ. Show stratum and aggregate rates; any standardized comparison should use a prespecified target population, and these data do not establish universal treatment superiority.

**F. Reasoning.** A high=9/10=90%; B high=80/100=80%; A low=1/100=1%; B low=0/10=0%; A total=10/110=9.0909%; B total=80/110=72.7273% Units: percent. Tolerances: `{"percent": "0.0001"}`. Treat these as limits of the evidence: Counts and risk strata are explicit. Both within-stratum and overall rates have unique denominators.

**G. Relevant failure modes.** Declared labels: `arithmetic_error`, `statistical_interpretation_error`, `concept_gap`. Prior case-specific note: `arithmetic_error`, `statistical_interpretation_error`, `unsupported_claim`; family `data_simpsons`, hidden-test split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: numeric_values checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `statistical_interpretation` | Explains the aggregate reversal through differing group composition and distinguishes descriptive rates from a target-population-adjusted comparison. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Explains the aggregate reversal through differing group composition and distinguishes descriptive rates from a target-population-adjusted comparison. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
| `evidence_uncertainty` | Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: A_high_percent = 9E+1; B_high_percent = 8E+1; A_low_percent = 1; B_low_percent = 0; A_overall_percent = 9.090909090909090909090909091; B_overall_percent = 72.72727272727272727272727273. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Exact value(s), tolerance boundary and just-outside value, missing and extra observations, wrong denominator/formula, and verify no unscored semantic dimension receives credit. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Dataset reasoning; stratified rates, aggregation, and target-population comparison.
- **Task adequacy and assumptions:** Counts and risk strata are explicit. Both within-stratum and overall rates have unique denominators.
- **Reference and example:** A: 90% high-risk, 1% low-risk, 10/110 = 9.09% overall. B: 80% high-risk, 0% low-risk, 80/110 = 72.73% overall. Differing group mixes explain the aggregate pattern; standardize only to a prespecified population and do not infer universal treatment superiority. Synthetic example: strata with different prevalence can reverse aggregate rankings.
- **Scoring and evidence:** `numerical_correctness`, `statistical_interpretation`, `business_relevance`, `evidence_uncertainty`. Six numeric rates are mechanically verifiable; interpretation and recommendation restraint need human review.
- **Failure and isolation:** `arithmetic_error`, `statistical_interpretation_error`, `unsupported_claim`; family `data_simpsons`, hidden-test split.
- **Disposition:** **Defensible, retain revised.** Rates and denominators were independently checked; the reference appropriately separates description from causal treatment recommendation.


### data_forecast_uncertainty - proposed reference

**A. Identity.** Category `dataset_reasoning`; family `data_forecast`; split `hidden_test`; modes `base, context_assisted`; reference ID `data_forecast_uncertainty`; current status `proposed_reference` (prior status `revised`); scorer `human_review`.

**B. Real-world skill.** Dataset reasoning; forecast intervals and uncertainty communication. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** planning can consider the range while recognizing model/forecast uncertainty.

**D. Actual benchmark prompt and inputs.** A forecast estimates next-quarter demand at 1,000 units, with a prediction interval of 800 to 1,250. Explain the estimate and interval without presenting the point forecast as certain.

```json
{
  "point_forecast": 1000,
  "prediction_interval": [
    800,
    1250
  ],
  "period": "next quarter"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Point estimate, next-quarter period, and interval endpoints are given; nominal coverage and interval procedure are absent.

**E. Exact recommended reference answer.** The point forecast is 1,000 units for next quarter, with the stated prediction interval of 800 to 1,250 under the model and interval specification. Actual demand is uncertain. The prompt gives no nominal interval coverage level, so do not assign one.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: units next quarter. Tolerances: `{}`. Treat these as limits of the evidence: Point estimate, next-quarter period, and interval endpoints are given; nominal coverage and interval procedure are absent.

**G. Relevant failure modes.** Declared labels: `statistical_interpretation_error`, `unsupported_execution_claim`. Prior case-specific note: `statistical_interpretation_error`, `unsupported_claim`; family `data_forecast`, hidden-test split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `statistical_interpretation` | Reports point estimate and interval, recognizes model uncertainty, and does not invent a coverage level. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Reports point estimate and interval, recognizes model uncertainty, and does not invent a coverage level. |
| `evidence_uncertainty` | Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. |
| `business_relevance` | Connects the result or recommendation to the stated business decision without introducing unsupported facts. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Connects the result or recommendation to the stated business decision without introducing unsupported facts. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `human_review`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Plausible fully responsive and misleading/unsupported answers; confirm all semantic dimensions remain unassessed by automation. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Dataset reasoning; forecast intervals and uncertainty communication.
- **Task adequacy and assumptions:** Point estimate, next-quarter period, and interval endpoints are given; nominal coverage and interval procedure are absent.
- **Reference and example:** State 1,000 units with the supplied 800–1,250 prediction interval and uncertainty; do not invent coverage. Synthetic example: planning can consider the range while recognizing model/forecast uncertainty.
- **Scoring and evidence:** `statistical_interpretation`, `evidence_uncertainty`, `business_relevance`; semantic review is required, especially to avoid assigning unsupported coverage.
- **Failure and isolation:** `statistical_interpretation_error`, `unsupported_claim`; family `data_forecast`, hidden-test split.
- **Disposition:** **Defensible, retain revised.** The answer preserves the given interval and calls out the missing coverage level.


### tool_json_only - proposed reference

**A. Identity.** Category `tools_reliability`; family `tool_json_format`; split `development`; modes `base, context_assisted`; reference ID `tool_json_only`; current status `proposed_reference` (prior status `not_required`); scorer `json_object`.

**B. Real-world skill.** Tool/output reliability; strict JSON-only output with exact keys, values, and types. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a numeric `3` must not be replaced by the string `"3"`.

**D. Actual benchmark prompt and inputs.** Return only a JSON object with exactly the keys `status` and `count`. Use status `ok` and numeric count 3. Do not use Markdown fences.

```json
{
  "requested_shape": {
    "status": "string",
    "count": "number"
  }
}
```

Task adequacy and assumptions from the prior case-specific analysis: Required object is fully specified: status string `ok`, numeric count 3; no fences or prose.

**E. Exact recommended reference answer.** {"status":"ok","count":3}

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: not separately specified. Tolerances: `{}`. Treat these as limits of the evidence: Required object is fully specified: status string `ok`, numeric count 3; no fences or prose.

**G. Relevant failure modes.** Declared labels: `output_format_failure`. Prior case-specific note: `output_format_failure`; family `tool_json_format`, development.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `output_instruction_compliance` | Strict JSON parse, exact keys/types/values, no fences or surrounding prose. | `deterministic_component` | Deterministic component only: json_object checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Strict JSON parse, exact keys/types/values, no fences or surrounding prose. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `json_object`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Exact object; malformed/fenced output; extra prose/key; wrong type/value; only output-format dimension is machine-scored. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Tool/output reliability; strict JSON-only output with exact keys, values, and types.
- **Task adequacy and assumptions:** Required object is fully specified: status string `ok`, numeric count 3; no fences or prose.
- **Reference / example:** `{"status":"ok","count":3}`. Synthetic example: a numeric `3` must not be replaced by the string `"3"`.
- **Scoring:** `output_instruction_compliance`; complete JSON parse, exact key/type/value checks are deterministic. This tests format, not broader semantic ability.
- **Failure and isolation:** `output_format_failure`; family `tool_json_format`, development.
- **Disposition:** **Retain not_required.** The scoring mechanism fully covers this narrow format task and does not claim general semantic correctness.


### tool_exact_sentence_count - proposed reference

**A. Identity.** Category `tools_reliability`; family `tool_sentence_count`; split `hidden_test`; modes `base, context_assisted`; reference ID `tool_exact_sentence_count`; current status `proposed_reference` (prior status `revised`); scorer `sentence_count`.

**B. Real-world skill.** Tool/output reliability; exact sentence and abbreviation compliance with semantic content. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** “Revenue is measured before costs. Profit is what remains after costs.”

**D. Actual benchmark prompt and inputs.** Explain that revenue is before costs and profit is after costs in exactly two sentences. Do not use abbreviations.

```json
{}
```

Task adequacy and assumptions from the prior case-specific analysis: It requires two sentences explaining revenue-before-costs and profit-after-costs and prohibits abbreviations.

**E. Exact recommended reference answer.** A response must contain exactly two non-empty sentences. One sentence must explain that revenue is before costs; the other must explain that profit is after costs. It must not use abbreviations; sentence-count and abbreviation checks are separate from human semantic review.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: not separately specified. Tolerances: `{}`. Treat these as limits of the evidence: It requires two sentences explaining revenue-before-costs and profit-after-costs and prohibits abbreviations.

**G. Relevant failure modes.** Declared labels: `output_format_failure`, `concept_gap`. Prior case-specific note: `output_format_failure`, `concept_gap`; family `tool_sentence_count`, hidden-test split.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `abbreviation_compliance` | No detected abbreviations under the documented abbreviation pattern; human review may catch forms outside the detector. | `deterministic_component` | Deterministic component only: sentence_count checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: No detected abbreviations under the documented abbreviation pattern; human review may catch forms outside the detector. |
| `conceptual_correctness` | A human verifies revenue/profit meaning separately. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: A human verifies revenue/profit meaning separately. |
| `output_instruction_compliance` | Follows the requested format, scope, assumptions, and output constraints. | `deterministic_component` | Deterministic component only: sentence_count checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Follows the requested format, scope, assumptions, and output constraints. |
| `sentence_count_compliance` | Exactly two sentences under the documented terminal-punctuation convention. | `deterministic_component` | Deterministic component only: sentence_count checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Exactly two sentences under the documented terminal-punctuation convention. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `sentence_count`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Valid two-sentence answer; wrong counts; decimal points; common abbreviations including Dr. and e.g.; punctuation/ellipsis boundaries; output-instruction full credit requires both explicit format constraints. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=true` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Tool/output reliability; exact sentence and abbreviation compliance with semantic content.
- **Task adequacy and assumptions:** It requires two sentences explaining revenue-before-costs and profit-after-costs and prohibits abbreviations.
- **Reference and example:** Two non-empty sentences, one for each concept, and no abbreviations. Synthetic example: “Revenue is measured before costs. Profit is what remains after costs.”
- **Scoring and evidence:** `abbreviation_compliance`, `conceptual_correctness`, `output_instruction_compliance`, `sentence_count_compliance`. Sentence boundary and common abbreviation checks are deterministic heuristics; conceptual correctness remains human-reviewed. Full output-instruction credit requires both explicit format constraints to pass.
- **Failure and isolation:** `output_format_failure`, `concept_gap`; family `tool_sentence_count`, hidden-test split.
- **Disposition:** **Defensible, retain revised.** The scorer now keeps sentence count and abbreviation scores separate and does not give full instruction credit when either fails. Abbreviation detection is not exhaustive; the hidden case is repository-readable, not protected.

## Not-required references (16)

All 16 test meaningful competencies and retain reference answers/rubrics; none
is excluded or deleted. Their status is retained as a reference lifecycle
label, not as a claim that every answer dimension is machine-scored. For each
numeric case, the deterministic helper checks the specified values, while
declared formula, interpretation, or business dimensions remain subject to
manual scoring. Tool helpers likewise do not prove that free text satisfies
the full rubric.


### tool_arguments_valid - proposed reference

**A. Identity.** Category `tools_reliability`; family `percentage_rate_denominator`; split `validation`; modes `base, context_assisted, tool_assisted`; reference ID `tool_arguments_valid`; current status `proposed_reference` (prior status `not_required`); scorer `tool_arguments`.

**B. Real-world skill.** Tool/output reliability; correct an invalid argument shape, preserve required string type, and identify the defect. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** `{"expression":"18 / 600 * 100"}` has the required property/type.

**D. Actual benchmark prompt and inputs.** A proposed calculator call is `{"expression": 3, "extra": true}`. The contract requires one expression string. Correct the arguments to calculate 18 divided by 600 as a percentage, and identify why the proposed arguments are invalid.

```json
{
  "proposed_arguments": {
    "expression": 3,
    "extra": true
  },
  "requested_operation": "(18 / 600) * 100"
}
```

Task adequacy and assumptions from the prior case-specific analysis: Contract requires one expression string; proposed arguments have numeric expression plus an extra property; operation is 18/600 as percent.

**E. Exact recommended reference answer.** Reject the proposed numeric expression and extra property. Correct arguments use the single required string property `expression`, with an arithmetic expression equivalent to `(18 / 600) * 100`; expected result is 3%.

**F. Reasoning.** 18 / 600 * 100 = 3 Units: percent. Tolerances: `{"value": "0.0001"}`. Treat these as limits of the evidence: Contract requires one expression string; proposed arguments have numeric expression plus an extra property; operation is 18/600 as percent.

**G. Relevant failure modes.** Declared labels: `tool_selection_error`, `invalid_tool_arguments`. Prior case-specific note: `tool_selection_error`, `invalid_tool_arguments`, `formula_selection_error`; family `percentage_rate_denominator`, validation.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `deterministic_component` | Deterministic component only: tool_arguments checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `tool_use_correctness` | Uses exact allowed tool and arguments satisfy its declared schema. | `deterministic_component` | Deterministic component only: tool_arguments checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Uses exact allowed tool and arguments satisfy its declared schema. |
| `formula_method_selection` | Selects the requested method and applies the stated population, denominator, grain, and assumptions. | `deterministic_component` | Deterministic component only: tool_arguments checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Selects the requested method and applies the stated population, denominator, grain, and assumptions. |
| `conceptual_correctness` | Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. |
**I. Deterministic verification.** `validate_numeric_references()` recomputes from the authoritative fixture: percent = 3. Stored references are compared with their configured tolerances. This establishes arithmetic only.

**J. Regression tests.** Valid tool/schema/formula; wrong tool; invalid/extra argument; equivalent ratio; coincidental `1 + 2`; independent tool/formula/arithmetic scores. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Tool/output reliability; correct an invalid argument shape, preserve required string type, and identify the defect.
- **Task adequacy and assumptions:** Contract requires one expression string; proposed arguments have numeric expression plus an extra property; operation is 18/600 as percent.
- **Reference / example:** Use one string expression equivalent to `(18/600)×100`, result 3%, and explain both schema violations. Synthetic example: `{"expression":"18 / 600 * 100"}` has the required property/type.
- **Scoring:** `tool_use_correctness`, `numerical_correctness`, `formula_method_selection`, `conceptual_correctness`. The scorer verifies tool, schema, expression structure, and value; explanation remains human-scored. The scorer now reports that review as pending, not `not_required`.
- **Failure and isolation:** `tool_selection_error`, `invalid_tool_arguments`, `formula_selection_error`; family `percentage_rate_denominator`, validation.
- **Disposition:** **Retain not_required for reference approval; response is not auto-passed.** The reference is determinate from the contract. A false-negative formula-equivalence defect was corrected; semantically equivalent ratio rearrangements are accepted while coincidental sums are rejected.


### tool_trace_result_grounding - proposed reference

**A. Identity.** Category `tools_reliability`; family `tool_result_grounding`; split `hidden_test`; modes `base, context_assisted, tool_assisted`; reference ID `tool_trace_result_grounding`; current status `proposed_reference` (prior status `not_required`); scorer `tool_trace`.

**B. Real-world skill.** Tool/output reliability; report an observed result without altering it or claiming unsupported execution. This case tests the task requested in the prompt, not an unstated broader ability.

**C. Real-world illustration (synthetic only; not benchmark input).** a visible failed trace must not be rewritten as a success.

**D. Actual benchmark prompt and inputs.** Report the result of the requested calculation, if it is available. Preserve the returned value and state when the result is unavailable.

```json
{
  "task": "Report a calculation result only when an observed result is provided."
}
```

Task adequacy and assumptions from the prior case-specific analysis: Tool-assisted mode supplies a successful calculator trace of 18.5%; base mode has no result and should state unavailability.

**E. Exact recommended reference answer.** The supplied tool result is 18.5%. Do not claim independent execution beyond the visible trace.

**F. Reasoning.** No separate numerical calculation is stored; answer from the supplied information without inventing missing facts. Units: percent. Tolerances: `{"value": "0"}`. Treat these as limits of the evidence: Tool-assisted mode supplies a successful calculator trace of 18.5%; base mode has no result and should state unavailability.

**G. Relevant failure modes.** Declared labels: `result_interpretation_error`, `unsupported_execution_claim`. Prior case-specific note: `result_interpretation_error`, `unsupported_execution_claim`; family `tool_result_grounding`, hidden_test.

**H. Scoring contract.** 0 = incorrect/unsupported; 1 = partly correct or materially incomplete; 2 = complete and correct; NA = explicitly not applicable only. The rubric criterion is case-specific; absence of deterministic coverage means no automated credit.

| Dimension | Existing rubric criterion | Operational disposition | Machine-check scope / limit |
|---|---|---|---|
| `numerical_correctness` | Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. |
| `tool_use_correctness` | Uses the observed successful trace. | `deterministic_component` | Deterministic component only: tool_trace checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Uses the observed successful trace. |
| `unsupported_claims` | Does not alter the returned value or invent further execution. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Does not alter the returned value or invent further execution. |
| `execution_claim_integrity` | Claims about execution agree with the supplied trace; caller-supplied traces are not authenticated by this helper. | `deterministic_component` | Deterministic component only: tool_trace checks the structured observation; this does not validate the reference semantically or score unobserved response content. Rubric: Claims about execution agree with the supplied trace; caller-supplied traces are not authenticated by this helper. |
| `result_interpretation_error` | Preserves the observed result or failure and does not invent, alter, or overinterpret it. | `not_assessed_by_deterministic_scorer` | Not assessed by the deterministic scorer. The existing human rubric remains the scoring contract; do not award credit automatically. Rubric: Preserves the observed result or failure and does not invent, alter, or overinterpret it. |
**I. Deterministic verification.** No dependable deterministic raw-prose check exists for scorer type `tool_trace`. Only structured observations named in the operational policy are checked; all other dimensions are explicitly not assessed.

**J. Regression tests.** Matching and mismatching trace; failed call honestly reported; fabricated success claim; trace origin remains unauthenticated. Focused suite: `backend/tests/test_ba_da_benchmark.py`; additional offline gates: structural/lifecycle, all numeric references, SQL fixture, and model-input projection checks.

**K. Final disposition.** Retain `proposed_reference`; do not relabel as `approved` from automated checks. Operationally eligible under `validate_baseline_readiness()`. `human_review_required=false` is informational, not a run blocker. No automatic model credit is assigned to dimensions declared not assessed.

**Prior case-specific review evidence (historical lifecycle snapshot; preserved):**

- **Category and competency:** Tool/output reliability; report an observed result without altering it or claiming unsupported execution.
- **Task adequacy and assumptions:** Tool-assisted mode supplies a successful calculator trace of 18.5%; base mode has no result and should state unavailability.
- **Reference / example:** Report the supplied 18.5% only when the trace is present; do not claim independent execution. Synthetic example: a visible failed trace must not be rewritten as a success.
- **Scoring:** `numerical_correctness`, `tool_use_correctness`, `unsupported_claims`, `execution_claim_integrity`, `result_interpretation_error`. Trace fields can be compared deterministically; generated prose is not parsed or authenticated by this helper and needs human review.
- **Failure and isolation:** `result_interpretation_error`, `unsupported_execution_claim`; family `tool_result_grounding`, hidden_test.
- **Disposition:** **Retain not_required for reference approval; response requires review.** The stored expected result matches the fixture, but the status is not evidence that actual generated prose was machine-validated. The hidden test is not access-controlled.

## Benchmark-wide checks and limitations

- **Identity and category coverage:** 60 cases/references; no duplicate or mismatched IDs; 10 cases in each of the six categories.
- **Splits and leakage:** 20 cases per split; every category uses a 4/3/3 distribution. Family isolation and the configured near-duplicate check pass. Related rate, weighted-average, causal, clarification, and profit-margin variants remain within one split per family.
- **Model-input projection:** `model_input()` includes prompt/inputs plus allowed context/tool material and only explicitly visible traces. It excludes reference answers, calculations, scoring rubrics, review labels, family IDs, and split labels. This is a code projection, not an access-control boundary: all JSON, including hidden-test material, is readable in the repository.
- **Manual scoring:** Completeness and human-review status are separate. Empty/partial score maps remain unscored/partial; `NA` is explicit. Revised reference cases remain review-required even when deterministic observations match.
- **Sentence scoring:** Count and abbreviation compliance remain independent. Output-instruction credit requires both explicit constraints. The detector is heuristic, not a general natural-language instruction evaluator.
- **Formula checking:** The calculator scorer accepts multiplicative/divisive rearrangements of the stated numerator, multiplier, and denominator, and rejects same-result expressions formed by unrelated additions or constants. It is specialized to this one contract, not a general symbolic algebra equivalence engine.
- **SQL:** The synthetic fixture uses in-memory SQLite with authorizer restrictions, a single statement, an operation limit, and a row cap. This validates only the fixture/query result and does not establish production SQL safety or semantic intent.
- **Tool traces:** The scorer compares caller-supplied trace objects; it cannot authenticate their origin or grade natural-language output on its own. No real tool adapter is run by this benchmark.
- **Schema validation:** No JSON Schema validator was installed in the environment. JSON parsing, Python structural/reference validation, and the existing schema-contract tests are available and are used; no dependency was installed.

## Prior audit conclusion (historical; superseded by the current operational proposal addendum)

Reference answer corrections:

1. `ba_order_bottleneck`: made the required follow-up an explicit question.
2. `calc_pct_change`: added the requested percentage-change formula.
3. `calc_units`: added the supplied tonnes-to-kilograms conversion.

The latter two records moved from `not_required` to `revised` with
`human_review_required=true`; they are **not approved**. The lifecycle now
contains 19 revised and 16 not-required records.

Disposition counts for the 35 reviewed records:

- **Approved during this review:** 0.
- **Rewritten and then approved:** 0.
- **Rewritten but still revised:** 3 (`ba_order_bottleneck`, `calc_pct_change`,
  `calc_units`).
- **Retained as not_required reference reviews:** 16. None is excluded from
  the benchmark; all have an answer/rubric, while response dimensions not
  covered by deterministic helpers still require scoring.
- **Replaced:** 0.
- **Still requiring human semantic review:** 19 revised references.
- **Intentionally non-scored cases:** 0.

Scorer corrections:

- Accept algebraically equivalent multiplication/division arrangements for
  the declared 18/600 percentage operation, while rejecting coincidental
  arithmetic such as `1 + 2`.
- Report `human_review_status="pending"` when semantic rubric dimensions
  remain unscored, even if the reference lifecycle itself is `not_required`.

**No Qwen/Ollama inference, training, fine-tuning, package installation,
external API call, or Git history operation was performed.** The 19 revised
references still require human semantic review before a baseline result can
be described as fully adjudicated. This was the prior baseline gate; the current offline readiness policy and remaining semantic limitations are stated in the operational addendum below.

## Readiness, findings, and remaining limits

- Lifecycle verified from current source: 25 approved, 35 proposed_reference (19 former revised; 16 former not_required), 0 pending.
- All 60 case/reference IDs are unique and one-to-one; this review intentionally covers exactly the 35 proposals.
- Existing 25 approved answers are not changed by this transition.
- `validate_baseline_readiness()` checks structure, all 25 numeric references, and the SQLite fixture and reports unassessed dimensions; it is a nonblocking operational gate, not semantic approval.
- The hidden-test set is readable from the repository and is not access-controlled. In-memory SQL checks do not prove production safety or query intent. Caller-provided tool traces are not authenticated. Free-text semantic answers are not parsed by keyword rules.
- No model inference, training/fine-tuning, package installation, external API call, or Git history operation was performed.

## Previously corrected references

`ba_order_bottleneck` asks an explicit throughput/capacity follow-up; `calc_pct_change` shows the requested percentage formula; `calc_units` uses the supplied conversion factor. These answers are present in the current source as project-selected proposals where applicable; no approved reference answer was edited.
