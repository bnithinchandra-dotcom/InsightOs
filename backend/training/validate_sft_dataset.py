from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.tools import TOOL_DEFINITIONS
from pydantic import ValidationError


DATASET_PATH = Path(__file__).parent / "qwen3-8b" / "insightos-agent-sft-v1.jsonl"
SCHEMA_PATH = Path(__file__).parent / "qwen3-0.6b" / "insightos-sft.schema.json"
_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CATEGORIES = {
    "business_reasoning",
    "schema_interpretation",
    "structured_output",
    "tool_selection",
    "grounding_safety",
}
_SPLITS = {"train", "validation", "test"}
_STATUSES = {"draft", "reviewed", "approved", "rejected"}
_REQUIRED_FIELDS = {
    "id",
    "scenario_group",
    "category",
    "split",
    "messages",
    "tools",
    "source",
    "license",
    "validation_status",
    "reviewer_notes",
}
_MESSAGE_FIELDS = {"role", "content", "name", "tool_call_id", "tool_calls"}


class DatasetValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _validate_example(
    example: Any,
    line_number: int,
    tool_models: dict[str, type],
    tool_schemas: dict[str, dict[str, Any]],
    *,
    require_approved: bool,
) -> tuple[str | None, str | None, str | None, list[str]]:
    prefix = f"line {line_number}"
    errors: list[str] = []
    if not isinstance(example, dict):
        return None, None, None, [f"{prefix}: expected a JSON object"]

    missing = _REQUIRED_FIELDS - example.keys()
    extra = example.keys() - _REQUIRED_FIELDS
    if missing:
        errors.append(f"{prefix}: missing fields {sorted(missing)}")
    if extra:
        errors.append(f"{prefix}: unexpected fields {sorted(extra)}")

    example_id = example.get("id")
    scenario_group = example.get("scenario_group")
    split = example.get("split")
    if not isinstance(example_id, str) or not _ID_PATTERN.fullmatch(example_id):
        errors.append(f"{prefix}: id must be lower-case kebab-case")
    if not isinstance(scenario_group, str) or not scenario_group.strip():
        errors.append(f"{prefix}: scenario_group must be a non-empty string")
    category = example.get("category")
    if category not in _CATEGORIES:
        errors.append(f"{prefix}: unsupported category {category!r}")
    if split not in _SPLITS:
        errors.append(f"{prefix}: unsupported split {split!r}")

    validation_status = example.get("validation_status")
    if validation_status not in _STATUSES:
        errors.append(f"{prefix}: unsupported validation_status {validation_status!r}")
    if require_approved and split == "train" and validation_status != "approved":
        errors.append(f"{prefix}: training examples must be human-approved")

    source = example.get("source")
    if (
        not isinstance(source, dict)
        or set(source) - {"kind", "reference", "accessed_on"}
        or source.get("kind") not in {"synthetic", "public"}
        or not isinstance(source.get("reference"), str)
        or not source["reference"].strip()
    ):
        errors.append(f"{prefix}: invalid source metadata")
    elif "accessed_on" in source:
        try:
            date.fromisoformat(source["accessed_on"])
        except (TypeError, ValueError):
            errors.append(f"{prefix}: source.accessed_on must be an ISO date")
    if not isinstance(example.get("license"), str) or not example["license"].strip():
        errors.append(f"{prefix}: license must be a non-empty string")
    if not isinstance(example.get("reviewer_notes"), str):
        errors.append(f"{prefix}: reviewer_notes must be a string")

    available_tools = _validate_tool_schemas(
        example.get("tools"),
        prefix,
        tool_schemas,
        errors,
    )
    messages = example.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        errors.append(f"{prefix}: messages must contain at least two entries")
        messages = []

    call_ids: dict[str, str] = {}
    for message_index, message in enumerate(messages):
        message_prefix = f"{prefix} message {message_index}"
        if not isinstance(message, dict):
            errors.append(f"{message_prefix}: expected an object")
            continue
        if set(message) - _MESSAGE_FIELDS:
            errors.append(f"{message_prefix}: contains unexpected fields")
        if message.get("role") not in {"system", "user", "assistant", "tool"}:
            errors.append(f"{message_prefix}: invalid role")
        if not isinstance(message.get("content"), str):
            errors.append(f"{message_prefix}: content must be a string")
        if message.get("role") == "tool":
            if not isinstance(message.get("name"), str):
                errors.append(f"{message_prefix}: tool result must name its tool")
            tool_call_id = message.get("tool_call_id")
            if (
                not isinstance(tool_call_id, str)
                or tool_call_id not in call_ids
                or call_ids.get(tool_call_id) != message.get("name")
            ):
                errors.append(f"{message_prefix}: tool result has no matching call")

        calls = message.get("tool_calls", [])
        if not isinstance(calls, list):
            errors.append(f"{message_prefix}: tool_calls must be a list")
            continue
        for call in calls:
            if (
                not isinstance(call, dict)
                or set(call) != {"id", "type", "function"}
                or call.get("type") != "function"
            ):
                errors.append(f"{message_prefix}: malformed tool call")
                continue
            function = call.get("function") if isinstance(call, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            arguments = function.get("arguments") if isinstance(function, dict) else None
            call_id = call.get("id") if isinstance(call, dict) else None
            if (
                not isinstance(function, dict)
                or set(function) != {"name", "arguments"}
                or not isinstance(call_id, str)
                or call_id in call_ids
                or not isinstance(name, str)
                or not isinstance(arguments, dict)
                or name not in available_tools
                or name not in tool_models
            ):
                errors.append(f"{message_prefix}: invalid or unregistered tool call")
                continue
            call_ids[call_id] = name
            try:
                tool_models[name].model_validate(arguments)
            except ValidationError as error:
                errors.append(
                    f"{message_prefix}: invalid arguments for {name} "
                    f"({error.__class__.__name__})"
                )

    return (
        example_id if isinstance(example_id, str) else None,
        scenario_group if isinstance(scenario_group, str) else None,
        split if isinstance(split, str) else None,
        errors,
    )


def _validate_tool_schemas(
    tools: Any,
    prefix: str,
    tool_schemas: dict[str, dict[str, Any]],
    errors: list[str],
) -> set[str]:
    if not isinstance(tools, list):
        errors.append(f"{prefix}: tools must be a list")
        return set()
    available: set[str] = set()
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if (
            not isinstance(tool, dict)
            or set(tool) != {"type", "function"}
            or tool.get("type") != "function"
            or not isinstance(function, dict)
            or set(function) != {"name", "description", "parameters"}
            or not isinstance(name, str)
            or name not in tool_schemas
        ):
            errors.append(f"{prefix}: contains an invalid or unregistered tool schema")
            continue
        if name in available:
            errors.append(f"{prefix}: duplicates tool schema {name}")
        if _without_titles(tool) != _without_titles(tool_schemas[name]):
            errors.append(f"{prefix}: tool schema {name} does not match the registry")
        available.add(name)
    return available


def _without_titles(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_titles(item)
            for key, item in value.items()
            if key != "title"
        }
    if isinstance(value, list):
        return [_without_titles(item) for item in value]
    return value


def validate_dataset(
    dataset_path: Path = DATASET_PATH,
    *,
    require_approved: bool = False,
) -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
        if (
            schema.get("$id") != "urn:insightos:schemas:qwen3-sft-v1"
            or set(schema.get("required", [])) != _REQUIRED_FIELDS
        ):
            raise DatasetValidationError(["SFT JSON Schema identifier is unexpected"])

        tool_models = {
            definition.name: definition.arguments_model
            for definition in TOOL_DEFINITIONS
        }
        tool_schemas = {
            definition.name: definition.provider_schema()
            for definition in TOOL_DEFINITIONS
        }
        errors: list[str] = []
        examples_by_id: dict[str, tuple[str, str]] = {}
        counts: Counter[str] = Counter()
        approved_training_examples = 0
    with dataset_path.open(encoding="utf-8") as dataset_file:
        for line_number, line in enumerate(dataset_file, start=1):
            if not line.strip():
                errors.append(f"line {line_number}: blank lines are not allowed")
                continue
            try:
                example = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append(f"line {line_number}: invalid JSON ({error.msg})")
                continue

            example_id, group, split, example_errors = _validate_example(
                example,
                line_number,
                tool_models,
                tool_schemas,
                require_approved=require_approved,
            )
            errors.extend(example_errors)
            if isinstance(split, str):
                counts[split] += 1
            if (
                isinstance(example, dict)
                and example.get("split") == "train"
                and example.get("validation_status") == "approved"
            ):
                approved_training_examples += 1
            if example_id is not None and group is not None and split is not None:
                if example_id in examples_by_id:
                    errors.append(f"line {line_number}: duplicate id {example_id}")
                else:
                    examples_by_id[example_id] = (group, split)

    group_splits: dict[str, str] = {}
    for example_id, (group, split) in examples_by_id.items():
        previous = group_splits.setdefault(group, split)
        if previous != split:
            errors.append(
                f"scenario_group {group!r} leaks across {previous!r} and {split!r}"
            )
    for split in _SPLITS:
        if counts[split] == 0:
            errors.append(f"split {split!r} is empty")
    if errors:
        raise DatasetValidationError(errors)

    return {
        "dataset_version": "insightos-agent-sft-v1",
        "examples": sum(counts.values()),
        "split_counts": dict(sorted(counts.items())),
        "scenario_groups": len(group_splits),
        "human_approved_training_examples": approved_training_examples,
        "training_ready": approved_training_examples == counts["train"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the InsightOS SFT dataset.")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--require-approved",
        action="store_true",
        help="Fail unless every training example has human approval.",
    )
    args = parser.parse_args()
    try:
        result = validate_dataset(args.dataset, require_approved=args.require_approved)
    except (DatasetValidationError, OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
