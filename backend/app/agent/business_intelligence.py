from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AgentRole = Literal[
    "orchestrator",
    "data_intelligence",
    "analytics_modelling",
    "insights_visualization",
]
PromptVariant = Literal["baseline", "optimized"]
MetricDefinitionStatus = Literal[
    "standard_definition",
    "requires_business_definition",
]


@dataclass(frozen=True)
class SpecialistPromptProfile:
    role: AgentRole
    title: str
    version: str
    objective: str
    instruction: str


@dataclass(frozen=True)
class ModelSpecializationProfile:
    model_id: str
    intended_role: str
    compatible_agent_roles: frozenset[AgentRole]
    instruction: str


@dataclass(frozen=True)
class BusinessMetricDefinition:
    key: str
    display_name: str
    required_inputs: tuple[str, ...]
    aggregation: str
    dimensions: tuple[str, ...]
    unit: str
    validation_rules: tuple[str, ...]
    provenance_requirements: tuple[str, ...]
    engine_reference: str
    definition_status: MetricDefinitionStatus


BI_PROMPT_VERSION = "bi-role-prompts-v2"

SPECIALIST_PROMPT_PROFILES: dict[AgentRole, SpecialistPromptProfile] = {
    "orchestrator": SpecialistPromptProfile(
        role="orchestrator",
        title="Business Intelligence Orchestrator",
        version=BI_PROMPT_VERSION,
        objective="Route business questions to verified evidence and deterministic capabilities.",
        instruction=(
            "Prefer persisted verified results before running analysis. Use only the "
            "registered tool required by the explicit request; never calculate or "
            "reprocess dataset values in the model. Ask when a metric definition, "
            "field mapping, denominator, or period is not explicit. Delegate only "
            "to Data Intelligence when a verified profile is present in trusted prior "
            "results and specialist review is needed. Otherwise leave the handoff null "
            "and use an available registered tool or ask for clarification. A handoff "
            "proposal is not execution. The model-facing request contains only the "
            "agent and objective. The runtime creates workflow and task identifiers, "
            "source identifiers, evidence references, contract versions, and "
            "permitted-tool lists. Never invent or request those runtime-owned values."
        ),
    ),
    "data_intelligence": SpecialistPromptProfile(
        role="data_intelligence",
        title="Data Intelligence Specialist",
        version=BI_PROMPT_VERSION,
        objective="Prepare compact, evidence-backed dataset context for the next specialist.",
        instruction=(
            "You are Agent 1, Data Intelligence. Inspect only supplied verified metadata. Preserve unknown and inferred "
            "states, list observed fields exactly, and return the requested operation "
            "and supplied evidence references without adding calculations."
        ),
    ),
    "analytics_modelling": SpecialistPromptProfile(
        role="analytics_modelling",
        title="Data Analysis Associate",
        version=BI_PROMPT_VERSION,
        objective="Authorize deterministic analysis without duplicating engine work.",
        instruction=(
            "You are Agent 2, Analytics and Modelling. Do not calculate dataset values. Approve only the exact requested "
            "allowlisted tool and arguments; reject changed arguments and preserve "
            "the upstream evidence references."
        ),
    ),
    "insights_visualization": SpecialistPromptProfile(
        role="insights_visualization",
        title="Reporting Specialist",
        version=BI_PROMPT_VERSION,
        objective="Present verified analytical outputs concisely for business users.",
        instruction=(
            "You are Agent 3, Insights and Visualization. Summarize only the supplied verified engine output. Cite exactly the "
            "supplied references; add no numbers, metrics, fields, operations, or "
            "visualization specifications unsupported by that output."
        ),
    ),
}

MODEL_SPECIALIZATION_PROFILES: dict[str, ModelSpecializationProfile] = {
    "qwen3:8b": ModelSpecializationProfile(
        model_id="qwen3:8b",
        intended_role="senior_business_analyst",
        compatible_agent_roles=frozenset({"orchestrator", "analytics_modelling"}),
        instruction=(
            "Senior analyst: separate evidence from interpretation; do not infer causes."
        ),
    ),
    "qwen3:4b-instruct": ModelSpecializationProfile(
        model_id="qwen3:4b-instruct",
        intended_role="data_analysis_associate",
        compatible_agent_roles=frozenset({"data_intelligence", "analytics_modelling"}),
        instruction=(
            "Associate: follow the fixed checklist; do not expand scope."
        ),
    ),
    "qwen3:4b": ModelSpecializationProfile(
        model_id="qwen3:4b",
        intended_role="data_analysis_associate",
        compatible_agent_roles=frozenset({"data_intelligence", "analytics_modelling"}),
        instruction=(
            "Associate: follow the fixed checklist; do not expand scope."
        ),
    ),
    "qwen3:0.6b": ModelSpecializationProfile(
        model_id="qwen3:0.6b",
        intended_role="reporting_specialist",
        compatible_agent_roles=frozenset({"insights_visualization"}),
        instruction=(
            "Reporting formatter only; add no claims or causes."
        ),
    ),
}


def specialist_prompt(
    role: AgentRole,
    variant: PromptVariant,
    *,
    baseline_instruction: str,
    model_id: str | None = None,
) -> str:
    if variant == "baseline":
        return baseline_instruction
    if variant != "optimized":
        raise ValueError(f"Unsupported BI prompt variant: {variant!r}")
    instructions = [SPECIALIST_PROMPT_PROFILES[role].instruction]
    model_profile = MODEL_SPECIALIZATION_PROFILES.get(model_id or "")
    if model_profile is not None and role in model_profile.compatible_agent_roles:
        instructions.append(model_profile.instruction)
    return " ".join(instructions)


def model_specialization_instruction(
    model_id: str,
    role: AgentRole,
) -> str | None:
    profile = MODEL_SPECIALIZATION_PROFILES.get(model_id)
    if profile is None or role not in profile.compatible_agent_roles:
        return None
    return profile.instruction


_GROUPED_AGGREGATION = "app.agent.tools.generate_evidence_report"
_QUALITY_ANALYSIS = "app.agent.tools.run_quality_analysis"
_TIME_SERIES = "app.agent.tools.run_monthly_forecast"
_EDA = "app.agent.tools.run_eda_analysis"


BUSINESS_METRIC_CATALOG: tuple[BusinessMetricDefinition, ...] = (
    BusinessMetricDefinition(
        "revenue",
        "Revenue",
        ("revenue_amount", "period"),
        "sum",
        ("period", "product", "region", "channel"),
        "currency",
        ("Validate currency, refunds, and recognized-revenue policy.",),
        ("source dataset and file", "field mapping", "filters", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "net_revenue",
        "Net revenue",
        ("gross_revenue", "discounts", "returns", "period"),
        "sum",
        ("period", "product", "region", "channel"),
        "currency",
        ("Confirm which deductions are included.",),
        ("source dataset and file", "field mappings", "filters", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "gross_profit",
        "Gross profit",
        ("revenue_amount", "cost_of_goods_sold", "period"),
        "sum",
        ("period", "product", "region"),
        "currency",
        ("Confirm cost scope and revenue recognition policy.",),
        ("source dataset and file", "field mappings", "filters", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "gross_margin",
        "Gross margin",
        ("gross_profit", "revenue_amount", "period"),
        "ratio",
        ("period", "product", "region"),
        "percent",
        ("Require a valid positive revenue denominator.",),
        ("source dataset and file", "field mappings", "numerator", "denominator", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "operating_profit",
        "Operating profit",
        ("revenue_amount", "operating_expenses", "period"),
        "difference",
        ("period", "product", "region"),
        "currency",
        ("Confirm which expenses qualify as operating expenses.",),
        ("source dataset and file", "field mappings", "filters", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "operating_margin",
        "Operating margin",
        ("operating_profit", "revenue_amount", "period"),
        "ratio",
        ("period", "product", "region"),
        "percent",
        ("Require a valid positive revenue denominator.",),
        ("source dataset and file", "field mappings", "numerator", "denominator", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "average_order_value",
        "Average order value",
        ("order_revenue", "order_id", "period"),
        "ratio",
        ("period", "region", "channel"),
        "currency_per_order",
        ("Confirm order grain and treatment of cancellations and returns.",),
        ("source dataset and file", "order grain", "field mappings", "filters", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "order_count",
        "Order count",
        ("order_id", "period"),
        "distinct_count",
        ("period", "product", "region", "channel"),
        "orders",
        ("Validate the order identifier and row grain.",),
        ("source dataset and file", "identifier field", "filters", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "units_sold",
        "Units sold",
        ("quantity", "period"),
        "sum",
        ("period", "product", "region", "channel"),
        "units",
        ("Validate quantity signs and return treatment.",),
        ("source dataset and file", "field mapping", "filters", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "customer_count",
        "Customer count",
        ("customer_id", "period"),
        "distinct_count",
        ("period", "region", "channel"),
        "customers",
        ("Validate customer identifier and active-customer definition.",),
        ("source dataset and file", "identifier field", "population", "period"),
        _GROUPED_AGGREGATION,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "customer_acquisition_cost",
        "Customer acquisition cost",
        ("acquisition_cost", "new_customer_count", "period"),
        "ratio",
        ("period", "channel", "campaign"),
        "currency_per_customer",
        ("Require an explicit acquisition-cost scope and new-customer definition.",),
        ("source dataset and file", "cost scope", "customer cohort", "period"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "customer_lifetime_value",
        "Customer lifetime value",
        ("customer_revenue", "customer_cost", "customer_periods"),
        "cohort_model",
        ("cohort", "segment", "region"),
        "currency_per_customer",
        ("Require an approved lifetime, margin, and discounting methodology.",),
        ("source dataset and file", "cohort definition", "methodology", "period"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "churn_rate",
        "Churn rate",
        ("churned_customers", "eligible_customers", "period"),
        "ratio",
        ("period", "cohort", "segment"),
        "percent",
        ("Require a churn event, eligible population, and period definition.",),
        ("source dataset and file", "cohort", "numerator", "denominator", "period"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "retention_rate",
        "Retention rate",
        ("retained_customers", "eligible_customers", "period"),
        "ratio",
        ("period", "cohort", "segment"),
        "percent",
        ("Require cohort start, retained-event, and denominator definitions.",),
        ("source dataset and file", "cohort", "numerator", "denominator", "period"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "conversion_rate",
        "Conversion rate",
        ("converted_entities", "eligible_entities", "period"),
        "ratio",
        ("period", "channel", "campaign", "funnel_stage"),
        "percent",
        ("Require funnel event, eligible population, and attribution-window definitions.",),
        ("source dataset and file", "funnel", "numerator", "denominator", "period"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "return_rate",
        "Return rate",
        ("returned_orders", "eligible_orders", "period"),
        "ratio",
        ("period", "product", "region", "channel"),
        "percent",
        ("Confirm whether the metric is order-, unit-, or value-based.",),
        ("source dataset and file", "return definition", "denominator", "period"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "defect_rate",
        "Defect rate",
        ("defective_units", "inspected_units", "period"),
        "ratio",
        ("period", "product", "facility", "batch"),
        "percent",
        ("Require inspection coverage and defect criteria.",),
        ("source dataset and file", "inspection scope", "numerator", "denominator", "period"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "on_time_delivery_rate",
        "On-time delivery rate",
        ("on_time_deliveries", "eligible_deliveries", "period"),
        "ratio",
        ("period", "carrier", "region", "service_level"),
        "percent",
        ("Require promised-date and delivery-event definitions.",),
        ("source dataset and file", "delivery definition", "numerator", "denominator", "period"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "inventory_turnover",
        "Inventory turnover",
        ("cost_of_goods_sold", "average_inventory", "period"),
        "ratio",
        ("period", "product", "facility"),
        "turns_per_period",
        ("Confirm average inventory method and period alignment.",),
        ("source dataset and file", "field mappings", "period", "inventory methodology"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "days_sales_outstanding",
        "Days sales outstanding",
        ("accounts_receivable", "credit_sales", "period"),
        "ratio",
        ("period", "customer", "region"),
        "days",
        ("Confirm credit-sales basis and day-count convention.",),
        ("source dataset and file", "field mappings", "period", "day-count convention"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "forecast_accuracy",
        "Forecast accuracy",
        ("forecast_value", "actual_value", "period"),
        "holdout_metric",
        ("period", "product", "region"),
        "metric_specific",
        ("Use persisted holdout metrics; confirm the selected error measure.",),
        ("forecast artifact", "holdout window", "target field", "metric definition"),
        _TIME_SERIES,
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "revenue_growth",
        "Revenue growth",
        ("revenue_amount", "comparison_period"),
        "period_over_period_ratio",
        ("period", "product", "region", "channel"),
        "percent",
        ("Require comparable periods and a nonzero baseline.",),
        ("source dataset and file", "field mappings", "comparison periods", "filters"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "monthly_recurring_revenue",
        "Monthly recurring revenue",
        ("recurring_contract_value", "contract_status", "period"),
        "sum",
        ("period", "plan", "region", "segment"),
        "currency",
        ("Require recurring-contract, active-status, and proration policies.",),
        ("source dataset and file", "contract policy", "field mappings", "period"),
        "requires_explicit_metric_formula",
        "requires_business_definition",
    ),
    BusinessMetricDefinition(
        "missing_value_rate",
        "Missing-value rate",
        ("column_values", "row_count"),
        "quality_count_ratio",
        ("table", "column"),
        "percent",
        ("Use persisted quality evidence and its exact row denominator.",),
        ("quality result", "table", "column", "row-count denominator"),
        _QUALITY_ANALYSIS,
        "standard_definition",
    ),
    BusinessMetricDefinition(
        "duplicate_row_count",
        "Duplicate row count",
        ("table_rows",),
        "quality_count",
        ("table",),
        "rows",
        ("Use persisted quality evidence; do not infer duplicates from samples.",),
        ("quality result", "table", "source fingerprint"),
        _QUALITY_ANALYSIS,
        "standard_definition",
    ),
    BusinessMetricDefinition(
        "correlation",
        "Correlation",
        ("numeric_field_x", "numeric_field_y"),
        "eda_statistic",
        ("table", "period", "segment"),
        "correlation_coefficient",
        ("Use a supported EDA result and report its applied row handling.",),
        ("EDA result", "field pair", "filters", "source fingerprint"),
        _EDA,
        "standard_definition",
    ),
)

_METRICS_BY_KEY = {metric.key: metric for metric in BUSINESS_METRIC_CATALOG}


def get_business_metric(key: str) -> BusinessMetricDefinition | None:
    return _METRICS_BY_KEY.get(key)
