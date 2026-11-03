from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


SOURCE_REFERENCE = (
    "Original synthetic InsightOS EDA scenario based on concepts in the user prompt; "
    "referenced PDF and source archive were not available."
)
LICENSE = "Original synthetic content"
from training.reference_eda.scenario_catalogue import (
    SCENARIOS as APPROVED_SCENARIOS,
    Scenario as ApprovedScenario,
    Topic as ApprovedTopic,
)
from app.agent.inspection_handoff import InspectionHandoff, build_inspection_handoff
from app.agent.tools import (
    DatasetInspectionHandoff,
    ToolResult,
    _profile_summary,
    _quality_summary,
)

SYSTEM_MESSAGE = (
    "You are an InsightOS business analytics assistant. Use only the supplied "
    "synthetic schema, fixture, and tool results. The fixture is a small teaching "
    "sample, not production evidence; do not generalize or claim statistical "
    "significance. Distinguish an analysis plan from executed work. Do not invent "
    "columns, results, relationships, or causes. Check grain, missingness, "
    "denominators, and time coverage before interpreting metrics. Use only "
    "registered tools, and do not claim a tool ran unless its response appears. "
    "Return safe, read-only SQL when requested; do not use SELECT *."
)


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
    table: str
    dax_table: str
    grain: str
    columns: dict[str, str]
    rows: tuple[dict[str, Any], ...]
    topics: tuple[Topic, ...]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="campaign",
        title="Synthetic campaign performance",
        table="campaign_daily",
        dax_table="Campaign Daily",
        grain="one campaign-channel-day row",
        columns={
            "event_date": "campaign activity date",
            "campaign": "campaign label",
            "channel": "acquisition channel",
            "spend": "advertising spend in synthetic currency units",
            "impressions": "served impressions",
            "clicks": "click count",
            "leads": "lead count",
            "conversions": "attributed conversion count",
            "attributed_revenue": "attributed revenue in synthetic currency units",
            "audience_segment": "optional audience label",
        },
        rows=(
            {"event_date": "2025-01-03", "campaign": "Search-A", "channel": "search", "spend": 120, "impressions": 6000, "clicks": 300, "leads": 60, "conversions": 15, "attributed_revenue": 900, "audience_segment": "new"},
            {"event_date": "2025-01-04", "campaign": "Search-A", "channel": "search", "spend": 80, "impressions": 4000, "clicks": 160, "leads": 32, "conversions": 8, "attributed_revenue": 640, "audience_segment": None},
            {"event_date": "2025-01-03", "campaign": "Social-B", "channel": "social", "spend": 100, "impressions": 10000, "clicks": 200, "leads": 50, "conversions": 5, "attributed_revenue": 400, "audience_segment": "new"},
            {"event_date": "2025-01-04", "campaign": "Social-B", "channel": "social", "spend": 100, "impressions": 8000, "clicks": 160, "leads": 40, "conversions": 4, "attributed_revenue": 320, "audience_segment": "returning"},
            {"event_date": "2025-01-05", "campaign": "Email-C", "channel": "email", "spend": 20, "impressions": 1000, "clicks": 100, "leads": 20, "conversions": 10, "attributed_revenue": 500, "audience_segment": "returning"},
        ),
        topics=(
            Topic("spend-efficiency", "compare spend efficiency by channel", "ROAS is not causal return and depends on the attribution window", "channel", "spend"),
            Topic("funnel-conversion", "inspect conversion through the campaign funnel", "funnel rates need consistent event definitions and denominators", "campaign", "conversions"),
            Topic("click-quality", "compare clicks and leads by channel", "a click is not a qualified lead or a customer", "channel", "clicks"),
            Topic("attribution-window", "review attributed revenue by campaign", "attributed revenue depends on the stated attribution rule", "campaign", "attributed_revenue"),
            Topic("date-coverage", "check campaign coverage over time", "five rows over three days do not establish seasonality", "event_date", "impressions"),
            Topic("segment-missingness", "assess the optional audience segment", "missing segment labels must not be silently imputed", "audience_segment", "leads"),
            Topic("outlier-review", "screen spend for unusual values", "a small-sample extreme is a review flag, not an error by itself", "channel", "spend"),
            Topic("duplicate-grain", "verify the campaign-day grain", "campaign keys may require channel and date together", "event_date", "leads"),
            Topic("channel-mix", "describe channel mix without overclaiming", "mix comparisons can be confounded by budget and audience differences", "channel", "impressions"),
            Topic("zero-denominator", "validate rates before dividing", "undefined rates need an explicit zero-denominator guard", "campaign", "clicks"),
        ),
    ),
    Scenario(
        key="commerce",
        title="Synthetic online order lines",
        table="order_lines",
        dax_table="Order Line",
        grain="one product line within an order",
        columns={
            "order_id": "synthetic order identifier",
            "order_date": "order placement date",
            "product": "product label",
            "units": "units on the line",
            "unit_price": "listed unit price",
            "discount_amount": "line-level discount amount",
            "unit_cost": "unit cost",
        },
        rows=(
            {"order_id": "O-101", "order_date": "2025-01-02", "product": "shirt", "units": 2, "unit_price": 30, "discount_amount": 0, "unit_cost": 12},
            {"order_id": "O-102", "order_date": "2025-01-02", "product": "mug", "units": 3, "unit_price": 12, "discount_amount": 2, "unit_cost": 4},
            {"order_id": "O-103", "order_date": "2025-01-03", "product": "shirt", "units": 1, "unit_price": 30, "discount_amount": 5, "unit_cost": 12},
            {"order_id": "O-104", "order_date": "2025-01-04", "product": "bottle", "units": 2, "unit_price": 20, "discount_amount": 0, "unit_cost": 9},
            {"order_id": "O-105", "order_date": "2025-01-05", "product": "mug", "units": 1, "unit_price": 12, "discount_amount": 0, "unit_cost": 4},
        ),
        topics=(
            Topic("line-grain", "confirm order-line versus order-level grain", "summing order totals after joining lines can double-count", "product", "units"),
            Topic("net-sales", "define net sales consistently", "discounts must be subtracted once at their stored grain", "product", "unit_price"),
            Topic("gross-margin", "compare gross margin by product", "revenue and cost must use compatible quantities and grains", "product", "unit_cost"),
            Topic("discount-mix", "inspect discount distribution", "discount amount is not a discount rate without a price denominator", "product", "discount_amount"),
            Topic("order-date", "review daily order coverage", "a few dates cannot support a seasonal conclusion", "order_date", "units"),
            Topic("basket-size", "estimate units per order safely", "count distinct orders rather than counting product lines as orders", "order_date", "units"),
            Topic("product-mix", "compare product contribution", "small counts should be shown beside product averages", "product", "units"),
            Topic("cost-missingness", "check cost coverage before margin analysis", "missing or stale costs can bias margin calculations", "product", "unit_cost"),
            Topic("duplicate-lines", "test for duplicate order-line keys", "order_id alone is not unique when an order has multiple products", "order_id", "units"),
            Topic("returns-scope", "clarify whether returns are in scope", "the fixture has no return field, so returned revenue cannot be inferred", "product", "unit_price"),
        ),
    ),
    Scenario(
        key="subscription",
        title="Synthetic monthly subscription aggregates",
        table="subscription_months",
        dax_table="Subscription Month",
        grain="one month-level aggregate row",
        columns={
            "month": "calendar month",
            "starting_customers": "customers active at period start",
            "new_customers": "new customers during the month",
            "churned_customers": "customers lost during the month",
            "ending_customers": "customers active at period end",
            "mrr": "monthly recurring revenue in synthetic currency units",
        },
        rows=(
            {"month": "2025-01", "starting_customers": 100, "new_customers": 12, "churned_customers": 5, "ending_customers": 107, "mrr": 10700},
            {"month": "2025-02", "starting_customers": 107, "new_customers": 10, "churned_customers": 7, "ending_customers": 110, "mrr": 11000},
            {"month": "2025-03", "starting_customers": 110, "new_customers": 8, "churned_customers": 11, "ending_customers": 107, "mrr": 10700},
        ),
        topics=(
            Topic("churn-denominator", "calculate logo churn using the period-start base", "do not divide churn by ending customers when the requested definition is period-start churn", "month", "churned_customers"),
            Topic("mrr-reconciliation", "reconcile monthly recurring revenue", "aggregate MRR is not customer-level expansion or contraction evidence", "month", "mrr"),
            Topic("customer-bridge", "check the customer-count bridge", "ending equals starting plus new minus churned only under consistent definitions", "month", "ending_customers"),
            Topic("cohort-limits", "describe what cohort analysis would require", "monthly aggregates do not identify signup cohorts", "month", "new_customers"),
            Topic("plan-mix", "assess plan-level retention", "the fixture has no plan dimension", "month", "starting_customers"),
            Topic("period-coverage", "check month continuity", "three months are insufficient for a seasonal claim", "month", "mrr"),
            Topic("zero-base", "guard churn calculations for a zero base", "a zero starting-customer denominator makes the rate undefined", "month", "starting_customers"),
            Topic("gross-versus-net", "clarify gross and net MRR", "the fixture does not include discounts, refunds, or taxes", "month", "mrr"),
            Topic("aggregate-grain", "avoid customer-level claims from aggregates", "one row per month cannot establish individual customer behavior", "month", "churned_customers"),
            Topic("bridge-anomaly", "screen for count-bridge inconsistencies", "an arithmetic mismatch should trigger definition checks before an error claim", "month", "new_customers"),
        ),
    ),
    Scenario(
        key="inventory",
        title="Synthetic stock snapshots",
        table="stock_snapshots",
        dax_table="Stock Snapshot",
        grain="one SKU-location-date snapshot",
        columns={
            "sku": "synthetic stock-keeping-unit label",
            "location": "warehouse location label",
            "snapshot_date": "inventory snapshot date",
            "on_hand": "units currently on hand",
            "units_sold_7d": "units sold in the prior seven days",
            "lead_time_days": "supplier lead time in days",
            "stockout_days_7d": "stockout days in the prior seven days",
        },
        rows=(
            {"sku": "SKU-A", "location": "north", "snapshot_date": "2025-02-01", "on_hand": 20, "units_sold_7d": 14, "lead_time_days": 3, "stockout_days_7d": 0},
            {"sku": "SKU-B", "location": "north", "snapshot_date": "2025-02-01", "on_hand": 3, "units_sold_7d": 21, "lead_time_days": 5, "stockout_days_7d": 2},
            {"sku": "SKU-C", "location": "south", "snapshot_date": "2025-02-01", "on_hand": 12, "units_sold_7d": 7, "lead_time_days": 2, "stockout_days_7d": 0},
            {"sku": "SKU-D", "location": "south", "snapshot_date": "2025-02-01", "on_hand": 0, "units_sold_7d": 10, "lead_time_days": 4, "stockout_days_7d": 3},
            {"sku": "SKU-E", "location": "west", "snapshot_date": "2025-02-01", "on_hand": 8, "units_sold_7d": 14, "lead_time_days": 3, "stockout_days_7d": 1},
        ),
        topics=(
            Topic("stockout-rate", "summarize observed stockout incidence", "the snapshot count is not a forecast of future stockouts", "location", "stockout_days_7d"),
            Topic("days-cover", "estimate days of cover only where demand is positive", "zero demand needs a separate undefined or not-applicable state", "sku", "on_hand"),
            Topic("lead-time", "compare on-hand stock with lead time", "one weekly demand window may not represent replenishment demand", "location", "lead_time_days"),
            Topic("location-mix", "compare inventory across locations", "location averages can hide SKU-level shortages", "location", "on_hand"),
            Topic("snapshot-grain", "verify SKU-location-date uniqueness", "multiple snapshots must not be summed as simultaneous inventory", "snapshot_date", "on_hand"),
            Topic("demand-window", "check the demand observation window", "seven-day sales are not a validated demand forecast", "sku", "units_sold_7d"),
            Topic("zero-stock", "separate zero on-hand from missing stock", "zero is observed quantity, not missing data", "location", "on_hand"),
            Topic("reorder-context", "identify inputs needed for a reorder policy", "safety stock and service-level targets are not supplied", "sku", "lead_time_days"),
            Topic("stockout-duration", "summarize stockout duration", "stockout days are bounded by the reported seven-day window", "location", "stockout_days_7d"),
            Topic("small-sku-count", "communicate limits of the SKU sample", "five synthetic SKUs do not establish network-wide risk", "location", "units_sold_7d"),
        ),
    ),
    Scenario(
        key="service",
        title="Synthetic customer-support tickets",
        table="support_tickets",
        dax_table="Support Ticket",
        grain="one ticket",
        columns={
            "ticket_id": "synthetic ticket identifier",
            "created_date": "ticket creation date",
            "channel": "support channel",
            "priority": "ticket priority label",
            "resolution_hours": "elapsed hours to resolution",
            "reopened": "whether the ticket was reopened",
            "csat_score": "optional satisfaction score from one to five",
        },
        rows=(
            {"ticket_id": "T-201", "created_date": "2025-03-01", "channel": "email", "priority": "normal", "resolution_hours": 2, "reopened": 0, "csat_score": 5},
            {"ticket_id": "T-202", "created_date": "2025-03-01", "channel": "chat", "priority": "high", "resolution_hours": 6, "reopened": 1, "csat_score": 4},
            {"ticket_id": "T-203", "created_date": "2025-03-02", "channel": "email", "priority": "normal", "resolution_hours": 8, "reopened": 0, "csat_score": 3},
            {"ticket_id": "T-204", "created_date": "2025-03-02", "channel": "web", "priority": "urgent", "resolution_hours": 24, "reopened": 1, "csat_score": None},
            {"ticket_id": "T-205", "created_date": "2025-03-03", "channel": "chat", "priority": "normal", "resolution_hours": 4, "reopened": 0, "csat_score": 5},
        ),
        topics=(
            Topic("median-resolution", "summarize resolution time robustly", "a small sample median does not establish an SLA", "channel", "resolution_hours"),
            Topic("reopen-rate", "calculate the observed reopen share", "reopened tickets need a clearly stated ticket denominator", "priority", "reopened"),
            Topic("csat-response-bias", "inspect CSAT coverage before comparing scores", "respondents may differ from nonrespondents", "channel", "csat_score"),
            Topic("priority-mix", "compare ticket mix by priority", "raw resolution averages are confounded by priority mix", "priority", "resolution_hours"),
            Topic("arrival-coverage", "review ticket arrival dates", "three days do not establish a weekly pattern", "created_date", "resolution_hours"),
            Topic("open-ticket-censoring", "identify censoring risks in resolution time", "unresolved tickets are absent from this resolved-only fixture", "channel", "resolution_hours"),
            Topic("ticket-key", "check ticket identifier uniqueness", "one row per ticket must be verified before counting", "ticket_id", "reopened"),
            Topic("csat-scale", "validate the satisfaction score scale", "the score is ordinal and missing for one synthetic ticket", "priority", "csat_score"),
            Topic("channel-mix", "describe channel differences cautiously", "channel comparisons are descriptive and not causal", "channel", "resolution_hours"),
            Topic("sample-size", "report the small sample with its limits", "five synthetic tickets cannot support general service claims", "created_date", "reopened"),
        ),
    ),
)

Scenario = ApprovedScenario
Topic = ApprovedTopic
SCENARIOS = APPROVED_SCENARIOS
_SCENARIO_BY_KEY = {scenario.key: scenario for scenario in SCENARIOS}
_CENT = Decimal("0.01")


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _rounded(value: Decimal, places: Decimal = _CENT) -> str:
    return str(value.quantize(places, rounding=ROUND_HALF_UP))


def profile_for(scenario: Scenario) -> dict[str, Any]:
    rows = scenario.rows
    duplicate_count = len(rows) - len(
        {json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows}
    )
    missing = {
        column: sum(row.get(column) is None for row in rows)
        for column in scenario.columns
    }
    return {
        "row_count": len(rows),
        "column_count": len(scenario.columns),
        "duplicate_row_count": duplicate_count,
        "missing_values_by_column": {
            name: count for name, count in missing.items() if count
        },
    }


def metric_summary(scenario: Scenario) -> dict[str, str]:
    rows = scenario.rows
    if scenario.key == "marketing":
        results: dict[str, str] = {}
        for channel in sorted({row["channel"] for row in rows}):
            channel_rows = [row for row in rows if row["channel"] == channel]
            spend = sum((_decimal(row["spend"]) for row in channel_rows), Decimal(0))
            leads = sum((_decimal(row["leads"]) for row in channel_rows), Decimal(0))
            qualified = sum(
                (_decimal(row["qualified_leads"]) for row in channel_rows),
                Decimal(0),
            )
            revenue = sum(
                (_decimal(row["attributed_net_revenue"]) for row in channel_rows),
                Decimal(0),
            )
            conversions = sum(
                (_decimal(row["deduplicated_conversions"]) for row in channel_rows),
                Decimal(0),
            )
            results[f"{channel}_spend"] = _rounded(spend)
            results[f"{channel}_leads"] = _rounded(leads)
            results[f"{channel}_qualified_leads"] = _rounded(qualified)
            results[f"{channel}_qualified_cpa"] = (
                _rounded(spend / qualified) if qualified else "undefined"
            )
            results[f"{channel}_attributed_net_revenue"] = _rounded(revenue)
            results[f"{channel}_roas"] = _rounded(revenue / spend) if spend else "undefined"
            results[f"{channel}_deduplicated_conversions"] = _rounded(conversions)
        spend = sum((_decimal(row["spend"]) for row in rows), Decimal(0))
        revenue = sum(
            (_decimal(row["attributed_net_revenue"]) for row in rows), Decimal(0)
        )
        return {
            **results,
            "spend": _rounded(spend),
            "attributed_revenue": _rounded(revenue),
            "roas": _rounded(revenue / spend) if spend else "undefined",
        }
    if scenario.key == "ecommerce":
        sales = sum(
            (
                _decimal(row["quantity"]) * _decimal(row["unit_price"])
                - _decimal(row["allocated_discount"])
                - _decimal(row["refund_amount"])
                for row in rows
                if not row["cancelled"]
            ),
            Decimal(0),
        )
        cost = sum(
            (
                _decimal(row["quantity"]) * _decimal(row["unit_cost"])
                for row in rows
                if not row["cancelled"]
            ),
            Decimal(0),
        )
        margin = (sales - cost) / sales * 100 if sales else None
        product_results: dict[str, str] = {}
        for product in sorted({row["product"] for row in rows}):
            product_rows = [
                row
                for row in rows
                if row["product"] == product and not row["cancelled"]
            ]
            product_sales = sum(
                (
                    _decimal(row["quantity"]) * _decimal(row["unit_price"])
                    - _decimal(row["allocated_discount"])
                    - _decimal(row["refund_amount"])
                    for row in product_rows
                ),
                Decimal(0),
            )
            product_cost = sum(
                (
                    _decimal(row["quantity"]) * _decimal(row["unit_cost"])
                    for row in product_rows
                ),
                Decimal(0),
            )
            product_margin = (
                (product_sales - product_cost) / product_sales * 100
                if product_sales
                else None
            )
            product_results[f"{product}_net_revenue"] = _rounded(product_sales)
            product_results[f"{product}_gross_margin_percent"] = (
                _rounded(product_margin) if product_margin is not None else "undefined"
            )
        return {
            **product_results,
            "net_revenue": _rounded(sales),
            "cost_of_goods_sold": _rounded(cost),
            "gross_profit": _rounded(sales - cost),
            "gross_margin_percent": _rounded(margin) if margin is not None else "undefined",
            "included_order_lines": str(sum(not row["cancelled"] for row in rows)),
        }
    if scenario.key == "maintenance":
        complete = [row for row in rows if row["sensor_complete"]]
        confirmed_failures = sum(
            bool(row["failure_within_30d"]) for row in complete
        )
        return {
            "observation_count": str(len(rows)),
            "complete_sensor_observations": str(len(complete)),
            "incomplete_sensor_observations": str(len(rows) - len(complete)),
            "confirmed_failure_labels_among_complete": str(confirmed_failures),
            "prediction_horizon_days": "30",
        }
    if scenario.key == "fraud":
        reviewed = [row for row in rows if row["review_status"] == "completed"]
        flagged = [row for row in reviewed if row["flagged"]]
        true_positive = sum(bool(row["confirmed_fraud"]) for row in flagged)
        false_positive = len(flagged) - true_positive
        confirmed = sum(bool(row["confirmed_fraud"]) for row in reviewed)
        false_negative = confirmed - true_positive
        return {
            "reviewed_transactions": str(len(reviewed)),
            "flagged_transactions": str(len(flagged)),
            "confirmed_fraud": str(confirmed),
            "true_positives": str(true_positive),
            "false_positives": str(false_positive),
            "false_negatives": str(false_negative),
            "precision_percent": _rounded(Decimal(true_positive) / len(flagged) * 100) if flagged else "undefined",
            "recall_percent": _rounded(Decimal(true_positive) / confirmed * 100) if confirmed else "undefined",
            "label_rule": "completed review only",
        }
    if scenario.key == "healthcare":
        eligible = [
            row
            for row in rows
            if row["eligible_discharge"]
            and row["followup_complete"]
        ]
        readmissions = sum(
            bool(row["unplanned_readmission_30d"]) and not row["planned_readmission"]
            for row in eligible
        )
        leakage_rows = sum(
            bool(row["eligible_discharge"])
            and not row["features_available_at_discharge"]
            for row in rows
        )
        return {
            "eligible_complete_discharge_episodes": str(len(eligible)),
            "unplanned_readmissions_within_30d": str(readmissions),
            "readmission_rate_percent": (
                _rounded(Decimal(readmissions) / len(eligible) * 100)
                if eligible
                else "undefined"
            ),
            "episodes_with_post_discharge_feature_risk": str(leakage_rows),
            "planned_readmission_policy": "excluded from outcome numerator",
            "clinical_review": "required",
        }
    raise ValueError(f"No metric summary for scenario {scenario.key}")


def _tool_catalogue() -> dict[str, dict[str, Any]]:
    from app.agent.tools import TOOL_DEFINITIONS

    return {
        definition.name: definition.provider_schema()
        for definition in TOOL_DEFINITIONS
    }


def _format_schema(scenario: Scenario) -> str:
    return "\n".join(
        f"- {name}: {description}" for name, description in scenario.columns.items()
    )


def _dax_example(scenario: Scenario, topic: Topic) -> str:
    if scenario.key == "ecommerce":
        return (
            "```dax\n"
            "Net Line Revenue =\n"
            "SUMX (\n"
            "    FILTER ( 'Order Line', NOT 'Order Line'[cancelled] ),\n"
            "    'Order Line'[quantity] * 'Order Line'[unit_price]\n"
            "        - 'Order Line'[allocated_discount]\n"
            "        - 'Order Line'[refund_amount]\n"
            ")\n\n"
            "COGS =\n"
            "SUMX (\n"
            "    FILTER ( 'Order Line', NOT 'Order Line'[cancelled] ),\n"
            "    'Order Line'[quantity] * 'Order Line'[unit_cost]\n"
            ")\n\n"
            "Gross Margin % =\n"
            "DIVIDE ( [Net Line Revenue] - [COGS], [Net Line Revenue] )\n"
            "```\n"
            "This fixture allocates discounts at line grain, subtracts refunds, "
            "excludes cancelled lines, and omits unavailable tax and shipping. "
            "DIVIDE returns blank for zero net revenue."
        )
    if scenario.key == "marketing":
        return (
            "```dax\n"
            "Advertising Spend = SUM ( 'Marketing Daily'[spend] )\n\n"
            "Qualified Leads = SUM ( 'Marketing Daily'[qualified_leads] )\n\n"
            "Cost per Qualified Lead =\n"
            "DIVIDE ( [Advertising Spend], [Qualified Leads] )\n\n"
            "Attributed Net Revenue =\n"
            "SUM ( 'Marketing Daily'[attributed_net_revenue] )\n\n"
            "ROAS = DIVIDE ( [Attributed Net Revenue], [Advertising Spend] )\n"
            "```\n"
            "Use the supplied last-click, 30-day attribution definition. ROAS is "
            "attributed revenue divided by spend, not causal lift."
        )
    if scenario.key == "maintenance":
        return (
            "```dax\n"
            "Complete Sensor Observations =\n"
            "CALCULATE (\n"
            "    COUNTROWS ( 'Machine Observation' ),\n"
            "    'Machine Observation'[sensor_complete] = TRUE ()\n"
            ")\n\n"
            "Confirmed Failures Within 30 Days =\n"
            "CALCULATE (\n"
            "    COUNTROWS ( 'Machine Observation' ),\n"
            "    'Machine Observation'[sensor_complete] = TRUE (),\n"
            "    'Machine Observation'[failure_within_30d] = TRUE ()\n"
            ")\n"
            "```\n"
            "The outcome is a confirmed label within the stated horizon; incomplete "
            "sensors increase uncertainty and do not establish failure."
        )
    if scenario.key == "fraud":
        return (
            "```dax\n"
            "Reviewed Transactions =\n"
            "CALCULATE ( COUNTROWS ( 'Fraud Review' ), 'Fraud Review'[review_status] = \"completed\" )\n\n"
            "True Positives =\n"
            "CALCULATE (\n"
            "    COUNTROWS ( 'Fraud Review' ),\n"
            "    'Fraud Review'[review_status] = \"completed\",\n"
            "    'Fraud Review'[flagged] = TRUE (),\n"
            "    'Fraud Review'[confirmed_fraud] = TRUE ()\n"
            ")\n\n"
            "Flagged Reviewed Transactions =\n"
            "CALCULATE (\n"
            "    COUNTROWS ( 'Fraud Review' ),\n"
            "    'Fraud Review'[review_status] = \"completed\",\n"
            "    'Fraud Review'[flagged] = TRUE ()\n"
            ")\n\n"
            "Confirmed Fraud Transactions =\n"
            "CALCULATE (\n"
            "    COUNTROWS ( 'Fraud Review' ),\n"
            "    'Fraud Review'[review_status] = \"completed\",\n"
            "    'Fraud Review'[confirmed_fraud] = TRUE ()\n"
            ")\n\n"
            "Precision = DIVIDE ( [True Positives], [Flagged Reviewed Transactions] )\n"
            "Recall = DIVIDE ( [True Positives], [Confirmed Fraud Transactions] )\n"
            "```\n"
            "Define the denominator measures over completed reviews; a flag is "
            "suspicion for investigation, not confirmed fraud."
        )
    if scenario.key == "healthcare":
        return (
            "```dax\n"
            "Eligible Followed Discharges =\n"
            "CALCULATE (\n"
            "    COUNTROWS ( 'Discharge Episode' ),\n"
            "    'Discharge Episode'[eligible_discharge] = TRUE (),\n"
            "    'Discharge Episode'[followup_complete] = TRUE ()\n"
            ")\n\n"
            "Unplanned Readmissions Within 30 Days =\n"
            "CALCULATE (\n"
            "    COUNTROWS ( 'Discharge Episode' ),\n"
            "    'Discharge Episode'[eligible_discharge] = TRUE (),\n"
            "    'Discharge Episode'[followup_complete] = TRUE (),\n"
            "    'Discharge Episode'[planned_readmission] = FALSE (),\n"
            "    'Discharge Episode'[unplanned_readmission_30d] = TRUE ()\n"
            ")\n\n"
            "30-Day Readmission Rate =\n"
            "DIVIDE ( [Unplanned Readmissions Within 30 Days], [Eligible Followed Discharges] )\n"
            "```\n"
            "Use only features available at discharge for prediction and keep human "
            "clinical review in the decision loop."
        )
    return (
        f"```dax\nObserved Rows =\n"
        f"COUNTROWS ( '{scenario.dax_table}' )\n"
        "```\n"
        f"Use `{topic.dimension}` as the visual grouping and confirm the intended "
        "grain and filters. A row count is descriptive, not a business outcome."
    )


def _code_example(scenario: Scenario, topic: Topic, topic_index: int) -> tuple[str, str]:
    if topic_index % 3 == 0:
        queries = {
            "marketing": (
                'SELECT channel, SUM(spend) AS spend, '
                'SUM(qualified_leads) AS qualified_leads, '
                'ROUND(1.0 * SUM(spend) / NULLIF(SUM(qualified_leads), 0), 2) AS qualified_cpa, '
                'SUM(attributed_net_revenue) AS attributed_net_revenue, '
                'ROUND(1.0 * SUM(attributed_net_revenue) / NULLIF(SUM(spend), 0), 2) AS roas '
                'FROM marketing_daily GROUP BY channel ORDER BY channel'
            ),
            "maintenance": (
                'SELECT machine, COUNT(*) AS observations, '
                'SUM(CASE WHEN sensor_complete = 1 THEN 1 ELSE 0 END) AS complete_observations, '
                'SUM(CASE WHEN sensor_complete = 1 AND failure_within_30d = 1 THEN 1 ELSE 0 END) '
                'AS confirmed_failures_within_30d FROM machine_observations '
                'GROUP BY machine ORDER BY machine'
            ),
            "ecommerce": (
                'SELECT SUM(CASE WHEN cancelled = 0 THEN '
                'quantity * unit_price - allocated_discount - refund_amount ELSE 0 END) AS net_revenue, '
                'SUM(CASE WHEN cancelled = 0 THEN quantity * unit_cost ELSE 0 END) AS cogs, '
                'ROUND(100.0 * (SUM(CASE WHEN cancelled = 0 THEN '
                'quantity * unit_price - allocated_discount - refund_amount ELSE 0 END) - '
                'SUM(CASE WHEN cancelled = 0 THEN quantity * unit_cost ELSE 0 END)) / '
                'NULLIF(SUM(CASE WHEN cancelled = 0 THEN '
                'quantity * unit_price - allocated_discount - refund_amount ELSE 0 END), 0), 2) '
                'AS gross_margin_percent FROM order_lines'
            ),
            "fraud": (
                'SELECT SUM(CASE WHEN flagged = 1 AND confirmed_fraud = 1 THEN 1 ELSE 0 END) AS true_positives, '
                'SUM(CASE WHEN flagged = 1 THEN 1 ELSE 0 END) AS flagged_transactions, '
                'SUM(CASE WHEN confirmed_fraud = 1 THEN 1 ELSE 0 END) AS confirmed_fraud, '
                'ROUND(100.0 * SUM(CASE WHEN flagged = 1 AND confirmed_fraud = 1 THEN 1 ELSE 0 END) / '
                'NULLIF(SUM(CASE WHEN flagged = 1 THEN 1 ELSE 0 END), 0), 2) AS precision_percent, '
                'ROUND(100.0 * SUM(CASE WHEN flagged = 1 AND confirmed_fraud = 1 THEN 1 ELSE 0 END) / '
                'NULLIF(SUM(CASE WHEN confirmed_fraud = 1 THEN 1 ELSE 0 END), 0), 2) AS recall_percent '
                'FROM fraud_reviews WHERE review_status = \'completed\''
            ),
            "healthcare": (
                'SELECT SUM(CASE WHEN eligible_discharge = 1 AND followup_complete = 1 THEN 1 ELSE 0 END) '
                'AS eligible_discharges, SUM(CASE WHEN eligible_discharge = 1 AND followup_complete = 1 '
                'AND planned_readmission = 0 AND unplanned_readmission_30d = 1 THEN 1 ELSE 0 END) '
                'AS unplanned_readmissions, ROUND(100.0 * SUM(CASE WHEN eligible_discharge = 1 '
                'AND followup_complete = 1 AND planned_readmission = 0 AND '
                'unplanned_readmission_30d = 1 THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN '
                'eligible_discharge = 1 AND followup_complete = 1 THEN 1 ELSE 0 END), 0), 2) '
                'AS readmission_rate_percent FROM discharge_episodes'
            ),
        }
        query = queries[scenario.key]
        return (
            f"```sql\n{query}\n```\n"
            f"This read-only query calculates a fixture KPI for {scenario.key}. "
            f"Respect the supplied definitions: {topic.pitfall}",
            query,
        )
    if topic_index % 3 == 1:
        code = {
            "marketing": (
                "summary = {}\n"
                "for channel in sorted({row['channel'] for row in rows}):\n"
                "    channel_rows = [row for row in rows if row['channel'] == channel]\n"
                "    spend = sum(row['spend'] for row in channel_rows)\n"
                "    leads = sum(row['leads'] for row in channel_rows)\n"
                "    qualified = sum(row['qualified_leads'] for row in channel_rows)\n"
                "    revenue = sum(row['attributed_net_revenue'] for row in channel_rows)\n"
                "    summary[channel] = {'spend': spend, 'leads': leads, 'qualified_leads': qualified,\n"
                "        'qualified_cpa': spend / qualified if qualified else None,\n"
                "        'attributed_net_revenue': revenue, 'roas': revenue / spend if spend else None}\n"
            ),
            "maintenance": (
                "complete = [row for row in rows if row['sensor_complete']]\n"
                "summary = {'complete_sensor_observations': len(complete),\n"
                "    'incomplete_sensor_observations': len(rows) - len(complete),\n"
                "    'confirmed_failures_within_30d': sum(row['failure_within_30d'] for row in complete)}\n"
            ),
            "ecommerce": (
                "included = [row for row in rows if not row['cancelled']]\n"
                "net_revenue = sum(row['quantity'] * row['unit_price'] - row['allocated_discount']\n"
                "    - row['refund_amount'] for row in included)\n"
                "cogs = sum(row['quantity'] * row['unit_cost'] for row in included)\n"
                "summary = {'net_revenue': net_revenue, 'cogs': cogs,\n"
                "    'gross_profit': net_revenue - cogs,\n"
                "    'gross_margin_percent': 100 * (net_revenue - cogs) / net_revenue if net_revenue else None}\n"
            ),
            "fraud": (
                "reviewed = [row for row in rows if row['review_status'] == 'completed']\n"
                "flagged = [row for row in reviewed if row['flagged']]\n"
                "true_positives = sum(row['confirmed_fraud'] for row in flagged)\n"
                "confirmed = sum(row['confirmed_fraud'] for row in reviewed)\n"
                "summary = {'true_positives': true_positives, 'false_positives': len(flagged) - true_positives,\n"
                "    'precision_percent': 100 * true_positives / len(flagged) if flagged else None,\n"
                "    'recall_percent': 100 * true_positives / confirmed if confirmed else None}\n"
            ),
            "healthcare": (
                "eligible = [row for row in rows if row['eligible_discharge'] and row['followup_complete']]\n"
                "readmissions = sum(row['unplanned_readmission_30d'] and not row['planned_readmission']\n"
                "    for row in eligible)\n"
                "summary = {'eligible_discharge_episodes': len(eligible),\n"
                "    'unplanned_readmissions_30d': readmissions,\n"
                "    'readmission_rate_percent': 100 * readmissions / len(eligible) if eligible else None,\n"
                "    'post_discharge_feature_risk_rows': sum(row['eligible_discharge'] and\n"
                "        not row['features_available_at_discharge'] for row in rows)}\n"
            ),
        }[scenario.key]
        return (
            f"```python\n{code}```\n"
            f"This calculates a deterministic {scenario.key} measure from the "
            "provided fixture only. Guard zero denominators and retain its stated limitations.",
            code,
        )
    return _dax_example(scenario, topic), ""


def _synthetic_profile_response(
    scenario: Scenario,
    dataset_id: int,
    file_id: int,
) -> dict[str, Any]:
    unique_rows = {
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in scenario.rows
    }
    columns = []
    for position, name in enumerate(scenario.columns):
        values = [row.get(name) for row in scenario.rows]
        present = [value for value in values if value is not None]
        type_names = sorted(
            {
                "boolean"
                if isinstance(value, bool)
                else "integer"
                if isinstance(value, int)
                else "number"
                if isinstance(value, float)
                else "string"
                for value in present
            }
        )
        columns.append(
            {
                "name": name,
                "position": position,
                "physical_types": type_names,
                "missing_value_count": len(values) - len(present),
                "empty_value_count": sum(value == "" for value in present),
                "distinct_value_count": len(
                    {
                        json.dumps(
                            value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        for value in present
                    }
                ),
                "distinct_count_exact": True,
            }
        )
    checksum = hashlib.sha256(
        json.dumps(
            list(scenario.rows),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "dataset_id": dataset_id,
        "file_id": file_id,
        "profile_result": {
            "version": 1,
            "warnings": ["Synthetic fixture profile; not a persisted live result."],
            "unsupported_analyses": [
                "Synthetic fixture results are not production or population evidence."
            ],
            "tables": [
                {
                    "name": scenario.table,
                    "row_count": len(scenario.rows),
                    "column_count": len(scenario.columns),
                    "duplicate_row_count": len(scenario.rows) - len(unique_rows),
                    "duplicate_row_count_exact": True,
                    "columns": columns,
                }
            ],
        },
        "source_sha256": checksum,
    }


def _synthetic_inspection_handoff(
    scenario: Scenario,
    dataset_id: int,
    file_id: int,
) -> dict[str, Any]:
    profile = _synthetic_profile_response(scenario, dataset_id, file_id)
    checksum = profile["source_sha256"]
    handoff = build_inspection_handoff(
        file_record={
            "id": file_id,
            "dataset_id": dataset_id,
            "filename": f"{scenario.table}.synthetic",
            "checksum": checksum,
            "status": "Ready",
            "updated_at": "2025-01-01T00:00:00Z",
        },
        profile_response=profile,
        quality_response={
            "dataset_id": dataset_id,
            "file_id": file_id,
            "status": "Unavailable",
            "report_is_current": False,
            "quality_report": None,
        },
    )
    return InspectionHandoff.model_validate(handoff).model_dump(mode="json")


def _synthetic_dataset_inspection(
    scenario: Scenario,
    dataset_id: int,
    file_id: int,
    profile_response: dict[str, Any],
) -> dict[str, Any]:
    profile_summary = _profile_summary(profile_response)
    return DatasetInspectionHandoff.model_validate(
        {
            "contract_version": 1,
            "agent": "data_intelligence",
            "dataset_id": dataset_id,
            "file_id": file_id,
            "status": "completed",
            "file": {
                "id": file_id,
                "filename": f"{scenario.table}.synthetic",
                "status": "Ready",
                "tables": profile_summary["profile"]["tables"],
            },
            "profile": profile_summary["profile"],
            "source_fingerprint": profile_response["source_sha256"],
        }
    ).model_dump(mode="json")


def _serialized_tool_result(
    data: dict[str, Any] | list[Any],
    *,
    summary: str,
    warnings: tuple[str, ...] = (),
    references: tuple[dict[str, Any], ...] = (),
    limitations: tuple[str, ...] = (),
) -> str:
    result = ToolResult(
        status="succeeded",
        summary=summary,
        data=data,
        warnings=warnings,
        references=references,
        limitations=limitations,
        error_code=None,
    )
    return json.dumps(
        asdict(result),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _tool_example(
    scenario: Scenario,
    topic: Topic,
    scenario_index: int,
    topic_index: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    catalogue = _tool_catalogue()
    dataset_id = 1000 + scenario_index * 10 + topic_index
    file_id = 2000 + scenario_index * 10 + topic_index
    tool_names = (
        "get_inspection_handoff",
        "get_profile",
        "get_quality_report",
        "run_quality_analysis",
        "run_eda_analysis",
    )
    tool_name = tool_names[topic_index % len(tool_names)]
    if tool_name in {
        "get_inspection_handoff",
        "get_profile",
        "get_quality_report",
        "run_quality_analysis",
    }:
        arguments = {"dataset_id": dataset_id, "file_id": file_id}
    else:
        arguments = {
            "dataset_id": dataset_id,
            "file_id": file_id,
            "analysis_type": "bivariate",
            "selected_columns": [topic.dimension, topic.measure],
        }
    call_id = f"call-{scenario.key}-{topic.key}"
    profile_response = _synthetic_profile_response(
        scenario,
        dataset_id,
        file_id,
    )
    profile_summary = _profile_summary(profile_response)
    if tool_name == "get_inspection_handoff":
        result_data = _synthetic_inspection_handoff(
            scenario,
            dataset_id,
            file_id,
        )
        result_json = _serialized_tool_result(
            result_data,
            summary="Built a schema-validated handoff from the synthetic fixture.",
            warnings=tuple(result_data["unresolved"]),
            limitations=tuple(result_data["limitations"]),
        )
    elif tool_name == "get_profile":
        result_json = _serialized_tool_result(
            profile_summary,
            summary="Retrieved the synthetic fixture profile.",
            limitations=(
                "This is a mocked training fixture, not a persisted live profile.",
            ),
        )
    elif tool_name == "get_quality_report":
        quality_data = _quality_summary(
            {
                "dataset_id": dataset_id,
                "file_id": file_id,
                "status": "Unavailable",
                "report_is_current": False,
                "error": "No stored quality report exists for this synthetic fixture.",
            }
        )
        result_json = _serialized_tool_result(
            quality_data,
            summary="No stored quality report is available for this synthetic fixture.",
            warnings=("The synthetic fixture has no persisted quality report.",),
            limitations=("No quality analysis was executed.",),
        )
    elif tool_name == "run_quality_analysis":
        result_data = {
            "dataset_id": dataset_id,
            "file_id": file_id,
            "status": "Completed",
            "report_is_current": True,
            "synthetic_fixture": True,
            "agent1_inspection": _synthetic_dataset_inspection(
                scenario,
                dataset_id,
                file_id,
                profile_response,
            ),
            "missing_values_by_column": profile_for(scenario)[
                "missing_values_by_column"
            ],
        }
        result_json = _serialized_tool_result(
            result_data,
            summary="Mocked deterministic quality results for the synthetic fixture.",
            references=(
                {
                    "type": "synthetic_fixture",
                    "dataset_id": dataset_id,
                    "file_id": file_id,
                },
            ),
            limitations=("No live data or production quality engine was accessed.",),
        )
    else:
        result_data = {
            "id": 3000 + scenario_index * 10 + topic_index,
            "dataset_id": dataset_id,
            "file_id": file_id,
            "status": "Completed",
            "analysis_type": "bivariate",
            "selected_columns": [topic.dimension, topic.measure],
            "synthetic_fixture": True,
            "metric_summary": metric_summary(scenario),
            "agent1_inspection": _synthetic_dataset_inspection(
                scenario,
                dataset_id,
                file_id,
                profile_response,
            ),
        }
        result_json = _serialized_tool_result(
            result_data,
            summary="Mocked completed EDA result for the synthetic fixture.",
            references=(
                {
                    "type": "synthetic_fixture",
                    "dataset_id": dataset_id,
                    "file_id": file_id,
                    "analysis_id": result_data["id"],
                    "source_sha256": profile_response["source_sha256"],
                },
            ),
            limitations=("No live data or production EDA engine was accessed.",),
        )

    tool_messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": arguments},
                }
            ],
        },
        {
            "role": "tool",
            "name": tool_name,
            "tool_call_id": call_id,
            "content": result_json,
        },
    ]
    final = (
        f"The mocked {tool_name} response is grounded in the explicitly supplied "
        f"synthetic dataset_id={dataset_id} and file_id={file_id}. It covers "
        f"{len(scenario.rows)} rows and {len(scenario.columns)} columns. Treat the "
        "result as synthetic teaching evidence only; no live dataset was accessed."
    )
    return [copy.deepcopy(catalogue[tool_name])], tool_messages, final


def _example_messages(
    scenario: Scenario,
    topic: Topic,
    scenario_index: int,
    topic_index: int,
    mode: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dataset_id = 1000 + scenario_index * 10 + topic_index
    file_id = 2000 + scenario_index * 10 + topic_index
    stakeholder_request = (
        f"I'm the {scenario.stakeholder_role}. My goal is to "
        f"{scenario.stakeholder_goal}. Can you help me {topic.focus}?"
    )
    clarification = (
        f"Before I answer, I need to account for this limitation: {topic.pitfall} "
        "Can you provide the synthetic table definition and the available evidence?"
    )
    prompt_context = (
        f"Scenario: {scenario.title}. This is a fully synthetic teaching fixture. "
        f"Stakeholder role: {scenario.stakeholder_role}. "
        f"Business goal: {scenario.stakeholder_goal}. "
        f"Table `{scenario.table}` has grain: {scenario.grain}. "
        f"Available fields:\n{_format_schema(scenario)}\n"
        f"Required definitions and controls: "
        f"{json.dumps(scenario.definitions, ensure_ascii=False, sort_keys=True)}\n"
        f"Explicit synthetic tool scope, if a registered tool is needed: "
        f"dataset_id={dataset_id}, file_id={file_id}. "
        "These identifiers refer only to this mocked example and not to a live workspace.\n"
        f"Analysis focus: {topic.focus}. Key caution: {topic.pitfall}."
    )
    tools: list[dict[str, Any]] = []
    prefix: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": stakeholder_request},
        {"role": "assistant", "content": clarification},
        {"role": "user", "content": prompt_context},
    ]
    if mode == "schema":
        question = (
            f"Explain the row grain and identify which fields can support an initial "
            f"EDA of {topic.focus}. Name one validation check. Do not claim to have "
            "run an analysis."
        )
        answer = (
            f"The stated grain is {scenario.grain}. For {topic.focus}, "
            f"`{topic.dimension}` is a candidate grouping field and "
            f"`{topic.measure}` is a candidate measure, subject to checking types, "
            "nulls, and key uniqueness. The schema alone does not establish joins, "
            f"causation, or results. In particular, {topic.pitfall}."
        )
        prefix.append({"role": "user", "content": question})
        return prefix + [{"role": "assistant", "content": answer}], tools
    if mode == "plan":
        question = (
            f"Give a concise EDA plan for {topic.focus}. Include profile checks, "
            "a suitable comparison, and the limit on what can be concluded. "
            "Do not report fixture results."
        )
        answer = (
            f"1. Confirm the {scenario.grain} and validate "
            f"`{topic.dimension}` and `{topic.measure}` types, missingness, and "
            "key behavior. 2. Profile counts and distributions before comparing "
            f"{topic.focus}. 3. Report the denominator, time coverage, and sample "
            f"size; {topic.pitfall}. This is a plan only; no analysis has run."
        )
        prefix.append({"role": "user", "content": question})
        return prefix + [{"role": "assistant", "content": answer}], tools
    if mode == "tool":
        tools, tool_messages, answer = _tool_example(
            scenario, topic, scenario_index, topic_index
        )
        prefix.append(
            {
                "role": "user",
                "content": (
                    f"Use the registered tool that best addresses {topic.focus}. "
                    "Use the dataset and file identifiers I supplied, and do not "
                    "claim to have accessed live data."
                ),
            }
        )
        return prefix + tool_messages + [{"role": "assistant", "content": answer}], tools
    if mode == "results":
        summary = metric_summary(scenario)
        question = (
            "Interpret this computed summary from the synthetic rows, and state "
            f"one important limitation for {topic.focus}: "
            f"{json.dumps(summary, sort_keys=True)}"
        )
        answer = (
            f"For {topic.focus}, the deterministic synthetic fixture calculation "
            f"reports {json.dumps(summary, sort_keys=True)} from "
            f"{len(scenario.rows)} rows at grain {scenario.grain}. "
            f"Definitions: {json.dumps(scenario.definitions, sort_keys=True)}. "
            f"Limitations: {topic.pitfall} These results describe only this small "
            "fixture; they are not production evidence and do not establish causality."
        )
        prefix.append({"role": "user", "content": question})
        return prefix + [{"role": "assistant", "content": answer}], tools
    if mode == "code":
        answer, _ = _code_example(scenario, topic, topic_index)
        question = (
            f"Provide a concise, executable example in "
            f"{'SQL' if topic_index % 3 == 0 else 'Python' if topic_index % 3 == 1 else 'DAX'} "
            f"that calculates a relevant business measure for {topic.focus}. Use "
            "only fields shown above, guard invalid denominators, and do not claim execution."
        )
        prefix.append({"role": "user", "content": question})
        return prefix + [{"role": "assistant", "content": answer}], tools
    raise ValueError(f"Unknown example mode: {mode}")


def build_examples() -> list[dict[str, Any]]:
    modes = ("schema", "plan", "tool", "results", "code")
    examples: list[dict[str, Any]] = []
    for scenario_index, scenario in enumerate(SCENARIOS):
        if len(scenario.topics) != 10:
            raise ValueError(f"{scenario.key} must define exactly ten topic groups")
        for topic_index, topic in enumerate(scenario.topics):
            split = (
                "train"
                if topic_index < 8
                else "validation"
                if topic_index == 8
                else "test"
            )
            group = f"eda-{scenario.key}-{topic.key}"
            for mode in modes:
                messages, tools = _example_messages(
                    scenario, topic, scenario_index, topic_index, mode
                )
                example_id = f"eda-{scenario.key}-{topic.key}-{mode}"
                examples.append(
                    {
                        "id": example_id,
                        "scenario_group": group,
                        "category": {
                            "schema": "schema_interpretation",
                            "plan": "business_reasoning",
                            "tool": "tool_selection",
                            "results": "business_reasoning",
                            "code": "structured_output",
                        }[mode],
                        "split": split,
                        "messages": messages,
                        "tools": tools,
                        "source": {"kind": "synthetic", "reference": SOURCE_REFERENCE},
                        "license": LICENSE,
                        "validation_status": "draft",
                        "reviewer_notes": (
                            "Draft only; synthetic fixture; requires domain-expert "
                            "human review before approval or training."
                        ),
                    }
                )
    return examples


def fixture_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for scenario in SCENARIOS:
        payload[scenario.key] = {
            "title": scenario.title,
            "table": scenario.table,
            "grain": scenario.grain,
            "definitions": scenario.definitions,
            "columns": scenario.columns,
            "rows": list(scenario.rows),
            "profile": profile_for(scenario),
            "expected_summary": metric_summary(scenario),
        }
    return payload


def build_sqlite_fixture(scenario_key: str) -> sqlite3.Connection:
    scenario = _SCENARIO_BY_KEY[scenario_key]
    connection = sqlite3.connect(":memory:")
    column_definitions = ", ".join(
        f'"{column}" {"TEXT" if all(isinstance(row[column], str) or row[column] is None for row in scenario.rows) else "REAL"}'
        for column in scenario.columns
    )
    connection.execute(
        f'CREATE TABLE "{scenario.table}" ({column_definitions})'
    )
    columns = list(scenario.columns)
    placeholders = ", ".join("?" for _ in columns)
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    connection.executemany(
        f'INSERT INTO "{scenario.table}" ({quoted_columns}) VALUES ({placeholders})',
        [[row.get(column) for column in columns] for row in scenario.rows],
    )
    return connection


def execute_readonly_sql(scenario_key: str, query: str) -> list[tuple[Any, ...]]:
    if not re.match(r"^\s*SELECT\b", query, flags=re.IGNORECASE):
        raise ValueError("Only a single read-only SELECT query is permitted.")
    if ";" in query.rstrip().removesuffix(";"):
        raise ValueError("Only one SQL statement is permitted.")
    connection = build_sqlite_fixture(scenario_key)
    try:
        connection.execute("PRAGMA query_only = ON")
        return connection.execute(query).fetchall()
    finally:
        connection.close()


def validate_sql_examples(examples: list[dict[str, Any]]) -> int:
    topic_by_group = {
        f"eda-{scenario.key}-{topic.key}": (scenario, topic, topic_index)
        for scenario in SCENARIOS
        for topic_index, topic in enumerate(scenario.topics)
    }
    validated = 0
    for example in examples:
        if example["category"] != "structured_output":
            continue
        answer = example["messages"][-1]["content"]
        match = re.search(r"```sql\n(.*?)\n```", answer, flags=re.DOTALL)
        code = re.search(r"```python\n(.*?)```", answer, flags=re.DOTALL)
        if match:
            scenario, _, _ = topic_by_group[example["scenario_group"]]
            execute_readonly_sql(scenario.key, match.group(1))
            validated += 1
        elif code:
            compile(code.group(1), f"{example['id']}.py", "exec")
            validated += 1
        elif "```dax" in answer:
            scenario, topic, _ = topic_by_group[example["scenario_group"]]
            required_by_scenario = {
                "marketing": (
                    "[spend]",
                    "[qualified_leads]",
                    "[attributed_net_revenue]",
                    "DIVIDE",
                ),
                "maintenance": (
                    "[sensor_complete]",
                    "[failure_within_30d]",
                    "COUNTROWS",
                ),
                "ecommerce": (
                    "[quantity]",
                    "[unit_price]",
                    "[allocated_discount]",
                    "[refund_amount]",
                    "[unit_cost]",
                    "DIVIDE",
                ),
                "fraud": (
                    "[flagged]",
                    "[confirmed_fraud]",
                    "[review_status]",
                    "DIVIDE",
                ),
                "healthcare": (
                    "[eligible_discharge]",
                    "[followup_complete]",
                    "[planned_readmission]",
                    "[unplanned_readmission_30d]",
                    "DIVIDE",
                ),
            }
            required = required_by_scenario[scenario.key]
            if (
                f"'{scenario.dax_table}'" not in answer
                or not all(token in answer for token in required)
            ):
                raise ValueError(f"{example['id']} references an unexpected DAX field")
            validated += 1
    return validated


def corpus_counts(examples: list[dict[str, Any]]) -> dict[str, Any]:
    split_counts = Counter(example["split"] for example in examples)
    group_splits: dict[str, set[str]] = {}
    scenario_counts = Counter(example["id"].split("-")[1] for example in examples)
    for example in examples:
        group_splits.setdefault(example["scenario_group"], set()).add(example["split"])
    return {
        "examples": len(examples),
        "split_counts": dict(sorted(split_counts.items())),
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "scenario_groups": len(group_splits),
        "leaking_groups": sorted(
            group for group, splits in group_splits.items() if len(splits) > 1
        ),
        "draft_examples": sum(
            example["validation_status"] == "draft" for example in examples
        ),
        "approved_examples": sum(
            example["validation_status"] == "approved" for example in examples
        ),
    }
