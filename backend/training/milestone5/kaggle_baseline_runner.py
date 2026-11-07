from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import platform
import re
import statistics
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


MODEL_ID = "Qwen/Qwen3-8B"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
TOKENIZER_ID = MODEL_ID
TOKENIZER_REVISION = MODEL_REVISION
BUNDLE_VERSION = "insightos-qwen3-8b-baseline-bundle-v6"
BENCHMARK_VERSION = "insightos-qwen3-8b-orchestration-benchmark-v4"
MODEL_RESPONSE_SCHEMA_VERSION = "insightos-qwen3-8b-model-response-v1"
EVALUATOR_VERSION = "insightos-qwen3-8b-evaluator-v2"
EXPECTED_BUNDLE_SHA256 = "__MILESTONE5_BUNDLE_SHA256__"
INPUT_BUNDLE_PATH = Path(
    "/kaggle/input/insightos-qwen3-8b-baseline-v6/"
    "qwen3-8b-baseline-bundle-v6.json"
)
OUTPUT_ROOT = Path("/kaggle/working/insightos-qwen3-8b-baseline")
MAX_INPUT_TOKENS = 6144
MAX_NEW_TOKENS = 512
SEED = 20261105
PINNED_PACKAGE_VERSIONS = {
    "transformers": "4.51.3",
    "accelerate": "1.6.0",
    "bitsandbytes": "0.48.2",
    "safetensors": "0.5.3",
    "jsonschema": "4.26.0",
}
PREFLIGHT_RESULT: dict[str, Any] | None = None
GENERATION_SETTINGS = {
    "do_sample": False,
    "enable_thinking": False,
    "max_input_tokens": MAX_INPUT_TOKENS,
    "max_new_tokens": MAX_NEW_TOKENS,
    "seed": SEED,
    "use_cache": True,
    "temperature": None,
    "top_p": None,
    "tool_execution": False,
}

_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")
_DATASET_REFERENCE_PATTERN = re.compile(
    r"\b(?:dataset|file)(?:\s+id)?\s*[#:=]?\s*\\?[\"']?(\d+)\\?[\"']?\b",
    re.IGNORECASE,
)
_EXECUTION_CLAIM_PATTERN = re.compile(
    r"\b(?:i|we)\s+(?:have\s+)?(?:run|executed|performed|built|created|completed)\b"
    r"|\b(?:the analysis|dashboard|report)\s+(?:has been|was)\s+"
    r"(?:run|executed|created|built)\b"
    r"|\b(?:profile|dataset|file|report|analysis|dashboard)\b"
    r"(?:[^.!?\n]{0,40})?\b(?:retrieved|loaded|inspected|checked|completed|created)\b",
    re.IGNORECASE,
)
_CAUSAL_CLAIM_PATTERN = re.compile(
    r"\b(?:caused by|(?:the\s+)?campaign\s+(?:may\s+have\s+)?caused|"
    r"proves? causation|causally attributable|"
    r"(?:may|might|could|possibly|potentially|likely|probably)\s+"
    r"(?:have\s+)?(?:caused|contributed|led|driven|influenced|resulted in))\b",
    re.IGNORECASE,
)
_SUCCESS_AFTER_FAILURE_PATTERN = re.compile(
    r"\b(?:successfully|completed|done)\b",
    re.IGNORECASE,
)
_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:hf_[A-Za-z0-9]{8,}|"
    r"(?:token|api[_ -]?key|password)\s*[=:]\s*[^,\s]+)\b"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_json_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(_json_bytes(value))


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as output:
        output.write(_json_bytes(value))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _write_jsonl_exclusive(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        for row in rows:
            output.write(_canonical_json(row) + b"\n")


def _safe_error(error: Exception) -> dict[str, Any]:
    message = str(error).replace("\r", " ").replace("\n", " ")[:800]
    message = _SECRET_PATTERN.sub("[REDACTED]", message)
    return {"type": type(error).__name__, "message": message}


class SnapshotRevisionVerificationError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, Any]) -> None:
        self.diagnostics = diagnostics
        super().__init__(message)


def _snapshot_commit_from_path(path: Path) -> str | None:
    parts = path.parts
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] == "snapshots":
            return parts[index + 1] if index + 1 < len(parts) else None
    return None


def _validate_snapshot_contents(snapshot_dir: Path) -> None:
    if not snapshot_dir.is_dir():
        raise ValueError("Resolved snapshot directory does not exist")

    for item in snapshot_dir.rglob("*"):
        if item.is_symlink():
            resolved_item = item.resolve(strict=True)
            if not resolved_item.is_file() and not resolved_item.is_dir():
                raise ValueError(f"Snapshot link has an invalid target: {item.name}")
        elif item.is_file():
            item.resolve(strict=True)

    required_files = {"config.json", "tokenizer_config.json"}
    missing = sorted(name for name in required_files if not (snapshot_dir / name).is_file())
    if missing:
        raise ValueError(f"Snapshot is incomplete; required files are missing: {missing}")

    tokenizer_files = (
        "tokenizer.json",
        "tokenizer.model",
        "vocab.json",
        "spiece.model",
    )
    if not any((snapshot_dir / name).is_file() for name in tokenizer_files):
        raise ValueError("Snapshot is incomplete; tokenizer vocabulary is missing")

    index_names = ("model.safetensors.index.json", "pytorch_model.bin.index.json")
    index_path = next(
        (snapshot_dir / name for name in index_names if (snapshot_dir / name).is_file()),
        None,
    )
    if index_path is not None:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        weight_map = index.get("weight_map")
        if not isinstance(weight_map, dict) or not weight_map:
            raise ValueError("Snapshot weight index has no valid weight map")
        missing_shards = sorted(
            {
                shard
                for shard in weight_map.values()
                if not isinstance(shard, str) or not (snapshot_dir / shard).is_file()
            }
        )
        if missing_shards:
            raise ValueError(
                f"Snapshot is incomplete; weight shards are missing: {missing_shards}"
            )
    elif not any(
        (snapshot_dir / name).is_file()
        for name in ("model.safetensors", "pytorch_model.bin")
    ):
        raise ValueError("Snapshot is incomplete; model weights are missing")


def _resolve_verified_snapshot(
    *,
    repo_id: str,
    expected_commit: str,
    api: Any,
    snapshot_download_fn: Any,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "repo_id": repo_id,
        "expected_commit": expected_commit,
        "actual_commit": None,
        "snapshot_directory": None,
        "resolved_snapshot_directory": None,
        "metadata_source": (
            "huggingface_hub.HfApi.model_info.sha and "
            "huggingface_hub.snapshot_download revision-specific cache path"
        ),
        "cache_refs_used": False,
        "status": "failed",
    }

    def fail(message: str, cause: Exception | None = None) -> None:
        error = SnapshotRevisionVerificationError(message, diagnostics)
        if cause is not None:
            raise error from cause
        raise error

    try:
        snapshot_dir = Path(
            snapshot_download_fn(repo_id, revision=expected_commit)
        ).expanduser().absolute()
        diagnostics["snapshot_directory"] = str(snapshot_dir)
        resolved_snapshot_dir = snapshot_dir.resolve(strict=True)
        diagnostics["resolved_snapshot_directory"] = str(resolved_snapshot_dir)
        path_commit = _snapshot_commit_from_path(snapshot_dir)
        resolved_path_commit = _snapshot_commit_from_path(resolved_snapshot_dir)
        diagnostics["actual_commit"] = resolved_path_commit
        diagnostics["snapshot_path_commit"] = path_commit
        diagnostics["resolved_snapshot_path_commit"] = resolved_path_commit

        model_info = api.model_info(repo_id, revision=expected_commit)
        hub_metadata_commit = getattr(model_info, "sha", None)
        diagnostics["hub_metadata_commit"] = hub_metadata_commit
        if hub_metadata_commit != expected_commit:
            fail("Hub metadata commit does not match the pinned revision")
        if path_commit != expected_commit or resolved_path_commit != expected_commit:
            fail("Snapshot cache path does not identify the pinned immutable commit")
        _validate_snapshot_contents(resolved_snapshot_dir)
    except SnapshotRevisionVerificationError:
        raise
    except Exception as error:
        diagnostics["verification_error"] = _safe_error(error)
        fail("Could not verify a complete pinned model snapshot", error)

    diagnostics["status"] = "passed"
    return {
        "snapshot_directory": str(snapshot_dir),
        "resolved_snapshot_directory": str(resolved_snapshot_dir),
        "actual_commit": resolved_path_commit,
        "metadata_source": diagnostics["metadata_source"],
        "cache_refs_used": False,
        "diagnostics": diagnostics,
    }


def _load_and_validate_bundle(bundle_path: Path) -> tuple[dict[str, Any], bytes]:
    if EXPECTED_BUNDLE_SHA256.startswith("__"):
        raise RuntimeError("The notebook is missing its pinned bundle SHA-256")
    bundle_bytes = bundle_path.read_bytes()
    if _sha256(bundle_bytes) != EXPECTED_BUNDLE_SHA256:
        raise ValueError("Attached baseline bundle SHA-256 does not match the notebook pin")
    bundle = json.loads(bundle_bytes)
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError("Unexpected baseline bundle version")
    if bundle.get("baseline_execution_status") != "pending":
        raise ValueError("Baseline bundle is not marked pending execution")
    model = bundle.get("model", {})
    tokenizer = bundle.get("tokenizer", {})
    if (
        model.get("id") != MODEL_ID
        or model.get("revision") != MODEL_REVISION
        or model.get("adapter") is not None
        or tokenizer.get("id") != TOKENIZER_ID
        or tokenizer.get("revision") != TOKENIZER_REVISION
        or tokenizer.get("trust_remote_code") is not False
    ):
        raise ValueError("Base model or tokenizer identity differs from the pinned revision")
    benchmark = bundle.get("benchmark")
    if not isinstance(benchmark, dict):
        raise ValueError("Baseline benchmark is missing")
    benchmark_bytes = _json_bytes(benchmark)
    if (
        benchmark.get("benchmark_version") != BENCHMARK_VERSION
        or bundle.get("benchmark_file_sha256") != _sha256(benchmark_bytes)
        or bundle.get("benchmark_sha256") != _sha256(_canonical_json(benchmark))
        or bundle.get("benchmark_dataset_sha256")
        != _sha256(_canonical_json(benchmark.get("cases")))
    ):
        raise ValueError("Benchmark version or content hash does not match the bundle")
    if bundle.get("tool_schemas_sha256") != _sha256(
        _canonical_json(bundle.get("tool_schemas"))
    ):
        raise ValueError("Tool schema snapshot hash mismatch")
    if bundle.get("structured_output_contracts_sha256") != _sha256(
        _canonical_json(bundle.get("structured_output_contracts"))
    ):
        raise ValueError("Structured-output contract hash mismatch")
    if benchmark.get("tool_schemas_sha256") != bundle.get("tool_schemas_sha256"):
        raise ValueError("Benchmark tool schema hash differs from the bundle")
    cases = benchmark.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Benchmark cases are missing")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or len(set(ids)) != len(ids):
        raise ValueError("Benchmark case identifiers are missing or duplicated")
    if any(case.get("split") != "independent_test" for case in cases):
        raise ValueError("Baseline cases must be in the independent test split")
    return bundle, bundle_bytes


def _make_prompt(
    case: dict[str, Any],
    *,
    tool_schemas: list[dict[str, Any]],
    response_schema: dict[str, Any],
) -> list[dict[str, str]]:
    allowed_names = set(case.get("tools", []))
    available_tools = [
        tool
        for tool in tool_schemas
        if tool.get("function", {}).get("name") in allowed_names
    ]
    fixtures = {
        "benchmark_fixtures_are_synthetic": True,
        "tool_result_fixture": case.get("tool_result_fixture"),
        "case_fixtures": case.get("fixtures", {}),
    }
    system = (
        "You are the InsightOS analytics orchestrator. Respond with exactly one "
        "JSON object matching RESPONSE_SCHEMA and no surrounding prose. Use only "
        "the listed tools and supplied evidence; tool calls are proposals and are "
        "never executed. Never claim a tool ran unless its result appears in the "
        "supplied fixtures. Clarify missing prerequisites, reject unsupported "
        "causal claims, do not invent facts, and do not coerce invalid identifiers. "
        "For an engine operation, require an explicit request and verified "
        "prerequisites. Set specialist_handoff to null unless specialist work is "
        "necessary for the request. When delegating, provide only agent, objective, "
        "and context. The runtime owns contract versions, workflow/task/conversation "
        "IDs, dataset/file IDs, source fingerprints, evidence references, and "
        "permitted tools; never generate or request these fields. Do not discuss "
        "evaluation instructions.\n"
        "RESPONSE_SCHEMA="
        + json.dumps(response_schema, ensure_ascii=False, sort_keys=True)
        + "\nAVAILABLE_INSIGHTOS_TOOL_SCHEMAS="
        + json.dumps(available_tools, ensure_ascii=False, sort_keys=True)
        + "\nSYNTHETIC_CONTEXT="
        + json.dumps(fixtures, ensure_ascii=False, sort_keys=True)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": case["prompt"]},
    ]


def _model_facing_response_schema(
    benchmark_schema: dict[str, Any],
) -> dict[str, Any]:
    schema = copy.deepcopy(benchmark_schema)
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "specialist_handoff" not in properties:
        raise ValueError("Benchmark response schema has no specialist_handoff field")
    properties["specialist_handoff"] = {
        "oneOf": [
            {"type": "null"},
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "agent": {
                        "type": "string",
                        "enum": [
                            "data_intelligence",
                            "analytics_modelling",
                            "insights_visualization",
                        ],
                    },
                    "objective": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "context": {"type": "object"},
                },
                "required": ["agent", "objective", "context"],
            },
        ]
    }
    return schema


def _flatten_schema_errors(errors: list[Any]) -> list[Any]:
    pending = list(errors)
    flattened = []
    while pending:
        error = pending.pop()
        if error.context:
            pending.extend(error.context)
        else:
            flattened.append(error)
    return sorted(
        flattened,
        key=lambda error: (list(map(str, error.absolute_path)), error.message),
    )


def _parse_one_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in model output")
    value, end = decoder.raw_decode(text[start:])
    if text[start + end :].strip():
        raise ValueError("Unexpected non-whitespace text follows the JSON object")
    return value


def _input_identifiers(case: dict[str, Any]) -> tuple[set[int], set[str]]:
    allowed_numbers: set[int] = set()
    allowed_identifiers: set[str] = set()
    source = {
        "prompt": case.get("prompt", ""),
        "tool_result_fixture": case.get("tool_result_fixture"),
        "fixtures": case.get("fixtures", {}),
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True)
    allowed_numbers.update(
        int(value) for value in _NUMBER_PATTERN.findall(encoded) if value.isdigit()
    )
    for match in _DATASET_REFERENCE_PATTERN.finditer(encoded):
        allowed_identifiers.add(match.group(1))
    return allowed_numbers, allowed_identifiers


def _score_case(
    case: dict[str, Any],
    output: Any,
    *,
    response_validator: Any,
    tool_by_name: dict[str, dict[str, Any]],
    specialist_validator: Any | None = None,
    delegation_validator: Any | None = None,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    errors: list[str] = []
    if not isinstance(output, dict):
        return {
            "checks": {"structured_output_valid": False},
            "applicable_checks": ["structured_output_valid"],
            "failure_classifications": ["invalid_response_schema"],
            "errors": ["Parsed model output is not a JSON object"],
            "response_schema_errors": [],
            "passed": False,
            "tool_call_count": 0,
            "tool_calls_with_valid_arguments": 0,
            "unregistered_tool_names": [],
            "out_of_scope_dataset_file_references": [],
            "unsupported_claim_heuristic": True,
        }

    try:
        top_level_schema_errors = list(response_validator.iter_errors(output))
        schema_errors = _flatten_schema_errors(top_level_schema_errors)
        checks["structured_output_valid"] = not top_level_schema_errors
        response_schema_errors = [
            {
                "instance_path": "/" + "/".join(map(str, error.absolute_path)),
                "schema_path": "/" + "/".join(map(str, error.absolute_schema_path)),
                "validator": error.validator,
                "message": error.message,
            }
            for error in schema_errors[:64]
        ]
        if schema_errors:
            errors.extend(
                f"Response schema at {item['instance_path'] or '/'}: "
                f"{item['validator']}: {item['message']}"
                for item in response_schema_errors
            )
    except Exception as error:
        checks["structured_output_valid"] = False
        response_schema_errors = []
        errors.append(f"Response schema: {type(error).__name__}")

    calls = output.get("tool_calls")
    if not isinstance(calls, list):
        calls = []
    names = [
        call.get("name")
        for call in calls
        if isinstance(call, dict) and isinstance(call.get("name"), str)
    ]
    allowed_names = set(case.get("tools", []))
    unregistered_names = sorted(
        {name for name in names if name not in tool_by_name}
    )
    checks["all_tool_names_registered"] = not unregistered_names
    checks["tools_within_case_allowlist"] = all(
        name in allowed_names for name in names
    )

    calls_with_valid_arguments = 0
    argument_errors: list[dict[str, str]] = []
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            argument_errors.append(
                {"index": str(index), "error": "tool call is not an object"}
            )
            continue
        name = call.get("name")
        arguments = call.get("arguments")
        schema = tool_by_name.get(name) if isinstance(name, str) else None
        if schema is None or not isinstance(arguments, dict):
            argument_errors.append(
                {"index": str(index), "error": "unknown tool or missing arguments object"}
            )
            continue
        try:
            from jsonschema import Draft202012Validator

            Draft202012Validator(schema).validate(arguments)
            calls_with_valid_arguments += 1
        except Exception as error:
            argument_errors.append(
                {"index": str(index), "error": type(error).__name__}
            )
    checks["all_tool_arguments_valid"] = not argument_errors
    if argument_errors:
        errors.extend(
            f"Tool arguments at index {item['index']}: {item['error']}"
            for item in argument_errors
        )

    response = output.get("response")
    response = response if isinstance(response, str) else ""
    expected = case.get("expected", {})
    applicable = [
        "structured_output_valid",
        "all_tool_names_registered",
        "tools_within_case_allowlist",
        "all_tool_arguments_valid",
    ]
    expected_decision = expected.get("decision")
    checks["expected_decision_correct"] = (
        output.get("decision") == expected_decision
    )
    applicable.append("expected_decision_correct")

    if "tool_calls_exact" in expected:
        actual_sequence = [
            {"name": call.get("name"), "arguments": call.get("arguments")}
            for call in calls
            if isinstance(call, dict)
        ]
        checks["expected_tool_sequence_correct"] = (
            actual_sequence == expected["tool_calls_exact"]
        )
        applicable.append("expected_tool_sequence_correct")
    forbidden = set(expected.get("forbidden_tool_names", []))
    if forbidden:
        checks["forbidden_tools_not_proposed"] = not any(
            name in forbidden for name in names
        )
        applicable.append("forbidden_tools_not_proposed")
    for name, maximum in expected.get("max_tool_calls_by_name", {}).items():
        check_name = f"tool_call_limit_{name}"
        checks[check_name] = names.count(name) <= maximum
        applicable.append(check_name)
    if expected.get("clarification_required"):
        questions = output.get("clarification_questions")
        checks["clarification_required"] = (
            output.get("decision") == "clarify"
            and isinstance(questions, list)
            and bool(questions)
        )
        applicable.append("clarification_required")
    handoff = output.get("specialist_handoff")
    expected_agent = expected.get("specialist_agent")
    if expected_agent is not None:
        validator = delegation_validator or specialist_validator
        check_name = (
            "specialist_delegation_contract_valid"
            if delegation_validator is not None
            else "specialist_input_contract_valid"
        )
        if validator is None:
            checks[check_name] = False
            errors.append("Specialist delegation contract validator is unavailable")
        else:
            try:
                validator.validate(handoff)
                checks[check_name] = True
            except Exception as error:
                checks[check_name] = False
                errors.append(
                    f"Specialist delegation contract: {type(error).__name__}"
                )
        checks["specialist_route_correct"] = (
            isinstance(handoff, dict)
            and handoff.get("agent") == expected_agent
        )
        applicable.extend([check_name, "specialist_route_correct"])
    else:
        checks["no_unrequested_specialist_handoff"] = handoff is None
        applicable.append("no_unrequested_specialist_handoff")

    output_text = " ".join(
        [
            output.get("intent", "") if isinstance(output.get("intent"), str) else "",
            " ".join(
                item for item in output.get("plan", []) if isinstance(item, str)
            )
            if isinstance(output.get("plan"), list)
            else "",
            response,
        ]
    )
    failure_checks = set(expected.get("safety_checks", []))
    checks["no_false_execution_claim"] = (
        _EXECUTION_CLAIM_PATTERN.search(output_text) is None
    )
    if "no_false_execution_claim" in failure_checks:
        applicable.append("no_false_execution_claim")
    if "no_tool_call" in failure_checks:
        checks["no_tool_call"] = not calls
        applicable.append("no_tool_call")
    if "no_causal_claim" in failure_checks:
        checks["no_causal_claim"] = _CAUSAL_CLAIM_PATTERN.search(response) is None
        applicable.append("no_causal_claim")
    if "no_success_claim_after_failure" in failure_checks:
        checks["no_success_claim_after_failure"] = (
            _SUCCESS_AFTER_FAILURE_PATTERN.search(response) is None
        )
        applicable.append("no_success_claim_after_failure")
    if "numeric_claims_grounded" in failure_checks:
        allowed_numbers, _ = _input_identifiers(case)
        claimed_numbers = {
            token.rstrip("%")
            for token in _NUMBER_PATTERN.findall(response)
        }
        checks["numeric_claims_grounded"] = all(
            token in {str(number) for number in allowed_numbers}
            for token in claimed_numbers
        )
        applicable.append("numeric_claims_grounded")
    _, allowed_ids = _input_identifiers(case)
    observed_refs = sorted(
        set(match.group(1) for match in _DATASET_REFERENCE_PATTERN.finditer(output_text))
    )
    out_of_scope_refs = sorted(set(observed_refs) - allowed_ids)
    checks["no_hallucinated_dataset_file_reference"] = not out_of_scope_refs
    applicable.append("no_hallucinated_dataset_file_reference")

    failures = [name for name in applicable if not checks.get(name, False)]
    classifications: set[str] = set()
    for name in failures:
        if name == "structured_output_valid":
            classifications.add("invalid_response_schema")
        elif name == "all_tool_names_registered":
            classifications.add("unregistered_tool")
        elif name == "all_tool_arguments_valid":
            classifications.add("tool_arguments_invalid")
        elif name == "expected_decision_correct":
            classifications.add("incorrect_decision")
        elif name == "expected_tool_sequence_correct":
            classifications.add("incorrect_tool_selection")
        elif name in {
            "no_false_execution_claim",
            "no_causal_claim",
            "no_success_claim_after_failure",
            "numeric_claims_grounded",
        }:
            classifications.add("unsupported_claim_heuristic")
        elif name == "no_hallucinated_dataset_file_reference":
            classifications.add("hallucinated_reference_heuristic")
        elif name == "specialist_route_correct":
            classifications.add("incorrect_specialist_routing")
        elif name == "specialist_delegation_contract_valid":
            classifications.add("invalid_specialist_delegation")
        elif name == "specialist_input_contract_valid":
            classifications.add("invalid_specialist_input")
        else:
            classifications.add("contract_or_policy_violation")
    unsupported_heuristic = any(
        not checks.get(name, True)
        for name in (
            "no_false_execution_claim",
            "no_causal_claim",
            "no_success_claim_after_failure",
            "numeric_claims_grounded",
        )
    )
    return {
        "checks": checks,
        "applicable_checks": applicable,
        "failure_classifications": sorted(classifications),
        "errors": errors,
        "response_schema_errors": response_schema_errors,
        "passed": not failures,
        "tool_call_count": len(calls),
        "tool_calls_with_valid_arguments": calls_with_valid_arguments,
        "tool_argument_errors": argument_errors,
        "unregistered_tool_names": unregistered_names,
        "out_of_scope_dataset_file_references": out_of_scope_refs,
        "dataset_file_reference_count": len(observed_refs),
        "unsupported_claim_heuristic": unsupported_heuristic,
    }


def _runtime_metadata() -> dict[str, Any]:
    import importlib.metadata as metadata
    import torch

    versions = {}
    for name in PINNED_PACKAGE_VERSIONS:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    cuda_available = torch.cuda.is_available()
    gpu_name = None
    gpu_total_memory_bytes = None
    gpu_free_memory_bytes = None
    if cuda_available:
        gpu = torch.cuda.get_device_properties(0)
        gpu_name = gpu.name
        gpu_total_memory_bytes = int(gpu.total_memory)
        gpu_free_memory_bytes = int(torch.cuda.mem_get_info(0)[0])
        gpu_compute_capability = list(torch.cuda.get_device_capability(0))
    else:
        gpu_compute_capability = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": torch.__version__,
        "torch_cuda_build": torch.version.cuda,
        "cuda_available": cuda_available,
        "gpu_name": gpu_name,
        "gpu_compute_capability": gpu_compute_capability,
        "gpu_total_memory_bytes": gpu_total_memory_bytes,
        "gpu_free_memory_bytes": gpu_free_memory_bytes,
        "package_versions": versions,
        "captured_at_utc": _utc_now(),
    }


def _torch_version_supported(version: str) -> bool:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        return False
    version_tuple = tuple(int(part) for part in match.groups())
    return (2, 6, 0) <= version_tuple < (3, 0, 0)


class RuntimePreflightError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        diagnostics: dict[str, Any],
    ) -> None:
        diagnostics["status"] = "failed"
        diagnostics["failure_category"] = category
        diagnostics["failure_message"] = message
        self.diagnostics = diagnostics
        super().__init__(
            f"{category}: {message}; diagnostics="
            f"{json.dumps(diagnostics, sort_keys=True, default=str)}"
        )


def _preflight_runtime(
    *,
    torch_module: Any = None,
    bitsandbytes_module: Any = None,
    transformers_module: Any = None,
    package_metadata: Any = None,
    cextension_module: Any = None,
    cuda_specs_module: Any = None,
) -> dict[str, Any]:
    import importlib.metadata

    if torch_module is None:
        import torch as torch_module
    diagnostics: dict[str, Any] = {
        "status": "running",
        "python_version": platform.python_version(),
        "pytorch_version": str(getattr(torch_module, "__version__", "unknown")),
        "torch_cuda_version": getattr(
            getattr(torch_module, "version", None), "cuda", None
        ),
        "cuda_available": False,
        "gpu_name": None,
        "gpu_compute_capability": None,
        "gpu_total_memory_bytes": None,
        "gpu_free_memory_bytes": None,
        "bitsandbytes_version": None,
        "bitsandbytes_library_path": None,
        "bitsandbytes_library_compiled_with_cuda": None,
        "detected_cuda_binary_version": None,
        "available_cuda_binary_versions": [],
        "nf4_quantization_probe": "not_run",
    }

    def fail(category: str, message: str) -> None:
        raise RuntimePreflightError(category, message, diagnostics)

    package_metadata = package_metadata or importlib.metadata
    try:
        diagnostics["bitsandbytes_version"] = package_metadata.version(
            "bitsandbytes"
        )
    except package_metadata.PackageNotFoundError:
        diagnostics["bitsandbytes_version"] = None

    torch_version_cuda = diagnostics["torch_cuda_version"]
    if torch_version_cuda is None:
        fail(
            "no_cuda_enabled_pytorch",
            "The installed PyTorch build has no CUDA runtime",
        )
    if not _torch_version_supported(diagnostics["pytorch_version"]):
        fail(
            "unsupported_pytorch_version",
            "Expected PyTorch >=2.6.0,<3.0.0; found "
            f"{diagnostics['pytorch_version']}",
        )
    diagnostics["cuda_available"] = bool(torch_module.cuda.is_available())
    if not diagnostics["cuda_available"]:
        fail(
            "no_nvidia_gpu",
            "CUDA-enabled PyTorch is installed but no usable NVIDIA GPU is visible",
        )

    try:
        gpu = torch_module.cuda.get_device_properties(0)
        capability = tuple(torch_module.cuda.get_device_capability(0))
        free_memory, total_memory = torch_module.cuda.mem_get_info(0)
    except Exception as error:
        fail("no_nvidia_gpu", f"Could not inspect CUDA device 0: {error}")
    diagnostics.update(
        {
            "gpu_name": getattr(gpu, "name", None),
            "gpu_compute_capability": list(capability),
            "gpu_total_memory_bytes": int(total_memory),
            "gpu_free_memory_bytes": int(free_memory),
        }
    )
    if capability < (6, 0):
        fail(
            "incompatible_gpu_architecture",
            "NF4 CUDA inference requires compute capability 6.0 or newer; "
            f"found {capability[0]}.{capability[1]}",
        )

    if diagnostics["bitsandbytes_version"] is None:
        fail("bitsandbytes_package_missing", "bitsandbytes is not installed")
    if diagnostics["bitsandbytes_version"] != PINNED_PACKAGE_VERSIONS[
        "bitsandbytes"
    ]:
        fail(
            "bitsandbytes_version_mismatch",
            "Expected bitsandbytes "
            f"{PINNED_PACKAGE_VERSIONS['bitsandbytes']}; found "
            f"{diagnostics['bitsandbytes_version']}",
        )

    installed: dict[str, str] = {}
    for package, expected in PINNED_PACKAGE_VERSIONS.items():
        try:
            installed[package] = package_metadata.version(package)
        except package_metadata.PackageNotFoundError:
            fail("pinned_package_missing", f"{package} is not installed")
        if installed[package] != expected:
            fail(
                "pinned_package_version_mismatch",
                f"Expected {package}=={expected}; found {installed[package]}",
            )
    diagnostics["package_versions"] = installed

    if bitsandbytes_module is None:
        try:
            import bitsandbytes as bitsandbytes_module
        except Exception as error:
            load_error = f"{type(error).__name__}: {error}"
            if "binary not found" in load_error.lower():
                fail("missing_cuda_binary", load_error)
            fail(
                "bitsandbytes_cuda_binary_load_failure",
                f"Could not import bitsandbytes: {load_error}",
            )
    cextension = getattr(bitsandbytes_module, "cextension", None)
    native_library = getattr(cextension, "lib", None)
    native_handle = getattr(native_library, "_lib", None)
    loaded_path = getattr(native_handle, "_name", None)
    diagnostics["bitsandbytes_library_path"] = (
        str(loaded_path) if loaded_path is not None else None
    )
    diagnostics["bitsandbytes_library_compiled_with_cuda"] = bool(
        getattr(native_library, "compiled_with_cuda", False)
    )
    binary_match = re.search(
        r"libbitsandbytes_cuda(\d+)",
        str(loaded_path or ""),
    )
    if binary_match:
        detected = binary_match.group(1)
        diagnostics["detected_cuda_binary_version"] = (
            f"{detected[:-1]}.{detected[-1]}"
        )

    expected_cuda_version = "".join(torch_version_cuda.split(".")[:2])
    expected_cuda_version = expected_cuda_version.ljust(2, "0")
    if len(expected_cuda_version) == 2:
        expected_cuda_version = (
            expected_cuda_version[0] + expected_cuda_version[1]
        )
    cuda_binary_path = None
    available_versions: list[str] = []
    try:
        if cextension_module is None:
            from bitsandbytes import cextension as cextension_module
        if cuda_specs_module is None:
            from bitsandbytes import cuda_specs as cuda_specs_module

        available_versions = cextension_module.get_available_cuda_binary_versions()
        specs = cuda_specs_module.get_cuda_specs()
        if specs is not None:
            expected_cuda_version = specs.cuda_version_string
            cuda_binary_path = cextension_module.get_cuda_bnb_library_path(specs)
    except Exception as error:
        fail(
            "bitsandbytes_cuda_binary_inspection_failure",
            f"Could not inspect bitsandbytes CUDA binaries: {error}",
        )
    diagnostics["available_cuda_binary_versions"] = available_versions
    diagnostics["expected_cuda_binary_version"] = (
        f"{expected_cuda_version[:-1]}.{expected_cuda_version[-1]}"
        if expected_cuda_version.isdigit() and len(expected_cuda_version) >= 2
        else expected_cuda_version
    )
    diagnostics["expected_cuda_binary_path"] = (
        str(cuda_binary_path) if cuda_binary_path is not None else None
    )
    if cuda_binary_path is None or not cuda_binary_path.is_file():
        fail(
            "missing_cuda_binary",
            "No bitsandbytes CUDA binary is installed for PyTorch CUDA "
            f"{torch_version_cuda}; expected {cuda_binary_path}",
        )
    if not diagnostics["bitsandbytes_library_compiled_with_cuda"]:
        fail(
            "bitsandbytes_cpu_only_fallback",
            "bitsandbytes loaded a CPU-only native library instead of CUDA",
        )
    if (
        diagnostics["detected_cuda_binary_version"]
        != diagnostics["expected_cuda_binary_version"]
    ):
        fail(
            "bitsandbytes_cuda_binary_mismatch",
            "Loaded bitsandbytes CUDA binary does not match the PyTorch CUDA "
            "runtime",
        )

    if transformers_module is None:
        from transformers import BitsAndBytesConfig
    else:
        BitsAndBytesConfig = transformers_module.BitsAndBytesConfig
    try:
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch_module.float16,
        )
    except Exception as error:
        fail(
            "nf4_configuration_failure",
            f"Could not initialize NF4 configuration: {error}",
        )
    if not (
        quantization.load_in_4bit
        and quantization.bnb_4bit_quant_type == "nf4"
        and quantization.bnb_4bit_use_double_quant
        and quantization.bnb_4bit_compute_dtype is torch_module.float16
    ):
        fail("nf4_configuration_failure", "NF4 configuration is inconsistent")

    try:
        probe = torch_module.randn(128, device="cuda", dtype=torch_module.float16)
        quantized, quant_state = bitsandbytes_module.functional.quantize_4bit(
            probe,
            compress_statistics=True,
            quant_type="nf4",
        )
        restored = bitsandbytes_module.functional.dequantize_4bit(
            quantized,
            quant_state=quant_state,
        )
        torch_module.cuda.synchronize()
        if quantized.device.type != "cuda":
            fail(
                "nf4_kernel_execution_failure",
                "NF4 quantization output is not on CUDA",
            )
        if restored.device.type != "cuda":
            fail(
                "nf4_kernel_execution_failure",
                "NF4 dequantization output is not on CUDA",
            )
        if not torch_module.isfinite(quantized).all().item() or not torch_module.isfinite(
            restored
        ).all().item():
            fail(
                "nf4_kernel_execution_failure",
                "NF4 quantization/dequantization produced non-finite values",
            )
        diagnostics["nf4_quantization_probe"] = "passed"
        diagnostics["cuda_synchronization"] = "passed"
    except RuntimePreflightError:
        raise
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        architecture_markers = (
            "no kernel image is available",
            "invalid device function",
            "not compatible with the current gpu",
            "unsupported gpu architecture",
            "requires sm_",
        )
        category = (
            "incompatible_gpu_architecture"
            if any(marker in message.lower() for marker in architecture_markers)
            else "nf4_kernel_execution_failure"
        )
        diagnostics["nf4_quantization_probe"] = "failed"
        fail(category, f"NF4 CUDA quantize/dequantize probe failed: {message}")

    diagnostics["status"] = "passed"
    diagnostics["failure_category"] = None
    diagnostics["peft_and_trl"] = (
        "not used or installed for inference-only baseline evaluation"
    )
    return diagnostics


def _active_run(
    bundle_sha256: str,
    benchmark_sha256: str,
    evaluator_identity: str = (
        f"{EVALUATOR_VERSION}:{MODEL_RESPONSE_SCHEMA_VERSION}"
    ),
) -> tuple[str, Path, dict[str, Any]]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    pointer_path = OUTPUT_ROOT / "active_run.json"
    if pointer_path.is_file():
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            run_dir = OUTPUT_ROOT / pointer["run_id"]
            progress_path = run_dir / "progress_checkpoint.json"
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            if (
                pointer.get("bundle_sha256") == bundle_sha256
                and pointer.get("benchmark_sha256") == benchmark_sha256
                and pointer.get("evaluator_identity") == evaluator_identity
                and progress.get("evaluator_identity") == evaluator_identity
                and progress.get("execution_status") in {"running", "incomplete"}
            ):
                return pointer["run_id"], run_dir, progress
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            pass
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    run_dir = OUTPUT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    progress = {
        "checkpoint_version": "insightos-qwen3-8b-progress-v1",
        "evaluator_identity": evaluator_identity,
        "run_id": run_id,
        "bundle_sha256": bundle_sha256,
        "benchmark_sha256": benchmark_sha256,
        "expected_case_ids": [],
        "execution_status": "running",
        "started_at_utc": _utc_now(),
        "cases": {},
        "events": [],
        "run_errors": [],
    }
    _write_json_atomic(run_dir / "progress_checkpoint.json", progress)
    _write_json_atomic(
        pointer_path,
        {
            "run_id": run_id,
            "bundle_sha256": bundle_sha256,
            "benchmark_sha256": benchmark_sha256,
            "evaluator_identity": evaluator_identity,
        },
    )
    return run_id, run_dir, progress


def _case_result_rows(
    cases: list[dict[str, Any]],
    progress: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        entry = progress["cases"].get(case["id"])
        if not isinstance(entry, dict):
            rows.append(
                {
                    "case_id": case["id"],
                    "category": case["category"],
                    "status": "not_run",
                    "passed": False,
                    "checks": {},
                    "applicable_checks": [],
                    "failure_classifications": ["inference_not_completed"],
                    "errors": [],
                    "raw_output_path": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "generation_latency_ms": None,
                }
            )
            continue
        attempts = entry.get("attempts", [])
        successful = next(
            (
                attempt
                for attempt in reversed(attempts)
                if attempt.get("status") == "generated"
            ),
            None,
        )
        if successful is None:
            latest = attempts[-1] if attempts else {}
            rows.append(
                {
                    "case_id": case["id"],
                    "category": case["category"],
                    "status": "inference_failed",
                    "passed": False,
                    "checks": {},
                    "applicable_checks": [],
                    "failure_classifications": ["inference_failed"],
                    "errors": [latest["error"]] if latest.get("error") else [],
                    "raw_output_path": None,
                    "input_tokens": latest.get("input_tokens"),
                    "output_tokens": None,
                    "generation_latency_ms": latest.get("generation_latency_ms"),
                }
            )
            continue
        score = successful["score"]
        rows.append(
            {
                "case_id": case["id"],
                "category": case["category"],
                "status": "evaluated",
                "passed": score["passed"],
                "checks": score["checks"],
                "applicable_checks": score["applicable_checks"],
                "failure_classifications": score["failure_classifications"],
                "errors": score["errors"],
                "response_schema_errors": score.get(
                    "response_schema_errors", []
                ),
                "raw_output_path": successful["raw_output_path"],
                "input_tokens": successful.get("input_tokens"),
                "output_tokens": successful.get("output_tokens"),
                "generation_latency_ms": successful.get("generation_latency_ms"),
                "tool_call_count": score["tool_call_count"],
                "tool_calls_with_valid_arguments": score[
                    "tool_calls_with_valid_arguments"
                ],
                "tool_argument_errors": score.get("tool_argument_errors", []),
                "unregistered_tool_names": score.get(
                    "unregistered_tool_names", []
                ),
                "out_of_scope_dataset_file_references": score.get(
                    "out_of_scope_dataset_file_references", []
                ),
                "dataset_file_reference_count": score.get(
                    "dataset_file_reference_count", 0
                ),
                "unsupported_claim_heuristic": score.get(
                    "unsupported_claim_heuristic", False
                ),
            }
        )
    return rows


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "status": "available" if denominator else "unavailable_no_applicable_observations",
    }


def _aggregate_metrics(
    cases: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    row_by_id = {row["case_id"]: row for row in rows}
    generated = [row for row in rows if row["status"] == "evaluated"]
    expected_calls = [
        case
        for case in cases
        if "tool_calls_exact" in case.get("expected", {})
    ]
    exact_tool_selection = sum(
        bool(row_by_id[case["id"]]["checks"].get("expected_tool_sequence_correct"))
        for case in expected_calls
    )
    clarification_cases = [
        case
        for case in cases
        if case.get("expected", {}).get("decision") == "clarify"
    ]
    clarifications_correct = sum(
        bool(row_by_id[case["id"]]["checks"].get("clarification_required"))
        for case in clarification_cases
    )
    specialist_cases = [
        case for case in cases if case.get("expected", {}).get("specialist_agent")
    ]
    specialist_correct = sum(
        bool(row_by_id[case["id"]]["checks"].get("specialist_route_correct"))
        for case in specialist_cases
    )
    specialist_handoff_correct = sum(
        bool(
            row_by_id[case["id"]]["checks"].get(
                "specialist_route_correct"
                if case.get("expected", {}).get("specialist_agent") is not None
                else "no_unrequested_specialist_handoff"
            )
        )
        for case in cases
    )
    structured_valid = sum(
        bool(row_by_id[case["id"]]["checks"].get("structured_output_valid"))
        for case in cases
    )
    all_calls = sum(row.get("tool_call_count", 0) for row in generated)
    valid_calls = sum(
        row.get("tool_calls_with_valid_arguments", 0) for row in generated
    )
    unregistered_calls = sum(
        len(row.get("unregistered_tool_names", [])) for row in generated
    )
    reference_count = sum(
        row.get("dataset_file_reference_count", 0) for row in generated
    )
    out_of_scope_reference_count = sum(
        len(row.get("out_of_scope_dataset_file_references", []))
        for row in generated
    )
    response_count = len(generated)
    unsupported_cases = sum(
        row.get("unsupported_claim_heuristic", False) for row in generated
    )
    numeric_rows = [
        row
        for row in generated
        if "numeric_claims_grounded" in row.get("applicable_checks", [])
    ]
    latencies = [
        row["generation_latency_ms"]
        for row in generated
        if isinstance(row.get("generation_latency_ms"), (int, float))
    ]
    input_tokens = [
        row["input_tokens"]
        for row in rows
        if isinstance(row.get("input_tokens"), int)
    ]
    output_tokens = [
        row["output_tokens"]
        for row in generated
        if isinstance(row.get("output_tokens"), int)
    ]
    category_metrics: dict[str, Any] = {}
    categories = sorted({case["category"] for case in cases})
    for category in categories:
        category_cases = [case for case in cases if case["category"] == category]
        passed = sum(bool(row_by_id[case["id"]]["passed"]) for case in category_cases)
        category_metrics[category] = {
            **_rate(passed, len(category_cases)),
            "case_ids": [case["id"] for case in category_cases],
        }
    failure_counts: dict[str, int] = {}
    for row in rows:
        for failure in row.get("failure_classifications", []):
            failure_counts[failure] = failure_counts.get(failure, 0) + 1
    return {
        "expected_cases": len(cases),
        "inference_completed_cases": len(generated),
        "inference_failed_or_not_run_cases": len(cases) - len(generated),
        "overall_automated_pass_rate": _rate(
            sum(bool(row["passed"]) for row in rows),
            len(cases),
        ),
        "tool_selection_accuracy": _rate(
            exact_tool_selection,
            len(expected_calls),
        ),
        "tool_argument_schema_validity": _rate(valid_calls, all_calls),
        "structured_output_validity": _rate(structured_valid, len(cases)),
        "expected_decision_accuracy": _rate(
            sum(
                bool(row_by_id[case["id"]]["checks"].get("expected_decision_correct"))
                for case in cases
            ),
            len(cases),
        ),
        "required_clarification_accuracy": _rate(
            clarifications_correct,
            len(clarification_cases),
        ),
        "specialist_routing_accuracy": _rate(
            specialist_correct,
            len(specialist_cases),
        ),
        "specialist_handoff_policy_accuracy": _rate(
            specialist_handoff_correct,
            len(cases),
        ),
        "unsupported_claim_heuristic_rate": {
            **_rate(unsupported_cases, response_count),
            "limitation": (
                "Automated false-execution, causal-language, and numeric-grounding "
                "heuristics only; this does not detect all unsupported factual or "
                "analytical claims. Semantic review is required."
            ),
        },
        "numeric_grounding_heuristic_rate": {
            **_rate(
                sum(
                    row["checks"].get("numeric_claims_grounded") is True
                    for row in numeric_rows
                ),
                len(numeric_rows),
            ),
            "limitation": (
                "Checks only whether numeric literals in the response occur in "
                "the case prompt or fixtures. It does not validate calculations, "
                "units, context, causal meaning, or qualitative factual claims."
            ),
        },
        "unregistered_tool_name_rate": _rate(unregistered_calls, all_calls),
        "out_of_scope_dataset_file_reference_rate": _rate(
            out_of_scope_reference_count,
            reference_count,
        ),
        "semantic_hallucination_rate": {
            "numerator": None,
            "denominator": None,
            "value": None,
            "status": "unavailable_requires_human_semantic_review",
        },
        "latency_ms": {
            "count": len(latencies),
            "mean": statistics.fmean(latencies) if latencies else None,
            "median": statistics.median(latencies) if latencies else None,
            "p95": (
                sorted(latencies)[max(0, math.ceil(0.95 * len(latencies)) - 1)]
                if latencies
                else None
            ),
            "measurement": "torch-synchronized model.generate duration per case",
        },
        "token_usage": {
            "input_tokens_total": sum(input_tokens) if input_tokens else None,
            "input_tokens_count": len(input_tokens),
            "output_tokens_total": sum(output_tokens) if output_tokens else None,
            "output_tokens_count": len(output_tokens),
        },
        "failure_counts_by_classification": dict(sorted(failure_counts.items())),
        "per_category_automated_pass_rate": category_metrics,
        "human_semantic_review_status": "pending",
    }


def _prepare_attempt(
    *,
    run_dir: Path,
    case: dict[str, Any],
    progress: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    entry = progress["cases"].setdefault(case["id"], {"attempts": []})
    attempts = entry["attempts"]
    for prior in attempts:
        if prior.get("status") == "in_progress":
            prior["status"] = "interrupted"
            prior["error"] = {
                "type": "InterruptedRun",
                "message": "Execution stopped before this case was checkpointed.",
            }
    attempt_number = len(attempts) + 1
    attempt = {
        "attempt": attempt_number,
        "status": "in_progress",
        "started_at_utc": _utc_now(),
        "input_tokens": None,
        "output_tokens": None,
        "generation_latency_ms": None,
        "raw_output_path": None,
    }
    attempts.append(attempt)
    raw_path = (
        run_dir
        / "raw_outputs"
        / case["id"]
        / f"attempt-{attempt_number:03d}.json"
    )
    progress["events"].append(
        {
            "timestamp_utc": _utc_now(),
            "case_id": case["id"],
            "attempt": attempt_number,
            "event": "inference_started",
        }
    )
    _write_json_atomic(run_dir / "progress_checkpoint.json", progress)
    return attempt, raw_path


def _write_run_artifacts(
    *,
    run_dir: Path,
    run_id: str,
    bundle: dict[str, Any],
    bundle_bytes: bytes,
    progress: dict[str, Any],
    model_metadata: dict[str, Any] | None,
    runtime_metadata: dict[str, Any] | None,
    run_error: dict[str, str] | None,
) -> Path:
    benchmark = bundle["benchmark"]
    cases = benchmark["cases"]
    case_rows = _case_result_rows(cases, progress)
    metrics = _aggregate_metrics(cases, case_rows)
    generated_count = metrics["inference_completed_cases"]
    execution_status = (
        "executed"
        if generated_count == len(cases)
        else "incomplete"
    )
    completed_at = _utc_now()
    report = {
        "report_version": "insightos-qwen3-8b-baseline-run-v2",
        "evaluator_version": EVALUATOR_VERSION,
        "model_response_schema_version": MODEL_RESPONSE_SCHEMA_VERSION,
        "evaluator_identity": progress.get("evaluator_identity"),
        "status": "executed" if execution_status == "executed" else "pending",
        "execution_status": execution_status,
        "run_id": run_id,
        "started_at_utc": progress.get("started_at_utc"),
        "completed_at_utc": completed_at,
        "model": model_metadata,
        "runtime": runtime_metadata,
        "generation_settings": GENERATION_SETTINGS,
        "bundle_sha256": _sha256(bundle_bytes),
        "benchmark_version": benchmark["benchmark_version"],
        "benchmark_sha256": bundle["benchmark_sha256"],
        "benchmark_file_sha256": bundle["benchmark_file_sha256"],
        "benchmark_dataset_sha256": bundle["benchmark_dataset_sha256"],
        "response_schema_source": (
            "benchmark response schema adapted for the model-facing delegation DTO"
        ),
        "model_response_schema_sha256": _sha256(
            _canonical_json(
                _model_facing_response_schema(benchmark["response_schema"])
            )
        ),
        "tool_schemas_sha256": bundle["tool_schemas_sha256"],
        "structured_output_contracts_sha256": bundle[
            "structured_output_contracts_sha256"
        ],
        "metrics": metrics,
        "run_error": run_error,
        "run_errors": progress.get("run_errors", []),
        "limitations": [
            "The 12-case benchmark is a smoke suite, not a statistically powered benchmark.",
            "This is the non-thinking baseline (enable_thinking=False), not a complete evaluation of Qwen3 reasoning-mode performance.",
            "Numeric grounding checks literal membership in the prompt or fixtures; they do not validate calculations, units, context, causal meaning, or qualitative factual claims.",
            "Automated unsupported-claim checks do not detect all unsupported factual or analytical claims; human semantic review is required.",
            "Tool proposals are never executed; no application or database side effects occur.",
            "A generated but invalid response is still a recorded model output and is scored as a failure.",
        ],
        "training_authorized": False,
    }
    progress["execution_status"] = execution_status
    progress["completed_at_utc"] = completed_at
    _write_json_atomic(run_dir / "progress_checkpoint.json", progress)

    exports_dir = run_dir / "exports"
    exports_dir.mkdir(exist_ok=True)
    export_number = len(
        [path for path in exports_dir.iterdir() if path.is_dir()]
    ) + 1
    snapshot_dir = exports_dir / f"export-{export_number:03d}"
    snapshot_dir.mkdir(exist_ok=False)
    _write_json_exclusive(snapshot_dir / "baseline_run_report.json", report)
    _write_json_exclusive(snapshot_dir / "baseline_bundle.json", bundle)
    _write_json_exclusive(
        snapshot_dir / "environment_manifest.json",
        bundle["kaggle_environment"],
    )
    _write_json_exclusive(snapshot_dir / "benchmark.json", benchmark)
    _write_json_exclusive(snapshot_dir / "aggregate_metrics.json", metrics)
    _write_json_exclusive(snapshot_dir / "model_metadata.json", model_metadata or {})
    _write_json_exclusive(snapshot_dir / "runtime_metadata.json", runtime_metadata or {})
    _write_jsonl_exclusive(snapshot_dir / "case_results.jsonl", case_rows)

    files = []
    root_files = [
        path
        for path in run_dir.rglob("*")
        if path.is_file()
        and not path.name.endswith(".tmp")
        and exports_dir not in path.parents
    ]
    snapshot_files = [
        path
        for path in snapshot_dir.rglob("*")
        if path.is_file() and not path.name.endswith(".tmp")
    ]
    export_sources = [(path, path.relative_to(run_dir)) for path in root_files]
    export_sources.extend(
        (
            path,
            path.relative_to(snapshot_dir),
        )
        for path in snapshot_files
    )
    for path, relative_path in sorted(
        export_sources, key=lambda item: item[1].as_posix()
    ):
        relative = relative_path.as_posix()
        files.append(
            {
                "path": relative,
                "sha256": _sha256(path.read_bytes()),
                "size_bytes": path.stat().st_size,
            }
        )
    manifest = {
        "manifest_version": "insightos-qwen3-8b-baseline-execution-v2",
        "evaluator_version": EVALUATOR_VERSION,
        "model_response_schema_version": MODEL_RESPONSE_SCHEMA_VERSION,
        "evaluator_identity": progress.get("evaluator_identity"),
        "run_id": run_id,
        "execution_status": execution_status,
        "baseline_status": report["status"],
        "created_at_utc": completed_at,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "tokenizer_id": TOKENIZER_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "bundle_sha256": _sha256(bundle_bytes),
        "benchmark_version": benchmark["benchmark_version"],
        "benchmark_sha256": bundle["benchmark_sha256"],
        "benchmark_file_sha256": bundle["benchmark_file_sha256"],
        "expected_case_ids": [case["id"] for case in cases],
        "generated_case_ids": [
            row["case_id"] for row in case_rows if row["status"] == "evaluated"
        ],
        "file_count": len(files),
        "export_number": export_number,
        "files": files,
        "training_authorized": False,
    }
    manifest_path = snapshot_dir / "execution_manifest.json"
    _write_json_exclusive(manifest_path, manifest)
    archive_path = OUTPUT_ROOT / (
        f"insightos-qwen3-8b-baseline-v1-{run_id}-export-{export_number:03d}.zip"
    )
    with zipfile.ZipFile(
        archive_path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path, relative_path in export_sources:
            archive.write(path, arcname=relative_path.as_posix())
        archive.write(manifest_path, arcname="execution_manifest.json")
    pointer_path = OUTPUT_ROOT / "active_run.json"
    _write_json_atomic(
        pointer_path,
        {
            "run_id": run_id,
            "bundle_sha256": _sha256(bundle_bytes),
            "benchmark_sha256": bundle["benchmark_sha256"],
            "execution_status": execution_status,
            "archive_name": archive_path.name,
        },
    )
    return archive_path


def run_baseline(
    bundle_path: Path = INPUT_BUNDLE_PATH,
    output_root: Path = OUTPUT_ROOT,
) -> Path:
    global OUTPUT_ROOT
    OUTPUT_ROOT = output_root
    bundle: dict[str, Any] = {}
    bundle_bytes = b""
    model_metadata = None
    runtime_metadata = None
    run_error = None
    model = None
    tokenizer = None
    run_dir: Path | None = None
    progress: dict[str, Any] = {}
    run_id = "preflight-" + uuid.uuid4().hex[:8]
    evaluator_identity = f"{EVALUATOR_VERSION}:{MODEL_RESPONSE_SCHEMA_VERSION}"
    try:
        bundle, bundle_bytes = _load_and_validate_bundle(bundle_path)
        benchmark = bundle["benchmark"]
        model_response_schema = _model_facing_response_schema(
            benchmark["response_schema"]
        )
        evaluator_identity = (
            f"{EVALUATOR_VERSION}:{MODEL_RESPONSE_SCHEMA_VERSION}:"
            f"{_sha256(_canonical_json(model_response_schema))}"
        )
        run_id, run_dir, progress = _active_run(
            _sha256(bundle_bytes),
            bundle["benchmark_sha256"],
            evaluator_identity,
        )
        cases = benchmark["cases"]
        progress["expected_case_ids"] = [case["id"] for case in cases]
        progress["execution_status"] = "running"
        import torch
        from jsonschema import Draft202012Validator
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
        )

        if isinstance(PREFLIGHT_RESULT, RuntimePreflightError):
            raise PREFLIGHT_RESULT
        preflight = PREFLIGHT_RESULT or _preflight_runtime()
        runtime_metadata = _runtime_metadata()
        runtime_metadata["preflight"] = preflight
        torch.manual_seed(SEED)
        torch.cuda.manual_seed_all(SEED)
        if (TOKENIZER_ID, TOKENIZER_REVISION) != (MODEL_ID, MODEL_REVISION):
            raise RuntimeError(
                "Model and tokenizer must use the same pinned repository commit"
            )
        from huggingface_hub import HfApi, snapshot_download

        model_snapshot = _resolve_verified_snapshot(
            repo_id=MODEL_ID,
            expected_commit=MODEL_REVISION,
            api=HfApi(),
            snapshot_download_fn=snapshot_download,
        )
        runtime_metadata["model_snapshot_verification"] = (
            model_snapshot["diagnostics"]
        )
        tokenizer_start = time.perf_counter()
        tokenizer = AutoTokenizer.from_pretrained(
            model_snapshot["resolved_snapshot_directory"],
            trust_remote_code=False,
            local_files_only=True,
        )
        tokenizer_load_ms = round((time.perf_counter() - tokenizer_start) * 1000, 3)
        tokenizer_revision = model_snapshot["actual_commit"]
        if not isinstance(tokenizer.chat_template, str) or not tokenizer.chat_template:
            raise RuntimeError("Pinned tokenizer has no chat template")
        template_probe = tokenizer.apply_chat_template(
            [{"role": "user", "content": "Return one JSON object."}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        if not isinstance(template_probe, str) or not template_probe:
            raise RuntimeError("Qwen3 chat-template smoke check failed")

        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        model_start = time.perf_counter()
        model = AutoModelForCausalLM.from_pretrained(
            model_snapshot["resolved_snapshot_directory"],
            trust_remote_code=False,
            local_files_only=True,
            quantization_config=quantization,
            device_map="auto",
            torch_dtype=torch.float16,
        )
        model.eval()
        torch.cuda.synchronize()
        model_load_ms = round((time.perf_counter() - model_start) * 1000, 3)
        model_config_commit = getattr(model.config, "_commit_hash", None)
        if model_config_commit is not None and model_config_commit != MODEL_REVISION:
            raise RuntimeError(
                "Loaded model config commit differs from the verified snapshot"
            )
        model_revision = model_snapshot["actual_commit"]
        model_metadata = {
            "model_id": MODEL_ID,
            "requested_revision": MODEL_REVISION,
            "loaded_revision": model_revision,
            "model_config_name_or_path": model.config._name_or_path,
            "model_config_commit": model_config_commit,
            "verified_snapshot_directory": model_snapshot[
                "resolved_snapshot_directory"
            ],
            "revision_metadata_source": model_snapshot["metadata_source"],
            "adapter_loaded": False,
            "training": False,
            "tokenizer_id": TOKENIZER_ID,
            "tokenizer_requested_revision": TOKENIZER_REVISION,
            "tokenizer_loaded_revision": tokenizer_revision,
            "tokenizer_name_or_path": tokenizer.name_or_path,
            "tokenizer_snapshot_directory": model_snapshot[
                "resolved_snapshot_directory"
            ],
            "tokenizer_load_ms": tokenizer_load_ms,
            "tokenizer_vocab_size": len(tokenizer),
            "chat_template_sha256": _sha256(
                tokenizer.chat_template.encode("utf-8")
            ),
            "thinking_mode": False,
            "model_load_ms": model_load_ms,
            "quantization": {
                "load_in_4bit": True,
                "type": "NF4",
                "double_quantization": True,
                "compute_dtype": "float16",
            },
        }
        tool_by_name = {
            item["function"]["name"]: item["function"]["parameters"]
            for item in bundle["tool_schemas"]
        }
        response_validator = Draft202012Validator(model_response_schema)
        delegation_validator = Draft202012Validator(
            model_response_schema["properties"]["specialist_handoff"]["oneOf"][1]
        )
        for case in cases:
            case_entry = progress["cases"].get(case["id"], {})
            if any(
                attempt.get("status") == "generated"
                for attempt in case_entry.get("attempts", [])
            ):
                continue
            attempt, raw_path = _prepare_attempt(
                run_dir=run_dir,
                case=case,
                progress=progress,
            )
            case_start = time.perf_counter()
            input_token_count = None
            try:
                messages = _make_prompt(
                    case,
                    tool_schemas=bundle["tool_schemas"],
                    response_schema=model_response_schema,
                )
                prompt = tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
                encoded = tokenizer(
                    prompt,
                    return_tensors="pt",
                    truncation=False,
                )
                input_token_count = int(encoded["input_ids"].shape[1])
                if input_token_count > MAX_INPUT_TOKENS:
                    raise ValueError(
                        f"Prompt has {input_token_count} tokens; configured maximum "
                        f"is {MAX_INPUT_TOKENS}"
                    )
                input_device = model.get_input_embeddings().weight.device
                encoded = {
                    name: tensor.to(input_device)
                    for name, tensor in encoded.items()
                }
                torch.cuda.synchronize()
                generation_start = time.perf_counter()
                with torch.inference_mode():
                    generated = model.generate(
                        **encoded,
                        max_new_tokens=MAX_NEW_TOKENS,
                        do_sample=False,
                        use_cache=True,
                        pad_token_id=tokenizer.eos_token_id,
                    )
                torch.cuda.synchronize()
                generation_ms = round(
                    (time.perf_counter() - generation_start) * 1000,
                    3,
                )
                output_token_count = int(
                    generated.shape[1] - encoded["input_ids"].shape[1]
                )
                raw_text = tokenizer.decode(
                    generated[0, encoded["input_ids"].shape[1] :],
                    skip_special_tokens=True,
                ).strip()
                parsed = None
                parse_error = None
                try:
                    parsed = _parse_one_json(raw_text)
                except (ValueError, json.JSONDecodeError) as error:
                    parse_error = _safe_error(error)
                score = _score_case(
                    case,
                    parsed,
                    response_validator=response_validator,
                    tool_by_name=tool_by_name,
                    delegation_validator=delegation_validator,
                ) if parse_error is None else {
                    "checks": {
                        "structured_output_valid": False,
                        "expected_decision_correct": False,
                        "no_false_execution_claim": (
                            _EXECUTION_CLAIM_PATTERN.search(raw_text) is None
                        ),
                    },
                    "applicable_checks": [
                        "structured_output_valid",
                        "expected_decision_correct",
                        "no_false_execution_claim",
                    ],
                    "failure_classifications": ["invalid_json"],
                    "errors": [parse_error["message"]],
                    "response_schema_errors": [],
                    "passed": False,
                    "tool_call_count": 0,
                    "tool_calls_with_valid_arguments": 0,
                    "unregistered_tool_names": [],
                    "out_of_scope_dataset_file_references": [],
                    "dataset_file_reference_count": 0,
                    "unsupported_claim_heuristic": False,
                }
                raw_record = {
                    "case_id": case["id"],
                    "attempt": attempt["attempt"],
                    "generated_at_utc": _utc_now(),
                    "raw_output": raw_text,
                    "parsed_output": parsed,
                    "parse_error": parse_error,
                    "checks": score["checks"],
                    "failure_classifications": score["failure_classifications"],
                    "input_tokens": input_token_count,
                    "output_tokens": output_token_count,
                    "generation_latency_ms": generation_ms,
                }
                _write_json_exclusive(raw_path, raw_record)
                attempt.update(
                    {
                        "status": "generated",
                        "completed_at_utc": _utc_now(),
                        "raw_output_path": raw_path.relative_to(run_dir).as_posix(),
                        "input_tokens": input_token_count,
                        "output_tokens": output_token_count,
                        "generation_latency_ms": generation_ms,
                        "score": score,
                    }
                )
                progress["events"].append(
                    {
                        "timestamp_utc": _utc_now(),
                        "case_id": case["id"],
                        "attempt": attempt["attempt"],
                        "event": "inference_checkpointed",
                        "parse_valid": parse_error is None,
                    }
                )
            except Exception as error:
                attempt.update(
                    {
                        "status": "inference_failed",
                        "completed_at_utc": _utc_now(),
                        "input_tokens": input_token_count,
                        "generation_latency_ms": round(
                            (time.perf_counter() - case_start) * 1000,
                            3,
                        ),
                        "error": _safe_error(error),
                    }
                )
                progress["events"].append(
                    {
                        "timestamp_utc": _utc_now(),
                        "case_id": case["id"],
                        "attempt": attempt["attempt"],
                        "event": "inference_failed",
                        "error_type": type(error).__name__,
                    }
                )
                print(
                    "CASE_FAILED",
                    case["id"],
                    type(error).__name__,
                    _safe_error(error)["message"],
                )
            _write_json_atomic(run_dir / "progress_checkpoint.json", progress)
            print(
                "CASE_CHECKPOINTED",
                case["id"],
                attempt["status"],
                attempt.get("generation_latency_ms"),
                attempt.get("score", {}).get("passed"),
            )
    except Exception as error:
        run_error = _safe_error(error)
        if isinstance(error, SnapshotRevisionVerificationError):
            run_error["diagnostics"] = error.diagnostics
            if runtime_metadata is not None:
                runtime_metadata["model_snapshot_verification"] = (
                    error.diagnostics
                )
        if isinstance(error, RuntimePreflightError):
            runtime_metadata = {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "torch": error.diagnostics.get("pytorch_version"),
                "torch_cuda_build": error.diagnostics.get("torch_cuda_version"),
                "cuda_available": error.diagnostics.get("cuda_available"),
                "gpu_name": error.diagnostics.get("gpu_name"),
                "gpu_compute_capability": error.diagnostics.get(
                    "gpu_compute_capability"
                ),
                "gpu_total_memory_bytes": error.diagnostics.get(
                    "gpu_total_memory_bytes"
                ),
                "gpu_free_memory_bytes": error.diagnostics.get(
                    "gpu_free_memory_bytes"
                ),
                "package_versions": error.diagnostics.get("package_versions", {}),
                "preflight": error.diagnostics,
            }
        if run_dir is None:
            OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
            run_dir = OUTPUT_ROOT / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            progress = {
                "checkpoint_version": "insightos-qwen3-8b-progress-v1",
                "evaluator_identity": evaluator_identity,
                "run_id": run_id,
                "bundle_sha256": _sha256(bundle_bytes) if bundle_bytes else None,
                "benchmark_sha256": bundle.get("benchmark_sha256"),
                "expected_case_ids": [
                    case["id"] for case in bundle.get("benchmark", {}).get("cases", [])
                ],
                "execution_status": "incomplete",
                "started_at_utc": _utc_now(),
                "cases": {},
                "events": [],
                "run_errors": [],
            }
        progress.setdefault("run_errors", []).append(
            {
                "timestamp_utc": _utc_now(),
                **run_error,
            }
        )
        progress["events"].append(
            {
                "timestamp_utc": _utc_now(),
                "event": "run_preflight_or_model_load_failed",
                "error_type": type(error).__name__,
            }
        )
        _write_json_atomic(run_dir / "progress_checkpoint.json", progress)
        print("RUN_FAILED", type(error).__name__, run_error["message"])
    finally:
        if run_dir is None:
            raise RuntimeError("Could not establish a run directory for diagnostics")
        if bundle:
            try:
                if runtime_metadata is None:
                    runtime_metadata = {
                        "captured_at_utc": _utc_now(),
                        "python": platform.python_version(),
                        "platform": platform.platform(),
                        "capture_status": "failed_before_cuda_metadata",
                    }
                archive_path = _write_run_artifacts(
                    run_dir=run_dir,
                    run_id=run_id,
                    bundle=bundle,
                    bundle_bytes=bundle_bytes,
                    progress=progress,
                    model_metadata=model_metadata,
                    runtime_metadata=runtime_metadata,
                    run_error=run_error,
                )
            except FileExistsError as error:
                raise RuntimeError(
                    "Run artifacts already exist; refusing to overwrite the previous export"
                ) from error
        else:
            raise RuntimeError(
                "The pinned baseline bundle could not be validated; no result ZIP was created"
            )
    print("RESULT_ZIP_READY", archive_path.name)
    return archive_path


# %% [RUN_BASELINE]
archive_path = run_baseline()


# %% [FINAL_ARCHIVE_VALIDATION]
def validate_final_archive(archive_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("ZIP contains duplicate member names")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe ZIP member path: {name}")
        if "execution_manifest.json" not in names:
            raise ValueError("ZIP is missing execution_manifest.json")
        manifest = json.loads(archive.read("execution_manifest.json"))
        listed = {item["path"]: item for item in manifest.get("files", [])}
        if set(names) - {"execution_manifest.json"} != set(listed):
            raise ValueError("ZIP contents do not match the execution manifest")
        for name, item in listed.items():
            contents = archive.read(name)
            if (
                len(contents) != item["size_bytes"]
                or _sha256(contents) != item["sha256"]
            ):
                raise ValueError(f"ZIP artifact hash or size mismatch: {name}")
        required = {
            "baseline_bundle.json",
            "benchmark.json",
            "case_results.jsonl",
            "aggregate_metrics.json",
            "model_metadata.json",
            "runtime_metadata.json",
            "baseline_run_report.json",
            "progress_checkpoint.json",
        }
        if not required.issubset(listed):
            raise ValueError("ZIP is missing required baseline artifacts")
        return {
            "filename": archive_path.name,
            "size_bytes": archive_path.stat().st_size,
            "sha256": _sha256(archive_path.read_bytes()),
            "member_count": len(names),
            "members": names,
            "execution_status": manifest.get("execution_status"),
            "run_id": manifest.get("run_id"),
        }


archive_validation = validate_final_archive(archive_path)
print(json.dumps(archive_validation, indent=2, sort_keys=True))
