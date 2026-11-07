from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MILESTONE5_ROOT = BACKEND_ROOT / "training" / "milestone5"
INCOMING_DIR = MILESTONE5_ROOT / "kaggle_results" / "incoming"
VALIDATED_DIR = MILESTONE5_ROOT / "kaggle_results" / "validated"
BUNDLE_PATH = MILESTONE5_ROOT / "qwen3-8b-baseline-bundle-v6.json"
MODEL_ID = "Qwen/Qwen3-8B"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
TOKENIZER_ID = MODEL_ID
TOKENIZER_REVISION = MODEL_REVISION
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
_ARCHIVE_NAME = re.compile(
    r"^insightos-qwen3-8b-baseline-v1-"
    r"(?P<run_id>\d{8}T\d{6}Z-[0-9a-f]{8})-export-(?P<export>\d{3})\.zip$"
)

_REQUIRED_FILES = {
    "baseline_bundle.json",
    "benchmark.json",
    "case_results.jsonl",
    "aggregate_metrics.json",
    "model_metadata.json",
    "runtime_metadata.json",
    "baseline_run_report.json",
    "progress_checkpoint.json",
    "environment_manifest.json",
}


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


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and "\\" not in name
        and ".." not in path.parts
        and not name.endswith("/")
    )


def _require_type(value: Any, expected: type, label: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"{label} has an invalid type")


def _load_expected_bundle() -> tuple[dict[str, Any], bytes]:
    bundle_bytes = BUNDLE_PATH.read_bytes()
    bundle = json.loads(bundle_bytes)
    pending_report_path = MILESTONE5_ROOT / "qwen3-8b-baseline-report-v5.json"
    pending_report = json.loads(pending_report_path.read_text(encoding="utf-8"))
    if pending_report.get("bundle_sha256") != _sha256(bundle_bytes):
        raise ValueError("Local baseline bundle differs from its preparation-time pin")
    if bundle.get("bundle_version") != "insightos-qwen3-8b-baseline-bundle-v6":
        raise ValueError("Local pinned baseline bundle has an unexpected version")
    if bundle.get("model", {}).get("id") != MODEL_ID:
        raise ValueError("Local pinned model identifier is unexpected")
    if bundle.get("model", {}).get("revision") != MODEL_REVISION:
        raise ValueError("Local pinned model revision is unexpected")
    environment_path = MILESTONE5_ROOT / "kaggle-environment-v4.json"
    environment_bytes = environment_path.read_bytes()
    if (
        bundle.get("kaggle_environment") != json.loads(environment_bytes)
        or bundle.get("kaggle_environment_sha256") != _sha256(environment_bytes)
        or bundle["kaggle_environment"]["packages"].get("bitsandbytes")
        != "0.48.2"
    ):
        raise ValueError("Local Kaggle environment manifest differs from its pin")
    return bundle, bundle_bytes


def _validate_result_rows(
    data: bytes,
    *,
    expected_cases: list[dict[str, Any]],
    artifacts: dict[str, bytes],
    bundle: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as error:
        raise RuntimeError(
            "Local result validation requires jsonschema==4.26.0; install it "
            "in the validation environment, not the backend runtime."
        ) from error

    rows = []
    for line_number, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"case_results.jsonl has a blank line at {line_number}")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"case_results.jsonl line {line_number} is invalid JSON"
            ) from error
        _require_type(row, dict, f"case result line {line_number}")
        rows.append(row)
    expected_by_id = {case["id"]: case for case in expected_cases}
    if [row.get("case_id") for row in rows] != [
        case["id"] for case in expected_cases
    ]:
        raise ValueError("Result cases are missing, duplicated, reordered, or unexpected")
    tool_by_name = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in bundle["tool_schemas"]
    }
    response_validator = Draft202012Validator(
        bundle["benchmark"]["response_schema"]
    )

    for row in rows:
        case_id = row["case_id"]
        case = expected_by_id[case_id]
        if row.get("category") != case["category"]:
            raise ValueError(f"{case_id} has an unexpected capability category")
        if row.get("status") != "evaluated":
            raise ValueError(
                f"{case_id} did not complete inference; diagnostic archives are not accepted"
            )
        _require_type(row.get("passed"), bool, f"{case_id}.passed")
        _require_type(row.get("checks"), dict, f"{case_id}.checks")
        _require_type(row.get("applicable_checks"), list, f"{case_id}.applicable_checks")
        _require_type(
            row.get("failure_classifications"),
            list,
            f"{case_id}.failure_classifications",
        )
        for name, value in row["checks"].items():
            if not isinstance(name, str) or not isinstance(value, bool):
                raise ValueError(f"{case_id} contains an invalid check entry")
        if not all(isinstance(item, str) for item in row["applicable_checks"]):
            raise ValueError(f"{case_id} contains invalid applicable check names")
        if not all(
            isinstance(item, str) for item in row["failure_classifications"]
        ):
            raise ValueError(f"{case_id} contains an invalid failure classification")
        if row["passed"] != all(
            row["checks"].get(name) is True for name in row["applicable_checks"]
        ):
            raise ValueError(f"{case_id} pass flag disagrees with its check results")
        raw_path = row.get("raw_output_path")
        if not isinstance(raw_path, str) or raw_path not in artifacts:
            raise ValueError(f"{case_id} is missing its raw output artifact")
        raw_record = json.loads(artifacts[raw_path])
        _require_type(raw_record, dict, f"{case_id} raw output")
        if (
            raw_record.get("case_id") != case_id
            or not isinstance(raw_record.get("attempt"), int)
            or not isinstance(raw_record.get("raw_output"), str)
        ):
            raise ValueError(f"{case_id} raw output record has an invalid schema")
        parsed = raw_record.get("parsed_output")
        if parsed is None:
            if not isinstance(raw_record.get("parse_error"), dict):
                raise ValueError(f"{case_id} has no parsed result or parse error")
            try:
                _parse_one_json(raw_record["raw_output"])
            except (ValueError, json.JSONDecodeError):
                pass
            else:
                raise ValueError(f"{case_id} raw output parses despite its parse error")
            if row["checks"].get("structured_output_valid") is not False:
                raise ValueError(f"{case_id} parse failure is not scored as invalid")
        else:
            if raw_record.get("parse_error") is not None:
                raise ValueError(f"{case_id} has conflicting parse result fields")
            try:
                reparsed = _parse_one_json(raw_record["raw_output"])
            except (ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"{case_id} parsed output has invalid raw JSON") from error
            if reparsed != parsed:
                raise ValueError(f"{case_id} parsed result differs from raw output")
            schema_valid = not _schema_errors(response_validator, parsed)
            if row["checks"].get("structured_output_valid") is not schema_valid:
                raise ValueError(f"{case_id} schema-validity check is inconsistent")
            calls = parsed.get("tool_calls", []) if isinstance(parsed, dict) else []
            if not isinstance(calls, list):
                calls = []
            registered = all(
                isinstance(call, dict)
                and isinstance(call.get("name"), str)
                and call["name"] in tool_by_name
                for call in calls
            )
            if row["checks"].get("all_tool_names_registered") is not registered:
                raise ValueError(f"{case_id} registered-tool check is inconsistent")
            args_valid = True
            for call in calls:
                if (
                    not isinstance(call, dict)
                    or call.get("name") not in tool_by_name
                    or not isinstance(call.get("arguments"), dict)
                ):
                    args_valid = False
                    break
                validator = Draft202012Validator(tool_by_name[call["name"]])
                if list(validator.iter_errors(call["arguments"])):
                    args_valid = False
                    break
            if row["checks"].get("all_tool_arguments_valid") is not args_valid:
                raise ValueError(f"{case_id} tool-argument check is inconsistent")
            expected = case.get("expected", {})
            if "tool_calls_exact" in expected:
                actual = [
                    {"name": call.get("name"), "arguments": call.get("arguments")}
                    for call in calls
                    if isinstance(call, dict)
                ]
                if row["checks"].get("expected_tool_sequence_correct") is not (
                    actual == expected["tool_calls_exact"]
                ):
                    raise ValueError(f"{case_id} expected-tool check is inconsistent")
            if "decision" in expected:
                if row["checks"].get("expected_decision_correct") is not (
                    parsed.get("decision") == expected["decision"]
                    if isinstance(parsed, dict)
                    else False
                ):
                    raise ValueError(f"{case_id} decision check is inconsistent")
            if expected.get("specialist_contract") == "specialist_input":
                specialist_validator = Draft202012Validator(
                    bundle["structured_output_contracts"]["specialist_input"]
                )
                handoff = parsed.get("specialist_handoff")
                specialist_valid = not list(specialist_validator.iter_errors(handoff))
                if row["checks"].get("specialist_input_contract_valid") is not (
                    specialist_valid
                ):
                    raise ValueError(f"{case_id} specialist schema check is inconsistent")
                route_correct = (
                    isinstance(handoff, dict)
                    and handoff.get("agent") == expected["specialist_agent"]
                )
                if row["checks"].get("specialist_route_correct") is not route_correct:
                    raise ValueError(f"{case_id} specialist route check is inconsistent")
    return rows


def _parse_one_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found")
    value, end = decoder.raw_decode(text[start:])
    if text[start + end :].strip():
        raise ValueError("Trailing text follows JSON")
    return value


def _schema_errors(validator: Any, value: Any) -> list[Any]:
    return list(validator.iter_errors(value))


def _validate_rate(
    aggregate: dict[str, Any],
    name: str,
    numerator: int,
    denominator: int,
) -> None:
    metric = aggregate.get(name)
    if not isinstance(metric, dict):
        raise ValueError(f"Aggregate metric {name} has an invalid schema")
    expected_value = numerator / denominator if denominator else None
    expected_status = (
        "available" if denominator else "unavailable_no_applicable_observations"
    )
    if (
        metric.get("numerator") != numerator
        or metric.get("denominator") != denominator
        or metric.get("value") != expected_value
        or metric.get("status") != expected_status
    ):
        raise ValueError(f"Aggregate metric {name} disagrees with case results")


def validate_archive(
    archive_path: Path,
    *,
    incoming_dir: Path = INCOMING_DIR,
    validated_dir: Path = VALIDATED_DIR,
) -> dict[str, Any]:
    archive_path = archive_path.resolve()
    if archive_path.parent != incoming_dir.resolve():
        raise ValueError(f"Place the results ZIP directly in {incoming_dir}")
    match = _ARCHIVE_NAME.fullmatch(archive_path.name)
    if match is None:
        raise ValueError("Unexpected results ZIP filename")
    if not archive_path.is_file():
        raise FileNotFoundError(f"Results ZIP does not exist: {archive_path}")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Results ZIP exceeds the accepted maximum size")
    bundle, local_bundle_bytes = _load_expected_bundle()
    expected_bundle_hash = _sha256(local_bundle_bytes)
    sft_manifest_path = (
        BACKEND_ROOT
        / "training"
        / "qwen3-8b"
        / "releases"
        / "insightos-agent-sft-v3"
        / "release_manifest.json"
    )
    sft_manifest = json.loads(sft_manifest_path.read_text(encoding="utf-8"))
    sft_file = sft_manifest_path.with_name(sft_manifest["dataset_file"])
    if _sha256(sft_file.read_bytes()) != sft_manifest["dataset_file_sha256"]:
        raise ValueError("Local Qwen3-8B SFT v3 release hash no longer matches")
    canonical_sft_hash = _sha256(
        _canonical_json(
            {
                "dataset_version": sft_manifest["dataset_version"],
                "files": {
                    sft_manifest["dataset_file"]: sft_manifest[
                        "dataset_file_sha256"
                    ],
                },
            }
        )
    )
    if sft_manifest["dataset_sha256"] != canonical_sft_hash:
        raise ValueError("Local SFT v3 dataset fingerprint is not canonical")
    if (
        bundle["benchmark"]["independence_audit"]["training_dataset_sha256"]
        != sft_manifest["dataset_sha256"]
    ):
        raise ValueError("Benchmark leakage audit is not bound to the local SFT release")
    expected_cases = bundle["benchmark"]["cases"]

    with zipfile.ZipFile(archive_path, "r") as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise ValueError("Results ZIP contains duplicate member names")
        if any(not _safe_member(name) for name in names):
            raise ValueError("Results ZIP contains an unsafe member path")
        if sum(item.file_size for item in infos) > MAX_ARCHIVE_BYTES:
            raise ValueError("Uncompressed results ZIP exceeds the accepted size")
        if any(item.file_size > MAX_MEMBER_BYTES for item in infos):
            raise ValueError("A results ZIP member exceeds the accepted size")
        if "execution_manifest.json" not in names:
            raise ValueError("Results ZIP is missing its execution manifest")
        manifest = json.loads(archive.read("execution_manifest.json"))
        if manifest.get("manifest_version") != (
            "insightos-qwen3-8b-baseline-execution-v1"
        ):
            raise ValueError("Unexpected execution manifest version")
        if manifest.get("run_id") != match.group("run_id"):
            raise ValueError("ZIP filename run ID differs from its manifest")
        if manifest.get("execution_status") != "executed":
            raise ValueError("Baseline execution is incomplete; ZIP is diagnostic only")
        if manifest.get("baseline_status") != "executed":
            raise ValueError("Baseline report does not mark a fully executed run")
        if manifest.get("training_authorized") is not False:
            raise ValueError("Execution artifact must not authorize training")
        if manifest.get("model_id") != MODEL_ID or manifest.get(
            "model_revision"
        ) != MODEL_REVISION:
            raise ValueError("Execution artifact uses the wrong model revision")
        if manifest.get("tokenizer_id") != TOKENIZER_ID or manifest.get(
            "tokenizer_revision"
        ) != TOKENIZER_REVISION:
            raise ValueError("Execution artifact uses the wrong tokenizer revision")
        if manifest.get("bundle_sha256") != expected_bundle_hash:
            raise ValueError("Execution artifact uses a different pinned bundle")
        if manifest.get("benchmark_version") != bundle["benchmark"]["benchmark_version"]:
            raise ValueError("Execution artifact uses a different benchmark version")
        if manifest.get("benchmark_sha256") != bundle["benchmark_sha256"]:
            raise ValueError("Execution artifact benchmark hash is unexpected")
        expected_ids = [case["id"] for case in expected_cases]
        if manifest.get("expected_case_ids") != expected_ids:
            raise ValueError("Execution manifest benchmark coverage is unexpected")
        if manifest.get("generated_case_ids") != expected_ids:
            raise ValueError("Execution did not generate output for every benchmark case")
        listed = manifest.get("files")
        if not isinstance(listed, list):
            raise ValueError("Execution manifest file inventory is invalid")
        file_records = {}
        for item in listed:
            if not isinstance(item, dict):
                raise ValueError("Execution manifest contains an invalid file record")
            name = item.get("path")
            digest = item.get("sha256")
            size = item.get("size_bytes")
            if (
                not isinstance(name, str)
                or not _safe_member(name)
                or name == "execution_manifest.json"
                or name in file_records
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(size, int)
                or size < 0
            ):
                raise ValueError("Execution manifest contains an invalid file entry")
            file_records[name] = item
        if manifest.get("file_count") != len(file_records):
            raise ValueError("Execution manifest file count is inconsistent")
        archive_names = set(names) - {"execution_manifest.json"}
        if archive_names != set(file_records):
            raise ValueError("ZIP members differ from the execution manifest")
        artifacts: dict[str, bytes] = {}
        for name, item in file_records.items():
            contents = archive.read(name)
            if len(contents) != item["size_bytes"] or _sha256(contents) != item["sha256"]:
                raise ValueError(f"Artifact hash or size mismatch: {name}")
            artifacts[name] = contents
        if not _REQUIRED_FILES.issubset(artifacts):
            raise ValueError("Results ZIP is missing required baseline files")
        if _sha256(artifacts["baseline_bundle.json"]) != expected_bundle_hash:
            raise ValueError("Packaged baseline bundle differs from the local pinned bundle")
        packaged_bundle = json.loads(artifacts["baseline_bundle.json"])
        if packaged_bundle != bundle:
            raise ValueError("Packaged baseline bundle content is not the pinned bundle")
        environment_bytes = artifacts["environment_manifest.json"]
        if (
            _sha256(environment_bytes) != bundle.get("kaggle_environment_sha256")
            or json.loads(environment_bytes) != bundle.get("kaggle_environment")
        ):
            raise ValueError("Kaggle environment manifest differs from the pinned bundle")
        benchmark_bytes = artifacts["benchmark.json"]
        if _sha256(benchmark_bytes) != bundle["benchmark_file_sha256"]:
            raise ValueError("Packaged benchmark file hash mismatch")
        benchmark = json.loads(benchmark_bytes)
        if benchmark != bundle["benchmark"]:
            raise ValueError("Packaged benchmark differs from the pinned benchmark")
        model = json.loads(artifacts["model_metadata.json"])
        quantization = model.get("quantization")
        if (
            model.get("model_id") != MODEL_ID
            or model.get("requested_revision") != MODEL_REVISION
            or model.get("loaded_revision") != MODEL_REVISION
            or model.get("adapter_loaded") is not False
            or model.get("training") is not False
            or model.get("tokenizer_id") != TOKENIZER_ID
            or model.get("tokenizer_requested_revision") != TOKENIZER_REVISION
            or model.get("tokenizer_loaded_revision") != TOKENIZER_REVISION
            or model.get("verified_snapshot_directory")
            != model.get("tokenizer_snapshot_directory")
            or not isinstance(model.get("verified_snapshot_directory"), str)
            or not model.get("verified_snapshot_directory")
            or model.get("revision_metadata_source")
            != (
                "huggingface_hub.HfApi.model_info.sha and "
                "huggingface_hub.snapshot_download revision-specific cache path"
            )
            or model.get("model_config_commit") not in (None, MODEL_REVISION)
            or model.get("thinking_mode") is not False
            or not isinstance(quantization, dict)
            or quantization.get("load_in_4bit") is not True
            or quantization.get("type") != "NF4"
            or quantization.get("double_quantization") is not True
            or quantization.get("compute_dtype") != "float16"
        ):
            raise ValueError("Loaded model/tokenizer metadata does not match the pinned identity")
        if not re.fullmatch(r"[0-9a-f]{64}", str(model.get("chat_template_sha256"))):
            raise ValueError("Tokenizer chat-template hash is missing")
        runtime = json.loads(artifacts["runtime_metadata.json"])
        snapshot_verification = runtime.get("model_snapshot_verification")
        if (
            not isinstance(snapshot_verification, dict)
            or snapshot_verification.get("status") != "passed"
            or snapshot_verification.get("expected_commit") != MODEL_REVISION
            or snapshot_verification.get("actual_commit") != MODEL_REVISION
            or snapshot_verification.get("hub_metadata_commit") != MODEL_REVISION
            or snapshot_verification.get("snapshot_path_commit") != MODEL_REVISION
            or snapshot_verification.get("resolved_snapshot_path_commit")
            != MODEL_REVISION
            or snapshot_verification.get("cache_refs_used") is not False
            or snapshot_verification.get("resolved_snapshot_directory")
            != model.get("verified_snapshot_directory")
        ):
            raise ValueError("Verified immutable model/tokenizer snapshot metadata is invalid")
        package_versions = runtime.get("package_versions")
        expected_package_versions = bundle["kaggle_environment"]["packages"]
        torch_version = runtime.get("torch")
        torch_match = (
            re.match(r"^(\d+)\.(\d+)\.(\d+)", torch_version)
            if isinstance(torch_version, str)
            else None
        )
        torch_version_tuple = (
            tuple(int(part) for part in torch_match.groups())
            if torch_match
            else None
        )
        if (
            runtime.get("cuda_available") is not True
            or not isinstance(runtime.get("gpu_name"), str)
            or not runtime["gpu_name"]
            or not isinstance(runtime.get("gpu_total_memory_bytes"), int)
            or not isinstance(runtime.get("gpu_free_memory_bytes"), int)
            or runtime["gpu_free_memory_bytes"] < 0
            or not isinstance(package_versions, dict)
            or any(
                package_versions.get(name) != version
                for name, version in expected_package_versions.items()
            )
            or not isinstance(runtime.get("python"), str)
            or torch_version_tuple is None
            or not (2, 6, 0) <= torch_version_tuple < (3, 0, 0)
            or not isinstance(runtime.get("preflight"), dict)
            or runtime["preflight"].get("status") != "passed"
            or runtime["preflight"].get("cuda_available") is not True
            or runtime["preflight"].get("torch_cuda_version")
            != runtime.get("torch_cuda_build")
            or runtime["preflight"].get("nf4_quantization_probe") != "passed"
            or runtime["preflight"].get("cuda_synchronization") != "passed"
            or runtime["preflight"].get("bitsandbytes_library_compiled_with_cuda")
            is not True
            or runtime["preflight"].get("bitsandbytes_version") != "0.48.2"
            or not isinstance(
                runtime["preflight"].get("bitsandbytes_library_path"), str
            )
            or "libbitsandbytes_cuda"
            not in runtime["preflight"]["bitsandbytes_library_path"]
            or runtime["preflight"].get("detected_cuda_binary_version")
            != runtime["preflight"].get("expected_cuda_binary_version")
            or runtime["preflight"].get("gpu_compute_capability")
            != runtime.get("gpu_compute_capability")
            or runtime["preflight"].get("gpu_name") != runtime.get("gpu_name")
            or runtime["preflight"].get("gpu_total_memory_bytes")
            != runtime.get("gpu_total_memory_bytes")
            or not isinstance(
                runtime["preflight"].get("gpu_free_memory_bytes"), int
            )
            or runtime["preflight"]["gpu_free_memory_bytes"] < 0
            or runtime["preflight"].get("package_versions")
            != runtime.get("package_versions")
        ):
            raise ValueError(
                "Runtime hardware, PyTorch, package, or CUDA NF4 preflight metadata is invalid"
            )
        report = json.loads(artifacts["baseline_run_report.json"])
        if (
            report.get("report_version") != "insightos-qwen3-8b-baseline-run-v1"
            or report.get("execution_status") != "executed"
            or report.get("status") != "executed"
            or report.get("bundle_sha256") != expected_bundle_hash
            or report.get("benchmark_version") != bundle["benchmark"]["benchmark_version"]
            or report.get("benchmark_sha256") != bundle["benchmark_sha256"]
        ):
            raise ValueError("Run report identity or execution status is inconsistent")
        rows = _validate_result_rows(
            artifacts["case_results.jsonl"],
            expected_cases=expected_cases,
            artifacts=artifacts,
            bundle=bundle,
        )
        aggregate = json.loads(artifacts["aggregate_metrics.json"])
        if report.get("metrics") != aggregate:
            raise ValueError("Run report metrics differ from aggregate_metrics.json")
        if (
            aggregate.get("expected_cases") != len(expected_cases)
            or aggregate.get("inference_completed_cases") != len(expected_cases)
        ):
            raise ValueError("Aggregate report does not cover every benchmark case")
        overall_rate = aggregate.get("overall_automated_pass_rate")
        expected_passes = sum(row["passed"] for row in rows)
        _validate_rate(
            aggregate,
            "overall_automated_pass_rate",
            expected_passes,
            len(expected_cases),
        )
        exact_tool_cases = [
            case
            for case in expected_cases
            if case.get("expected", {}).get("tool_calls_exact")
        ]
        exact_tool_passes = sum(
            rows[index]["checks"].get("expected_tool_sequence_correct") is True
            for index, case in enumerate(expected_cases)
            if case in exact_tool_cases
        )
        _validate_rate(
            aggregate,
            "tool_selection_accuracy",
            exact_tool_passes,
            len(exact_tool_cases),
        )
        valid_arguments = sum(
            row.get("tool_calls_with_valid_arguments", 0) for row in rows
        )
        total_calls = sum(row.get("tool_call_count", 0) for row in rows)
        _validate_rate(
            aggregate,
            "tool_argument_schema_validity",
            valid_arguments,
            total_calls,
        )
        schema_valid = sum(
            row["checks"].get("structured_output_valid") is True for row in rows
        )
        _validate_rate(
            aggregate,
            "structured_output_validity",
            schema_valid,
            len(expected_cases),
        )
        decision_correct = sum(
            row["checks"].get("expected_decision_correct") is True for row in rows
        )
        _validate_rate(
            aggregate,
            "expected_decision_accuracy",
            decision_correct,
            len(expected_cases),
        )
        numeric_cases = [
            row
            for row in rows
            if "numeric_claims_grounded" in row["applicable_checks"]
        ]
        numeric_grounded = sum(
            row["checks"].get("numeric_claims_grounded") is True
            for row in numeric_cases
        )
        _validate_rate(
            aggregate,
            "numeric_grounding_heuristic_rate",
            numeric_grounded,
            len(numeric_cases),
        )
        clarification_cases = [
            case
            for case in expected_cases
            if case.get("expected", {}).get("decision") == "clarify"
        ]
        clarification_correct = sum(
            rows[index]["checks"].get("clarification_required") is True
            for index, case in enumerate(expected_cases)
            if case in clarification_cases
        )
        _validate_rate(
            aggregate,
            "required_clarification_accuracy",
            clarification_correct,
            len(clarification_cases),
        )
        specialist_cases = [
            case
            for case in expected_cases
            if case.get("expected", {}).get("specialist_agent")
        ]
        specialist_correct = sum(
            rows[index]["checks"].get("specialist_route_correct") is True
            for index, case in enumerate(expected_cases)
            if case in specialist_cases
        )
        _validate_rate(
            aggregate,
            "specialist_routing_accuracy",
            specialist_correct,
            len(specialist_cases),
        )

    run_id = manifest["run_id"]
    destination = validated_dir / run_id
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite imported run: {destination}")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for name, contents in artifacts.items():
            target = destination.joinpath(*PurePosixPath(name).parts)
            if not _safe_member(name):
                raise ValueError(f"Unsafe extracted path: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(contents)
        validation_report = {
            "validation_version": "insightos-qwen3-8b-baseline-import-v1",
            "status": "executed",
            "run_id": run_id,
            "archive_name": archive_path.name,
            "archive_sha256": _sha256(archive_path.read_bytes()),
            "bundle_sha256": expected_bundle_hash,
            "benchmark_version": bundle["benchmark"]["benchmark_version"],
            "benchmark_sha256": bundle["benchmark_sha256"],
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "case_count": len(rows),
            "automated_pass_count": sum(row["passed"] for row in rows),
            "human_semantic_review_status": "pending",
            "training_authorized": False,
            "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        with (destination / "import_validation_report.json").open(
            "x", encoding="utf-8", newline="\n"
        ) as output:
            json.dump(validation_report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except Exception:
        raise
    return validation_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and import an executed Kaggle Qwen3-8B baseline ZIP."
    )
    parser.add_argument(
        "archive",
        type=Path,
        nargs="?",
        help="Path to the run ZIP placed directly in kaggle_results/incoming/",
    )
    args = parser.parse_args()
    if args.archive is None:
        candidates = sorted(INCOMING_DIR.glob("insightos-qwen3-8b-baseline-v1-*.zip"))
        if len(candidates) != 1:
            parser.error(
                f"Expected exactly one results ZIP in {INCOMING_DIR}; found {len(candidates)}"
            )
        archive_path = candidates[0]
    else:
        archive_path = args.archive
    print(json.dumps(validate_archive(archive_path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
