from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Topic:
    key: str
    focus: str
    pitfall: str
    dimension: str
    measure: str


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    stakeholder_role: str
    stakeholder_goal: str
    table: str
    dax_table: str
    grain: str
    columns: dict[str, str]
    rows: tuple[dict[str, Any], ...]
    topics: tuple[Topic, ...]
    definitions: dict[str, str]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="marketing",
        title="Synthetic marketing channel performance",
        stakeholder_role="growth marketing director",
        stakeholder_goal="allocate next month's channel budget without mistaking attribution for causality",
        table="marketing_daily",
        dax_table="Marketing Daily",
        grain="one channel-campaign-day row",
        columns={
            "event_date": "campaign reporting date",
            "campaign": "synthetic campaign label",
            "channel": "acquisition channel",
            "spend": "advertising spend in synthetic rupees",
            "clicks": "click count",
            "leads": "deduplicated lead count",
            "qualified_leads": "leads meeting the supplied qualification rule",
            "deduplicated_conversions": "deduplicated attributed conversions",
            "attributed_net_revenue": "attributed revenue after discounts and refunds",
        },
        rows=(
            {"event_date": "2025-01-03", "campaign": "Search-A", "channel": "Google", "spend": 300, "clicks": 150, "leads": 30, "qualified_leads": 18, "deduplicated_conversions": 5, "attributed_net_revenue": 900},
            {"event_date": "2025-01-04", "campaign": "Search-A", "channel": "Google", "spend": 300, "clicks": 150, "leads": 30, "qualified_leads": 18, "deduplicated_conversions": 5, "attributed_net_revenue": 900},
            {"event_date": "2025-01-03", "campaign": "Social-B", "channel": "Facebook", "spend": 250, "clicks": 250, "leads": 50, "qualified_leads": 10, "deduplicated_conversions": 3, "attributed_net_revenue": 450},
            {"event_date": "2025-01-04", "campaign": "Social-B", "channel": "Facebook", "spend": 250, "clicks": 250, "leads": 50, "qualified_leads": 10, "deduplicated_conversions": 2, "attributed_net_revenue": 450},
            {"event_date": "2025-01-03", "campaign": "Email-C", "channel": "Email", "spend": 50, "clicks": 100, "leads": 20, "qualified_leads": 8, "deduplicated_conversions": 4, "attributed_net_revenue": 320},
            {"event_date": "2025-01-04", "campaign": "Email-C", "channel": "Email", "spend": 50, "clicks": 100, "leads": 20, "qualified_leads": 8, "deduplicated_conversions": 4, "attributed_net_revenue": 320},
            {"event_date": "2025-01-03", "campaign": "NoSpend-D", "channel": "NoSpend", "spend": 0, "clicks": 0, "leads": 0, "qualified_leads": 0, "deduplicated_conversions": 0, "attributed_net_revenue": 0},
        ),
        topics=(
            Topic("qualified-leads", "compare qualified leads by channel", "raw lead volume does not measure acquisition quality", "channel", "qualified_leads"),
            Topic("qualified-cpa", "compare cost per qualified lead", "use spend divided by qualified leads, not all leads", "channel", "spend"),
            Topic("attributed-roas", "compare attributed net revenue and ROAS", "attribution is not causation and requires a defined model and window", "channel", "attributed_net_revenue"),
            Topic("budget-decision", "advise whether to move budget between channels", "recommend a controlled experiment before reallocating based on a small fixture", "channel", "qualified_leads"),
            Topic("conversion-deduplication", "check conversion deduplication", "duplicate conversion events can inflate attributed revenue", "campaign", "deduplicated_conversions"),
            Topic("attribution-window", "explain the attribution window", "last-click attribution within 30 days is a convention, not causal proof", "event_date", "attributed_net_revenue"),
            Topic("channel-confounding", "compare channel efficiency cautiously", "audience and campaign mix can confound channel comparisons", "channel", "spend"),
            Topic("funnel-denominators", "calculate funnel rates with correct denominators", "state whether the rate is per click, lead, or qualified lead", "channel", "leads"),
            Topic("date-coverage", "review date coverage before trend claims", "two daily observations per channel cannot establish seasonality", "event_date", "spend"),
            Topic("zero-qualified-leads", "guard CPA when no qualified leads exist", "CPA is undefined when qualified leads are zero", "channel", "qualified_leads"),
        ),
        definitions={
            "attribution_model": "last-click",
            "attribution_window": "30 days",
            "conversion_deduplication": "one conversion per synthetic conversion identifier",
            "conversion_audit_limit": "fixture counts are pre-deduplicated aggregates; raw conversion identifiers are not included",
            "attribution_audit_limit": "the model and window are declared assumptions; aggregate rows do not contain touch-level evidence to independently audit attribution",
            "attributed_net_revenue": "revenue after discounts and refunds; cancellations excluded",
            "qualified_lead": "lead passing the fixture's explicit qualification rule",
            "zero_denominator": "qualified CPA and ROAS are undefined when their denominator is zero; impressions and CTR are not present in this fixture",
            "causal_limit": "attributed revenue does not establish incremental causal impact",
        },
    ),
    Scenario(
        key="maintenance",
        title="Synthetic predictive-maintenance sensor observations",
        stakeholder_role="plant reliability manager",
        stakeholder_goal="prioritize safe inspection using a 30-day failure-risk signal",
        table="machine_observations",
        dax_table="Machine Observation",
        grain="one machine observation at a prediction timestamp",
        columns={
            "machine": "synthetic machine label",
            "observed_at": "sensor measurement timestamp",
            "vibration_mm_s": "vibration measured at observation time",
            "temperature_c": "temperature measured at observation time",
            "pressure_kpa": "pressure measured at observation time",
            "sensor_complete": "whether all required sensors were observed",
            "failure_at": "confirmed future failure timestamp, when one occurs",
            "failure_within_30d": "confirmed failure label in the following 30 days",
        },
        rows=(
            {"machine": "M-A", "observed_at": "2025-02-01T00:00:00Z", "vibration_mm_s": 2.1, "temperature_c": 68.0, "pressure_kpa": 101.0, "sensor_complete": True, "failure_at": None, "failure_within_30d": False},
            {"machine": "M-A", "observed_at": "2025-02-08T00:00:00Z", "vibration_mm_s": 2.4, "temperature_c": 70.0, "pressure_kpa": 100.0, "sensor_complete": True, "failure_at": None, "failure_within_30d": False},
            {"machine": "M-B", "observed_at": "2025-02-01T00:00:00Z", "vibration_mm_s": 4.8, "temperature_c": 88.0, "pressure_kpa": None, "sensor_complete": False, "failure_at": "2025-02-10T00:00:00Z", "failure_within_30d": True},
            {"machine": "M-B", "observed_at": "2025-02-08T00:00:00Z", "vibration_mm_s": 4.2, "temperature_c": 84.0, "pressure_kpa": 94.0, "sensor_complete": True, "failure_at": "2025-02-10T00:00:00Z", "failure_within_30d": True},
            {"machine": "M-C", "observed_at": "2025-02-01T00:00:00Z", "vibration_mm_s": 2.8, "temperature_c": 72.0, "pressure_kpa": 99.0, "sensor_complete": True, "failure_at": None, "failure_within_30d": False},
        ),
        topics=(
            Topic("prediction-horizon", "define the failure-prediction horizon", "the label refers only to confirmed failure within 30 days after the prediction timestamp", "machine", "failure_within_30d"),
            Topic("time-series-leakage", "prevent future sensor leakage", "features must be computed only from observations available at prediction time", "observed_at", "vibration_mm_s"),
            Topic("incomplete-sensors", "handle incomplete recent sensor readings", "missing pressure means elevated risk is uncertain, not confirmed failure", "machine", "pressure_kpa"),
            Topic("risk-not-failure", "communicate risk without asserting failure", "a risk score is not an observed machine failure", "machine", "failure_within_30d"),
            Topic("inspection-action", "recommend a safe maintenance follow-up", "incomplete readings justify inspection, not automatic shutdown", "machine", "sensor_complete"),
            Topic("label-window", "validate the outcome label window", "exclude failures before the prediction timestamp from future labels", "observed_at", "failure_within_30d"),
            Topic("rolling-features", "design leakage-safe rolling sensor features", "rolling windows must end at or before each prediction timestamp", "machine", "temperature_c"),
            Topic("machine-group-split", "split time series by machine and time", "nearby windows from the same machine must not leak across evaluation splits", "machine", "observed_at"),
            Topic("alert-threshold", "set alert thresholds with operating costs", "threshold selection needs validation and maintenance cost context", "machine", "vibration_mm_s"),
            Topic("sensor-coverage", "report sensor coverage before interpreting risk", "five synthetic observations cannot validate a deployed failure model", "machine", "sensor_complete"),
        ),
        definitions={
            "prediction_horizon": "30 days after observed_at",
            "label": "confirmed failure occurring strictly after observed_at and within the next 30 days",
            "label_grain": "one prediction-observation row; repeated positive rows can refer to the same machine failure and are not unique failure-event counts",
            "feature_cutoff": "only measurements timestamped at or before the prediction time",
            "missing_sensor_policy": "incomplete sensor coverage increases uncertainty; do not impute an observed reading",
            "action_policy": "risk can prompt qualified inspection; never assert failure or automatic shutdown from this fixture",
            "evaluation_split": "group by machine and order chronologically; keep overlapping windows within one split",
        },
    ),
    Scenario(
        key="ecommerce",
        title="Synthetic e-commerce order lines",
        stakeholder_role="e-commerce finance lead",
        stakeholder_goal="reconcile revenue and gross-margin reporting at the correct order-line grain",
        table="order_lines",
        dax_table="Order Line",
        grain="one product line within an order",
        columns={
            "order_id": "synthetic order identifier; repeated across line items",
            "line_id": "synthetic unique order-line identifier",
            "order_date": "order placement date",
            "product": "product label",
            "quantity": "units on the order line",
            "unit_price": "listed unit price in synthetic rupees",
            "allocated_discount": "line-level allocated discount",
            "refund_amount": "refund assigned to the line",
            "unit_cost": "unit cost per item",
            "cancelled": "whether the line was cancelled",
        },
        rows=(
            {"order_id": "O-101", "line_id": "L-1", "order_date": "2025-01-02", "product": "shirt", "quantity": 2, "unit_price": 300, "allocated_discount": 20, "refund_amount": 0, "unit_cost": 120, "cancelled": False},
            {"order_id": "O-101", "line_id": "L-2", "order_date": "2025-01-02", "product": "mug", "quantity": 1, "unit_price": 120, "allocated_discount": 10, "refund_amount": 0, "unit_cost": 40, "cancelled": False},
            {"order_id": "O-102", "line_id": "L-3", "order_date": "2025-01-03", "product": "shirt", "quantity": 1, "unit_price": 300, "allocated_discount": 30, "refund_amount": 100, "unit_cost": 120, "cancelled": False},
            {"order_id": "O-103", "line_id": "L-4", "order_date": "2025-01-04", "product": "bottle", "quantity": 2, "unit_price": 200, "allocated_discount": 0, "refund_amount": 0, "unit_cost": 90, "cancelled": False},
            {"order_id": "O-104", "line_id": "L-5", "order_date": "2025-01-05", "product": "mug", "quantity": 1, "unit_price": 120, "allocated_discount": 0, "refund_amount": 120, "unit_cost": 40, "cancelled": False},
            {"order_id": "O-105", "line_id": "L-6", "order_date": "2025-01-05", "product": "bottle", "quantity": 1, "unit_price": 200, "allocated_discount": 0, "refund_amount": 0, "unit_cost": 90, "cancelled": True},
            {"order_id": "O-106", "line_id": "L-7", "order_date": "2025-01-06", "product": "fully-refunded", "quantity": 1, "unit_price": 50, "allocated_discount": 0, "refund_amount": 50, "unit_cost": 0, "cancelled": False},
        ),
        topics=(
            Topic("order-line-grain", "verify order-line versus order-level grain", "order_id repeats; never sum order totals after joining them onto every line", "order_id", "quantity"),
            Topic("net-line-revenue", "calculate net line revenue", "subtract allocated discounts and refunds at line grain", "product", "unit_price"),
            Topic("gross-profit", "calculate gross profit from net revenue and COGS", "exclude cancelled lines and avoid multiplying cost through joins", "product", "unit_cost"),
            Topic("shared-discount", "allocate shared order discounts", "an order-level discount needs a documented allocation rule before line analysis", "order_id", "allocated_discount"),
            Topic("refund-treatment", "account for refunds and cancellations", "refunds reduce net revenue; cancelled lines are excluded under this fixture definition", "order_date", "refund_amount"),
            Topic("zero-revenue-margin", "guard gross margin for zero net revenue", "do not divide by zero or report a margin for a zero-revenue group", "product", "refund_amount"),
            Topic("tax-shipping", "state the scope of sales revenue", "tax and shipping are excluded because they are not supplied", "order_date", "unit_price"),
            Topic("product-mix", "compare product contribution", "small line counts and differing order sizes limit comparisons", "product", "quantity"),
            Topic("join-duplication", "detect join-induced revenue duplication", "aggregate line facts before joining to order-level facts", "line_id", "unit_price"),
            Topic("period-coverage", "check order-date coverage", "four days do not establish seasonality or long-run growth", "order_date", "quantity"),
        ),
        definitions={
            "net_line_revenue": "(quantity * unit_price) - allocated_discount - refund_amount",
            "gross_profit": "net revenue minus quantity multiplied by unit cost",
            "gross_margin": "gross profit divided by net revenue, multiplied by 100; undefined at zero net revenue",
            "discount": "order-level discounts must be allocated once using an explicit documented rule",
            "cancelled_lines": "excluded from sales, refunds, and COGS under this fixture definition",
            "tax_shipping": "excluded because not present in the fixture",
            "accounting_scope": "fixture net line sales under these assumptions, not complete accounting revenue; tax, shipping, and returns beyond the supplied refund field are absent",
            "grain": "one order line; aggregate order-level facts before joining to line facts",
        },
    ),
    Scenario(
        key="fraud",
        title="Synthetic fraud-review decisions",
        stakeholder_role="fraud operations manager",
        stakeholder_goal="balance fraud detection performance against investigation capacity and error costs",
        table="fraud_reviews",
        dax_table="Fraud Review",
        grain="one synthetic transaction after label verification",
        columns={
            "transaction_id": "synthetic transaction identifier",
            "amount_rupees": "transaction amount in synthetic rupees",
            "risk_score": "model score available at decision time",
            "flagged": "whether the chosen threshold flagged the transaction",
            "confirmed_fraud": "fraud confirmed by completed review",
            "review_status": "label verification status",
        },
        rows=(
            {"transaction_id": "F-1", "amount_rupees": 900, "risk_score": 0.92, "flagged": True, "confirmed_fraud": True, "review_status": "completed"},
            {"transaction_id": "F-2", "amount_rupees": 450, "risk_score": 0.81, "flagged": True, "confirmed_fraud": False, "review_status": "completed"},
            {"transaction_id": "F-3", "amount_rupees": 1200, "risk_score": 0.76, "flagged": True, "confirmed_fraud": True, "review_status": "completed"},
            {"transaction_id": "F-4", "amount_rupees": 80, "risk_score": 0.18, "flagged": False, "confirmed_fraud": True, "review_status": "completed"},
            {"transaction_id": "F-5", "amount_rupees": 200, "risk_score": 0.22, "flagged": False, "confirmed_fraud": False, "review_status": "completed"},
        ),
        topics=(
            Topic("precision-recall", "evaluate fraud precision and recall", "a flagged transaction is suspicious, not confirmed fraud", "flagged", "confirmed_fraud"),
            Topic("false-positive-cost", "explain false-positive investigation costs", "threshold choice requires investigation capacity and error costs", "review_status", "amount_rupees"),
            Topic("threshold-tradeoff", "describe the detection versus investigation trade-off", "improved recall can increase false positives and manual workload", "flagged", "risk_score"),
            Topic("label-verification", "verify fraud label maturity", "unreviewed or provisional flags must not be used as confirmed labels", "review_status", "confirmed_fraud"),
            Topic("risk-not-confirmation", "communicate risk scores cautiously", "a high risk score is not proof of fraud", "transaction_id", "risk_score"),
            Topic("class-imbalance", "report class prevalence and sample limits", "five synthetic cases cannot establish deployed precision or recall", "confirmed_fraud", "transaction_id"),
            Topic("threshold-selection", "select thresholds using validation data", "do not tune thresholds on the held-out test cases", "risk_score", "flagged"),
            Topic("cost-sensitive-decision", "include false-negative and false-positive costs", "the fixture omits monetary harm and review costs", "flagged", "amount_rupees"),
            Topic("time-split", "avoid temporal and label leakage", "use only transaction-time features and labels verified after the outcome window", "transaction_id", "risk_score"),
            Topic("review-capacity", "recommend human review for flagged cases", "automation should not treat a score as a final fraud decision", "flagged", "review_status"),
        ),
        definitions={
            "fraud_label": "confirmed_fraud is valid only when review_status is completed",
            "flag_threshold": "risk_score >= 0.75",
            "precision": "true positives divided by all flagged transactions",
            "recall": "true positives divided by all confirmed-fraud transactions",
            "false_positive": "flagged transaction with completed review and confirmed_fraud false",
            "decision_policy": "flag for human investigation; never equate flagging with confirmed fraud",
            "evaluation_split": "use chronological, label-maturity-aware splits; held-out data is final evaluation only",
        },
    ),
    Scenario(
        key="healthcare",
        title="Synthetic readmission outcome aggregates",
        stakeholder_role="hospital quality director",
        stakeholder_goal="understand 30-day unplanned readmissions while preserving clinical review and privacy",
        table="discharge_episodes",
        dax_table="Discharge Episode",
        grain="one eligible synthetic discharge episode",
        columns={
            "episode_id": "synthetic non-identifying discharge episode key",
            "discharge_date": "index discharge date",
            "eligible_discharge": "whether the episode meets the defined denominator criteria",
            "followup_complete": "whether the full 30-day outcome window is observed",
            "planned_readmission": "whether a subsequent admission was planned",
            "unplanned_readmission_30d": "unplanned readmission within 30 days",
            "features_available_at_discharge": "whether model features were cut off at discharge",
            "risk_score": "synthetic decision-support score using only features available at discharge; not a diagnosis",
        },
        rows=(
            {"episode_id": "E-1", "discharge_date": "2025-03-01", "eligible_discharge": True, "followup_complete": True, "planned_readmission": False, "unplanned_readmission_30d": True, "features_available_at_discharge": True, "risk_score": 0.72},
            {"episode_id": "E-2", "discharge_date": "2025-03-02", "eligible_discharge": True, "followup_complete": True, "planned_readmission": False, "unplanned_readmission_30d": False, "features_available_at_discharge": True, "risk_score": 0.28},
            {"episode_id": "E-3", "discharge_date": "2025-03-03", "eligible_discharge": True, "followup_complete": True, "planned_readmission": True, "unplanned_readmission_30d": False, "features_available_at_discharge": True, "risk_score": 0.61},
            {"episode_id": "E-4", "discharge_date": "2025-03-04", "eligible_discharge": True, "followup_complete": True, "planned_readmission": False, "unplanned_readmission_30d": True, "features_available_at_discharge": False, "risk_score": None},
            {"episode_id": "E-5", "discharge_date": "2025-03-05", "eligible_discharge": False, "followup_complete": True, "planned_readmission": False, "unplanned_readmission_30d": False, "features_available_at_discharge": True, "risk_score": 0.12},
        ),
        topics=(
            Topic("eligible-denominator", "define eligible discharge episodes", "the rate denominator is eligible index discharges, not all encounters", "eligible_discharge", "unplanned_readmission_30d"),
            Topic("planned-readmission", "distinguish planned from unplanned readmissions", "planned readmissions are excluded from the outcome numerator by this definition", "planned_readmission", "unplanned_readmission_30d"),
            Topic("thirty-day-window", "apply the 30-day outcome window", "readmission outside 30 days does not meet this target", "discharge_date", "unplanned_readmission_30d"),
            Topic("clinical-review", "respond to a high risk score safely", "risk is decision support, not diagnosis or automatic intervention", "episode_id", "risk_score"),
            Topic("feature-leakage", "prevent post-discharge feature leakage", "only information available at discharge may enter a prediction", "features_available_at_discharge", "risk_score"),
            Topic("patient-privacy", "keep synthetic examples non-identifying", "do not add patient names, dates of birth, addresses, or medical record numbers", "episode_id", "risk_score"),
            Topic("episode-grain", "validate discharge episode grain", "multiple encounters for one person require an explicit episode-linkage policy", "episode_id", "eligible_discharge"),
            Topic("followup-censoring", "handle incomplete 30-day follow-up", "episodes without complete follow-up cannot safely be labeled negative", "discharge_date", "unplanned_readmission_30d"),
            Topic("rate-interpretation", "report readmission rates with denominator", "five synthetic episodes do not estimate clinical performance", "eligible_discharge", "unplanned_readmission_30d"),
            Topic("decision-support", "recommend human clinical review", "clinical staff retain decision authority; no automatic care change is supported", "episode_id", "risk_score"),
        ),
        definitions={
            "eligible_discharge": "synthetic index discharge meeting the defined cohort criteria",
            "followup_complete": "the full 30-day outcome window is observed",
            "readmission_outcome": "unplanned readmission within 30 days after the index discharge",
            "planned_readmission": "excluded from the unplanned readmission numerator",
            "rate": "eligible index discharge episodes with complete 30-day follow-up and an unplanned readmission divided by eligible index discharge episodes with complete follow-up",
            "feature_cutoff": "all predictors must be available no later than index discharge",
            "clinical_policy": "score is decision support only; qualified clinicians retain all decisions",
            "privacy": "synthetic non-identifying episode keys only; no patient-level identifiers",
        },
    ),
)
