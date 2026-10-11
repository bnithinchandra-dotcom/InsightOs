import copy
import math
import re


QUALITY_RULE_CATALOG_VERSION = "1"
QUALITY_RULE_CATEGORIES = frozenset(
    {
        "BA",
        "DA",
        "statistical",
        "temporal",
        "relational",
        "governance",
    }
)
QUALITY_RULE_STATUSES = frozenset(
    {
        "implemented",
        "partial",
        "planned",
        "unsupported",
    }
)
_RULE_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_VERSION = re.compile(r"^\d+(?:\.\d+){0,2}$")
_ENTRY_KEYS = {
    "rule_id",
    "version",
    "name",
    "description",
    "category",
    "purpose",
    "use_cases",
    "scope",
    "severity",
    "required_inputs",
    "prerequisites",
    "configuration_schema",
    "default_configuration",
    "method",
    "supported_operations",
    "planned_operations",
    "required_approvals",
    "risks",
    "limitations",
    "status",
    "default_enabled",
    "implementation_reference",
}
_OPERATIONS = frozenset(
    {"detection", "recommendation", "transformation", "validation"}
)


def _object_schema(properties=None, required=()):
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


def _entry(
    rule_id,
    name,
    description,
    category,
    purpose,
    use_cases,
    required_inputs,
    prerequisites,
    method,
    supported_operations,
    risks,
    limitations,
    *,
    status="planned",
    configuration_schema=None,
    default_configuration=None,
    required_approvals=(),
    default_enabled=False,
    test_references=(),
    scope="column_or_table",
    severity="info",
):
    operations = list(supported_operations)
    implementation_reference = None
    if status == "implemented":
        implementation_reference = {
            "executor_id": rule_id,
            "source": "backend/app/quality_rules.py",
            "tests": list(test_references),
        }
    return {
        "rule_id": rule_id,
        "version": "1",
        "name": name,
        "description": description,
        "category": category,
        "purpose": purpose,
        "use_cases": list(use_cases),
        "scope": scope,
        "severity": severity,
        "required_inputs": list(required_inputs),
        "prerequisites": list(prerequisites),
        "configuration_schema": copy.deepcopy(configuration_schema),
        "default_configuration": copy.deepcopy(default_configuration),
        "method": method,
        "supported_operations": (
            operations if status == "implemented" else []
        ),
        "planned_operations": (
            [] if status in {"implemented", "unsupported"} else operations
        ),
        "required_approvals": list(required_approvals),
        "risks": list(risks),
        "limitations": list(limitations),
        "status": status,
        "default_enabled": default_enabled,
        "implementation_reference": implementation_reference,
    }


_EMPTY_CONFIGURATION = _object_schema()
_RULE_CATALOG = (
    _entry(
        "configured.business_key_duplicate_candidates",
        "Configured business-key duplicate candidates",
        "Finds repeated values for explicitly configured business keys.",
        "relational",
        "Surface possible repeated entities or transactions without assuming uniqueness.",
        ["Investigating possible key collisions"],
        ["verified normalized Parquet", "explicit table and key-column definition"],
        ["verified_normalized_parquet", "explicit_business_key_definition"],
        "Exact typed comparison of configured key columns; rows with null key values are excluded.",
        ["detection"],
        ["A repeated key can be legitimate unless the business definition requires uniqueness."],
        ["Business-key definitions are never inferred and the rule is disabled by default."],
        status="implemented",
        configuration_schema=_object_schema(
            {
                "keys": {
                    "type": "array",
                    "maxItems": 255,
                    "items": _object_schema(
                        {
                            "table_index": {"type": "integer", "minimum": 0},
                            "columns": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 64,
                                "uniqueItems": True,
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                        ("table_index", "columns"),
                    ),
                }
            },
            ("keys",),
        ),
        default_configuration={"keys": []},
        required_approvals=["explicit_business_key_definition"],
        scope="table",
        severity="low",
        test_references=[
            "backend/tests/test_quality_rules.py::test_business_key_duplicates_require_an_explicit_key_and_ignore_missing_keys"
        ],
    ),
    _entry(
        "da.categorical_encoding",
        "Categorical encoding",
        "Encode categorical values for downstream analytical models.",
        "DA",
        "Prepare categorical columns for model inputs while preserving a traceable mapping.",
        ["Preparing categorical features"],
        ["selected categorical columns", "encoding strategy"],
        ["explicit column selection", "approved encoding strategy"],
        "No encoding algorithm is available in this release.",
        ["transformation"],
        ["Encoding can change interpretation and may increase dimensionality."],
        ["No encoding is executed; mappings and transformation lineage are not implemented."],
        required_approvals=["approved_encoding_strategy"],
    ),
    _entry(
        "da.logical_constraints",
        "Logical constraint validation",
        "Evaluate explicitly configured logical constraints over dataset values.",
        "DA",
        "Find records that violate user-defined structural or logical conditions.",
        ["Checking configured value constraints"],
        ["normalized dataset", "explicit constraint definitions"],
        ["validated constraint definitions"],
        "No configurable logical-constraint evaluator is registered.",
        ["validation"],
        ["Incorrect constraints can label valid records as invalid."],
        ["Constraints are not inferred and are not executed."],
        required_approvals=["approved_constraint_definitions"],
    ),
    _entry(
        "da.numeric_scaling",
        "Numeric scaling",
        "Transform selected numeric features using an explicitly selected scaling method.",
        "DA",
        "Prepare numeric features for methods sensitive to variable scales.",
        ["Preparing model features"],
        ["selected numeric columns", "scaling method"],
        ["explicit column selection", "approved scaling parameters"],
        "No scaling transformation is available in this release.",
        ["transformation"],
        ["Scaling changes value interpretation and can be sensitive to extreme values."],
        ["No values are transformed and no scaling parameters are persisted."],
        required_approvals=["approved_scaling_parameters"],
    ),
    _entry(
        "da.schema_drift",
        "Schema drift comparison",
        "Compare a dataset schema with an explicitly selected baseline schema.",
        "DA",
        "Identify changes between dataset versions or expected schemas.",
        ["Reviewing schema changes across versions"],
        ["normalized schema", "explicit baseline schema"],
        ["validated baseline schema"],
        "Partial foundation only: current type-consistency metadata checks do not compare against a baseline.",
        ["detection", "validation"],
        ["A schema difference is not necessarily a breaking change."],
        ["Cross-version baseline comparison is not implemented; use type-consistency findings for current-file checks."],
        status="partial",
    ),
    _entry(
        "da.skewness_transformation",
        "Skewness transformation",
        "Apply an explicitly selected transformation to skewed numeric data.",
        "DA",
        "Prepare suitable numeric features for analysis or modeling.",
        ["Transforming selected numeric features"],
        ["selected numeric columns", "approved transformation method"],
        ["explicit column selection", "approved transformation parameters"],
        "No skewness transformation is available in this release.",
        ["transformation"],
        ["Transformations can alter scale and interpretation and may not suit non-positive values."],
        ["No skewness analysis or transformation is executed by this catalog entry."],
        required_approvals=["approved_transformation_parameters"],
    ),
    _entry(
        "da.string_normalization",
        "String normalization",
        "Normalize selected strings using an explicitly chosen formatting policy.",
        "DA",
        "Prepare strings for matching or downstream categorical processing.",
        ["Reviewing whitespace and formatting candidates"],
        ["selected string columns", "normalization policy"],
        ["explicit column selection", "approved normalization policy"],
        "Whitespace candidate detection exists; no value-normalization transformation is registered.",
        ["detection", "transformation"],
        ["Normalization may collapse distinct source values or identifiers."],
        ["Whitespace candidates are reported, but source values are never rewritten."],
        required_approvals=["approved_normalization_policy"],
    ),
    _entry(
        "generic.exact_duplicate_rows",
        "Exact duplicate rows",
        "Report repeated complete rows from a bounded normalized-row scan.",
        "DA",
        "Identify exact duplicate candidates for investigation.",
        ["Reviewing repeated complete records"],
        ["validated exact duplicate profile"],
        ["validated_exact_duplicate_profile"],
        "Scans normalized rows and compares deterministic typed row fingerprints.",
        ["detection"],
        ["Repeated complete rows may represent legitimate repeated events."],
        ["No rows are removed, merged, or rewritten."],
        status="implemented",
        configuration_schema=_EMPTY_CONFIGURATION,
        default_configuration={},
        default_enabled=True,
        scope="table",
        test_references=[
            "backend/tests/test_quality_rules.py::test_exact_duplicates_use_the_bounded_normalized_row_scan"
        ],
    ),
    _entry(
        "generic.missing_value_candidates",
        "Missing-value candidates",
        "Count nulls and exact empty strings using validated normalization metadata.",
        "DA",
        "Describe missingness without judging whether a value is invalid.",
        ["Reviewing column completeness"],
        ["validated normalization metadata", "per-column null and empty counts"],
        ["validated_normalization_metadata", "per_column_null_and_empty_counts"],
        "Counts configured null and empty-string values and reports row denominators.",
        ["detection"],
        ["Null and empty values can have different meanings and may be acceptable."],
        ["Does not classify MCAR/MAR/MNAR and does not impute or rewrite values."],
        status="implemented",
        configuration_schema=_object_schema(
            {
                "include_empty_strings": {"type": "boolean"},
                "include_null_values": {"type": "boolean"},
            },
            ("include_empty_strings", "include_null_values"),
        ),
        default_configuration={
            "include_empty_strings": True,
            "include_null_values": True,
        },
        default_enabled=True,
        scope="column",
        test_references=[
            "backend/tests/test_quality_rules.py::test_default_rule_reports_missing_values_as_candidates"
        ],
    ),
    _entry(
        "generic.numeric_outlier_candidates",
        "Numeric outlier candidates",
        "Apply configurable IQR or population Z-score bounds to finite numeric values.",
        "statistical",
        "Surface statistically unusual numeric observations for contextual review.",
        ["Investigating numeric observations without declaring errors"],
        ["verified normalized Parquet", "normalized numeric columns"],
        ["verified_normalized_parquet", "normalized_numeric_columns"],
        "Exact IQR quartiles with linear interpolation or deterministic population Z-score.",
        ["detection"],
        ["Statistical thresholds can flag valid rare or extreme observations."],
        ["Candidates are advisory; negative values are not inherently invalid and no records are changed."],
        status="implemented",
        configuration_schema=_object_schema(
            {
                "method": {"type": "string", "enum": ["iqr", "z_score"]},
                "threshold": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 100,
                },
                "min_samples": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 1_000_000,
                },
            },
            ("method", "threshold", "min_samples"),
        ),
        default_configuration={
            "method": "iqr",
            "threshold": 1.5,
            "min_samples": 4,
        },
        default_enabled=True,
        scope="column",
        test_references=[
            "backend/tests/test_quality_rules.py::test_iqr_outlier_candidates_are_deterministic_and_advisory",
            "backend/tests/test_quality_rules.py::test_z_score_is_explicit_and_small_or_constant_samples_are_not_flagged",
        ],
    ),
    _entry(
        "generic.numeric_validity_candidates",
        "Numeric validity candidates",
        "Find non-finite values or values structurally inconsistent with normalized numeric columns.",
        "DA",
        "Surface structural numeric issues without imposing business-specific value ranges.",
        ["Checking normalized numeric representation"],
        ["verified normalized Parquet", "normalized numeric columns"],
        ["verified_normalized_parquet", "normalized_numeric_columns"],
        "Checks numeric value structure and finite-value validity.",
        ["detection", "validation"],
        ["Structural invalidity does not determine whether a value is business-valid."],
        ["Negative values and statistical outliers are not treated as errors by this rule."],
        status="implemented",
        configuration_schema=_EMPTY_CONFIGURATION,
        default_configuration={},
        default_enabled=True,
        scope="column",
        severity="medium",
        test_references=[
            "backend/tests/test_quality_rules.py::test_rules_report_deterministic_type_string_and_numeric_candidates"
        ],
    ),
    _entry(
        "generic.string_whitespace_candidates",
        "String whitespace candidates",
        "Identify leading, trailing, or repeated whitespace in normalized string values.",
        "DA",
        "Surface possible string-format inconsistency without changing values.",
        ["Reviewing string formatting"],
        ["verified normalized Parquet", "normalized string columns"],
        ["verified_normalized_parquet", "normalized_string_columns"],
        "Inspects string boundaries and repeated whitespace characters.",
        ["detection"],
        ["Whitespace can be significant in identifiers or free text."],
        ["Does not normalize or rewrite source values."],
        status="implemented",
        configuration_schema=_EMPTY_CONFIGURATION,
        default_configuration={},
        default_enabled=True,
        scope="column",
        test_references=[
            "backend/tests/test_quality_rules.py::test_rules_report_deterministic_type_string_and_numeric_candidates"
        ],
    ),
    _entry(
        "generic.type_consistency",
        "Type consistency",
        "Compare validated source physical-type metadata with normalized column types.",
        "DA",
        "Surface mixed or incompatible type representations for review.",
        ["Reviewing type conflicts during normalization"],
        ["validated source type profile", "normalized column schema"],
        ["validated_source_type_profile", "normalized_column_schema"],
        "Compares source physical-type profile metadata to the normalized schema.",
        ["detection", "validation"],
        ["A type conflict can be intentional and does not establish a bad individual value."],
        ["Metadata-backed; it does not report row-level cast failures."],
        status="implemented",
        configuration_schema=_EMPTY_CONFIGURATION,
        default_configuration={},
        default_enabled=True,
        scope="column",
        severity="low",
        test_references=[
            "backend/tests/test_quality_rules.py::test_type_conflicts_are_reported_against_normalized_string_types"
        ],
    ),
    _entry(
        "ba.business_anomaly_detection",
        "Business anomaly detection",
        "Evaluate anomalies against explicit business definitions and contextual baselines.",
        "BA",
        "Distinguish unusual business observations from confirmed violations.",
        ["Reviewing configured business KPI or process anomalies"],
        ["approved business definitions", "business context and comparison baseline"],
        ["explicit business anomaly definition", "approved context and baseline"],
        "No business-semantic anomaly executor is registered.",
        ["detection", "recommendation"],
        ["Anomalies may be valid events; statistical unusualness alone is not an error."],
        ["No business semantics or anomaly recommendations are inferred or executed."],
        required_approvals=["approved_business_anomaly_definition"],
    ),
    _entry(
        "ba.business_definitions",
        "Business definitions",
        "Store explicit definitions for business entities, terms, and measures.",
        "BA",
        "Make domain meanings explicit before applying consequential business rules.",
        ["Defining measures and domain terms"],
        ["user-authored business definitions"],
        ["approved business definition"],
        "No business-definition store or evaluator is registered.",
        ["validation"],
        ["Ambiguous definitions can produce misleading downstream measures."],
        ["The catalog does not infer definitions from column names or values."],
        required_approvals=["approved_business_definition"],
    ),
    _entry(
        "ba.business_gap_detection",
        "Business gap detection",
        "Find missing expected business events or periods using explicit expectations.",
        "BA",
        "Identify gaps in a business process or reporting sequence.",
        ["Checking expected periods, events, or entity coverage"],
        ["approved expected-event or coverage definition"],
        ["explicit business gap definition"],
        "No business-gap detector is registered.",
        ["detection"],
        ["An apparent gap may reflect a valid inactive period or incomplete source coverage."],
        ["Expected events and periods are not inferred."],
        required_approvals=["approved_business_gap_definition"],
    ),
    _entry(
        "ba.category_grouping",
        "Business category grouping",
        "Map source categories into user-defined business groupings.",
        "BA",
        "Apply transparent, versioned category groupings for reporting.",
        ["Grouping territories, statuses, products, or channels"],
        ["explicit category mapping"],
        ["approved category mapping"],
        "No category grouping executor is registered.",
        ["transformation", "validation"],
        ["Grouping may merge categories that have distinct business meanings."],
        ["Mappings are not inferred and source categories are not rewritten."],
        required_approvals=["approved_category_mapping"],
    ),
    _entry(
        "ba.contextual_missingness",
        "Contextual missingness",
        "Evaluate missing values against explicitly configured business context.",
        "BA",
        "Separate expected missingness from gaps that matter to a defined process.",
        ["Reviewing conditional or process-specific missingness"],
        ["approved context rules", "normalized dataset"],
        ["explicit contextual missingness definitions"],
        "No contextual missingness evaluator is registered.",
        ["detection", "validation"],
        ["A value may be missing by design or due to collection and process conditions."],
        ["No MCAR/MAR/MNAR classification or business interpretation is performed."],
        required_approvals=["approved_contextual_missingness_definition"],
    ),
    _entry(
        "ba.grain_reconciliation",
        "Reporting-grain reconciliation",
        "Check aggregation and reconciliation across explicitly defined reporting grains.",
        "BA",
        "Validate totals and measures after approved aggregation.",
        ["Reconciling transaction, daily, monthly, or entity-level results"],
        ["approved reporting grains", "measure aggregation definitions"],
        ["explicit grain and aggregation definitions"],
        "No grain reconciliation executor is registered.",
        ["validation"],
        ["Invalid grain assumptions can double-count or omit records."],
        ["Aggregation and reconciliation are not executed."],
        required_approvals=["approved_reporting_grain_and_aggregation"],
    ),
    _entry(
        "ba.kpi_mapping",
        "KPI and status mapping",
        "Map source fields to explicitly defined KPIs, statuses, and business measures.",
        "BA",
        "Support consistent interpretation of business metrics and status values.",
        ["Defining measures and status mappings"],
        ["approved KPI definitions", "source field mapping"],
        ["explicit KPI and status definitions"],
        "No KPI or status mapping executor is registered.",
        ["validation"],
        ["Incorrect mappings can change reported business meaning."],
        ["KPI definitions are not inferred or applied."],
        required_approvals=["approved_kpi_and_status_definitions"],
    ),
    _entry(
        "ba.master_dimensions",
        "Master dimensions and territory mappings",
        "Validate or map values against explicitly supplied master dimensions.",
        "BA",
        "Support consistent entity and territory groupings across datasets.",
        ["Checking territory, product, organization, or customer dimensions"],
        ["approved master dimension data", "explicit mapping rules"],
        ["explicit master dimension source and mapping"],
        "No master-dimension or territory-mapping executor is registered.",
        ["detection", "validation", "transformation"],
        ["Mappings and joins can change grain or lose unmatched records."],
        ["No dimension join or mapping is performed."],
        required_approvals=["approved_dimension_source_and_mapping"],
    ),
    _entry(
        "ba.reporting_grain",
        "Reporting grain definition",
        "Define the entity and time grain used for a report or measure.",
        "BA",
        "Make the unit of analysis explicit before aggregating or comparing measures.",
        ["Defining row meaning and reporting periods"],
        ["user-approved grain definition"],
        ["explicit reporting grain"],
        "No reporting-grain model or validator is registered.",
        ["validation"],
        ["An incorrect grain can lead to invalid comparisons or double counting."],
        ["Grain is not inferred from table or column names."],
        required_approvals=["approved_reporting_grain"],
    ),
    _entry(
        "ba.scope_filters",
        "Business-scope filters",
        "Apply or validate user-approved filters defining the business population.",
        "BA",
        "Make consequential inclusion and exclusion criteria explicit.",
        ["Selecting an approved reporting population"],
        ["explicit business-scope filter"],
        ["approved filter definition"],
        "No business-scope filter planner or transformation is registered.",
        ["validation", "transformation"],
        ["Filtering can remove legitimate records and alter denominators."],
        ["No filters are inferred or applied."],
        required_approvals=["approved_business_scope_filter"],
    ),
    _entry(
        "governance.imputation",
        "Missing-value imputation",
        "Impute missing values only through a separately approved transformation workflow.",
        "governance",
        "Make any future imputation strategy explicit, auditable, and separate from detection.",
        ["Preparing an approved transformation"],
        ["selected columns", "approved imputation strategy"],
        ["explicit strategy and parameters", "transformation lineage"],
        "No imputation transformation is registered.",
        ["transformation"],
        ["Imputation changes data and can bias analysis if its assumptions are invalid."],
        ["The quality engine only reports missingness; it never imputes."],
        required_approvals=["approved_imputation_strategy"],
    ),
    _entry(
        "relational.referential_integrity",
        "Referential integrity",
        "Check explicitly configured references between related datasets.",
        "relational",
        "Surface unmatched references without silently removing or joining records.",
        ["Checking foreign-key-like relationships"],
        ["related normalized datasets", "explicit key relationships"],
        ["approved relationship definition"],
        "No referential-integrity checker or repair is registered.",
        ["detection", "validation"],
        ["Unmatched values may be valid or reflect incomplete source coverage."],
        ["Relationships are not inferred; no join or repair is performed."],
        required_approvals=["approved_relationship_definition"],
    ),
    _entry(
        "temporal.fiscal_calendar",
        "Fiscal calendar mapping",
        "Map dates to an explicitly configured fiscal calendar.",
        "temporal",
        "Support fiscal period reporting without assuming calendar-year boundaries.",
        ["Mapping dates to fiscal periods"],
        ["date columns", "approved fiscal calendar"],
        ["explicit fiscal calendar definition"],
        "No fiscal calendar mapping is registered.",
        ["validation", "transformation"],
        ["Wrong period boundaries can shift or misstate reported measures."],
        ["Fiscal calendars are not inferred and dates are not remapped."],
        required_approvals=["approved_fiscal_calendar"],
    ),
    _entry(
        "temporal.timezone_handling",
        "Timezone handling",
        "Interpret or convert timestamps using an explicitly selected timezone policy.",
        "temporal",
        "Support consistent time-based analysis across timezone-aware sources.",
        ["Reviewing timestamps across regions or daylight-saving changes"],
        ["timestamp columns", "approved timezone policy"],
        ["explicit timezone and ambiguity policy"],
        "No timezone conversion or ambiguity resolver is registered.",
        ["validation", "transformation"],
        ["Timezone assumptions can shift event dates and reporting periods."],
        ["Timestamps are not converted or assigned an inferred timezone."],
        required_approvals=["approved_timezone_policy"],
    ),
)
_RULE_CATALOG = tuple(sorted(_RULE_CATALOG, key=lambda entry: entry["rule_id"]))


def _validate_catalog():
    seen_ids = set()
    for entry in _RULE_CATALOG:
        if (
            set(entry) != _ENTRY_KEYS
            or not isinstance(entry["rule_id"], str)
            or _RULE_ID.fullmatch(entry["rule_id"]) is None
            or entry["rule_id"] in seen_ids
            or not isinstance(entry["version"], str)
            or _VERSION.fullmatch(entry["version"]) is None
            or not isinstance(entry["name"], str)
            or not entry["name"].strip()
            or not isinstance(entry["description"], str)
            or not entry["description"].strip()
            or not isinstance(entry["purpose"], str)
            or not entry["purpose"].strip()
            or not isinstance(entry["method"], str)
            or not entry["method"].strip()
            or entry["scope"] not in {"column", "table", "column_or_table"}
            or entry["severity"] not in {"info", "low", "medium", "high", "critical"}
            or entry["category"] not in QUALITY_RULE_CATEGORIES
            or entry["status"] not in QUALITY_RULE_STATUSES
            or not isinstance(entry["default_enabled"], bool)
            or not isinstance(entry["required_inputs"], list)
            or not isinstance(entry["prerequisites"], list)
            or not isinstance(entry["use_cases"], list)
            or not isinstance(entry["required_approvals"], list)
            or not isinstance(entry["risks"], list)
            or not isinstance(entry["limitations"], list)
            or any(
                not isinstance(text, str) or not text.strip()
                for field in (
                    "use_cases",
                    "required_inputs",
                    "prerequisites",
                    "required_approvals",
                    "risks",
                    "limitations",
                )
                for text in entry[field]
            )
            or not isinstance(entry["supported_operations"], list)
            or not isinstance(entry["planned_operations"], list)
            or any(
                operation not in _OPERATIONS
                for operation in (
                    entry["supported_operations"] + entry["planned_operations"]
                )
            )
            or entry["status"] == "implemented"
            and (
                not entry["supported_operations"]
                or entry["planned_operations"]
            )
            or entry["status"] != "implemented"
            and (
                entry["supported_operations"]
                or (
                    entry["status"] in {"partial", "planned"}
                    and not entry["planned_operations"]
                )
                or (
                    entry["status"] == "unsupported"
                    and entry["planned_operations"]
                )
            )
        ):
            raise ValueError("The quality-rule catalog contains an invalid entry.")
        implementation = entry["implementation_reference"]
        if entry["status"] == "implemented":
            if (
                not isinstance(implementation, dict)
                or implementation.get("executor_id") != entry["rule_id"]
                or not isinstance(implementation.get("source"), str)
                or not implementation["source"].strip()
                or not isinstance(implementation.get("tests"), list)
                or not implementation["tests"]
                or any(
                    not isinstance(reference, str) or not reference.strip()
                    for reference in implementation["tests"]
                )
                or entry["configuration_schema"] is None
                or entry["default_configuration"] is None
                or not _matches_configuration_schema(
                    entry["default_configuration"],
                    entry["configuration_schema"],
                )
            ):
                raise ValueError(
                    "Implemented catalog entries need an executor, tests, and configuration."
                )
        elif implementation is not None or entry["default_enabled"]:
            raise ValueError(
                "Non-implemented catalog entries cannot be executable or enabled."
            )
        seen_ids.add(entry["rule_id"])
    if tuple(entry["rule_id"] for entry in _RULE_CATALOG) != tuple(
        sorted(seen_ids)
    ):
        raise ValueError("Quality-rule catalog entries must be sorted by identifier.")


def _matches_configuration_schema(value, schema):
    value_type = schema.get("type")
    if value_type == "object":
        if not isinstance(value, dict):
            return False
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if any(name not in value for name in required):
            return False
        if not schema.get("additionalProperties", True) and set(value) - set(properties):
            return False
        return all(
            _matches_configuration_schema(value[name], properties[name])
            for name in value
            if name in properties
        )
    if value_type == "array":
        if not isinstance(value, list):
            return False
        if len(value) < schema.get("minItems", 0):
            return False
        if len(value) > schema.get("maxItems", float("inf")):
            return False
        if schema.get("uniqueItems") and any(
            value[index] == value[prior]
            for index in range(len(value))
            for prior in range(index)
        ):
            return False
        return all(
            _matches_configuration_schema(item, schema["items"])
            for item in value
        )
    if value_type == "string":
        if not isinstance(value, str):
            return False
        if len(value) < schema.get("minLength", 0):
            return False
    elif value_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif value_type == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or isinstance(value, float) and not math.isfinite(value)
        ):
            return False
    elif value_type == "boolean":
        return isinstance(value, bool)
    else:
        return False

    if "enum" in schema and value not in schema["enum"]:
        return False
    if "minimum" in schema and value < schema["minimum"]:
        return False
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        return False
    if "maximum" in schema and value > schema["maximum"]:
        return False
    return True


_validate_catalog()
_RULES_BY_ID = {entry["rule_id"]: entry for entry in _RULE_CATALOG}


def validate_quality_rule_configuration(rule_id: str, configuration: dict) -> None:
    entry = _RULES_BY_ID.get(rule_id)
    if entry is None:
        return
    schema = entry["configuration_schema"]
    if (
        entry["status"] != "implemented"
        or schema is None
        or not _matches_configuration_schema(configuration, schema)
    ):
        raise ValueError("Configuration does not match the catalog schema.")


def list_quality_rule_catalog(
    *,
    category: str | None = None,
    status: str | None = None,
) -> list[dict]:
    if category is not None and category not in QUALITY_RULE_CATEGORIES:
        raise ValueError("Unknown quality-rule category.")
    if status is not None and status not in QUALITY_RULE_STATUSES:
        raise ValueError("Unknown quality-rule implementation status.")
    return [
        copy.deepcopy(entry)
        for entry in _RULE_CATALOG
        if (category is None or entry["category"] == category)
        and (status is None or entry["status"] == status)
    ]


def get_quality_rule_catalog_entry(rule_id: str) -> dict | None:
    entry = _RULES_BY_ID.get(rule_id)
    return copy.deepcopy(entry) if entry is not None else None


def quality_rule_catalog_entries() -> list[dict]:
    return copy.deepcopy(list(_RULE_CATALOG))
