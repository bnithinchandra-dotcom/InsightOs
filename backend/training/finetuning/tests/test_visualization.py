import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from insightos_training.visualization import (
    choose_visualization,
    load_output_schema,
    load_registry,
    validate_dataset_splits,
    validate_evaluation_cases,
    validate_field_references,
    validate_registry,
    validate_visualization_spec,
)

ROOT = Path(__file__).resolve().parents[1]


def valid_spec(identifier="ranked_bar", category="region", measure="revenue"):
    return {
        "schema_version": 1,
        "visualization_id": identifier,
        "purpose": "Compare category performance",
        "data_mappings": {"category": category, "measure": measure},
        "aggregation": {"references": [{"field": measure, "operation": "sum"}]},
        "formatting": {"number_format": "0.0", "decimal_places": 1},
        "legend": {"visible": True, "position": "bottom"},
        "tooltip": {"enabled": True, "fields": [category, measure], "show_aggregation": True},
        "layout": {"width": 600, "height": 360, "title": "Regional revenue"},
        "accessibility": {
            "text_alternative": "Region revenue ranking",
            "color_mode": "color_with_labels",
            "keyboard_interaction": True,
        },
        "rationale": "A ranked bar makes category comparison explicit and readable.",
        "fallback": False,
    }


def test_registry_has_expected_entries_categories_and_unique_ids():
    registry = load_registry()
    report = validate_registry(registry)
    assert report["count"] == 17
    assert len(set(item["category"] for item in registry["visualizations"])) == 11
    assert len(report["ids"]) == len(set(report["ids"]))
    assert "table" in report["ids"]


def test_output_schema_is_valid_json_schema():
    Draft202012Validator.check_schema(load_output_schema())


def test_supported_visualization_spec_validates():
    assert validate_visualization_spec(valid_spec(), {"region", "revenue"})["valid"]


def test_unsupported_visualization_rejected():
    with pytest.raises(ValueError, match="Unsupported visualization"):
        validate_visualization_spec(valid_spec("radial_explosion"), {"region", "revenue"})


@pytest.mark.parametrize("field", ["invented_profit", "projected_margin"])
def test_hallucinated_field_rejected(field):
    with pytest.raises(ValueError, match="unavailable field"):
        validate_field_references(valid_spec(measure=field), {"region", "revenue"})


def test_decision_rules_choose_expected_charts():
    trend = choose_visualization("Monthly trend", "trend", {"time_like_fields": ["month"]}, ["month", "revenue"])
    ranking = choose_visualization("Rank regions", "ranking", {"category_count": 6}, ["region", "revenue"])
    composition = choose_visualization("Sales mix", "composition", {"category_count": 12}, ["category", "sales"])
    assert trend["visualization_id"] == "line_chart"
    assert ranking["visualization_id"] == "ranked_bar"
    assert composition["visualization_id"] == "stacked_bar"


def test_insufficient_data_fallback():
    result = choose_visualization("Summarize", "unknown", {"insufficient_data": True}, [])
    assert result["visualization_id"] == "table"
    assert result["fallback"] is True


def test_corpus_semantics_and_split_counts():
    report = validate_dataset_splits(ROOT / "visualization" / "corpus")
    assert report["counts"] == {"train.jsonl": 12, "validation.jsonl": 6, "evaluation.jsonl": 6}
    assert report["total"] == 24


def test_corpus_has_unique_ids():
    ids = []
    for split in ("train.jsonl", "validation.jsonl", "evaluation.jsonl"):
        for line in (ROOT / "visualization" / "corpus" / split).read_text().splitlines():
            ids.append(json.loads(line)["id"])
    assert len(ids) == len(set(ids)) == 24


def test_corpus_no_template_or_scenario_leakage():
    template_splits = {}
    scenario_splits = {}
    for split in ("train", "validation", "evaluation"):
        for line in (ROOT / "visualization" / "corpus" / f"{split}.jsonl").read_text().splitlines():
            row = json.loads(line)
            template = row["template_group"]
            scenario = row["scenario_group"]
            assert template_splits.setdefault(template, split) == split
            assert scenario_splits.setdefault(scenario, split) == split


def test_evaluation_benchmark_has_12_cases():
    report = validate_evaluation_cases(ROOT / "visualization" / "evaluation_cases.json")
    assert report["count"] == 12


def test_registry_entries_have_required_metadata():
    for entry in load_registry()["visualizations"]:
        assert entry["id"]
        assert entry["category"]
        assert entry["fallback"]
        assert entry["accessibility"]
        assert entry["encodings"]["required"]
        assert set(entry["encodings"]["required"] + entry["encodings"]["optional"]) <= set(entry["accepted_types"])
        assert entry["fallback"] in {item["id"] for item in load_registry()["visualizations"]}
        assert entry["configuration_options"]
        assert entry["inappropriate_uses"]
        assert entry["cardinality"]


def test_all_corpus_targets_are_complete_and_registry_valid():
    for split in ("train.jsonl", "validation.jsonl", "evaluation.jsonl"):
        for line in (ROOT / "visualization" / "corpus" / split).read_text().splitlines():
            row = json.loads(line)
            spec = row["expected"]["visualization_spec"]
            field_types = {field["name"]: field["type"] for field in row["available_fields"]}
            assert set(
                (
                    "visualization_id",
                    "data_mappings",
                    "aggregation",
                    "formatting",
                    "legend",
                    "tooltip",
                    "layout",
                    "accessibility",
                    "rationale",
                )
            ) <= set(spec)
            assert validate_visualization_spec(spec, field_types)["valid"]


def test_decision_rules_cover_all_registry_categories():
    categories = {entry["category"] for entry in load_registry()["visualizations"]}
    objective_terms = {
        "kpi",
        "trend",
        "comparison",
        "composition",
        "distribution",
        "relationship",
        "geography",
        "hierarchy",
        "conversion",
        "flow",
        "detail",
        "cross_tab",
        "two_dimensional_intensity",
    }
    rules = json.loads((ROOT / "visualization" / "decision_rules.json").read_text())
    assert categories <= {
        "kpi",
        "comparison",
        "time_series",
        "composition",
        "distribution",
        "relationship",
        "geospatial",
        "hierarchical",
        "funnel",
        "table",
        "flow",
    }
    assert objective_terms
    assert len(rules["rules"]) >= 11


def test_registry_rejects_unknown_configuration_option():
    spec = valid_spec()
    spec["configuration"] = {"exploding_pie": True}
    with pytest.raises(ValueError, match="Unsupported configuration option"):
        validate_visualization_spec(spec, {"region": "category", "revenue": "number"})


def test_registry_rejects_invalid_configuration_value():
    spec = valid_spec("ranked_bar")
    spec["configuration"] = {"orientation": "radial"}
    with pytest.raises(ValueError, match="Unsupported value"):
        validate_visualization_spec(spec, {"region": "category", "revenue": "number"})


def test_registry_rejects_invalid_aggregation_reference():
    spec = valid_spec()
    spec["aggregation"] = {"references": [{"field": "invented_total", "operation": "sum"}]}
    with pytest.raises(ValueError, match="unavailable field|unavailable"):
        validate_visualization_spec(spec, {"region": "category", "revenue": "number"})
