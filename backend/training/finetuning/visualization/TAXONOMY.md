# Visualization domain taxonomy

This catalog is explicitly extensible and not exhaustive. Every visualization
identifier is registered separately in `registry.json`; the model may not invent
new identifiers or fields.

## KPI and scorecards
- Questions: current value, target attainment, variance, status.
- Data: one numeric measure and optional target/date context.
- Use: executives need a single dependable current value.
- Avoid: historical trends or multi-category comparison.
- Accessibility: text alternative, units, target and trend direction.

## Comparison and ranking
- Ranked/grouped bars compare categories and series.
- Supports category plus numeric measure; appropriate for up to roughly 30
  ranked categories.
- Avoid line charts for unordered categories and avoid cluttered grouped bars.
- Use direct labels, legends, keyboard navigation and a textual ranking summary.

## Time-series and trends
- Line and area charts require ordered time fields and numeric measures.
- Prefer line charts for precision; avoid area charts when overlap hides detail.
- Label time granularity and use text alternatives for trend direction.

## Composition
- Donuts are best for few meaningful parts; stacked bars are safer for many
  categories and series comparison.
- Avoid pie/donut charts for precise ranking or many slices.
- Include values and labels, not color alone.

## Distribution
- Histograms show frequency shape; box plots summarize quartiles and outliers.
- Requires numeric measure; optional category for grouped distributions.
- Avoid hiding sample size and modality.

## Relationship
- Scatter plots require two numeric measures; heatmaps require two dimensions
  and an intensity measure.
- Avoid scatter plots for one numeric category and a third plotted measure.
- Provide trend/correlation summaries and accessible text tables.

## Geography
- Map symbols require valid geographic fields and numeric measures.
- Avoid maps when location values are ambiguous or no spatial comparison exists.
- Provide a geographic table and avoid encoding meaning by area size alone.

## Hierarchy
- Treemaps show nested composition when hierarchy and measures are valid.
- Avoid deep hierarchies with unreadable labels.
- Provide hierarchy labels and text alternatives.

## Funnel, process and flow
- Funnels require ordered stages and counts/rates.
- Sankey diagrams require source, target and measure fields.
- Avoid implying progression when stages or flows are absent.
- Provide ordered text lists and node/edge totals.

## Tables and matrices
- Tables and pivots are safe fallbacks and support detail analysis.
- Use semantic headers, sorting, pagination, units and text alternatives.
- A single KPI is better represented by a scorecard than a pivot.

## Specialized BI
- Heatmaps, pivots and specialized charts require explicit registry entries.
- Each entry defines required encodings, cardinality, limitations, fallback and
  accessibility guidance.

## Decision and selection responsibility

Chart selection is model-assisted but registry-constrained. Selection considers
the business question, analytical objective, field types, cardinality, time
granularity, series count, distribution, geography, hierarchy, and flow context.
The deterministic validator rejects unsupported identifiers, missing required
encodings, incompatible types, unknown fields, unsupported options, invalid
aggregation, and unresolved fallbacks. Rendering and calculation remain
deterministic responsibilities.
