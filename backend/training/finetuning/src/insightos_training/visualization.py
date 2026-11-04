from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
VISUALIZATION_DIR = ROOT / "visualization"


def load_json(name: str | Path) -> dict[str, Any]:
    path = Path(name)
    if not path.is_absolute():
        path = VISUALIZATION_DIR / path
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry() -> dict[str, Any]:
    return load_json("registry.json")


def load_output_schema() -> dict[str, Any]:
    return load_json("output_schema.json")


def load_decision_rules() -> dict[str, Any]:
    return load_json("decision_rules.json")


def validate_registry(registry: dict[str, Any]) -> dict[str, Any]:
    visualizations = registry.get("visualizations", [])
    ids = [item["id"] for item in visualizations]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate visualization identifier")
    if "table" not in ids:
        raise ValueError("Registry must include table fallback")
    required_metadata = {
        "category",
        "display_name",
        "analytical_purposes",
        "encodings",
        "accepted_types",
        "configuration_options",
        "cardinality",
        "limitations",
        "inappropriate_uses",
        "fallback",
        "accessibility",
    }
    entries = {item["id"]: item for item in visualizations}
    categories = set()
    for item in visualizations:
        missing = sorted(required_metadata - item.keys())
        if missing:
            raise ValueError(f"Registry visualization {item.get('id')} missing fields: {', '.join(missing)}")
        categories.add(item["category"])
        encodings = item["encodings"]
        if not isinstance(encodings.get("required"), list) or not isinstance(encodings.get("optional"), list):
            raise ValueError(f"Invalid encodings for visualization {item['id']}")
        for encoding in encodings["required"] + encodings["optional"]:
            if encoding not in item["accepted_types"]:
                raise ValueError(f"Visualization {item['id']} has no accepted types for encoding {encoding}")
        if not isinstance(item["configuration_options"], dict):
            raise ValueError(f"Configuration options for {item['id']} must be an object")
        fallback = item["fallback"]
        if fallback not in entries:
            raise ValueError(f"Visualization {item['id']} has unresolved fallback: {fallback}")
    return {"valid": True, "count": len(ids), "ids": ids}


def validate_field_references(spec: dict[str, Any], available_fields: set[str]) -> dict[str, Any]:
    referenced: set[str] = set()
    mappings = spec.get("data_mappings", {})
    for value in mappings.values():
        if isinstance(value, str):
            referenced.add(value)
        elif isinstance(value, list):
            referenced.update(item for item in value if isinstance(item, str))
        elif isinstance(value, dict):
            fields = value.get("fields", [])
            if isinstance(fields, str):
                referenced.add(fields)
            elif isinstance(fields, list):
                referenced.update(fields)
    for reference in spec.get("aggregation", {}).get("references", []):
        referenced.add(reference["field"])
    for tooltip_field in spec.get("tooltip", {}).get("fields", []):
        referenced.add(tooltip_field)
    unavailable = sorted(referenced - available_fields)
    if unavailable:
        raise ValueError(f"Visualization references unavailable field(s): {', '.join(unavailable)}")
    return {"valid": True, "fields": sorted(referenced)}


def validate_visualization_spec(spec: dict[str, Any], available_fields: set[str] | None = None) -> dict[str, Any]:
    schema = load_output_schema()
    errors = sorted(Draft202012Validator(schema).iter_errors(spec), key=lambda error: list(error.path))
    if errors:
        raise ValueError(f"Visualization specification failed schema validation: {errors[0].message}")
    registry = load_registry()
    entries = {item["id"]: item for item in registry["visualizations"]}
    if spec["visualization_id"] not in entries:
        raise ValueError(f"Unsupported visualization identifier: {spec['visualization_id']}")
    entry = entries[spec["visualization_id"]]
    if available_fields is not None:
        if isinstance(available_fields, set):
            field_types = {name: None for name in available_fields}
        else:
            field_types = dict(available_fields)
        validate_field_references(spec, set(field_types))
        mappings = spec["data_mappings"]
        required = entry["encodings"]["required"]
        missing = sorted(set(required) - set(mappings))
        if missing and not spec.get("fallback", False):
            raise ValueError(f"Missing required encodings for {entry['id']}: {', '.join(missing)}")
        for encoding, value in mappings.items():
            if encoding not in entry["encodings"]["required"] + entry["encodings"]["optional"]:
                raise ValueError(f"Unsupported data encoding for {entry['id']}: {encoding}")
            mapped_names = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            if isinstance(value, dict):
                mapped_names = [value["fields"]] if isinstance(value.get("fields"), str) else value.get("fields", [])
            for field_name in mapped_names:
                actual_type = field_types.get(field_name)
                accepted = entry["accepted_types"][encoding]
                if actual_type is not None and actual_type not in accepted:
                    raise ValueError(
                        f"Field {field_name} type {actual_type} incompatible with {entry['id']} encoding {encoding}"
                    )
        configuration = spec.get("configuration", {})
        for option, value in configuration.items():
            definition = entry["configuration_options"].get(option)
            if definition is None:
                raise ValueError(f"Unsupported configuration option for {entry['id']}: {option}")
            allowed = definition.get("allowed_values")
            if allowed is not None and value not in allowed:
                raise ValueError(f"Unsupported value for {entry['id']} configuration {option}: {value}")
        cardinality = spec.get("cardinality")
        if isinstance(cardinality, dict):
            for encoding, count in cardinality.items():
                limits = entry["cardinality"].get(encoding)
                if limits:
                    if count < limits.get("min", 0) or count > limits.get("max", count):
                        raise ValueError(f"Cardinality out of bounds for {entry['id']} encoding {encoding}")
        layout = spec.get("layout", {})
        for dimension in ("width", "height"):
            value = layout.get(dimension)
            if isinstance(value, int) and value <= 0:
                raise ValueError(f"Layout {dimension} must be positive")
            if isinstance(value, str) and value not in {"auto", "100%", "responsive"}:
                raise ValueError(f"Unsupported layout {dimension}: {value}")
        axis = spec.get("axis", {})
        for axis_name, field_name in axis.items():
            if axis_name.endswith("_title"):
                continue
            synthetic_axis_values = {"frequency", "stage", "value"}
            if (
                isinstance(field_name, str)
                and field_name not in synthetic_axis_values
                and field_name not in field_types
            ):
                raise ValueError(f"Axis references unavailable field: {field_name}")
        series = spec.get("series", {})
        series_field = series.get("field")
        if series_field is not None and available_fields is not None and series_field not in field_types:
            raise ValueError(f"Series references unavailable field: {series_field}")
    validate_field_references(spec, set(field_types)) if available_fields is not None else None
    aggregation = spec.get("aggregation", {})
    for reference in aggregation.get("references", []):
        if not isinstance(reference, dict) or set(("field", "operation")) - reference.keys():
            raise ValueError("Aggregation references require field and operation")
        if available_fields is not None and reference["field"] not in field_types:
            raise ValueError(f"Aggregation references unavailable field: {reference['field']}")
        if reference["operation"] not in {"count", "sum", "avg", "min", "max", "distinct_count", "none"}:
            raise ValueError(f"Unsupported aggregation operation: {reference['operation']}")
    return {"valid": True, "visualization_id": spec["visualization_id"]}


def choose_visualization(
    business_question: str, objective: str, profile: dict[str, Any], available_fields: list[str]
) -> dict[str, Any]:
    del business_question
    if profile.get("insufficient_data") or not available_fields:
        return {"visualization_id": "table", "fallback": True, "rationale": "Insufficient usable data"}
    if objective in {"trend", "time_series"} and profile.get("time_like_fields"):
        return {"visualization_id": "line_chart", "fallback": False, "rationale": "Time ordered numeric analysis"}
    if objective in {"ranking", "comparison"}:
        if profile.get("series_count", 1) > 1 and profile.get("category_count", 0) <= 20:
            return {
                "visualization_id": "grouped_bar",
                "fallback": False,
                "rationale": "Multiple series require grouped comparison",
            }
        return {"visualization_id": "ranked_bar", "fallback": False, "rationale": "Ranked category comparison"}
    if objective == "composition":
        count = profile.get("category_count", 0)
        chart = "donut_chart" if 2 <= count <= 6 else "stacked_bar"
        return {"visualization_id": chart, "fallback": False, "rationale": "Composition by category"}
    if objective == "distribution":
        if profile.get("category_count", 0) > 1:
            return {
                "visualization_id": "box_plot",
                "fallback": False,
                "rationale": "Grouped distributions need quartile comparison",
            }
        return {
            "visualization_id": "histogram",
            "fallback": False,
            "rationale": "A single numeric measure supports distribution shape",
        }
    if objective in {"correlation", "relationship"}:
        if profile.get("bubble_size_field"):
            return {
                "visualization_id": "scatter_plot",
                "fallback": False,
                "rationale": "A third numeric measure can be encoded by point size",
            }
        return {"visualization_id": "scatter_plot", "fallback": False, "rationale": "Two numeric measures"}
    if objective in {"geography", "geospatial"}:
        return {
            "visualization_id": "map_symbol",
            "fallback": False,
            "rationale": "Valid geographic values support symbol mapping",
        }
    if objective == "hierarchy":
        return {
            "visualization_id": "treemap",
            "fallback": False,
            "rationale": "Hierarchical composition needs nested area encoding",
        }
    if objective in {"conversion", "process"}:
        return {
            "visualization_id": "funnel",
            "fallback": False,
            "rationale": "Ordered conversion stages support a funnel",
        }
    if objective == "flow":
        return {
            "visualization_id": "sankey",
            "fallback": False,
            "rationale": "Source-target measures support flow visualization",
        }
    if objective == "cross_tab":
        return {
            "visualization_id": "matrix_pivot",
            "fallback": False,
            "rationale": "Two dimensions and a measure support a pivot",
        }
    if objective == "two_dimensional_intensity":
        return {
            "visualization_id": "heatmap",
            "fallback": False,
            "rationale": "Two dimensions and intensity support a heatmap",
        }
    if objective in {"current_value", "kpi"}:
        return {
            "visualization_id": "kpi_scorecard",
            "fallback": False,
            "rationale": "One current measure needs a KPI representation",
        }
    if objective == "detail":
        return {
            "visualization_id": "table",
            "fallback": False,
            "rationale": "Record detail is rendered reliably as a table",
        }
    return {"visualization_id": "table", "fallback": True, "rationale": "Safe fallback for ambiguous context"}


def validate_dataset_splits(root: Path) -> dict[str, Any]:
    counts = {}
    ids: set[str] = set()
    template_splits: dict[str, str] = {}
    scenario_splits: dict[str, str] = {}
    for name in ("train.jsonl", "validation.jsonl", "evaluation.jsonl"):
        path = root / name
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        counts[name] = len(rows)
        for row in rows:
            example_id = row.get("id")
            if not example_id:
                raise ValueError(f"Invalid development corpus row in {name}")
            if example_id in ids:
                raise ValueError(f"Duplicate development corpus example ID: {example_id}")
            ids.add(example_id)
            template = row.get("template_group")
            scenario = row.get("scenario_group")
            if not template or not scenario or not row.get("expected", {}).get("visualization_id"):
                raise ValueError(f"Invalid development corpus row in {name}")
            spec = row["expected"].get("visualization_spec")
            if not isinstance(spec, dict):
                raise ValueError(f"Missing complete visualization specification in {name}: {example_id}")
            field_types = {field["name"]: field["type"] for field in row.get("available_fields", [])}
            validate_visualization_spec(spec, field_types)
            split = name.removesuffix(".jsonl")
            if template in template_splits and template_splits[template] != split:
                raise ValueError(f"Template leakage across splits: {template}")
            if scenario in scenario_splits and scenario_splits[scenario] != split:
                raise ValueError(f"Scenario leakage across splits: {scenario}")
            template_splits[template] = split
            scenario_splits[scenario] = split
    return {"valid": True, "counts": counts, "total": sum(counts.values())}


def validate_evaluation_cases(path: Path) -> dict[str, Any]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("Evaluation cases must be a list")
    return {"valid": True, "count": len(cases)}
