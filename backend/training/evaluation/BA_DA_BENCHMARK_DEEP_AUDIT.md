# InsightOS BA/DA Benchmark Deep-Resolution Audit

**Scope:** all 60 current benchmark/reference pairs. This is an offline source audit; no model inference, training, package operation, external API call or historical report edit occurred.

> **Current reference lifecycle:** The inventory and A-K case identities below preserve the earlier review snapshot. The current reference document is v3 and has 25 `approved`, 35 `proposed_reference`, and 0 `pending`; the 35 proposals retain their prior 19 `revised` / 16 `not_required` statuses as provenance. Proposed references are operationally usable but are not human semantic approvals. See `BA_DA_FINAL_35_CASE_REVIEW.md` for the full source-aligned proposal register and dimension-level scoring dispositions.

## Executive summary

The repository contains 60 unique cases with one-to-one references, six categories of 10 cases, 20 cases in each split, and 51 families. At the time of this audit snapshot, lifecycle totals were 25 approved, 19 revised, 16 not_required and zero pending. The current lifecycle is superseded as described above. Automated calculation is not human semantic approval. The earlier review corrected three reference answers; two formerly not_required records were then revised.

Full A-K analyses follow below. Exact prompt, inputs and recommended answer are read directly from the current JSON. Illustrations are explicitly synthetic and do not add benchmark facts.

## Inventory

| ID | Category | Family | Split | Modes | Status | Scorer | Dimensions |
|---|---|---|---|---|---|---|---|
| ba_requirements_vs_solution | business_analyst | ba_requirements | development | base,context_assisted | approved | human_review | conceptual_correctness,business_relevance,evidence_uncertainty |
| ba_stakeholder_authority | business_analyst | ba_stakeholder | development | base,context_assisted | approved | human_review | conceptual_correctness,business_relevance,evidence_uncertainty |
| ba_retention_kpi | business_analyst | clarify_missing_information | validation | base,context_assisted | approved | clarification | clarification_completeness,conceptual_correctness,evidence_uncertainty,formula_method_selection,unsupported_claims |
| ba_order_bottleneck | business_analyst | ba_order_flow | hidden_test | base,context_assisted | revised | numeric_values | conceptual_correctness,business_relevance,evidence_uncertainty |
| ba_revenue_vs_profit | business_analyst | ba_revenue_profit | development | base,context_assisted | approved | human_review | conceptual_correctness,business_relevance,evidence_uncertainty |
| ba_churn_definition | business_analyst | clarify_missing_information | validation | base,context_assisted | approved | clarification | abstention_accuracy,clarification_completeness,evidence_uncertainty,formula_method_selection,unsupported_claims |
| ba_root_cause_correlation | business_analyst | causal_inference_variants | validation | base,context_assisted | approved | human_review | conceptual_correctness,statistical_interpretation,evidence_uncertainty,business_relevance |
| ba_acceptance_criteria | business_analyst | ba_acceptance | development | base,context_assisted | revised | human_review | conceptual_correctness,business_relevance,evidence_uncertainty |
| ba_evidence_recommendation | business_analyst | ba_recommendation | hidden_test | base,context_assisted | revised | numeric_values | business_relevance,evidence_uncertainty,unsupported_claims |
| ba_ambiguous_request | business_analyst | ba_ambiguity | hidden_test | base,context_assisted | approved | human_review | evidence_uncertainty,abstention_accuracy,business_relevance |
| da_profile_quality | data_analyst | da_profile | development | base,context_assisted | approved | human_review | conceptual_correctness,formula_method_selection,business_relevance |
| da_groupby_aggregation | data_analyst | da_aggregation | development | base,context_assisted,tool_assisted | revised | human_review | formula_method_selection,tool_use_correctness,output_instruction_compliance |
| da_one_to_many_join | data_analyst | da_join_cardinality | development | base,context_assisted | revised | numeric_values | conceptual_correctness,numerical_correctness,business_relevance |
| da_eda_order | data_analyst | da_eda | hidden_test | base,context_assisted | approved | human_review | formula_method_selection,evidence_uncertainty,business_relevance |
| da_zero_vs_missing | data_analyst | da_missing_values | validation | base,context_assisted | approved | human_review | conceptual_correctness,evidence_uncertainty,business_relevance |
| da_timeseries_chart | data_analyst | da_visualization | validation | base,context_assisted | approved | human_review | conceptual_correctness,business_relevance,evidence_uncertainty |
| da_segment_rates | data_analyst | percentage_rate_denominator | validation | base,context_assisted | approved | numeric_values | formula_method_selection,numerical_correctness,business_relevance |
| da_weighted_vs_unweighted | data_analyst | weighted_average_variants | validation | base,context_assisted | approved | numeric_values | formula_method_selection,numerical_correctness,business_relevance |
| da_target_leakage | data_analyst | da_leakage | hidden_test | base,context_assisted | approved | human_review | conceptual_correctness,evidence_uncertainty,business_relevance |
| da_insight_communication | data_analyst | da_communication | hidden_test | base,context_assisted | approved | numeric_values | numerical_correctness,statistical_interpretation,business_relevance,evidence_uncertainty |
| calc_pct_change | arithmetic_calculations | calc_percent_change | development | base,context_assisted,tool_assisted | revised | numeric_values | formula_method_selection,numerical_correctness,tool_use_correctness |
| calc_profit | arithmetic_calculations | profit_margin_calculations | development | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,business_relevance |
| calc_margin | arithmetic_calculations | profit_margin_calculations | development | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,business_relevance |
| calc_conversion_denominator | arithmetic_calculations | calc_conversion | validation | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness |
| calc_weighted_avg | arithmetic_calculations | weighted_average_variants | validation | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness |
| calc_region_ratio | arithmetic_calculations | percentage_rate_denominator | validation | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,business_relevance |
| calc_cagr | arithmetic_calculations | calc_compounded_growth | hidden_test | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness |
| calc_units | arithmetic_calculations | calc_unit_conversion | hidden_test | base,context_assisted,tool_assisted | revised | numeric_values | formula_method_selection,numerical_correctness,output_instruction_compliance |
| calc_multistep_profit_margin | arithmetic_calculations | calc_multistep_margin | hidden_test | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,business_relevance |
| calc_zero_denominator | arithmetic_calculations | calc_zero_denominator | hidden_test | base,context_assisted,tool_assisted | approved | human_review | formula_method_selection,evidence_uncertainty,abstention_accuracy,unsupported_claims |
| stat_mean_median_outlier | statistics_probability | stat_location_outlier | development | base,context_assisted,tool_assisted | revised | numeric_values | formula_method_selection,numerical_correctness,statistical_interpretation |
| stat_population_sample | statistics_probability | stat_variance_definition | development | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,statistical_interpretation |
| stat_iqr | statistics_probability | stat_iqr_convention | development | base,context_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,statistical_interpretation |
| stat_conditional_probability | statistics_probability | stat_conditional_probability | development | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,statistical_interpretation |
| stat_correlation_causation | statistics_probability | causal_inference_variants | validation | base,context_assisted | revised | human_review | conceptual_correctness,statistical_interpretation,evidence_uncertainty |
| stat_sampling_bias | statistics_probability | stat_sampling | validation | base,context_assisted | revised | human_review | conceptual_correctness,statistical_interpretation,evidence_uncertainty |
| stat_ci_interpretation | statistics_probability | stat_confidence_interval | validation | base,context_assisted | revised | human_review | statistical_interpretation,evidence_uncertainty,business_relevance |
| stat_pvalue | statistics_probability | stat_pvalue_meaning | hidden_test | base,context_assisted | revised | human_review | numerical_correctness,statistical_interpretation,evidence_uncertainty |
| stat_test_selection | statistics_probability | stat_test_choice | hidden_test | base,context_assisted | revised | human_review | formula_method_selection,statistical_interpretation,evidence_uncertainty |
| stat_multiple_comparisons | statistics_probability | stat_multiple_testing | hidden_test | base,context_assisted | revised | numeric_values | statistical_interpretation,evidence_uncertainty |
| data_monthly_margin | dataset_reasoning | data_monthly_margin | development | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,business_relevance |
| data_sales_vs_margin | dataset_reasoning | data_sales_margin_comparison | development | base,context_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,business_relevance |
| data_missing_required_column | dataset_reasoning | data_required_columns | development | base,context_assisted | revised | human_review | formula_method_selection,evidence_uncertainty,abstention_accuracy,unsupported_claims |
| data_unequal_group_sizes | dataset_reasoning | percentage_rate_denominator | validation | base,context_assisted,tool_assisted | not_required | numeric_values | formula_method_selection,numerical_correctness,business_relevance |
| data_outlier_handling | dataset_reasoning | data_outlier | validation | base,context_assisted | approved | human_review | conceptual_correctness,evidence_uncertainty,business_relevance |
| data_trend_vs_cause | dataset_reasoning | causal_inference_variants | validation | base,context_assisted | approved | human_review | statistical_interpretation,evidence_uncertainty,business_relevance |
| data_missingness_impact | dataset_reasoning | data_missingness_impact | validation | base,context_assisted | revised | human_review | statistical_interpretation,evidence_uncertainty,business_relevance |
| data_simpsons_paradox | dataset_reasoning | data_simpsons | hidden_test | base,context_assisted | revised | numeric_values | numerical_correctness,statistical_interpretation,business_relevance,evidence_uncertainty |
| data_misleading_aggregation_chart | dataset_reasoning | data_chart_aggregation | hidden_test | base,context_assisted | approved | human_review | statistical_interpretation,business_relevance,evidence_uncertainty |
| data_forecast_uncertainty | dataset_reasoning | data_forecast | hidden_test | base,context_assisted | revised | human_review | statistical_interpretation,evidence_uncertainty,business_relevance |
| tool_json_only | tools_reliability | tool_json_format | development | base,context_assisted | not_required | json_object | output_instruction_compliance |
| tool_exact_sentence_count | tools_reliability | tool_sentence_count | hidden_test | base,context_assisted | revised | sentence_count | abbreviation_compliance,conceptual_correctness,output_instruction_compliance,sentence_count_compliance |
| tool_sql_group_query | tools_reliability | tool_sql_aggregation | development | base,context_assisted,tool_assisted | approved | sql_fixture | formula_method_selection,output_instruction_compliance,query_result_correctness,sql_safety,tool_use_correctness |
| tool_calculator_choice | tools_reliability | tool_calculator_selection | development | base,context_assisted,tool_assisted | approved | human_review | formula_method_selection,tool_use_correctness,evidence_uncertainty,unsupported_claims |
| tool_statistical_tool_choice | tools_reliability | clarify_missing_information | validation | base,context_assisted,tool_assisted | approved | clarification | clarification_completeness,evidence_uncertainty,formula_method_selection,tool_use_correctness,unsupported_claims |
| tool_arguments_valid | tools_reliability | percentage_rate_denominator | validation | base,context_assisted,tool_assisted | not_required | tool_arguments | numerical_correctness,tool_use_correctness,formula_method_selection,conceptual_correctness |
| tool_trace_result_grounding | tools_reliability | tool_result_grounding | hidden_test | base,context_assisted,tool_assisted | not_required | tool_trace | numerical_correctness,tool_use_correctness,unsupported_claims,execution_claim_integrity,result_interpretation_error |
| tool_failed_execution | tools_reliability | tool_execution_failure | validation | base,context_assisted,tool_assisted | approved | tool_trace | tool_use_correctness,evidence_uncertainty,unsupported_claims,execution_claim_integrity,result_interpretation_error |
| tool_clarify_ambiguous_definition | tools_reliability | tool_definition_clarification | hidden_test | base,context_assisted | approved | human_review | evidence_uncertainty,abstention_accuracy,output_instruction_compliance |
| tool_no_execution_claim | tools_reliability | tool_no_execution_claim | hidden_test | base,context_assisted | approved | human_review | business_relevance,evidence_uncertainty,unsupported_claims,output_instruction_compliance |

## Scoring scale

- **0:** incorrect, missing or unsupported against the case rubric. **1:** partly correct but materially incomplete or imprecise. **2:** fully satisfies the case rubric without unsupported claims. **NA:** not assessed/not applicable; excluded from that dimension denominator. `needs_review` is a workflow state, not a score. There is no aggregate automatic pass.
### `ba_requirements_vs_solution`

**A. Identity.** Category `business_analyst`; family `ba_requirements`; split `development`; modes `base`, `context_assisted`. Reference ID `ba_requirements_vs_solution`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** This tests **requirements vs solution**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** A product team requests a colored warning tile but has not identified what decision it should change.

**D. Actual benchmark prompt and inputs.**

> A sponsor says, 'Build a dashboard with a red alert button.' State one business requirement you would clarify and distinguish it from this proposed solution.

```json
{"context":"The sponsor has not stated the decision or outcome the alert should support."}
```

**E. Exact recommended reference answer.**

> Clarify the business outcome or decision the alert should support; the red button is a proposed implementation, not a requirement.


**F. Reasoning.** The input states the sponsor gave no outcome/decision. Clarify that outcome; a red button is an implementation proposal, not a requirement.

**G. Relevant failure modes.** Treat the interface as the business goal, invent thresholds, or prescribe a design without asking the decision.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Separates desired outcome from interface choice. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `business_relevance`: Asks a useful outcome or decision question. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review checks outcome clarification and solution/requirement distinction; no result is mechanically derivable. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a rubric-satisfying response and a plausible counterexample; assert score_case returns needs_review and score_dimensions does not manufacture an aggregate pass.

**K. Final disposition.** Current source status is approved; this audit does not claim new human approval. Any semantic answer still follows its human-review scorer.

---

### `ba_stakeholder_authority`

**A. Identity.** Category `business_analyst`; family `ba_stakeholder`; split `development`; modes `base`, `context_assisted`. Reference ID `ba_stakeholder_authority`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** This tests **stakeholder analysis**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** A reporting change affects frontline sales, finance reporting and compliance workflows.

**D. Actual benchmark prompt and inputs.**

> A reporting change affects sales representatives, finance, and compliance. Who should be consulted, and what must be clarified before assigning approval authority?

```json
{"context":"No organization chart, decision rights, or regulatory jurisdiction is supplied."}
```

**E. Exact recommended reference answer.**

> Consult affected sales, finance, and compliance users; identify the accountable business owner and decision rights before assigning approval.


**F. Reasoning.** All three groups are named as affected and should be consulted. The fixture has no org chart or decision rights; identify the accountable owner rather than assigning authority.

**G. Relevant failure modes.** Omit an affected group, assume hierarchy, or invent approval authority/jurisdiction.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Identifies affected stakeholders without inventing organizational authority. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Requests decision-rights and jurisdiction context. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review checks affected-group coverage and asks for accountable owner; source contains no authority data. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a rubric-satisfying response and a plausible counterexample; assert score_case returns needs_review and score_dimensions does not manufacture an aggregate pass.

**K. Final disposition.** Current source status is approved; this audit does not claim new human approval. Any semantic answer still follows its human-review scorer.

---

### `ba_retention_kpi`

**A. Identity.** Category `business_analyst`; family `clarify_missing_information`; split `validation`; modes `base`, `context_assisted`. Reference ID `ba_retention_kpi`; status `approved`; scorer `clarification`; human review `false`.

**B. Real-world skill.** This tests **kpi definition**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** A subscription service must agree who enters the retained cohort and what endpoint counts as retained.

**D. Actual benchmark prompt and inputs.**

> Propose a measurable customer-retention KPI and specify the cohort, period, numerator, and denominator that must be defined.

```json
{"context":"No product, customer lifecycle, or business definition of retained is supplied."}
```

**E. Exact recommended reference answer.**

> Before selecting a retention rate, ask how the eligible cohort is defined, what measurement period applies, and what business rule defines retained/active. Do not assume a convention.

Units: `percent`; tolerance: `{}`.

**F. Reasoning.** A retention rate requires eligible cohort, measurement period and retained/active rule before numerator/denominator can be fixed. No business convention is provided.

**G. Relevant failure modes.** Invent a standard definition, period, numerator/denominator, or rate.

**H. Dimension-specific scoring contract.**

- `clarification_completeness`: Requests each listed missing input before a definitive calculation. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `formula_method_selection`: Selects the requested method and applies the stated population, denominator, grain, and assumptions. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `unsupported_claims`: Does not invent definitions, parameters, or computed results. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Structured helper checks required fields/invented values, not raw prose; it cannot itself judge terminology or compute a KPI. Scorer config: `{"required_fields":["eligible_cohort","measurement_period","retained_definition"],"type":"clarification"}`.

**J. Regression tests.** Test the full required field set, each material omission, invented value, and prose without structured extraction. Verify invented claim fails and prose is not auto-parsed.

**K. Final disposition.** Current source status is approved; this audit does not claim new human approval. Any semantic answer still follows its human-review scorer.

---

### `ba_order_bottleneck`

**A. Identity.** Category `business_analyst`; family `ba_order_flow`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `ba_order_bottleneck`; status `revised`; scorer `numeric_values`; human review `true`.

**B. Real-world skill.** This tests **process analysis**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** An order may wait longest in review while another stage still constrains total flow.

**D. Actual benchmark prompt and inputs.**

> Orders spend 1 hour in intake, 18 hours awaiting approval, and 2 hours in fulfillment. Identify the observed bottleneck and one follow-up question before recommending a change.

```json
{"stage_hours":{"approval":18,"fulfillment":2,"intake":1}}
```

**E. Exact recommended reference answer.**

> Approval has the longest listed wait at 18 hours. This identifies the longest observed delay, not necessarily the operational bottleneck. Ask: does approval constrain throughput or capacity compared with the other stages? Do not recommend a change from duration alone.

Source calculation: `max(1, 18, 2) = 18 hours at approval; duration alone does not establish throughput constraint`.
Units: `hours`; tolerance: `{"hours": "0"}`.

**F. Reasoning.** Approval is the maximum listed wait (18 hours). A long wait alone does not prove a throughput/capacity bottleneck; the explicit follow-up asks whether it constrains flow compared with other stages.

**G. Relevant failure modes.** Call the longest delay a proven bottleneck or recommend process changes without capacity evidence.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `business_relevance`: Distinguishes longest observed wait from a proven throughput bottleneck and proposes a relevant follow-up. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Does not infer a capacity constraint from duration alone. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal helper verifies only max(1,18,2)=18; caveat and follow-up need human review; revised reference must not pass. Scorer config: `{"expected_values":{"bottleneck_hours":"18"},"type":"numeric_values"}`.

**J. Regression tests.** Feed exact expected keys/values, a missing key, an extra key, and values at/beyond tolerance. Verify numeric dimensions only; semantic rubric omissions or revised status keep the overall result needs_review.

**K. Final disposition.** Revised and still requires semantic review; a numeric match is not approval.

---

### `ba_revenue_vs_profit`

**A. Identity.** Category `business_analyst`; family `ba_revenue_profit`; split `development`; modes `base`, `context_assisted`. Reference ID `ba_revenue_vs_profit`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** This tests **business metric concepts**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** An online shop may sell more while shipping, returns, labor or acquisition costs grow faster.

**D. Actual benchmark prompt and inputs.**

> Explain why a business can have rising revenue while profit falls. Do not invent figures.

```json
{}
```

**E. Exact recommended reference answer.**

> Revenue can rise while profit falls if costs rise faster than revenue, for example due to higher input, labor, fulfillment, or acquisition costs. Do not assert which cause occurred without evidence.


**F. Reasoning.** Profit can fall if costs rise faster than revenue; the listed cost drivers are possibilities, not observed causes. No figures are supplied.

**G. Relevant failure modes.** Equate revenue with profit, assert a particular cause, or invent figures.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Distinguishes sales revenue from profit after costs. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Presents causes as possibilities, not observed facts. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; check conditional explanation and evidence boundary. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a rubric-satisfying response and a plausible counterexample; assert score_case returns needs_review and score_dimensions does not manufacture an aggregate pass.

**K. Final disposition.** Current source status is approved; this audit does not claim new human approval. Any semantic answer still follows its human-review scorer.

---

### `ba_churn_definition`

**A. Identity.** Category `business_analyst`; family `clarify_missing_information`; split `validation`; modes `base`, `context_assisted`. Reference ID `ba_churn_definition`; status `approved`; scorer `clarification`; human review `false`.

**B. Real-world skill.** This tests **churn definition and abstention**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** A subscription team needs a defined opening population, churn event and measurement window.

**D. Actual benchmark prompt and inputs.**

> Calculate customer churn. The only supplied information is that the business has customers. State what is missing and do not invent a rate.

```json
{"provided":["The business has customers."]}
```

**E. Exact recommended reference answer.**

> Cannot calculate churn from the supplied statement. Ask for the eligible starting population, the agreed churn definition or required churn count, and the measurement period. Clarify reactivation only if it affects the chosen definition; do not invent a convention.

Units: `percent`; tolerance: `{}`.

**F. Reasoning.** Only "has customers" is known. Ask eligible starting population, agreed churn definition/count and period; reactivation only if relevant to chosen definition.

**G. Relevant failure modes.** Fabricate rate, denominator, period or convention; overstate that reactivation must always be included.

**H. Dimension-specific scoring contract.**

- `abstention_accuracy`: Declines to fabricate a result when required information is absent and asks for the minimum necessary clarification. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `clarification_completeness`: Requests each listed missing input before a definitive calculation. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `formula_method_selection`: Selects the requested method and applies the stated population, denominator, grain, and assumptions. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `unsupported_claims`: Does not invent definitions, parameters, or computed results. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Structured required-field helper cannot parse raw prose; test field coverage, omission and invented values. Scorer config: `{"required_fields":["eligible_population","churn_definition_or_numerator","measurement_period"],"type":"clarification"}`.

**J. Regression tests.** Test the full required field set, each material omission, invented value, and prose without structured extraction. Verify invented claim fails and prose is not auto-parsed.

**K. Final disposition.** Current source status is approved; this audit does not claim new human approval. Any semantic answer still follows its human-review scorer.

---

### `ba_root_cause_correlation`

**A. Identity.** Category `business_analyst`; family `causal_inference_variants`; split `validation`; modes `base`, `context_assisted`. Reference ID `ba_root_cause_correlation`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** This tests **root cause reasoning**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** Support tickets and cancellations can rise in the same month due to a shared event without tickets causing cancellations.

**D. Actual benchmark prompt and inputs.**

> Support tickets and cancellations both increased in the same month. What can be concluded, and what evidence would help test whether tickets caused cancellations?

```json
{"observed":"Both monthly counts increased; no customer-level linkage or experiment is provided."}
```

**E. Exact recommended reference answer.**

> Only co-movement in monthly counts is observed; causation is not established. Consider linked customer-level timing, other confounders, and a credible controlled or quasi-experimental analysis.


**F. Reasoning.** Monthly co-movement is the only observation. Causal claim needs linked timing, confounder analysis and credible controlled/quasi-experimental evidence.

**G. Relevant failure modes.** Claim tickets caused cancellations or infer customer-level sequence from monthly totals.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Does not infer causation from simultaneous increases. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Names additional evidence that could test the hypothesis. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; fixture has no linked records or design; causal wording cannot be scored automatically. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a rubric-satisfying response and a plausible counterexample; assert score_case returns needs_review and score_dimensions does not manufacture an aggregate pass.

**K. Final disposition.** Current source status is approved; this audit does not claim new human approval. Any semantic answer still follows its human-review scorer.

---

### `ba_acceptance_criteria`

**A. Identity.** Category `business_analyst`; family `ba_acceptance`; split `development`; modes `base`, `context_assisted`. Reference ID `ba_acceptance_criteria`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** This tests **acceptance criteria**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** A reporting team can test task completion only after users, task and success threshold are agreed.

**D. Actual benchmark prompt and inputs.**

> Draft two implementation-neutral measurable acceptance-criteria templates for 'make the monthly report easier to use'. Use explicit placeholders where the target user group, task, baseline, or threshold is missing, and state what must be confirmed before either criterion can be tested.

```json
{"context":"No baseline usability measure or target user group is given."}
```

**E. Exact recommended reference answer.**

> Offer two implementation-neutral templates: (1) at least [agreed percentage] of [target users] complete [priority report task] within [agreed time]; (2) the median time for [target users] to complete [priority report task] is at most [agreed time]. Agree on user/task definitions, any baseline, percentage threshold, and time threshold before treating either criterion as testable; do not invent numeric targets.


**F. Reasoning.** The two implementation-neutral templates use placeholders because population, task, baseline and thresholds are not supplied. Do not make them appear agreed/testable.

**G. Relevant failure modes.** Invent numeric targets, specify UI implementation, or give only one criterion.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `business_relevance`: Provides two measurable, implementation-neutral templates with explicit placeholders. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Requires users, tasks, baselines, and thresholds to be agreed rather than fabricated. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review checks two templates, placeholders and prerequisites; revised status requires continued review. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a rubric-satisfying response and a plausible counterexample; assert score_case returns needs_review and score_dimensions does not manufacture an aggregate pass.

**K. Final disposition.** Revised and still requires semantic review; a numeric match is not approval.

---

### `ba_evidence_recommendation`

**A. Identity.** Category `business_analyst`; family `ba_recommendation`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `ba_evidence_recommendation`; status `revised`; scorer `numeric_values`; human review `true`.

**B. Real-world skill.** This tests **evidence based recommendation**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** A small fulfillment pilot may look faster, while causality, representativeness and cost remain unknown.

**D. Actual benchmark prompt and inputs.**

> A pilot reduced average processing time from 10 days to 8 days for 12 cases, with no control group and no cost data. Give a cautious recommendation and identify key limitations.

```json
{"after_mean_days":8,"before_mean_days":10,"control_group":false,"cost_data":false,"pilot_cases":12}
```

**E. Exact recommended reference answer.**

> In the 12 pilot cases, the observed mean processing time was 2 days lower (20% relative to the 10-day baseline). This is preliminary descriptive evidence. The uncontrolled pilot and missing cost data do not establish causation, generalizability, or net benefit; use a suitable comparison and cost assessment before considering broad rollout.

Source calculation: `10 - 8 = 2 days; (2 / 10) * 100 = 20% relative decrease`.
Units: `days; percent`; tolerance: `{"days": "0", "percent": "0"}`.

**F. Reasoning.** Observed mean decreased 2 days (20% of 10) across 12 cases. No control or cost data means no causal, generalization or net-benefit conclusion.

**G. Relevant failure modes.** Claim rollout ROI, causation, or general population effect from this uncontrolled pilot.

**H. Dimension-specific scoring contract.**

- `business_relevance`: Recommends a bounded next step rather than deployment certainty. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Describes an observed pilot difference and states that no control, small sample, and missing cost data limit causal, generalization, and net-benefit claims. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `unsupported_claims`: Makes no claim beyond the supplied evidence or observable trace. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal scorer verifies 2 days and 20%; reviewer checks cautious recommendation and limitations; revised status gates any pass. Scorer config: `{"expected_values":{"difference_days":"2","relative_reduction_percent":"20"},"type":"numeric_values"}`.

**J. Regression tests.** Feed exact expected keys/values, a missing key, an extra key, and values at/beyond tolerance. Verify numeric dimensions only; semantic rubric omissions or revised status keep the overall result needs_review.

**K. Final disposition.** Revised and still requires semantic review; a numeric match is not approval.

---

### `ba_ambiguous_request`

**A. Identity.** Category `business_analyst`; family `ba_ambiguity`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `ba_ambiguous_request`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** This tests **clarification**: BA work clarifies outcomes, accountable decisions and evidence before prescribing implementation or action.

**C. Real-world illustration (synthetic; not benchmark input).** "Improve performance" is not actionable until the KPI, comparison, period and decision are stated.

**D. Actual benchmark prompt and inputs.**

> Compare our performance and tell me what to do.

```json
{"missing":["metric","period","comparison_group","decision_context"]}
```

**E. Exact recommended reference answer.**

> Ask which performance metric, time period, comparison group, and decision the user has in mind before analyzing or recommending action.


**F. Reasoning.** No metric, period, comparison group or intended decision is supplied; clarify all before analysis/recommendation.

**G. Relevant failure modes.** Choose an arbitrary KPI or comparator and recommend an action.

**H. Dimension-specific scoring contract.**

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `abstention_accuracy`: Seeks clarification rather than inventing a comparison. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

- `business_relevance`: Clarification is concise and decision-oriented. **0:** contradicted/missing; **1:** partly met; **2:** fully met without unsupported claims; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review checks focused clarification; no input supports a numeric answer. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a rubric-satisfying response and a plausible counterexample; assert score_case returns needs_review and score_dimensions does not manufacture an aggregate pass.

**K. Final disposition.** Current source status is approved; this audit does not claim new human approval. Any semantic answer still follows its human-review scorer.

---

### `da_profile_quality`

**A. Identity.** Category `data_analyst`; family `da_profile`; split `development`; modes `base`, `context_assisted`. Reference ID `da_profile_quality`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **data profiling**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** Before a store KPI goes live, inspect schema, key integrity, missingness and distributions.

**D. Actual benchmark prompt and inputs.**

> For a new orders table, name initial checks for missingness, duplicate keys, data types, invalid values, and distributions before calculating KPIs.

```json
{"schema":"not supplied","table":"orders"}
```

**E. Exact recommended reference answer.**

> Check schema/types, missingness, key uniqueness/duplicates, valid ranges/categories, and distributions; consult a data dictionary before assigning business meaning.


**F. Reasoning.** The schema is explicitly absent. Name checks for types, missingness, duplicate keys, valid values and distributions; use a data dictionary before assigning meaning.

**G. Relevant failure modes.** Assume schema/keys, calculate KPIs before profiling, or omit checks requested by prompt.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `formula_method_selection`: Covers type, missingness, duplicates, validity, and distributions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review checks checklist coverage; no actual table is supplied to profile. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Review a complete plan and a targeted counterexample; assert score_case returns needs_review, because no supplied dataset/result permits semantic auto-grading.

**K. Final disposition.** Current source status approved; this audit does not assert a new human approval.

---

### `da_groupby_aggregation`

**A. Identity.** Category `data_analyst`; family `da_aggregation`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `da_groupby_aggregation`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** Tests **sql aggregation**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** Monthly sales reporting must not merge the same named month across years.

**D. Actual benchmark prompt and inputs.**

> Assume SQLite and that order_date already represents the intended business timezone. Given orders(order_id, order_date, revenue), describe the SQL grouping needed for total revenue by calendar month. Group by year-month, not month name alone.

```json
{"schema":{"orders":{"order_date":"DATE","order_id":"INTEGER","revenue":"DECIMAL"}}}
```

**E. Exact recommended reference answer.**

> Assuming SQLite and that order_date already represents the intended business timezone, group by strftime('%Y-%m', order_date) and calculate SUM(revenue). The year-month key keeps the same month from different years separate.

Units: `revenue`; tolerances: `{}`.

**F. Reasoning.** SQLite and pre-normalized business timezone are explicit. Use `strftime('%Y-%m', order_date)` and `SUM(revenue)` at year-month grain.

**G. Relevant failure modes.** Group only by month name, apply an unsupported timezone transform, or use another dialect without acknowledging it.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses SQLite year-month grouping with SUM(revenue), not month name alone. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `tool_use_correctness`: Selects and structures the requested tool use correctly; do not claim execution unless supported by the supplied trace. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `output_instruction_compliance`: States the SQLite and pre-normalized business-timezone assumptions; query syntax must match that dialect if provided. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** No query executor is wired for this human scorer; review syntax, grain and assumptions against fixture schema. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Review a complete plan and a targeted counterexample; assert score_case returns needs_review, because no supplied dataset/result permits semantic auto-grading.

**K. Final disposition.** Revised and still requires semantic review; numeric checks cannot approve it.

---

### `da_one_to_many_join`

**A. Identity.** Category `data_analyst`; family `da_join_cardinality`; split `development`; modes `base`, `context_assisted`. Reference ID `da_one_to_many_join`; status `revised`; scorer `numeric_values`; human review `true`.

**B. Real-world skill.** Tests **join cardinality**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** A customer dimension joined to order facts repeats each customer attribute for matching orders.

**D. Actual benchmark prompt and inputs.**

> Customers has one row for each of C1 and C2. Orders has three rows for C1 and one for C2. Predict the number of rows after an inner join on customer_id and explain what happens to a customer-level amount repeated across joined rows.

```json
{"customers":[{"amount":10,"customer_id":"C1"},{"amount":20,"customer_id":"C2"}],"orders_per_customer":{"C1":3,"C2":1}}
```

**E. Exact recommended reference answer.**

> The inner join has 4 rows: three for C1 and one for C2. Customer-level values repeat on matching order rows; aggregate at the intended grain and do not blindly sum repeated customer-level values at order grain.

Source calculation: `3 matching C1 orders + 1 matching C2 order = 4 joined rows; customer amount repeats at order grain`.
Units: `rows`; tolerances: `{"rows": "0"}`.

**F. Reasoning.** C1 contributes three matching rows and C2 one, total 4. Customer amount repeats at order grain; aggregate at intended grain.

**G. Relevant failure modes.** Predict 2 rows, sum repeated customer-level values as if order-level, or claim one-to-one join.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Explains join multiplicity, repeated one-side attributes, and aggregation at the intended grain. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Numeric checker verifies joined_rows=4 only; multiplicity/grain interpretation is human-judged. Scorer config: `{"expected_values":{"joined_rows":"4"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected values, tolerance boundary/beyond, missing and extra observations; separately human-review the explanation/denominator and ensure unresolved semantic dimensions remain needs_review.

**K. Final disposition.** Revised and still requires semantic review; numeric checks cannot approve it.

---

### `da_eda_order`

**A. Identity.** Category `data_analyst`; family `da_eda`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `da_eda_order`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **exploratory analysis**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** A new sales export should be checked for grain, coverage and quality before interpreting trend.

**D. Actual benchmark prompt and inputs.**

> You receive a new transaction dataset with no data dictionary. Give a prioritized first-pass exploratory analysis plan and state what you would verify before interpreting a trend.

```json
{"data_dictionary":null}
```

**E. Exact recommended reference answer.**

> Prioritize schema/types, row counts and keys, missingness, duplicates, ranges/categories, distributions, and date coverage; verify definitions, granularity, and data quality before interpreting trends.


**F. Reasoning.** No data dictionary exists. Prioritize schema/types, counts/keys, missingness, duplicates, ranges/categories, distributions and date coverage; validate definitions/grain.

**G. Relevant failure modes.** Assume column meaning, skip data-quality checks, or infer causal trend before checking coverage.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Provides a sensible order and validates meaning/grain before conclusions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** No actual dataset to inspect; human review evaluates order/completeness of plan. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Review a complete plan and a targeted counterexample; assert score_case returns needs_review, because no supplied dataset/result permits semantic auto-grading.

**K. Final disposition.** Current source status approved; this audit does not assert a new human approval.

---

### `da_zero_vs_missing`

**A. Identity.** Category `data_analyst`; family `da_missing_values`; split `validation`; modes `base`, `context_assisted`. Reference ID `da_zero_vs_missing`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **missing value semantics**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** Zero units sold and an unreported quantity can carry different inventory meanings.

**D. Actual benchmark prompt and inputs.**

> A quantity field contains both 0 and blank values. Explain why they must not automatically be treated as equivalent and name a safe next check.

```json
{"quantity_values":[0,null,3]}
```

**E. Exact recommended reference answer.**

> Zero is an observed numeric value; blank may mean unknown, not collected, or not applicable. Check source semantics and missingness patterns before recoding.


**F. Reasoning.** 0 is observed numeric data; blank may be unknown, uncollected or not applicable. Check source semantics and missingness pattern.

**G. Relevant failure modes.** Automatically replace blank with zero or assert a missingness meaning absent source documentation.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Does not equate zero and missing without evidence. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Fixture has [0,null,3], but no source semantics; scorer cannot safely impute. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Review a complete plan and a targeted counterexample; assert score_case returns needs_review, because no supplied dataset/result permits semantic auto-grading.

**K. Final disposition.** Current source status approved; this audit does not assert a new human approval.

---

### `da_timeseries_chart`

**A. Identity.** Category `data_analyst`; family `da_visualization`; split `validation`; modes `base`, `context_assisted`. Reference ID `da_timeseries_chart`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **visualization selection**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** Weekly revenue over years is naturally shown as an ordered time series with comparable periods.

**D. Actual benchmark prompt and inputs.**

> Recommend a chart to show weekly revenue over two years and explain one way to avoid a misleading comparison.

```json
{"duration":"two years","measure":"revenue","time_grain":"week"}
```

**E. Exact recommended reference answer.**

> A line chart is suitable for weekly time series; use consistent time coverage and axis scale, and account for seasonality or comparable week effects.


**F. Reasoning.** A line chart is suitable. Check time coverage/axis scale and seasonality or comparable-week effects; no data values establish a trend.

**G. Relevant failure modes.** Use misleading category chart, inconsistent axes, or claim seasonality as observed.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Selects a time-series view and gives a valid comparison safeguard. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human reviewer checks chart and safeguard; there is no numeric series to validate. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Review a complete plan and a targeted counterexample; assert score_case returns needs_review, because no supplied dataset/result permits semantic auto-grading.

**K. Final disposition.** Current source status approved; this audit does not assert a new human approval.

---

### `da_segment_rates`

**A. Identity.** Category `data_analyst`; family `percentage_rate_denominator`; split `validation`; modes `base`, `context_assisted`. Reference ID `da_segment_rates`; status `approved`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **segment comparison**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** Regional return monitoring compares returns relative to each region's own order exposure.

**D. Actual benchmark prompt and inputs.**

> Region North has 18 returns from 600 orders; South has 12 returns from 200 orders. Which has the higher return rate, and what denominator should be used?

```json
{"North":{"orders":600,"returns":18},"South":{"orders":200,"returns":12}}
```

**E. Exact recommended reference answer.**

> North is 3%; South is 6%; South has the higher rate. Use each region's orders as its denominator.

Source calculation: `18 / 600 * 100 = 3%; 12 / 200 * 100 = 6%`.
Units: `percent`; tolerances: `{"percent": "0.01"}`.

**F. Reasoning.** North 18/600=3%; South 12/200=6%, so South is higher. Use each region's orders denominator; no significance/causality evidence.

**G. Relevant failure modes.** Compare counts only, swap denominators, or claim statistical significance/causation.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses orders within the corresponding region as denominator. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal scorer verifies two rates; denominator wording and unsupported inference remain human checks. Scorer config: `{"expected_values":{"North_percent":"3","South_percent":"6"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected values, tolerance boundary/beyond, missing and extra observations; separately human-review the explanation/denominator and ensure unresolved semantic dimensions remain needs_review.

**K. Final disposition.** Current source status approved; this audit does not assert a new human approval.

---

### `da_weighted_vs_unweighted`

**A. Identity.** Category `data_analyst`; family `weighted_average_variants`; split `validation`; modes `base`, `context_assisted`. Reference ID `da_weighted_vs_unweighted`; status `approved`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **weighted aggregation**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** A transaction-level basket mean weights store averages by each store's transaction volume.

**D. Actual benchmark prompt and inputs.**

> Store A has 90 transactions with an average basket of 10; Store B has 10 transactions with an average basket of 50. Calculate the combined transaction-weighted average and compare it with the simple average of the store averages.

```json
{"groups":[{"count":90,"mean":10,"name":"A"},{"count":10,"mean":50,"name":"B"}]}
```

**E. Exact recommended reference answer.**

> Transaction-weighted mean is 14; simple mean of store means is 30. The former weights each store by transaction count.

Source calculation: `(90*10 + 10*50) / (90+10) = 14; (10+50)/2 = 30`.
Units: `currency per transaction`; tolerances: `{"weighted_mean": "0.0001", "simple_mean": "0.0001"}`.

**F. Reasoning.** Weighted=(90*10+10*50)/100=14; simple mean=(10+50)/2=30. Counts are weights because group sizes differ.

**G. Relevant failure modes.** Swap results, report one value only, or call 30 the transaction-weighted mean.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Distinguishes transaction-weighted result from simple average. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal scorer checks both values; explanation of weighting needs semantic review. Scorer config: `{"expected_values":{"simple_mean":"30","weighted_mean":"14"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected values, tolerance boundary/beyond, missing and extra observations; separately human-review the explanation/denominator and ensure unresolved semantic dimensions remain needs_review.

**K. Final disposition.** Current source status approved; this audit does not assert a new human approval.

---

### `da_target_leakage`

**A. Identity.** Category `data_analyst`; family `da_leakage`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `da_target_leakage`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **target leakage**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** A credit prediction made at application time cannot use a repayment fact observed later.

**D. Actual benchmark prompt and inputs.**

> A model makes its late-payment prediction on the invoice due date. One feature is `days_past_due`, measured 30 days after the due date. Explain the leakage risk and what information must be available at prediction time.

```json
{"feature":"days_past_due","feature_measurement_time":"30 days after due date","prediction_time":"invoice due date","target":"late_payment"}
```

**E. Exact recommended reference answer.**

> Because prediction occurs on the due date, days_past_due measured 30 days afterward is future information and leaks the outcome period. Features must be available at or before the prediction timestamp.


**F. Reasoning.** Prediction is on due date; days_past_due measured 30 days later is future information. Features must be available by prediction timestamp.

**G. Relevant failure modes.** Treat later feature as available at prediction time or ignore timestamp boundary.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Identifies temporal leakage relative to prediction time. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human judgment applies temporal ordering from explicit fixture times; no pipeline is run. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Review a complete plan and a targeted counterexample; assert score_case returns needs_review, because no supplied dataset/result permits semantic auto-grading.

**K. Final disposition.** Current source status approved; this audit does not assert a new human approval.

---

### `da_insight_communication`

**A. Identity.** Category `data_analyst`; family `da_communication`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `da_insight_communication`; status `approved`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **insight communication**. DA practice validates data, grain, definitions, denominators, timestamps and visualization before drawing conclusions.

**C. Real-world illustration (synthetic; not benchmark input).** A monthly onboarding report should distinguish percentage points from relative change.

**D. Actual benchmark prompt and inputs.**

> A pilot's completion rate is 72% this month versus 68% last month. Write one decision-relevant statement that preserves the comparison and avoids claiming statistical significance or a cause.

```json
{"current_rate_percent":72,"prior_rate_percent":68,"sample_sizes":null}
```

**E. Exact recommended reference answer.**

> Completion rate is 4 percentage points higher this month (72% versus 68%). Without sample sizes or a design, significance and cause cannot be assessed.

Source calculation: `72 - 68 = 4 percentage points; relative increase = (72 - 68) / 68 * 100 = 5.882352941176470588...%`.
Units: `percentage points`; tolerances: `{"percentage_points": "0"}`.

**F. Reasoning.** 72%-68%=4 percentage points; relative change=4/68*100=5.88%. Missing sample sizes/design preclude significance or cause claims.

**G. Relevant failure modes.** Call 4 points 4% relative, claim significance/cause, or omit comparison.

**H. Dimension-specific scoring contract.**

- `numerical_correctness`: Distinguishes a 4 percentage-point difference from a relative percent change. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Does not claim significance or causality. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Scorer checks 4pp only. Relative calculation and calibrated language require human review. Scorer config: `{"expected_values":{"difference_percentage_points":"4"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected values, tolerance boundary/beyond, missing and extra observations; separately human-review the explanation/denominator and ensure unresolved semantic dimensions remain needs_review.

**K. Final disposition.** Current source status approved; this audit does not assert a new human approval.

---

### `calc_pct_change`

**A. Identity.** Category `arithmetic_calculations`; family `calc_percent_change`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_pct_change`; status `revised`; scorer `numeric_values`; human review `true`.

**B. Real-world skill.** Tests **percentage change**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** A campaign compares new sales with the original baseline.

**D. Actual benchmark prompt and inputs.**

> Sales increased from 80,000 to 100,000. Calculate the percentage change relative to the original value and show the formula.

```json
{"new":"100000","original":"80000","unit":"currency"}
```

**E. Exact recommended reference answer.**

> Percentage change = (100,000 - 80,000) / 80,000 × 100 = 25% increase.

Source calculation: `(100000 - 80000) / 80000 * 100 = 25`.
Units: `percent`; tolerance: `{"value": "0.0001"}`.

**F. Reasoning.** (100000-80000)/80000*100=25% increase. The corrected reference includes the requested formula and uses the original amount as denominator.

**G. Relevant failure modes.** Divide by new amount, report raw change as percent, or omit direction.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses original value as denominator. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `tool_use_correctness`: Selects and structures the requested tool use correctly; do not claim execution unless supported by the supplied trace. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal scorer checks 25; method rubric requires original denominator. Scorer config: `{"expected_values":{"percent_change":"25"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected key/value(s), one value at tolerance boundary and beyond, missing key and unexpected extra key. Include a plausible wrong denominator/formula as a negative; verify semantic rubric/revised review is not converted into automatic pass.

**K. Final disposition.** Current not_required status is preserved; this does not mean the calculation lacks a regression test.

---

### `calc_profit`

**A. Identity.** Category `arithmetic_calculations`; family `profit_margin_calculations`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_profit`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **profit amount**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** A retailer reports revenue less the stated total costs.

**D. Actual benchmark prompt and inputs.**

> Revenue is ₹500,000 and total costs are ₹375,000. Calculate profit in rupees.

```json
{"costs":"375000","revenue":"500000","unit":"INR"}
```

**E. Exact recommended reference answer.**

> ₹125,000 profit.

Source calculation: `500000 - 375000 = 125000`.
Units: `INR`; tolerance: `{"value": "0"}`.

**F. Reasoning.** 500000-375000=125000 INR. No tax/other adjustment is supplied.

**G. Relevant failure modes.** Add costs, calculate margin instead, or use wrong currency.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Subtracts costs from revenue. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal scorer checks 125000; unit label remains response-level review. Scorer config: `{"expected_values":{"profit":"125000"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected key/value(s), one value at tolerance boundary and beyond, missing key and unexpected extra key. Include a plausible wrong denominator/formula as a negative; verify semantic rubric/revised review is not converted into automatic pass.

**K. Final disposition.** Current not_required status is preserved; this does not mean the calculation lacks a regression test.

---

### `calc_margin`

**A. Identity.** Category `arithmetic_calculations`; family `profit_margin_calculations`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_margin`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **profit margin**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** Finance reports profit relative to revenue, distinct from markup on cost.

**D. Actual benchmark prompt and inputs.**

> Revenue is ₹240,000 and profit is ₹36,000. Calculate profit margin as a percentage and state the denominator.

```json
{"profit":"36000","revenue":"240000","unit":"percent"}
```

**E. Exact recommended reference answer.**

> 15%, using revenue as the denominator.

Source calculation: `36000 / 240000 * 100 = 15`.
Units: `percent`; tolerance: `{"value": "0.0001"}`.

**F. Reasoning.** 36000/240000*100=15%; revenue is the denominator.

**G. Relevant failure modes.** Divide by costs/profit, use amount as percent, or omit denominator.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses profit divided by revenue. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal scorer checks 15; denominator explanation is a semantic rubric. Scorer config: `{"expected_values":{"margin_percent":"15"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected key/value(s), one value at tolerance boundary and beyond, missing key and unexpected extra key. Include a plausible wrong denominator/formula as a negative; verify semantic rubric/revised review is not converted into automatic pass.

**K. Final disposition.** Current not_required status is preserved; this does not mean the calculation lacks a regression test.

---

### `calc_conversion_denominator`

**A. Identity.** Category `arithmetic_calculations`; family `calc_conversion`; split `validation`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_conversion_denominator`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **conversion rate**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** A funnel rate uses the stated eligible-lead population.

**D. Actual benchmark prompt and inputs.**

> 240 of 3,000 eligible leads converted. Calculate the conversion rate and identify the denominator.

```json
{"conversions":"240","eligible_leads":"3000"}
```

**E. Exact recommended reference answer.**

> 8%; eligible leads are the denominator.

Source calculation: `240 / 3000 * 100 = 8`.
Units: `percent`; tolerance: `{"value": "0.0001"}`.

**F. Reasoning.** 240/3000*100=8%; eligible leads are denominator.

**G. Relevant failure modes.** Use conversions as denominator or substitute an unstated population.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses eligible leads rather than conversions as denominator. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Numeric scorer checks 8; denominator label remains part of method rubric. Scorer config: `{"expected_values":{"conversion_percent":"8"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected key/value(s), one value at tolerance boundary and beyond, missing key and unexpected extra key. Include a plausible wrong denominator/formula as a negative; verify semantic rubric/revised review is not converted into automatic pass.

**K. Final disposition.** Current not_required status is preserved; this does not mean the calculation lacks a regression test.

---

### `calc_weighted_avg`

**A. Identity.** Category `arithmetic_calculations`; family `weighted_average_variants`; split `validation`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_weighted_avg`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **weighted average**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** Combine product-group means using each group's stated unit count.

**D. Actual benchmark prompt and inputs.**

> Group A has 20 units averaging 4. Group B has 80 units averaging 9. Calculate the combined weighted average.

```json
{"groups":[{"count":"20","mean":"4"},{"count":"80","mean":"9"}]}
```

**E. Exact recommended reference answer.**

> Combined weighted average is 8.

Source calculation: `(20*4 + 80*9) / (20+80) = 8`.
Units: `same as group means`; tolerance: `{"value": "0.0001"}`.

**F. Reasoning.** (20*4+80*9)/(20+80)=8.

**G. Relevant failure modes.** Use simple average, reverse weights, or fail to divide by total weight.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Weights group means by group sizes. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal scorer verifies weighted_average=8. Scorer config: `{"expected_values":{"weighted_average":"8"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected key/value(s), one value at tolerance boundary and beyond, missing key and unexpected extra key. Include a plausible wrong denominator/formula as a negative; verify semantic rubric/revised review is not converted into automatic pass.

**K. Final disposition.** Current not_required status is preserved; this does not mean the calculation lacks a regression test.

---

### `calc_region_ratio`

**A. Identity.** Category `arithmetic_calculations`; family `percentage_rate_denominator`; split `validation`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_region_ratio`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **consistent ratio denominator**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** Compare regional returns as a ratio over corresponding orders.

**D. Actual benchmark prompt and inputs.**

> North has 18 returns from 600 orders and South has 12 returns from 200 orders. Calculate each return rate and state which is higher.

```json
{"North":{"denominator":"600","numerator":"18"},"South":{"denominator":"200","numerator":"12"}}
```

**E. Exact recommended reference answer.**

> North is 3%; South is 6%; South is higher.

Source calculation: `18/600*100=3; 12/200*100=6`.
Units: `percent`; tolerance: `{"value": "0.0001"}`.

**F. Reasoning.** North 18/600=3%; South 12/200=6%; South higher.

**G. Relevant failure modes.** Mismatch numerators/denominators or select by raw count.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Applies the same numerator/denominator definition to both regions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Numeric scorer verifies both rates; ranking/denominator are explicit method checks. Scorer config: `{"expected_values":{"North_percent":"3","South_percent":"6"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected key/value(s), one value at tolerance boundary and beyond, missing key and unexpected extra key. Include a plausible wrong denominator/formula as a negative; verify semantic rubric/revised review is not converted into automatic pass.

**K. Final disposition.** Current not_required status is preserved; this does not mean the calculation lacks a regression test.

---

### `calc_cagr`

**A. Identity.** Category `arithmetic_calculations`; family `calc_compounded_growth`; split `hidden_test`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_cagr`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **compound annual growth**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** Finance distinguishes annualized compounding from total multi-year growth.

**D. Actual benchmark prompt and inputs.**

> Revenue grew from 100 to 121 over exactly two years. Calculate the compound annual growth rate as a percentage, not the total growth rate.

```json
{"beginning":"100","ending":"121","years":2}
```

**E. Exact recommended reference answer.**

> 10% compound annual growth rate.

Source calculation: `(121 / 100) ** (1 / 2) - 1 = 0.10`.
Units: `percent per year`; tolerance: `{"value": "0.0001"}`.

**F. Reasoning.** (121/100)^(1/2)-1=10% CAGR. The total growth is 21%, not annual rate.

**G. Relevant failure modes.** Report 21% as CAGR or use unsupported linear annualization.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Annualizes compounded growth over two years, not total growth. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal scorer recomputes CAGR and checks the tolerance from input. Scorer config: `{"expected_values":{"cagr_percent":"10"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected key/value(s), one value at tolerance boundary and beyond, missing key and unexpected extra key. Include a plausible wrong denominator/formula as a negative; verify semantic rubric/revised review is not converted into automatic pass.

**K. Final disposition.** Current not_required status is preserved; this does not mean the calculation lacks a regression test.

---

### `calc_units`

**A. Identity.** Category `arithmetic_calculations`; family `calc_unit_conversion`; split `hidden_test`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_units`; status `revised`; scorer `numeric_values`; human review `true`.

**B. Real-world skill.** Tests **unit consistency**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** Logistics converts a recorded metric tonne value to kilograms.

**D. Actual benchmark prompt and inputs.**

> A shipment weighs 2.5 metric tonnes. Convert it to kilograms and state the conversion used.

```json
{"kilograms_per_tonne":"1000","tonnes":"2.5"}
```

**E. Exact recommended reference answer.**

> 2.5 metric tonnes × 1,000 kilograms per tonne = 2,500 kilograms.

Source calculation: `2.5 metric tonnes * 1000 kilograms/tonne = 2500 kilograms`.
Units: `kilograms`; tolerance: `{"value": "0"}`.

**F. Reasoning.** 2.5 metric tonnes*1000 kilograms/tonne=2500 kilograms; the corrected answer states the supplied conversion factor.

**G. Relevant failure modes.** Use a different tonne/pound conversion or omit resulting unit.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses the supplied metric-tonne conversion factor and labels the result. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `output_instruction_compliance`: Follows the requested format, scope, assumptions, and output constraints. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Numeric scorer checks 2500; conversion wording/unit remain response-level review. Scorer config: `{"expected_values":{"kilograms":"2500"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected key/value(s), one value at tolerance boundary and beyond, missing key and unexpected extra key. Include a plausible wrong denominator/formula as a negative; verify semantic rubric/revised review is not converted into automatic pass.

**K. Final disposition.** Current not_required status is preserved; this does not mean the calculation lacks a regression test.

---

### `calc_multistep_profit_margin`

**A. Identity.** Category `arithmetic_calculations`; family `calc_multistep_margin`; split `hidden_test`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_multistep_profit_margin`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **multi step profit margin**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** Unit sales and stated fixed/variable costs feed a sequential finance calculation.

**D. Actual benchmark prompt and inputs.**

> A business sells 80 items for ₹125 each. Variable costs are ₹6,400 and fixed costs are ₹1,600. Calculate revenue, total costs, profit, and profit margin.

```json
{"fixed_costs":"1600","price_per_unit":"125","units":"80","variable_costs":"6400"}
```

**E. Exact recommended reference answer.**

> Revenue ₹10,000; total costs ₹8,000; profit ₹2,000; margin 20%.

Source calculation: `80*125=10000; 6400+1600=8000; 10000-8000=2000; 2000/10000*100=20`.
Units: `INR; percent`; tolerance: `{"INR": "0", "percent": "0.0001"}`.

**F. Reasoning.** Revenue=80*125=10000 INR; costs=6400+1600=8000; profit=2000; margin=2000/10000*100=20%.

**G. Relevant failure modes.** Omit fixed cost, misorder computations, or divide margin by cost.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses the correct order of revenue, cost, profit, and margin computations. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Numeric scorer requires all four expected keys; test incomplete and extra output. Scorer config: `{"expected_values":{"costs":"8000","margin_percent":"20","profit":"2000","revenue":"10000"},"type":"numeric_values"}`.

**J. Regression tests.** Test exact expected key/value(s), one value at tolerance boundary and beyond, missing key and unexpected extra key. Include a plausible wrong denominator/formula as a negative; verify semantic rubric/revised review is not converted into automatic pass.

**K. Final disposition.** Current not_required status is preserved; this does not mean the calculation lacks a regression test.

---

### `calc_zero_denominator`

**A. Identity.** Category `arithmetic_calculations`; family `calc_zero_denominator`; split `hidden_test`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `calc_zero_denominator`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **zero denominator abstention**. Business calculations must preserve units, denominator, operation order and the requested interpretation; a coincidentally matching value is not enough.

**C. Real-world illustration (synthetic; not benchmark input).** Finance must not report a margin percentage when revenue denominator is zero.

**D. Actual benchmark prompt and inputs.**

> Profit is 50 and revenue is 0. Calculate profit margin. If it is undefined, explain why rather than fabricating a percentage.

```json
{"profit":"50","revenue":"0"}
```

**E. Exact recommended reference answer.**

> Profit margin is undefined because revenue is zero and the formula divides by revenue; do not return a numeric percentage.

Source calculation: `50 / 0 is undefined`.
Units: `percent`; tolerance: `{}`.

**F. Reasoning.** Profit/revenue is undefined for 50/0; no finite percentage follows.

**G. Relevant failure modes.** Return zero, infinity or a fabricated percentage.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Selects the requested method and applies the stated population, denominator, grain, and assumptions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `abstention_accuracy`: Does not fabricate a finite percentage and explains zero denominator. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `unsupported_claims`: Makes no claim beyond the supplied evidence or observable trace. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** No specialized deterministic answer scorer; human review must verify abstention/explanation and no pass is generated. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review correct undefined/abstention explanation against zero, infinity and fabricated finite percentage; ensure score_case remains needs_review and no numeric pass is possible.

**K. Final disposition.** Current source status approved; numerical agreement does not itself prove human semantic approval.

---

### `stat_mean_median_outlier`

**A. Identity.** Category `statistics_probability`; family `stat_location_outlier`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `stat_mean_median_outlier`; status `revised`; scorer `numeric_values`; human review `true`.

**B. Real-world skill.** Tests **mean median outlier**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** A single unusually large basket can pull an average without determining a universal median rule.

**D. Actual benchmark prompt and inputs.**

> For values 2, 4, 4, and 10, calculate the mean and median. Briefly explain how the largest value affects each.

```json
{"values":["2","4","4","10"]}
```

**E. Exact recommended reference answer.**

> Mean = 5; median = 4. In this sample, the value 10 raises the mean above the median. The median can also change if values or central ranks change.

Source calculation: `(2+4+4+10)/4=5; median=(4+4)/2=4`.
Units: `same as observations`; tolerance: `{"value": "0"}`.

**F. Reasoning.** Mean=(2+4+4+10)/4=5; median=(4+4)/2=4. In this sample 10 puts mean above median; median may also change with values/ranks.

**G. Relevant failure modes.** Arithmetic error or claim that median can never change due to outliers.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Selects the requested method and applies the stated population, denominator, grain, and assumptions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Explains this sample's mean/median relationship without claiming that medians cannot change. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal checker verifies mean and median; influence explanation is semantic and reference is revised. Scorer config: `{"expected_values":{"mean":"5","median":"4"},"type":"numeric_values"}`.

**J. Regression tests.** Check each configured numeric reference and tolerances; include wrong denominator/convention and omitted/extra observations. Human-review interpretation separately and assert revised lifecycle never passes on number alone.

**K. Final disposition.** Revised; continue human review; arithmetic verification does not approve semantic interpretation.

---

### `stat_population_sample`

**A. Identity.** Category `statistics_probability`; family `stat_variance_definition`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `stat_population_sample`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **population vs sample variance**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** A quality report's variance formula depends on whether records are whole population or sample.

**D. Actual benchmark prompt and inputs.**

> Treat 2, 4, 6 as the entire population. Calculate the population variance. Then calculate the sample variance if the same three values are instead a sample.

```json
{"population_denominator":3,"sample_denominator":2,"values":["2","4","6"]}
```

**E. Exact recommended reference answer.**

> Mean is 4; population variance is 8/3; sample variance is 4.

Source calculation: `Squared deviations are 4,0,4; population variance=8/3; sample variance=8/(3-1)=4`.
Units: `squared input units`; tolerance: `{"population_variance": "0.0001", "sample_variance": "0.0001"}`.

**F. Reasoning.** Mean=4; squared deviations sum to 8; population variance=8/3, sample variance=8/(3-1)=4.

**G. Relevant failure modes.** Swap n and n-1, or confuse variance with standard deviation.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses N for population and n-1 for sample variance. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Interprets the result only to the extent supported by the design, assumptions, and stated population. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal helper recomputes both using explicit supplied denominators. Scorer config: `{"expected_values":{"population_variance":"2.6666666666666667","sample_variance":"4"},"type":"numeric_values"}`.

**J. Regression tests.** Check each configured numeric reference and tolerances; include wrong denominator/convention and omitted/extra observations. Human-review interpretation separately and assert revised lifecycle never passes on number alone.

**K. Final disposition.** Current not_required source status is preserved; each model interpretation still requires appropriate rubric review.

---

### `stat_iqr`

**A. Identity.** Category `statistics_probability`; family `stat_iqr_convention`; split `development`; modes `base`, `context_assisted`. Reference ID `stat_iqr`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **interquartile range**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** A process-monitoring team needs a consistent quartile convention for outlier screening.

**D. Actual benchmark prompt and inputs.**

> For sorted values 1, 2, 3, 4, 5, 6, 7, 8, use the median-of-halves convention. Calculate Q1, Q3, and the interquartile range.

```json
{"quartile_convention":"median_of_halves","sorted_values":[1,2,3,4,5,6,7,8]}
```

**E. Exact recommended reference answer.**

> Under the stated median-of-halves convention, Q1=2.5, Q3=6.5, and IQR=4.

Source calculation: `Lower half median=(2+3)/2=2.5; upper half median=(6+7)/2=6.5; IQR=4`.
Units: `same as observations`; tolerance: `{"value": "0"}`.

**F. Reasoning.** Sorted values and median-of-halves are given: lower-half median 2.5, upper-half median 6.5, IQR=4.

**G. Relevant failure modes.** Use unstated interpolation convention or mispartition the halves.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Applies the stated quartile convention. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Interprets the result only to the extent supported by the design, assumptions, and stated population. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal helper follows explicit convention; tests should pin values and convention. Scorer config: `{"expected_values":{"IQR":"4","Q1":"2.5","Q3":"6.5"},"type":"numeric_values"}`.

**J. Regression tests.** Check each configured numeric reference and tolerances; include wrong denominator/convention and omitted/extra observations. Human-review interpretation separately and assert revised lifecycle never passes on number alone.

**K. Final disposition.** Current not_required source status is preserved; each model interpretation still requires appropriate rubric review.

---

### `stat_conditional_probability`

**A. Identity.** Category `statistics_probability`; family `stat_conditional_probability`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `stat_conditional_probability`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **conditional probability**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** An analyst asks renewal rate among mobile users, not among the full customer base.

**D. Actual benchmark prompt and inputs.**

> In a group of 100 customers, 40 use mobile and 10 of those 40 renew. What is the probability of renewal given mobile use? State the denominator.

```json
{"customers":100,"mobile_users":40,"mobile_users_who_renew":10}
```

**E. Exact recommended reference answer.**

> 25%; denominator is the 40 mobile users.

Source calculation: `10 / 40 * 100 = 25`.
Units: `percent`; tolerance: `{"value": "0.0001"}`.

**F. Reasoning.** P(renew|mobile)=10/40=25%; conditioning set (40 mobile users) is denominator.

**G. Relevant failure modes.** Use all 100 customers or reverse conditional direction.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Conditions on mobile use and uses 40 as denominator. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Interprets the result only to the extent supported by the design, assumptions, and stated population. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal checker validates 25; denominator interpretation remains rubric check. Scorer config: `{"expected_values":{"conditional_probability_percent":"25"},"type":"numeric_values"}`.

**J. Regression tests.** Check each configured numeric reference and tolerances; include wrong denominator/convention and omitted/extra observations. Human-review interpretation separately and assert revised lifecycle never passes on number alone.

**K. Final disposition.** Current not_required source status is preserved; each model interpretation still requires appropriate rubric review.

---

### `stat_correlation_causation`

**A. Identity.** Category `statistics_probability`; family `causal_inference_variants`; split `validation`; modes `base`, `context_assisted`. Reference ID `stat_correlation_causation`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** Tests **correlation vs causation**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** Ice-cream sales and swimming incidents may co-vary with season without causal link.

**D. Actual benchmark prompt and inputs.**

> Ice-cream sales and swimming incidents rise together in summer. Does this establish that ice-cream sales cause incidents? Explain a plausible confounder and what evidence would strengthen a causal claim.

```json
{"observed":"Seasonal correlation only; no intervention or adjustment is supplied."}
```

**E. Exact recommended reference answer.**

> Seasonal co-movement does not establish that ice-cream sales cause swimming incidents. Season or temperature could influence both, but neither is proven to be a confounder here; a suitable designed intervention or defensible causal analysis would provide stronger evidence.


**F. Reasoning.** Season or temperature are plausible shared influences, not proven confounders. Need designed intervention or defensible causal analysis.

**G. Relevant failure modes.** State confounder as proven or claim sales cause incidents.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Rejects causal inference from co-movement and presents season/temperature only as possible, unproven confounders. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human semantic review only; no causal data/design exists; revised status remains review-required. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Use a semantically correct fixture and a plausible misconception as paired human-review cases; exercise score_case/score_dimensions to prove these free-text judgments remain needs_review, not automatic keyword scores.

**K. Final disposition.** Revised; continue human review; arithmetic verification does not approve semantic interpretation.

---

### `stat_sampling_bias`

**A. Identity.** Category `statistics_probability`; family `stat_sampling`; split `validation`; modes `base`, `context_assisted`. Reference ID `stat_sampling_bias`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** Tests **sampling bias**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** A customer survey sent only to recent login users may miss dormant customers in the target population.

**D. Actual benchmark prompt and inputs.**

> A customer satisfaction survey is sent only to users who logged in during the last week. Identify a likely sampling bias and one way to assess or reduce it.

```json
{"sampling_frame":"recently active users only"}
```

**E. Exact recommended reference answer.**

> If the target population includes users who did not log in during the last week, this survey excludes them and may overrepresent recent users. State the target population and sampling frame, compare respondents with that population, and consider broader sampling or justified adjustment; stratification alone does not guarantee bias removal.


**F. Reasoning.** If target includes nonrecent users, the sampling frame excludes them and can overrepresent active users. State target, compare respondents, broaden sample or justify adjustment; stratification alone is no guarantee.

**G. Relevant failure modes.** Assume target, assert a fixed bias direction/magnitude, or claim stratification removes all bias.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Explains sampling-frame coverage relative to a stated or clarified target population; does not claim that stratification automatically removes bias. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review checks conditional target caveat; no respondent population is supplied. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Use a semantically correct fixture and a plausible misconception as paired human-review cases; exercise score_case/score_dimensions to prove these free-text judgments remain needs_review, not automatic keyword scores.

**K. Final disposition.** Revised; continue human review; arithmetic verification does not approve semantic interpretation.

---

### `stat_ci_interpretation`

**A. Identity.** Category `statistics_probability`; family `stat_confidence_interval`; split `validation`; modes `base`, `context_assisted`. Reference ID `stat_ci_interpretation`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** Tests **confidence interval interpretation**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** A delivery team's confidence interval concerns the population mean under a procedure, not individual delivery times.

**D. Actual benchmark prompt and inputs.**

> A study estimates mean delivery delay as 4 days with a 95% confidence interval of 2 to 6 days. Interpret the interval without claiming that 95% of individual deliveries fall in it.

```json
{"confidence_level_percent":95,"estimate_days":4,"lower_days":2,"upper_days":6}
```

**E. Exact recommended reference answer.**

> Under the assumptions of the stated 95% confidence procedure, intervals produced by that procedure would contain the fixed population mean about 95% of the time over repeated samples. The reported interval is 2 to 6 days; it does not mean that 95% of individual delivery times fall there.

Units: `days`; tolerance: `{}`.

**F. Reasoning.** Under stated procedure assumptions, repeated intervals cover fixed mean about 95% across repeated samples; 2-6 is not a 95% individual range. No interval method supplied.

**G. Relevant failure modes.** Interpret as 95% of individual delays or claim realized parameter probability.

**H. Dimension-specific scoring contract.**

- `statistical_interpretation`: Gives the repeated-sampling coverage interpretation without assuming a specific interval method or confusing parameter coverage with individual outcomes. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; method/sample details missing; revised status is a semantic gate. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Use a semantically correct fixture and a plausible misconception as paired human-review cases; exercise score_case/score_dimensions to prove these free-text judgments remain needs_review, not automatic keyword scores.

**K. Final disposition.** Revised; continue human review; arithmetic verification does not approve semantic interpretation.

---

### `stat_pvalue`

**A. Identity.** Category `statistics_probability`; family `stat_pvalue_meaning`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `stat_pvalue`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** Tests **p value interpretation**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** An experiment report should not interpret a p-value as effect magnitude or probability the null is true.

**D. Actual benchmark prompt and inputs.**

> A test reports p = 0.03 under a preselected 0.05 threshold. State what this says under the null model and what it does not establish about effect size or the probability the null is true.

```json
{"p_value":0.03,"test_design":"not otherwise specified","threshold":0.05}
```

**E. Exact recommended reference answer.**

> For a specified test and its extremeness rule, if the null model and assumptions hold, p=0.03 is the probability of a result at least as extreme under that null. It is below the prespecified 0.05 threshold, but is not the probability that the null is true and does not measure effect size. The test design is not supplied, so the exact extremeness rule cannot be made more specific.


**F. Reasoning.** For the specified test/extremeness rule under null assumptions, p=.03 is probability of a result at least as extreme. It is below .05, not P(null true) or effect size; precise rule is absent.

**G. Relevant failure modes.** Call .03 chance null is true, effect size, or certainty of replication; invent test-tail.

**H. Dimension-specific scoring contract.**

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Defines p conditionally on the specified test's statistic/extremeness rule; rejects null-probability and effect-size interpretations. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** No deterministic semantic scorer; precise interpretation needs human review and explicitly notes missing test. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Use a semantically correct fixture and a plausible misconception as paired human-review cases; exercise score_case/score_dimensions to prove these free-text judgments remain needs_review, not automatic keyword scores.

**K. Final disposition.** Revised; continue human review; arithmetic verification does not approve semantic interpretation.

---

### `stat_test_selection`

**A. Identity.** Category `statistics_probability`; family `stat_test_choice`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `stat_test_selection`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** Tests **statistical test selection**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** A decision about group means depends on the estimand and design, not just variable types.

**D. Actual benchmark prompt and inputs.**

> You have two independent groups and a continuous outcome. The target estimand is the difference in population means. Before choosing a test, name the sample-size, distribution, outlier, and design details you need; give a conditional method choice without assuming those details.

```json
{"estimand":"difference in population means","groups":2,"independent":true,"outcome":"continuous","sample_sizes_and_distribution":null}
```

**E. Exact recommended reference answer.**

> For the stated difference in population means, a Welch two-sample t-test is a conditional option for independent groups. Confirm sample sizes, distribution/outliers, and whether the sampling/design assumptions are reasonable. A robust or nonparametric method may target a different estimand and is not automatically interchangeable.


**F. Reasoning.** Target is difference in population means for independent groups; Welch t-test is conditional on appropriate assumptions. Confirm n, distribution/outliers, design. Alternatives may target other estimands.

**G. Relevant failure modes.** Choose test unconditionally or claim nonparametric method necessarily estimates the same mean difference.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Gives Welch's test only as a conditional method for the stated independent-group mean difference and distinguishes alternatives with different estimands. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Interprets the result only to the extent supported by the design, assumptions, and stated population. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; fixture has no sample sizes/distribution/design details; revised reference remains unresolved semantically. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Use a semantically correct fixture and a plausible misconception as paired human-review cases; exercise score_case/score_dimensions to prove these free-text judgments remain needs_review, not automatic keyword scores.

**K. Final disposition.** Revised; continue human review; arithmetic verification does not approve semantic interpretation.

---

### `stat_multiple_comparisons`

**A. Identity.** Category `statistics_probability`; family `stat_multiple_testing`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `stat_multiple_comparisons`; status `revised`; scorer `numeric_values`; human review `true`.

**B. Real-world skill.** Tests **multiple comparisons**. Statistical practice identifies estimands, sampling/design assumptions and uncertainty, and avoids treating descriptive measures as causal proof.

**C. Real-world illustration (synthetic; not benchmark input).** A product analytics team testing many metrics should report the family and avoid declaring one result false or decisive.

**D. Actual benchmark prompt and inputs.**

> An analyst tests 20 independent null outcomes at alpha 0.05 and highlights one with p = 0.04. Calculate the expected number of false positives under the global null, explain why the finding needs caution, and name a suitable follow-up.

```json
{"per_test_alpha":0.05,"smallest_p_value":0.04,"tests":20}
```

**E. Exact recommended reference answer.**

> Under the global null and correctly calibrated tests at alpha 0.05, the expected number of false positives across 20 tests is 1. A p-value of 0.04 is not thereby known to be false and does not establish effect size; report the tested family and use a prespecified multiplicity procedure or independent confirmation.

Source calculation: `Expected false positives = sum of per-test alpha under the global null = 20 * 0.05 = 1; independence is not required for this expectation`.
Units: `expected false positives`; tolerance: `{"value": "0.0001"}`.

**F. Reasoning.** Under global null and calibrated tests, expected false positives=20*.05=1; independence not needed. p=.04 is not thereby known false; follow up with prespecified multiplicity or confirmation.

**G. Relevant failure modes.** Require independence for expected count, claim exactly one false positive, or say p=.04 is known false.

**H. Dimension-specific scoring contract.**

- `statistical_interpretation`: Explains expected false-positive count under the global null and does not classify the p=0.04 finding as false. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Numeric scorer verifies expected count 1; interpretation/follow-up requires human review; revised status prevents pass. Scorer config: `{"expected_values":{"expected_false_positives":"1"},"type":"numeric_values"}`.

**J. Regression tests.** Check each configured numeric reference and tolerances; include wrong denominator/convention and omitted/extra observations. Human-review interpretation separately and assert revised lifecycle never passes on number alone.

**K. Final disposition.** Revised; continue human review; arithmetic verification does not approve semantic interpretation.

---

### `data_monthly_margin`

**A. Identity.** Category `dataset_reasoning`; family `data_monthly_margin`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `data_monthly_margin`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **table calculation**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** A monthly finance table compares profit and margin independently for each reporting month.

**D. Actual benchmark prompt and inputs.**

> For each month, calculate profit and profit margin from this table. Use profit = revenue - costs and margin = profit / revenue × 100. January: revenue 100, costs 70. February: revenue 120, costs 90. March: revenue 150, costs 105.

```json
{"rows":[{"costs":"70","month":"January","revenue":"100"},{"costs":"90","month":"February","revenue":"120"},{"costs":"105","month":"March","revenue":"150"}]}
```

**E. Exact recommended reference answer.**

> January profit 30 and margin 30%; February profit 30 and margin 25%; March profit 45 and margin 30%.

Source calculation: `Jan:100-70=30;30/100*100=30. Feb:120-90=30;30/120*100=25. Mar:150-105=45;45/150*100=30`.
Units: `same currency units; percent`; tolerance: `{"value": "0.0001"}`.

**F. Reasoning.** Apply stated formulas per row: January 30/30%; February 30/25%; March 45/30%. Revenue is each month's margin denominator.

**G. Relevant failure modes.** Aggregate months before answering or use costs as denominator.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses revenue as denominator for each month's margin. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal helper checks six month-specific values computed from inputs; reference status remains not_required. Scorer config: `{"expected_values":{"February_margin_percent":"25","February_profit":"30","January_margin_percent":"30","January_profit":"30","March_margin_percent":"30","March_profit":"45"},"type":"numeric_values"}`.

**J. Regression tests.** Test all configured values, tolerance edges, missing/extra keys and an intentionally wrong denominator. For revised references confirm a correct numeric result remains needs_review pending semantic review.

**K. Final disposition.** Current not_required status is preserved; model-result interpretation remains distinct.

---

### `data_sales_vs_margin`

**A. Identity.** Category `dataset_reasoning`; family `data_sales_margin_comparison`; split `development`; modes `base`, `context_assisted`. Reference ID `data_sales_vs_margin`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **metric comparison**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** A regional dashboard ranks total sales separately from margin efficiency.

**D. Actual benchmark prompt and inputs.**

> North has revenue 200 and profit 40; South has revenue 100 and profit 30. Identify which has higher sales and which has higher profit margin.

```json
{"North":{"profit":"40","revenue":"200"},"South":{"profit":"30","revenue":"100"}}
```

**E. Exact recommended reference answer.**

> North has higher sales (200 versus 100); South has higher margin (30% versus 20%).

Source calculation: `North:40/200*100=20; South:30/100*100=30`.
Units: `currency; percent`; tolerance: `{"percent": "0.0001"}`.

**F. Reasoning.** North revenue 200>100, while North margin 40/200=20% and South 30/100=30%; South has higher margin.

**G. Relevant failure modes.** Infer margin from sales amount or confuse profit amount with profit/revenue.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Selects the requested method and applies the stated population, denominator, grain, and assumptions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Distinguishes sales volume from margin ranking. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal checker recalculates margins; rank statements are directly derived from supplied data. Scorer config: `{"expected_values":{"North_margin_percent":"20","South_margin_percent":"30"},"type":"numeric_values"}`.

**J. Regression tests.** Test all configured values, tolerance edges, missing/extra keys and an intentionally wrong denominator. For revised references confirm a correct numeric result remains needs_review pending semantic review.

**K. Final disposition.** Current not_required status is preserved; model-result interpretation remains distinct.

---

### `data_missing_required_column`

**A. Identity.** Category `dataset_reasoning`; family `data_required_columns`; split `development`; modes `base`, `context_assisted`. Reference ID `data_missing_required_column`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** Tests **missing column abstention**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** An order export may show value but not the cost basis needed for profitability.

**D. Actual benchmark prompt and inputs.**

> A table contains order_date and order_value, but no cost or profit field. Can you calculate profit margin? State what you would need to confirm about order_value, its currency and grain, what information is missing, and any assumptions you would need.

```json
{"columns":["order_date","order_value"]}
```

**E. Exact recommended reference answer.**

> The listed columns alone are insufficient. Confirm whether order_value represents revenue, its currency and grain, and obtain compatible cost data or profit for the same population and period. Define profit consistently; when revenue is nonzero, margin is profit divided by revenue, multiplied by 100 if expressed as a percentage. Do not infer missing cost or profit.


**F. Reasoning.** Confirm order_value revenue meaning, currency and grain; obtain compatible cost/profit for same population/period. Margin=profit/revenue when nonzero. Do not infer missing cost.

**G. Relevant failure modes.** Assume currency/grain, fabricate costs/profit, or combine incompatible populations.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Selects the requested method and applies the stated population, denominator, grain, and assumptions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Does not assume order_value is revenue; requests compatible cost/profit and matching population/period definitions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `abstention_accuracy`: Does not infer unobserved costs or profit. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `unsupported_claims`: Makes no claim beyond the supplied evidence or observable trace. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; no required cost data exists; revised status must remain needs_review. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a defensible response and a realistic overclaim; assert no automatic semantic pass and ensure missing data are not fabricated.

**K. Final disposition.** Revised; continue human semantic review; numeric verification is not approval.

---

### `data_unequal_group_sizes`

**A. Identity.** Category `dataset_reasoning`; family `percentage_rate_denominator`; split `validation`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `data_unequal_group_sizes`; status `not_required`; scorer `numeric_values`; human review `false`.

**B. Real-world skill.** Tests **unequal group comparison**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** A conversion dashboard compares segments with unequal exposure counts.

**D. Actual benchmark prompt and inputs.**

> Segment A has 9 successes in 10 trials; Segment B has 90 successes in 200 trials. Compare the rates and explain why comparing raw success counts would mislead.

```json
{"A":{"successes":9,"trials":10},"B":{"successes":90,"trials":200}}
```

**E. Exact recommended reference answer.**

> A rate is 90%; B rate is 45%. A has the higher rate despite fewer total successes; compare successes divided by each segment's trials.

Source calculation: `9/10*100=90;90/200*100=45`.
Units: `percent`; tolerance: `{"percent": "0.0001"}`.

**F. Reasoning.** A=9/10=90%; B=90/200=45%. A's rate is higher despite fewer raw successes; rates require respective trial denominators.

**G. Relevant failure modes.** Choose B by count alone or use shared/mismatched denominator.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Uses within-group denominator and explains why counts alone mislead. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal scorer verifies both rates; explanation of count-versus-rate remains semantic review. Scorer config: `{"expected_values":{"A_percent":"90","B_percent":"45"},"type":"numeric_values"}`.

**J. Regression tests.** Test all configured values, tolerance edges, missing/extra keys and an intentionally wrong denominator. For revised references confirm a correct numeric result remains needs_review pending semantic review.

**K. Final disposition.** Current not_required status is preserved; model-result interpretation remains distinct.

---

### `data_outlier_handling`

**A. Identity.** Category `dataset_reasoning`; family `data_outlier`; split `validation`; modes `base`, `context_assisted`. Reference ID `data_outlier_handling`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **outlier review**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** A single bulk order can be valid even when much larger than typical daily orders.

**D. Actual benchmark prompt and inputs.**

> Daily order values are mostly between 20 and 80, with one value of 900. Describe checks to perform before deciding whether to remove it.

```json
{"observed_value":900,"typical_range":[20,80]}
```

**E. Exact recommended reference answer.**

> Check source record, units, data-entry errors, transaction context, and distribution/robust summaries. Do not delete solely because it is unusual; preserve a documented, justified treatment.


**F. Reasoning.** 900 is unusual relative to 20-80; inspect source, units, errors, context and robust summaries before deciding treatment; document any exclusion.

**G. Relevant failure modes.** Delete only for magnitude or claim data-entry error without evidence.

**H. Dimension-specific scoring contract.**

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Requests provenance/context and does not automatically remove a valid outlier. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** No row provenance is supplied; the scorer must human-review investigation plan and must not label 900 invalid automatically. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a defensible response and a realistic overclaim; assert no automatic semantic pass and ensure missing data are not fabricated.

**K. Final disposition.** Current approved source status is retained; this audit does not newly certify semantic answers.

---

### `data_trend_vs_cause`

**A. Identity.** Category `dataset_reasoning`; family `causal_inference_variants`; split `validation`; modes `base`, `context_assisted`. Reference ID `data_trend_vs_cause`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **trend and causality**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** Support contacts may rise after price changes while seasonality, volume or other events also shift.

**D. Actual benchmark prompt and inputs.**

> A line chart shows support tickets rising after a price change. What can the chart establish, what can it not establish, and what additional analysis would help?

```json
{"observed":"Temporal co-occurrence only."}
```

**E. Exact recommended reference answer.**

> The chart establishes an observed temporal pattern only, not that the price change caused ticket growth. Consider seasonality, volume changes, other events, and a suitable comparison or causal design.


**F. Reasoning.** Chart supports temporal pattern only; it cannot isolate price effect. Consider seasonality, volume, other events and credible comparison/design.

**G. Relevant failure modes.** Treat after as because of, or invent a control/comparator.

**H. Dimension-specific scoring contract.**

- `statistical_interpretation`: Separates temporal association from causation and proposes relevant follow-up. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; chart values and causal design are not supplied. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a defensible response and a realistic overclaim; assert no automatic semantic pass and ensure missing data are not fabricated.

**K. Final disposition.** Current approved source status is retained; this audit does not newly certify semantic answers.

---

### `data_missingness_impact`

**A. Identity.** Category `dataset_reasoning`; family `data_missingness_impact`; split `validation`; modes `base`, `context_assisted`. Reference ID `data_missingness_impact`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** Tests **missing data interpretation**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** Regional income comparisons can change when complete-case deletion disproportionately removes one region.

**D. Actual benchmark prompt and inputs.**

> Income is missing for 35% of records, and missingness is more common in one region. Explain why dropping all incomplete rows could bias a regional comparison and what diagnostics to perform.

```json
{"missing_percent":35,"missingness_pattern":"more frequent in one region"}
```

**E. Exact recommended reference answer.**

> Because income is missing more often in one region, dropping incomplete records could change regional composition and bias the comparison, depending on the target estimand and missingness mechanism. Profile missingness by region and relevant variables; investigate plausible mechanisms and compare justified sensitivity approaches.


**F. Reasoning.** Missing income is 35% overall and more common in one region; deletion may alter regional composition. Bias depends on estimand/mechanism. Profile patterns and justified sensitivity analyses; do not invent mechanism.

**G. Relevant failure modes.** Assert MCAR/MAR/MNAR or bias direction/magnitude without evidence.

**H. Dimension-specific scoring contract.**

- `statistical_interpretation`: Describes possible composition change and bias without asserting a specific missingness mechanism. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; mechanism/estimand not supplied; revised lifecycle is review-required. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a defensible response and a realistic overclaim; assert no automatic semantic pass and ensure missing data are not fabricated.

**K. Final disposition.** Revised; continue human semantic review; numeric verification is not approval.

---

### `data_simpsons_paradox`

**A. Identity.** Category `dataset_reasoning`; family `data_simpsons`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `data_simpsons_paradox`; status `revised`; scorer `numeric_values`; human review `true`.

**B. Real-world skill.** Tests **confounding and aggregation**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** Treatment outcomes can reverse after aggregation when treatment arms have different risk composition.

**D. Actual benchmark prompt and inputs.**

> Treatment A succeeds for 9/10 high-risk and 1/100 low-risk cases. Treatment B succeeds for 80/100 high-risk and 0/10 low-risk cases. Explain how A can have a higher success rate within both risk groups but a lower overall rate, and what comparison is needed before recommending treatment.

```json
{"high_risk":{"A":{"successes":9,"trials":10},"B":{"successes":80,"trials":100}},"low_risk":{"A":{"successes":1,"trials":100},"B":{"successes":0,"trials":10}}}
```

**E. Exact recommended reference answer.**

> A's rates are 90% high-risk and 1% low-risk; B's are 80% high-risk and 0% low-risk. Overall A is 10/110 = 9.09%, while B is 80/110 = 72.73%, because group mixes differ. Show stratum and aggregate rates; any standardized comparison should use a prespecified target population, and these data do not establish universal treatment superiority.

Source calculation: `A high=9/10=90%; B high=80/100=80%; A low=1/100=1%; B low=0/10=0%; A total=10/110=9.0909%; B total=80/110=72.7273%`.
Units: `percent`; tolerance: `{"percent": "0.0001"}`.

**F. Reasoning.** A high 9/10=90%, low 1/100=1%, total 10/110 approximately 9.09%. B high 80/100=80%, low 0/10=0%, total 80/110 approximately 72.73%. Mix differs; standardize to prespecified target before recommendation.

**G. Relevant failure modes.** Miscompute rates, omit denominators, claim universal treatment superiority, or use strata to assert causal effect.

**H. Dimension-specific scoring contract.**

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `statistical_interpretation`: Explains the aggregate reversal through differing group composition and distinguishes descriptive rates from a target-population-adjusted comparison. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Decimal helper checks six rates; statistics/business explanation remains human reviewed; revised status prevents numeric-only pass. Scorer config: `{"expected_values":{"A_high_percent":"90","A_low_percent":"1","A_overall_percent":"9.0909090909","B_high_percent":"80","B_low_percent":"0","B_overall_percent":"72.7272727273"},"type":"numeric_values"}`.

**J. Regression tests.** Test all configured values, tolerance edges, missing/extra keys and an intentionally wrong denominator. For revised references confirm a correct numeric result remains needs_review pending semantic review.

**K. Final disposition.** Revised; continue human semantic review; numeric verification is not approval.

---

### `data_misleading_aggregation_chart`

**A. Identity.** Category `dataset_reasoning`; family `data_chart_aggregation`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `data_misleading_aggregation_chart`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **aggregation and chart integrity**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** Regional sales totals can reflect population size, and truncated axes distort visual differences.

**D. Actual benchmark prompt and inputs.**

> A chart compares total sales for regions with very different populations and uses a truncated vertical axis. Identify two interpretation risks and propose fairer views.

```json
{"axis":"truncated","region_populations":"substantially different"}
```

**E. Exact recommended reference answer.**

> Raw totals are not per-capita comparable when populations differ; a truncated axis exaggerates visual differences. Consider per-capita rates and a clearly labeled, appropriate axis, while preserving totals as a separate measure.


**F. Reasoning.** Raw totals are not per-capita comparable when population differs; truncated axis magnifies differences. Choose relevant denominator/clear axis while retaining totals as separate measure.

**G. Relevant failure modes.** Normalize by irrelevant population denominator, ignore axis truncation, or discard valid total measure.

**H. Dimension-specific scoring contract.**

- `statistical_interpretation`: Identifies denominator and axis risks and suggests fair alternatives. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** No population or exact axis values supplied; human-review only and no per-capita number may be invented. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a defensible response and a realistic overclaim; assert no automatic semantic pass and ensure missing data are not fabricated.

**K. Final disposition.** Current approved source status is retained; this audit does not newly certify semantic answers.

---

### `data_forecast_uncertainty`

**A. Identity.** Category `dataset_reasoning`; family `data_forecast`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `data_forecast_uncertainty`; status `revised`; scorer `human_review`; human review `true`.

**B. Real-world skill.** Tests **forecast uncertainty**. Dataset reasoning separates what a table/chart establishes from missing definitions, sampling, population and data-quality assumptions.

**C. Real-world illustration (synthetic; not benchmark input).** Inventory planning should preserve the forecast interval and not imply certainty or unstated coverage.

**D. Actual benchmark prompt and inputs.**

> A forecast estimates next-quarter demand at 1,000 units, with a prediction interval of 800 to 1,250. Explain the estimate and interval without presenting the point forecast as certain.

```json
{"period":"next quarter","point_forecast":1000,"prediction_interval":[800,1250]}
```

**E. Exact recommended reference answer.**

> The point forecast is 1,000 units for next quarter, with the stated prediction interval of 800 to 1,250 under the model and interval specification. Actual demand is uncertain. The prompt gives no nominal interval coverage level, so do not assign one.

Units: `units next quarter`; tolerance: `{}`.

**F. Reasoning.** Point forecast is 1,000 units; interval is 800-1,250. Actual demand uncertain; coverage probability unspecified, so do not assign one.

**G. Relevant failure modes.** Call point certain or interval 95% without source; confuse prediction interval with a confidence interval.

**H. Dimension-specific scoring contract.**

- `statistical_interpretation`: Reports point estimate and interval, recognizes model uncertainty, and does not invent a coverage level. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `business_relevance`: Connects the result or recommendation to the stated business decision without introducing unsupported facts. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; prompt gives no nominal coverage or interval construction; revised status gates final acceptance. Scorer config: `{"type":"human_review"}`.

**J. Regression tests.** Human-review a defensible response and a realistic overclaim; assert no automatic semantic pass and ensure missing data are not fabricated.

**K. Final disposition.** Revised; continue human semantic review; numeric verification is not approval.

---

### `tool_json_only`

**A. Identity.** Category `tools_reliability`; family `tool_json_format`; split `development`; modes `base`, `context_assisted`. Reference ID `tool_json_only`; status `not_required`; scorer `json_object`; human review `false`.

**B. Real-world skill.** Tests **strict json**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** A dashboard API may require a status/count object with no prose around it.

**D. Actual benchmark prompt and inputs.**

> Return only a JSON object with exactly the keys `status` and `count`. Use status `ok` and numeric count 3. Do not use Markdown fences.

```json
{"requested_shape":{"count":"number","status":"string"}}
```

**E. Exact recommended reference answer.**

> {"status":"ok","count":3}


**F. Reasoning.** Output must parse as the exact object status=ok and numeric count=3, with no fence or extra key/text.

**G. Relevant failure modes.** Malformed JSON, fences, extra prose/key, string count or array.

**H. Dimension-specific scoring contract.**

- `output_instruction_compliance`: Strict JSON parse, exact keys/types/values, no fences or surrounding prose. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** JSON scorer deterministically parses complete output and checks exact keys and types; it does not assess broader semantics. Scorer configuration: `{"exact_keys":["status","count"],"expected_object":{"count":3,"status":"ok"},"json_only":true,"type":"json_object"}`.

**J. Regression tests.** Test exact valid object; malformed JSON; fences; extra text/key; wrong value type; array/null. Assert only deterministic format compliance passes.

**K. Final disposition.** Current not_required status is retained; it is not a claim that the answer/trace was executed by this audit.

---

### `tool_exact_sentence_count`

**A. Identity.** Category `tools_reliability`; family `tool_sentence_count`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `tool_exact_sentence_count`; status `revised`; scorer `sentence_count`; human review `true`.

**B. Real-world skill.** Tests **exact sentence count**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** A support summary may have a strict two-sentence limit.

**D. Actual benchmark prompt and inputs.**

> Explain that revenue is before costs and profit is after costs in exactly two sentences. Do not use abbreviations.

```json
{}
```

**E. Exact recommended reference answer.**

> A response must contain exactly two non-empty sentences. One sentence must explain that revenue is before costs; the other must explain that profit is after costs. It must not use abbreviations; sentence-count and abbreviation checks are separate from human semantic review.


**F. Reasoning.** Needs one sentence explaining revenue before costs and one explaining profit after costs, no abbreviations. Count/abbreviation checks do not establish correctness.

**G. Relevant failure modes.** Wrong number of sentences, abbreviation, fragments or reversed business definitions.

**H. Dimension-specific scoring contract.**

- `abbreviation_compliance`: No detected abbreviations under the documented abbreviation pattern; human review may catch forms outside the detector. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `conceptual_correctness`: A human verifies revenue/profit meaning separately. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `output_instruction_compliance`: Follows the requested format, scope, assumptions, and output constraints. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `sentence_count_compliance`: Exactly two sentences under the documented terminal-punctuation convention. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Heuristic counts punctuation boundaries and recognized abbreviations; semantic correctness is intentionally human-reviewed. Scorer configuration: `{"abbreviation_pattern":"Common dotted abbreviations and all-uppercase tokens are rejected; this heuristic is not exhaustive.","counting_rule":"Sentence ends at .?! followed by whitespace or end-of-output; decimals do not end a sentence; ellipsis counts as one terminal boundary; recognized abbreviations are not boundaries and fail abbreviation compliance.","expected_count":2,"terminal_punctuation":[".","!","?"],"type":"sentence_count"}`.

**J. Regression tests.** Test exact two-sentence answer, one/three sentences, decimal punctuation, recognized abbreviation, acronym and ellipsis. Assert semantic dimension is needs_review regardless of deterministic count.

**K. Final disposition.** Revised reference remains subject to human semantic approval; deterministic checks cannot resolve it.

---

### `tool_sql_group_query`

**A. Identity.** Category `tools_reliability`; family `tool_sql_aggregation`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `tool_sql_group_query`; status `approved`; scorer `sql_fixture`; human review `false`.

**B. Real-world skill.** Tests **sql query generation**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** A regional finance report needs one ordered total per region, using only the supplied synthetic orders fixture.

**D. Actual benchmark prompt and inputs.**

> Using the supplied orders table, write a read-only SQL query that returns total revenue by region, ordered by region name. Return SQL only.

```json
{"dialect":"SQLite","rows":[["East",10],["East",15],["West",12]],"schema":"orders(region TEXT, revenue NUMERIC)"}
```

**E. Exact recommended reference answer.**

> A valid SQLite SELECT groups by region and sums revenue; results are East=25 and West=12.

Source calculation: `SQLite fixture: East 10+15=25; West 12`.
Units: `revenue`; tolerances: `{"value": "0"}`.

**F. Reasoning.** SQLite read-only SELECT groups region, sums revenue, orders region; fixture East=25, West=12. Fixture match is not proof of general intent.

**G. Relevant failure modes.** Wrong columns/order/aggregation, invalid identifier, second statement, write/schema change, unsafe function or claim of production execution.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Human reviewer checks generated query intent and schema grounding. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `output_instruction_compliance`: Query should be read-only and return grouped sums. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `query_result_correctness`: Returns expected column order and fixture rows; matching fixture results do not establish general query intent. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `sql_safety`: Query executes only within the authorized read-only in-memory SQLite fixture subset; success is not production authorization. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `tool_use_correctness`: Selects and structures the requested tool use correctly; do not claim execution unless supported by the supplied trace. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Scorer runs only isolated in-memory fixture under authorizer/statement/progress/row limits; query intent remains needs_review. Scorer configuration: `{"expected_columns":["region","total_revenue"],"expected_rows":[["East",25],["West",12]],"sql":"SELECT region, SUM(revenue) AS total_revenue FROM orders GROUP BY region ORDER BY region","type":"sql_fixture"}`.

**J. Regression tests.** Test canonical query, wrong aggregation/columns/order, invalid column, malformed SQL, multiple statement, DML/DDL, ATTACH/PRAGMA/extension and row limit. All execution must stay in-memory.

**K. Final disposition.** Current source status approved; the trace or format check does not itself approve a model interpretation.

---

### `tool_calculator_choice`

**A. Identity.** Category `tools_reliability`; family `tool_calculator_selection`; split `development`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `tool_calculator_choice`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **calculator selection**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** An analyst can compute a weighted mean when each group mean and matching count are available.

**D. Actual benchmark prompt and inputs.**

> You need to calculate the weighted average of 12 group means with different sample sizes. State whether a deterministic calculator tool is appropriate and what inputs are needed; do not claim a calculation has run.

```json
{"group_count":12,"means_and_sizes":"must be supplied for every group"}
```

**E. Exact recommended reference answer.**

> A deterministic calculator is appropriate after the twelve means and corresponding counts are supplied. Do not report a result before executing it.

Source calculation: `weighted_mean = sum(count_i * mean_i) / sum(count_i)`.
Units: `same as means`; tolerances: `{}`.

**F. Reasoning.** A deterministic calculator is appropriate only after all 12 means/counts are supplied; formula sum(n*mean)/sum(n). No values or execution supplied.

**G. Relevant failure modes.** Omit weights, choose unsupported results, or claim tool ran.

**H. Dimension-specific scoring contract.**

- `formula_method_selection`: Selects the requested method and applies the stated population, denominator, grain, and assumptions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `tool_use_correctness`: Selects calculation tool and requests complete inputs. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `unsupported_claims`: Does not claim calculation has run. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; no calculator adapter or trace exists. Scorer configuration: `{"type":"human_review"}`.

**J. Regression tests.** Human-review appropriate tool/clarification against a fabricated result or unsupported run claim; assert scorer never generates an automatic semantic pass.

**K. Final disposition.** Current source status approved; the trace or format check does not itself approve a model interpretation.

---

### `tool_statistical_tool_choice`

**A. Identity.** Category `tools_reliability`; family `clarify_missing_information`; split `validation`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `tool_statistical_tool_choice`; status `approved`; scorer `clarification`; human review `false`.

**B. Real-world skill.** Tests **statistical tool selection**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** A reproducible variance/interval request needs target variable and statistical conventions, not only row count.

**D. Actual benchmark prompt and inputs.**

> A dataset has 50,000 rows and you need a reproducible sample variance and confidence interval. Choose an appropriate computation tool and list key inputs or assumptions; do not report uncomputed values.

```json
{"confidence_level":null,"row_count":50000,"target":"sample variance and confidence interval"}
```

**E. Exact recommended reference answer.**

> Before computing, ask which column to analyze, whether variance is sample or population variance, which confidence-interval method to use, and the confidence level. Do not report uncomputed values or silently choose missing settings.


**F. Reasoning.** Ask target column, population/sample variance, interval method and confidence level; do not invent settings or report uncomputed values.

**G. Relevant failure modes.** Silently choose conventions or fabricate computed results.

**H. Dimension-specific scoring contract.**

- `clarification_completeness`: Requests each listed missing input before a definitive calculation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `formula_method_selection`: Selects the requested method and applies the stated population, denominator, grain, and assumptions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `tool_use_correctness`: Selects and structures the requested tool use correctly; do not claim execution unless supported by the supplied trace. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `unsupported_claims`: Does not invent definitions, parameters, or computed results. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Structured clarification scorer checks required fields but cannot parse prose or execute computation. Scorer configuration: `{"required_fields":["column","variance_convention","interval_method","confidence_level"],"type":"clarification"}`.

**J. Regression tests.** Test all required fields, each omitted field and invented results; confirm structured extraction is not confused with raw answer parsing.

**K. Final disposition.** Current source status approved; the trace or format check does not itself approve a model interpretation.

---

### `tool_arguments_valid`

**A. Identity.** Category `tools_reliability`; family `percentage_rate_denominator`; split `validation`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `tool_arguments_valid`; status `not_required`; scorer `tool_arguments`; human review `false`.

**B. Real-world skill.** Tests **tool argument schema**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** A calculator endpoint requires an expression string and exact argument schema.

**D. Actual benchmark prompt and inputs.**

> A proposed calculator call is `{"expression": 3, "extra": true}`. The contract requires one expression string. Correct the arguments to calculate 18 divided by 600 as a percentage, and identify why the proposed arguments are invalid.

```json
{"proposed_arguments":{"expression":3,"extra":true},"requested_operation":"(18 / 600) * 100"}
```

**E. Exact recommended reference answer.**

> Reject the proposed numeric expression and extra property. Correct arguments use the single required string property `expression`, with an arithmetic expression equivalent to `(18 / 600) * 100`; expected result is 3%.

Source calculation: `18 / 600 * 100 = 3`.
Units: `percent`; tolerances: `{"value": "0.0001"}`.

**F. Reasoning.** Reject number `3` and extra field. Use only string expression semantically equivalent to `(18/600)*100`; result 3%. The explanation remains human-reviewed.

**G. Relevant failure modes.** Accept extra/wrong type, wrong operands, coincidental `1+2`, unsupported syntax or wrong tool.

**H. Dimension-specific scoring contract.**

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `tool_use_correctness`: Uses exact allowed tool and arguments satisfy its declared schema. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `formula_method_selection`: Selects the requested method and applies the stated population, denominator, grain, and assumptions. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `conceptual_correctness`: Accurately explains the core concept and addresses the requested reasoning; a matching number alone is insufficient. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Constrained AST and formula matcher check schema, formula shape and value; conceptual explanation is unscored deterministically, so overall stays needs_review. Scorer configuration: `{"expected_values":{"percent":"3"},"required":{"expression":"(18 / 600) * 100"},"tool_id":"calculator","type":"tool_arguments"}`.

**J. Regression tests.** Test valid schema/formula, equivalent parentheses/order, coincidental wrong formula, wrong operands/tool, wrong types, extra keys, unsafe syntax and division by zero; explanation remains review-required.

**K. Final disposition.** Current not_required status is retained; it is not a claim that the answer/trace was executed by this audit.

---

### `tool_trace_result_grounding`

**A. Identity.** Category `tools_reliability`; family `tool_result_grounding`; split `hidden_test`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `tool_trace_result_grounding`; status `not_required`; scorer `tool_trace`; human review `false`.

**B. Real-world skill.** Tests **tool result interpretation**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** A report should quote a result visible in its execution record without claiming a second run.

**D. Actual benchmark prompt and inputs.**

> Report the result of the requested calculation, if it is available. Preserve the returned value and state when the result is unavailable.

```json
{"task":"Report a calculation result only when an observed result is provided."}
```

**E. Exact recommended reference answer.**

> The supplied tool result is 18.5%. Do not claim independent execution beyond the visible trace.

Units: `percent`; tolerances: `{"value": "0"}`.

**F. Reasoning.** Visible fixture trace says calculate succeeded and returned 18.5%; preserve result and disclose it is supplied trace.

**G. Relevant failure modes.** Alter result, report unavailable result, deny visible success or claim independent execution.

**H. Dimension-specific scoring contract.**

- `numerical_correctness`: Reports the independently verified numeric result with the requested units and tolerance; this does not by itself validate interpretation. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `tool_use_correctness`: Uses the observed successful trace. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `unsupported_claims`: Does not alter the returned value or invent further execution. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `execution_claim_integrity`: Claims about execution agree with the supplied trace; caller-supplied traces are not authenticated by this helper. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `result_interpretation_error`: Preserves the observed result or failure and does not invent, alter, or overinterpret it. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Helper checks caller-supplied trace fields and claim/status consistency but cannot authenticate trace origin or parse full natural-language answer. Scorer configuration: `{"expected_result":{"result":18.5,"unit":"%"},"expected_status":"succeeded","expected_tool":"calculate","type":"tool_trace"}`.

**J. Regression tests.** Test matching success and failure traces, changed result/error/tool, mismatching execution claim in both directions and omitted claim; preserve needs_review for interpretation.

**K. Final disposition.** Current not_required status is retained; it is not a claim that the answer/trace was executed by this audit.

---

### `tool_failed_execution`

**A. Identity.** Category `tools_reliability`; family `tool_execution_failure`; split `validation`; modes `base`, `context_assisted`, `tool_assisted`. Reference ID `tool_failed_execution`; status `approved`; scorer `tool_trace`; human review `false`.

**B. Real-world skill.** Tests **execution failure honesty**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** A failed regional query should surface the missing-column error and request schema correction.

**D. Actual benchmark prompt and inputs.**

> State whether the SQL analysis completed and what should happen next. Do not report a result unless one is available.

```json
{"task":"Report execution state and next step from any available SQL trace."}
```

**E. Exact recommended reference answer.**

> The query failed because `net_sales` is not a recognized column in the available schema. Ask for or inspect the schema and do not report a computed result.


**F. Reasoning.** Trace reports failed `no such column: net_sales`; no computed result exists. State failure and request/inspect schema.

**G. Relevant failure modes.** Invent result, claim success, silently substitute field or suppress error.

**H. Dimension-specific scoring contract.**

- `tool_use_correctness`: Selects and structures the requested tool use correctly; do not claim execution unless supported by the supplied trace. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Requests schema or correct column before retry. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `unsupported_claims`: Acknowledges failure and makes no success/result claim. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `execution_claim_integrity`: Claims about execution agree with the supplied trace; caller-supplied traces are not authenticated by this helper. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `result_interpretation_error`: Preserves the observed result or failure and does not invent, alter, or overinterpret it. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Helper compares expected trace and checks success claim consistency; caller-supplied trace is not independently authenticated. Scorer configuration: `{"expected_error":"no such column: net_sales","expected_status":"failed","expected_tool":"query_readonly_sql","must_not_claim_success":true,"type":"tool_trace"}`.

**J. Regression tests.** Test matching success and failure traces, changed result/error/tool, mismatching execution claim in both directions and omitted claim; preserve needs_review for interpretation.

**K. Final disposition.** Current source status approved; the trace or format check does not itself approve a model interpretation.

---

### `tool_clarify_ambiguous_definition`

**A. Identity.** Category `tools_reliability`; family `tool_definition_clarification`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `tool_clarify_ambiguous_definition`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **metric definition clarification**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** Active-customer growth needs agreed definition and comparison windows before calculation.

**D. Actual benchmark prompt and inputs.**

> Calculate active-customer growth. The business has not defined 'active customer' or the comparison periods. Ask the minimum focused clarification needed before calculating.

```json
{"undefined":["active_customer","comparison_periods"]}
```

**E. Exact recommended reference answer.**

> Ask how active customer is defined and which two periods should be compared before calculating.


**F. Reasoning.** Ask how active customer is defined and which two periods are compared; counts are not supplied.

**G. Relevant failure modes.** Assume login/purchase definition or invent comparison periods/rate.

**H. Dimension-specific scoring contract.**

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `abstention_accuracy`: Does not invent definition or periods. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `output_instruction_compliance`: Asks focused questions needed to proceed. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review; there is no required-field scorer on this case and no numeric result. Scorer configuration: `{"type":"human_review"}`.

**J. Regression tests.** Human-review appropriate tool/clarification against a fabricated result or unsupported run claim; assert scorer never generates an automatic semantic pass.

**K. Final disposition.** Current source status approved; the trace or format check does not itself approve a model interpretation.

---

### `tool_no_execution_claim`

**A. Identity.** Category `tools_reliability`; family `tool_no_execution_claim`; split `hidden_test`; modes `base`, `context_assisted`. Reference ID `tool_no_execution_claim`; status `approved`; scorer `human_review`; human review `false`.

**B. Real-world skill.** Tests **execution claim grounding**. Tool-use reliability combines contract correctness, output constraints, truthful execution reporting and separate evaluation of the result.

**C. Real-world illustration (synthetic; not benchmark input).** A finance CSV can only be described after actual access and header/type inspection.

**D. Actual benchmark prompt and inputs.**

> No file or tool is available. Describe a safe plan for checking whether a CSV contains revenue and cost columns. Do not claim to have opened, inspected, or analyzed a file.

```json
{"file_available":false,"tools_available":false}
```

**E. Exact recommended reference answer.**

> Describe a plan to inspect headers/schema, verify revenue and cost fields and their types, then report findings only after actual file access. Explicitly do not claim inspection occurred.


**F. Reasoning.** Give safe inspection plan and explicitly say no file was opened/analyzed; no file/tool is present.

**G. Relevant failure modes.** Claim file access, invent columns/results or state analysis completed.

**H. Dimension-specific scoring contract.**

- `business_relevance`: Gives a safe, relevant validation plan. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `evidence_uncertainty`: Separates observed facts from hypotheses, identifies material missing information, and calibrates uncertainty. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `unsupported_claims`: Makes no claim of opening, inspecting, or analyzing the absent file. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

- `output_instruction_compliance`: Follows the requested format, scope, assumptions, and output constraints. **0:** contradicts/omits; **1:** partial; **2:** complete under rubric; **NA:** not applicable/not assessed.

**I. Deterministic verification and limits.** Human review only; no file or authenticated tool trace exists. Scorer configuration: `{"type":"human_review"}`.

**J. Regression tests.** Human-review appropriate tool/clarification against a fabricated result or unsupported run claim; assert scorer never generates an automatic semantic pass.

**K. Final disposition.** Current source status approved; the trace or format check does not itself approve a model interpretation.

---

## Cross-case audit findings

### Reference lifecycle and semantic review

At the time of this audit snapshot, lifecycle totals were **25 approved / 19 revised / 16 not_required / 0 pending**. The prior audit recorded 25 approved and 17 revised; two subsequently corrected reference answers were then marked revised with `human_review_required=true`. The current lifecycle is documented at the top of this report and in `BA_DA_FINAL_35_CASE_REVIEW.md`. Neither this audit nor a passing calculation grants human approval. Approved describes the source reference lifecycle; it does not make free-form model answers deterministically correct.

### Scoring-contract reconciliation

Reviewing all 60 pairs showed that many `dimension_rubric` objects omitted dimensions listed in the corresponding case. This made complete case-level review underspecified and allowed deterministic outputs to obscure semantic gaps. The references now include rubric text for every declared dimension, and validation requires exact equality between case dimensions and reference rubric keys.

Two connected scorer issues were corrected:

- Tool argument validation previously reported a pass for a correct schema/formula even though the prompt also asks for an explanation. The case now declares the explanation-related dimensions; structural/formula/value checks are deterministic, but explanatory correctness keeps the result `needs_review`.
- Trace claim consistency now compares any supplied success/failure claim against the observed status for both successful and failed traces. A mismatch fails as `unsupported_execution_claim`; a matching trace is still only a fixture observation and remains subject to interpretation review.

Invented clarification values now use the distinct `unsupported_claim` failure label rather than `missing_context_error`. The benchmark and reference schemas carry that label. The sentence counter treats repeated ellipsis punctuation as one boundary and avoids decimal splitting; abbreviation recognition remains heuristic. The SQL authorizer is tested against wrong columns/aggregation, malformed and multi-statement queries, writes and unsupported functions, always using an in-memory fixture.

### Family, split and similarity review

There are 51 families across three 20-case splits. Family validation requires the intended groups to remain grouped and rejects a family crossing splits. Prompt-only similarity checks at 0.55 found:

| Cases | Similarity | Split/family disposition |
|---|---:|---|
| `da_segment_rates`, `calc_region_ratio` | 0.703 | Both validation; same `percentage_rate_denominator` family |
| `calc_profit`, `calc_margin` | 0.618 | Both development; same `profit_margin_calculations` family |

No detected pair crosses splits or lacks a shared family. This normalized-string heuristic is not semantic proof and can miss variants that change numbers or wording.

### Deterministic numeric and SQL results

`validate_numeric_references()` independently recomputes **25** numeric reference records from source inputs and checks declared tolerances. Specifically, the requested review anchors resolve to North/South return rates 3%/6%; weighted/unweighted means 14/30; 4 percentage points and approximately 5.88% relative increase; zero-denominator margin undefined; order-stage maximum 18 hours; pilot change 2 days/20%; four joined rows; mean/median 5/4; expected false-positive count 1; and Simpson rates A high/low/overall 90%/1%/approximately 9.09%, B 80%/0%/approximately 72.73%. These arithmetic checks do not settle causal, business or statistical interpretation.

The fixed SQL reference returns `[("East", 25), ("West", 12)]` on its synthetic in-memory fixture. Generated-SQL scoring likewise uses only a separately created in-memory fixture and restricted SQLite authorizer; it does not connect to user, application or production data.

### Offline validation record

Commands run from the repository:

```powershell
Push-Location backend
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv-qwen3-train\Scripts\python.exe -m unittest discover -s tests -p test_ba_da_benchmark.py -v
Pop-Location
```

Observed result in the prior audit: **19 tests passed**. The final review reran the expanded suite: **24 tests passed**. It covers inventory/lifecycle counts, dimension-rubric completeness, schema/version fields, mode projections, family/split integrity, similarity, Decimal references and tolerances, SQL fixture and generated-query restrictions, JSON output, sentence/abbreviation behavior, tool arguments, formula equivalence, trace claim integrity, clarification and review gates.

Additional observed offline checks:

- `validate_benchmark()`: 60 cases; six categories of 10; 20 per split; 60 references; 51 families; lifecycle 25/19/16/0.
- `validate_numeric_references()`: 25 reference records verified.
- `validate_sql_reference()`: `[("East", 25), ("West", 12)]`.
- All four evaluation JSON files parsed with duplicate-key detection; benchmark Python source parsed with `ast`.
- `git diff --check`: passed.
- Report audit: 60 case headings, 60 unique IDs in the same order as the benchmark, and A–K sections present for every case. Prompt/reference text was taken directly from the JSON records.

### Remaining limitations

- In this historical audit snapshot, the 19 revised references remained pending human semantic review; the automated audit could not approve them. The current nonblocking `proposed_reference` policy is documented above and in the current 35-case report.
- `human_review` and clarification scorers do not grade free-form prose. Clarification fields are structured observations supplied by a caller; their extraction itself is not validated here.
- Numeric scorers validate structured values. They do not prove the respondent selected the right reasoning unless a separate formula/method rubric is reviewed.
- Tool traces are caller-supplied and unauthenticated. The helper detects inconsistent claims against the supplied trace, not whether the trace is genuine.
- SQL success proves only allowed behavior and result on the tiny synthetic fixture. Query intent/generalization still requires review.
- Sentence and abbreviation recognition is heuristic and not comprehensive for all punctuation, abbreviations, languages or quotation styles.
- The hidden split is visible in repository files; it is not access-controlled.
- No Qwen, Ollama or Hugging Face model was loaded and no inference or training occurred. This audit reports no model weaknesses.

### Preservation and final worktree

The original baseline report hash remains `2E8EC217C46A24886E193C49A596A5BAFDD901E3C1C942DCC4D3640E4CA04EB7`. Rescored report hashes remain `34DD031449619F0BC748F2D07755D96D2CDCC6404BE789C364ABBD4FD50801C4` and `6B9E16615BEE8209AB44739453743073EB6F4E27C694BB19FA5988A0F47122B1`. Checkpoint, Ollama model, Python environments, user datasets and application data were not modified.

No staging, commit, push, branch change, reset, clean, stash, package installation, external API call, inference or training was performed during that audit snapshot. Pre-existing `backend/QWEN3_FINE_TUNING_PREPARATION.md`, `backend/training/qwen_baseline.py`, `.vscode/`, HF test/runner files and other untracked work are preserved. The audit report and benchmark-related changes remain unstaged.

### Current operational proposal status

The current v3 reference file contains 25 `approved` and 35 `proposed_reference` records, with zero `pending`, `revised`, or `not_required`. The proposals preserve their prior status in `previous_review_status` (19 `revised`, 16 `not_required`). This permits offline diagnostic use without claiming semantic approval. The dimension-level automated/not-assessed contract, current tests, and nonblocking readiness gate are in the benchmark code, schema, test module, and current 35-case review. This historical report's previous test totals and worktree observations must not be interpreted as results from the current update.
