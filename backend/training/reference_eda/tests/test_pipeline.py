from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from training.evaluation.hf_runner import (
    Generation,
    evaluate_cases_with_retries,
    load_suite,
)
from training.evaluation.human_review import _source_cases
from training.reference_eda.build_dataset import (
    DEFAULT_OUTPUT_DIR,
    SPLIT_FILENAMES,
    build_dataset,
)
from training.reference_eda.corpus import (
    SCENARIOS,
    build_examples,
    execute_readonly_sql,
    metric_summary,
    profile_for,
    validate_sql_examples,
)
from training.reference_eda.evaluate_adapter import _build_report
from training.reference_eda.train_lora import (
    CONFIG_DIR,
    _assistant_completion_records,
    preflight,
)
from training.reference_eda.validate_dataset import (
    validate_dataset,
    validate_generated_source,
)
from training.validate_sft_dataset import DatasetValidationError


class _CharacterTokenizer:
    pad_token_id = 0

    def apply_chat_template(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        tokenize: bool,
        add_generation_prompt: bool,
        enable_thinking: bool | None = None,
    ) -> list[int]:
        rendered = ""
        for message in messages:
            content = message.get("content", "")
            calls = message.get("tool_calls", [])
            if calls:
                content += json.dumps(calls, sort_keys=True)
            rendered += f"<{message['role']}>{content}</{message['role']}>"
        if add_generation_prompt:
            rendered += "<assistant>"
        assert tokenize
        assert tools is None or isinstance(tools, list)
        assert enable_thinking in {None, False}
        return [ord(character) for character in rendered]


def test_corpus_has_balanced_group_held_out_splits_and_no_duplicate_ids() -> None:
    examples = build_examples()
    ids = [example["id"] for example in examples]
    groups: dict[str, set[str]] = {}
    for example in examples:
        groups.setdefault(example["scenario_group"], set()).add(example["split"])

    assert len(examples) == 250
    assert len(ids) == len(set(ids))
    assert len(groups) == 50
    assert all(len(splits) == 1 for splits in groups.values())
    assert {
        split: sum(example["split"] == split for example in examples)
        for split in ("train", "validation", "test")
    } == {"train": 200, "validation": 25, "test": 25}
    assert {scenario.key for scenario in SCENARIOS} == {
        example["id"].split("-")[1] for example in examples
    }
    for split in ("train", "validation", "test"):
        assert {
            example["id"].split("-")[1]
            for example in examples
            if example["split"] == split
        } == {scenario.key for scenario in SCENARIOS}


def test_checked_in_splits_validate_against_tool_registry_and_source() -> None:
    report = validate_dataset(DEFAULT_OUTPUT_DIR)
    validate_generated_source(DEFAULT_OUTPUT_DIR)

    assert report["examples"] == 250
    assert report["scenario_groups"] == 50
    assert report["validated_sql_python_dax_examples"] == 50
    assert report["evaluation_cases"] == 20
    assert report["all_examples_remain_drafts"] is True
    assert report["evaluation_review_ready"] is False
    assert report["schema_validation"]["training_ready"] is False
    assert report["tool_calls_by_name"] == {
        "get_inspection_handoff": 10,
        "get_profile": 10,
        "get_quality_report": 10,
        "run_eda_analysis": 10,
        "run_quality_analysis": 10,
    }


def test_human_review_suite_loads_and_excludes_tool_execution_cases() -> None:
    suite_path = DEFAULT_OUTPUT_DIR / "human_review_suite.json"
    suite_version, cases = load_suite(suite_path)

    assert suite_version == "insightos-reference-eda-human-review-v2"
    assert len(cases) == 20
    assert all(case.scorer == "human_review" for case in cases)
    assert all(case.repetitions == 1 for case in cases)
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    assert sum(suite["cases"][0]["scoring_rubric"].values()) == 100
    assert all(
        case["scoring_rubric"] == suite["cases"][0]["scoring_rubric"]
        for case in suite["cases"]
    )
    assert all(
        case["acceptance_criteria"]
        and case["rejection_criteria"]
        and sum(case["case_rubric"].values()) == 100
        for case in suite["cases"]
    )
    assert {
        case["dataset_profile"]["scenario"] for case in suite["cases"]
    } == {scenario.key for scenario in SCENARIOS}
    critical_prompts = [
        case["prompt"]
        for case in suite["cases"]
        if case["id"].endswith("-results")
    ]
    assert len(critical_prompts) == 5
    assert any("Facebook" in prompt and "Google" in prompt for prompt in critical_prompts)
    assert any("78%" in prompt and "sensor" in prompt for prompt in critical_prompts)
    assert any("₹12.8 lakh" in prompt and "₹12.1 lakh" in prompt for prompt in critical_prompts)
    assert any("recall" in prompt.lower() and "false positives" in prompt.lower() for prompt in critical_prompts)
    assert any("patient" in prompt.lower() and "readmission" in prompt.lower() for prompt in critical_prompts)


def test_mocked_evaluation_report_is_compatible_with_human_review() -> None:
    suite_path = DEFAULT_OUTPUT_DIR / "human_review_suite.json"
    suite_version, cases = load_suite(suite_path)
    results = evaluate_cases_with_retries(
        cases,
        lambda case, feedback=None: Generation(
            text="A synthetic answer for review.",
            generated_tokens=6,
            duration_ms=1.0,
            tokens_per_second=6000.0,
            termination_reason="eos",
            final_text="A synthetic answer for review.",
            source="test_mock",
        ),
        max_attempts=1,
        deterministic_fallbacks=False,
    )
    report = _build_report(
        suite_version=suite_version,
        cases=cases,
        results=results,
        config={"model_id": "Qwen/Qwen3-0.6B", "revision": "a" * 40},
        readiness={"git_revision": "test", "cuda_vram_gb": None},
        model_load_ms=0.0,
        suite_path=suite_path,
        adapter_dir=Path("mock-adapter"),
    )

    assert report["counts"]["needs_review"] == 20
    assert len(_source_cases(report)) == 20


def test_draft_training_data_cannot_pass_human_approval_gate() -> None:
    with pytest.raises(DatasetValidationError, match="human-approved"):
        validate_dataset(DEFAULT_OUTPUT_DIR, require_approved=True)

    readiness = preflight(CONFIG_DIR / "qwen3-0.6b.json")
    assert readiness["ready"] is False
    assert any("explicit human approval" in blocker for blocker in readiness["blockers"])


def test_rebuild_does_not_import_legacy_embedded_review_metadata(tmp_path: Path) -> None:
    build_dataset(tmp_path)
    training_path = tmp_path / SPLIT_FILENAMES["train"]
    examples = [
        json.loads(line)
        for line in training_path.read_text(encoding="utf-8").splitlines()
    ]
    examples[0]["validation_status"] = "approved"
    examples[0]["reviewer_notes"] = "reviewer=Reviewer A; reviewed_on=2026-06-16"
    training_path.write_text(
        "".join(json.dumps(example, sort_keys=True) + "\n" for example in examples),
        encoding="utf-8",
    )
    suite_path = tmp_path / "human_review_suite.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    suite["cases"][0]["validation_status"] = "reviewed"
    suite["cases"][0]["reviewer_notes"] = (
        "reviewer=Reviewer B; reviewed_on=2026-06-16"
    )
    suite_path.write_text(json.dumps(suite), encoding="utf-8")

    build_dataset(tmp_path)

    rebuilt = [
        json.loads(line)
        for line in training_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rebuilt[0]["validation_status"] == "draft"
    assert "Draft only" in rebuilt[0]["reviewer_notes"]
    rebuilt_suite = json.loads(suite_path.read_text(encoding="utf-8"))
    assert rebuilt_suite["cases"][0]["validation_status"] == "draft"
    assert "Draft evaluation case" in rebuilt_suite["cases"][0]["reviewer_notes"]


def test_synthetic_fixture_statistics_are_deterministic_and_bounded() -> None:
    summaries = {scenario.key: metric_summary(scenario) for scenario in SCENARIOS}
    profiles = {scenario.key: profile_for(scenario) for scenario in SCENARIOS}

    assert set(summaries) == {
        "marketing",
        "maintenance",
        "ecommerce",
        "fraud",
        "healthcare",
    }
    assert summaries["marketing"]["Google_qualified_leads"] == "36.00"
    assert summaries["marketing"]["Google_qualified_cpa"] == "16.67"
    assert summaries["marketing"]["Google_roas"] == "3.00"
    assert summaries["marketing"]["Facebook_leads"] == "100.00"
    assert summaries["marketing"]["Facebook_qualified_leads"] == "20.00"
    assert summaries["marketing"]["NoSpend_qualified_cpa"] == "undefined"
    assert summaries["marketing"]["NoSpend_roas"] == "undefined"
    assert summaries["ecommerce"]["net_revenue"] == "1260.00"
    assert summaries["ecommerce"]["gross_profit"] == "640.00"
    assert summaries["ecommerce"]["gross_margin_percent"] == "50.79"
    assert summaries["ecommerce"]["fully-refunded_net_revenue"] == "0.00"
    assert summaries["ecommerce"]["fully-refunded_gross_margin_percent"] == "undefined"
    assert summaries["maintenance"]["prediction_horizon_days"] == "30"
    assert summaries["maintenance"]["incomplete_sensor_observations"] == "1"
    assert summaries["fraud"]["precision_percent"] == "66.67"
    assert summaries["fraud"]["recall_percent"] == "66.67"
    assert summaries["healthcare"]["eligible_complete_discharge_episodes"] == "4"
    assert summaries["healthcare"]["readmission_rate_percent"] == "50.00"
    assert summaries["healthcare"]["episodes_with_post_discharge_feature_risk"] == "1"
    assert profiles["maintenance"]["missing_values_by_column"] == {
        "pressure_kpa": 1,
        "failure_at": 3,
    }
    assert profiles["healthcare"]["missing_values_by_column"] == {"risk_score": 1}
    maintenance = next(scenario for scenario in SCENARIOS if scenario.key == "maintenance")
    for row in maintenance.rows:
        failure_at = row["failure_at"]
        if row["failure_within_30d"]:
            observed_at = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
            failure_time = datetime.fromisoformat(failure_at.replace("Z", "+00:00"))
            assert observed_at < failure_time <= observed_at + timedelta(days=30)
        else:
            assert failure_at is None


def test_tool_examples_match_registered_calls_and_serialized_result_contracts() -> None:
    from app.agent.contracts import ToolResult
    from app.agent.inspection_handoff import InspectionHandoff
    from app.agent.tools import DatasetInspectionHandoff, TOOL_DEFINITIONS

    definitions = {definition.name: definition for definition in TOOL_DEFINITIONS}
    required_result_fields = set(ToolResult.__dataclass_fields__)
    tool_calls = 0
    handoffs = 0
    for example in build_examples():
        messages = example["messages"]
        for message_index, message in enumerate(messages):
            for call in message.get("tool_calls", []):
                tool_calls += 1
                name = call["function"]["name"]
                definition = definitions[name]
                arguments = call["function"]["arguments"]
                definition.arguments_model.model_validate(arguments)
                preceding_context = "\n".join(
                    item["content"] for item in messages[:message_index]
                )
                assert f"dataset_id={arguments['dataset_id']}" in preceding_context
                assert f"file_id={arguments['file_id']}" in preceding_context
                result_message = next(
                    item
                    for item in messages[message_index + 1 :]
                    if item["role"] == "tool"
                    and item["tool_call_id"] == call["id"]
                )
                result = json.loads(result_message["content"])
                assert set(result) == required_result_fields
                assert result["status"] == "succeeded"
                if name == "get_inspection_handoff":
                    InspectionHandoff.model_validate(result["data"])
                    handoffs += 1
                if name in {"run_eda_analysis", "run_quality_analysis"}:
                    inspection = DatasetInspectionHandoff.model_validate(
                        result["data"]["agent1_inspection"]
                    )
                    assert inspection.dataset_id == arguments["dataset_id"]
                    assert inspection.file_id == arguments["file_id"]

    assert tool_calls == 50
    assert handoffs == 10


def test_python_kpi_examples_execute_and_match_fixture_calculations() -> None:
    expected = {
        "marketing": {
            "Google_qualified_cpa": 600 / 36,
            "Google_roas": 3.0,
            "Facebook_leads": 100,
            "Facebook_roas": 1.8,
        },
        "maintenance": {
            "complete_sensor_observations": 4,
            "incomplete_sensor_observations": 1,
            "confirmed_failures_within_30d": 1,
        },
        "ecommerce": {
            "net_revenue": 1260,
            "cogs": 620,
            "gross_profit": 640,
            "gross_margin_percent": 100 * 640 / 1260,
        },
        "fraud": {
            "true_positives": 2,
            "false_positives": 1,
            "precision_percent": 200 / 3,
            "recall_percent": 200 / 3,
        },
        "healthcare": {
            "eligible_discharge_episodes": 4,
            "unplanned_readmissions_30d": 2,
            "readmission_rate_percent": 50,
            "post_discharge_feature_risk_rows": 1,
        },
    }
    scenarios = {scenario.key: scenario for scenario in SCENARIOS}
    examples = [
        example
        for example in build_examples()
        if example["category"] == "structured_output"
    ]
    executed = 0
    for example in examples:
        content = example["messages"][-1]["content"]
        if "```python\n" not in content:
            continue
        scenario_key = example["id"].split("-")[1]
        scenario = scenarios[scenario_key]
        code = content.split("```python\n", 1)[1].split("```", 1)[0]
        scope: dict[str, Any] = {
            "rows": [dict(row) for row in scenario.rows],
        }
        exec(compile(code, example["id"], "exec"), scope)
        summary = scope["summary"]
        for key, value in expected[scenario_key].items():
            if scenario_key == "marketing":
                channel, metric = key.split("_", 1)
                actual = summary[channel][metric]
            else:
                actual = summary[key]
            assert actual == pytest.approx(value), (example["id"], key, actual)
        executed += 1
    assert executed == 15


def test_generated_sql_is_read_only_and_executes_on_its_fixture() -> None:
    examples = build_examples()
    assert validate_sql_examples(examples) == 50
    expected_sql = {
        "marketing": {
            "Email": (100.0, 16.0, 6.25, 640.0, 6.4),
            "Facebook": (500.0, 20.0, 25.0, 900.0, 1.8),
            "Google": (600.0, 36.0, 16.67, 1800.0, 3.0),
            "NoSpend": (0.0, 0.0, None, 0.0, None),
        },
        "maintenance": {
            "M-A": (2, 2, 0),
            "M-B": (2, 1, 1),
            "M-C": (1, 1, 0),
        },
        "ecommerce": (1260.0, 620.0, 50.79),
        "fraud": (2, 3, 3, 66.67, 66.67),
        "healthcare": (4, 2, 50.0),
    }
    checked = 0
    for example in examples:
        if example["category"] != "structured_output":
            continue
        content = example["messages"][-1]["content"]
        if "```sql\n" not in content:
            continue
        scenario_key = example["id"].split("-")[1]
        query = content.split("```sql\n", 1)[1].split("\n```", 1)[0]
        rows = execute_readonly_sql(scenario_key, query)
        if scenario_key == "marketing":
            actual = {row[0]: tuple(row[1:]) for row in rows}
            assert actual == expected_sql[scenario_key]
        elif scenario_key == "maintenance":
            assert {row[0]: tuple(row[1:]) for row in rows} == expected_sql[scenario_key]
        else:
            assert rows == [expected_sql[scenario_key]]
        checked += 1
    assert checked == 20
    assert execute_readonly_sql(
        "marketing",
        'SELECT "channel", COUNT(*) FROM "marketing_daily" GROUP BY "channel"',
    )
    with pytest.raises(ValueError, match="read-only SELECT"):
        execute_readonly_sql("marketing", "DELETE FROM marketing_daily")
    with pytest.raises(ValueError, match="one SQL statement"):
        execute_readonly_sql(
            "marketing",
            "SELECT 1; DROP TABLE marketing_daily",
        )


def test_completion_only_dataset_masks_prompt_and_tool_history() -> None:
    example = {
        "id": "test-tool-completion",
        "tools": [],
        "messages": [
            {"role": "system", "content": "Follow the task."},
            {"role": "user", "content": "Inspect the synthetic table."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "inspect", "arguments": {}},
                    }
                ],
            },
            {
                "role": "tool",
                "name": "inspect",
                "tool_call_id": "call-1",
                "content": '{"rows": 5}',
            },
            {"role": "assistant", "content": "The fixture contains five rows."},
        ],
    }
    tokenizer = _CharacterTokenizer()
    rows = _assistant_completion_records([example], tokenizer, 4096)

    assert len(rows) == 2
    for row in rows:
        first_target = next(
            index for index, label in enumerate(row["labels"]) if label != -100
        )
        assert all(label == -100 for label in row["labels"][:first_target])
        assert any(label != -100 for label in row["labels"][first_target:])


def test_model_configs_pin_all_requested_checkpoints() -> None:
    expected_revisions = {
        "qwen3-0.6b": "c1899de289a04d12100db370d81485cdf75e47ca",
        "qwen3-4b": "1cfa9a7208912126459214e8b04321603b3df60c",
        "qwen3-8b": "b968826d9c46dd6066d109eabc6255188de91218",
    }
    for model, revision in expected_revisions.items():
        config = json.loads((CONFIG_DIR / f"{model}.json").read_text(encoding="utf-8"))
        assert config["revision"] == revision
        assert config["quantization"]["backend"] == "bitsandbytes"
        assert config["training"]["assistant_completion_only_loss"] is True
        assert config["training"]["external_reporting"] is False
        assert config["training"]["require_human_approved_examples"] is True


def test_environment_report_handles_missing_torch_and_cpu_mode() -> None:
    from training.reference_eda.environment import (
        classify_environment,
        inspect_environment,
    )

    report = inspect_environment()
    classification = classify_environment(report)

    assert set(report["cuda"]) >= {
        "available",
        "runtime_version",
        "devices",
        "bf16_supported",
        "fp16_supported",
        "allocated_gib",
        "reserved_gib",
    }
    assert classification in {
        "unsupported-python",
        "cpu-only-development",
        "cuda-compatible",
        "gpu-unavailable",
        "gpu-cuda-unavailable",
    }


def test_adapter_inspection_rejects_incomplete_artifacts(tmp_path: Path) -> None:
    from training.reference_eda.cli import inspect_adapter

    report = inspect_adapter(tmp_path / "missing")
    assert report["exists"] is False
    assert report["compatible"] is False

    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
    (adapter_dir / "adapter_model.safetensors").write_bytes(b"weights")
    (adapter_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

    report = inspect_adapter(adapter_dir)
    assert report["compatible"] is True
