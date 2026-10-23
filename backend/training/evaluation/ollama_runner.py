from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import platform
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from training.evaluation.hf_runner import (
    CASE_MAX_OUTPUT_TOKENS,
    SCORING_VERSION,
    Generation,
    _final_answer_text,
    _git_metadata,
    _has_reasoning_leak,
    _has_think_tags,
    _truncation_stage,
    evaluate_cases_with_retries,
    load_suite,
    rescore_saved_report,
)
from training.evaluation.human_review import (
    comparison_reconciliation_summary,
    reconcile_report,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIRECTORY = REPOSITORY_ROOT / "backend" / "artifacts" / "evaluation-results"
DEFAULT_BASELINE = (
    RESULTS_DIRECTORY
    / "hf-qwen3-0.6b-diagnostic-20261022-2203-reliability-v2.json"
)
REPORT_SCHEMA_VERSION = "insightos-ollama-evaluation-report-v1"
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_TOKENS = 512
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_NDJSON_LINE_BYTES = 64 * 1024
OLLAMA_SYSTEM_PROMPT_VERSION = "insightos-ollama-evaluation-v6"
OLLAMA_SYSTEM_PROMPT = """Answer with only the final response requested by the user. Do not reveal internal reasoning, narrate your thought process, restate the prompt, or discuss these instructions. Follow exact output constraints. Use only supplied facts; never invent source columns, data, or completed actions.

For a data-analysis plan, distinguish a proposed plan from work actually performed. When a prompt describes a hypothetical CSV but supplies no actual file or schema, begin by clearly stating that the actual schema was not provided and its columns cannot be confirmed; describe required field roles conditionally rather than asserting they exist. For monthly profit-margin plans, identify date, revenue, and expense/cost fields; check that the date field exists and parses as dates, and validate required financial fields as numeric. State how invalid dates, missing values, and nonnumeric values would be flagged or excluded rather than invented or silently imputed. Group valid records by calendar month, calculate profit = revenue - expenses, and calculate margin = profit / revenue * 100 only where revenue is valid and positive. Zero revenue makes margin undefined, not 0%; zero profit with positive revenue yields a valid 0% margin. Flag negative revenue for validation or business-policy review and defer its treatment until the applicable policy is known; do not automatically classify it as invalid or exclude it without that policy. Do not claim division by a negative number is mathematically undefined. Negative profit with positive revenue gives a negative margin. Do not claim a file was opened, data inspected, or calculation executed unless it actually occurred.

Revenue is before costs; profit is revenue minus expenses; profit margin (%) is profit divided by revenue times 100, never revenue divided by total revenue. Missing or zero revenue means no margin."""


class OllamaRequestError(RuntimeError):
    category = "request_error"

    def __init__(
        self,
        message: str,
        *,
        cause_type: str | None = None,
        cause_message: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.cause_type = cause_type
        self.cause_message = cause_message
        self.http_status = http_status


class OllamaRequestTimeout(OllamaRequestError):
    category = "timeout"


class OllamaConnectionFailure(OllamaRequestError):
    category = "connection_error"


class OllamaHTTPFailure(OllamaRequestError):
    category = "http_error"


class OllamaMalformedResponse(OllamaRequestError):
    category = "malformed_json"


class OllamaMissingContent(OllamaRequestError):
    category = "missing_content"


class OllamaUnexpectedResponse(OllamaRequestError):
    category = "unexpected"


THINKING_CASE_MAX_OUTPUT_TOKENS = {
    "repeat_exact_token": 1536,
    "json_profit_margin": 1536,
    "instruction_exactly_two_sentences": 1536,
    "business_revenue_profit": 2048,
    "missing_churn_inputs": 1536,
    "analysis_plan_profit_margin": 2048,
    "tool_plan_without_execution": 2048,
}


def _token_limit_profile(model: str) -> tuple[str, dict[str, int]]:
    if "instruct" in model.casefold():
        return "instruct", dict(CASE_MAX_OUTPUT_TOKENS)
    return "thinking_capable", dict(THINKING_CASE_MAX_OUTPUT_TOKENS)


def _validate_local_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Ollama evaluation is restricted to a local HTTP endpoint.")
    if parsed.path not in {"", "/"}:
        raise ValueError("The Ollama base URL must not include a path.")
    return value.rstrip("/")


def _request_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if timeout_seconds <= 0 or timeout_seconds > DEFAULT_TIMEOUT_SECONDS:
        raise ValueError("Request timeout must be greater than 0 and at most 300 seconds.")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Ollama requests are restricted to a local HTTP endpoint.")
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    request_body = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    headers = {"Content-Type": "application/json"} if request_body is not None else {}
    connection = http.client.HTTPConnection(host, port, timeout=timeout_seconds)
    response: http.client.HTTPResponse | None = None
    deadline = time.monotonic() + timeout_seconds
    timed_out = threading.Event()
    request_finished = threading.Event()

    def abort_request() -> None:
        if request_finished.is_set():
            return
        timed_out.set()
        if response is not None:
            try:
                response.close()
            except (OSError, ValueError):
                pass
        if connection.sock is not None:
            try:
                connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            connection.close()
        except OSError:
            pass

    deadline_timer = threading.Timer(timeout_seconds, abort_request)
    try:
        deadline_timer.start()
        connection.request(
            "POST" if request_body is not None else "GET",
            path,
            body=request_body,
            headers=headers,
        )
        if timed_out.is_set() or time.monotonic() >= deadline:
            raise OllamaRequestTimeout(
                f"Ollama request exceeded its {timeout_seconds:g}-second deadline."
            )
        response = connection.getresponse()
        if response.status < 200 or response.status >= 300:
            raise OllamaHTTPFailure(
                f"Ollama returned HTTP status {response.status} "
                f"{response.reason or ''}".strip(),
                http_status=response.status,
            )
        chunks = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or timed_out.is_set():
                raise OllamaRequestTimeout(
                    f"Ollama request exceeded its {timeout_seconds:g}-second deadline."
                )
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > MAX_RESPONSE_BYTES:
                raise OllamaUnexpectedResponse(
                    "Ollama response exceeded the 1 MiB safe size limit."
                )
        if timed_out.is_set() or time.monotonic() >= deadline:
            raise OllamaRequestTimeout(
                f"Ollama request exceeded its {timeout_seconds:g}-second deadline."
            )
        request_finished.set()
    except OllamaRequestError:
        raise
    except (TimeoutError, socket.timeout) as exc:
        if timed_out.is_set() or time.monotonic() >= deadline:
            raise OllamaRequestTimeout(
                f"Ollama request exceeded its {timeout_seconds:g}-second deadline: {exc}",
                cause_type=type(exc).__name__,
                cause_message=str(exc),
            ) from exc
        raise OllamaRequestTimeout(
            f"Ollama socket operation timed out: {type(exc).__name__}: {exc}",
            cause_type=type(exc).__name__,
            cause_message=str(exc),
        ) from exc
    except (ConnectionError, OSError) as exc:
        if timed_out.is_set() or time.monotonic() >= deadline:
            raise OllamaRequestTimeout(
                f"Ollama request exceeded its {timeout_seconds:g}-second deadline: {exc}",
                cause_type=type(exc).__name__,
                cause_message=str(exc),
            ) from exc
        raise OllamaConnectionFailure(
            f"Ollama connection failed ({type(exc).__name__}): {exc}",
            cause_type=type(exc).__name__,
            cause_message=str(exc),
        ) from exc
    except http.client.HTTPException as exc:
        raise OllamaUnexpectedResponse(
            f"Ollama returned an unexpected HTTP response ({type(exc).__name__}): {exc}",
            cause_type=type(exc).__name__,
            cause_message=str(exc),
        ) from exc
    except ValueError as exc:
        if timed_out.is_set() or time.monotonic() >= deadline:
            raise OllamaRequestTimeout(
                f"Ollama request exceeded its {timeout_seconds:g}-second deadline: {exc}",
                cause_type=type(exc).__name__,
                cause_message=str(exc),
            ) from exc
        raise OllamaUnexpectedResponse(
            f"Ollama returned an unexpected HTTP response: {exc}",
            cause_type=type(exc).__name__,
            cause_message=str(exc),
        ) from exc
    finally:
        request_finished.set()
        deadline_timer.cancel()
        if response is not None:
            try:
                response.close()
            except (OSError, ValueError):
                pass
        try:
            connection.close()
        except OSError:
            pass
    raw = bytes(chunks)
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OllamaMalformedResponse(
            f"Ollama returned invalid JSON ({type(exc).__name__}): {exc}",
            cause_type=type(exc).__name__,
            cause_message=str(exc),
        ) from exc
    if not isinstance(result, dict):
        raise OllamaUnexpectedResponse("Ollama returned a non-object JSON response.")
    return result


def _request_stream_json(
    url: str,
    *,
    payload: dict[str, Any],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    http_connection_factory: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if timeout_seconds <= 0 or timeout_seconds > DEFAULT_TIMEOUT_SECONDS:
        raise ValueError("Request timeout must be greater than 0 and at most 300 seconds.")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Ollama requests are restricted to a local HTTP endpoint.")
    connection_factory = http_connection_factory or http.client.HTTPConnection
    connection = connection_factory(
        parsed.hostname,
        parsed.port or 80,
        timeout=min(timeout_seconds, 30),
    )
    response = None
    deadline = clock() + timeout_seconds
    endpoint = parsed.path or "/"
    if parsed.query:
        endpoint = f"{endpoint}?{parsed.query}"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    raw_bytes = 0
    message_content: list[str] = []
    thinking_content: list[str] = []
    completed_record: dict[str, Any] | None = None
    stream_chunks = 0

    def response_socket() -> Any:
        if connection.sock is not None:
            return connection.sock
        response_stream = getattr(response, "fp", None)
        raw_stream = getattr(response_stream, "raw", None)
        return getattr(raw_stream, "_sock", None)

    def set_read_timeout() -> None:
        remaining = deadline - clock()
        if remaining <= 0:
            raise OllamaRequestTimeout(
                f"Ollama request exceeded its {timeout_seconds:g}-second deadline."
            )
        sock = response_socket()
        if sock is not None:
            sock.settimeout(min(remaining, 30))

    try:
        connection.request(
            "POST",
            endpoint,
            body=body,
            headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        )
        set_read_timeout()
        response = connection.getresponse()
        if response.status < 200 or response.status >= 300:
            raise OllamaHTTPFailure(
                f"Ollama returned HTTP status {response.status} "
                f"{response.reason or ''}".strip(),
                http_status=response.status,
            )
        pending_line = bytearray()
        done = False
        while not done:
            set_read_timeout()
            chunk = response.read1(8 * 1024)
            if clock() >= deadline:
                raise OllamaRequestTimeout(
                    f"Ollama request exceeded its {timeout_seconds:g}-second deadline."
                )
            if not chunk:
                if pending_line:
                    lines = [bytes(pending_line)]
                    pending_line.clear()
                else:
                    lines = []
            else:
                pending_line.extend(chunk)
                lines = []
                while b"\n" in pending_line:
                    line, _, remainder = pending_line.partition(b"\n")
                    lines.append(line)
                    pending_line = bytearray(remainder)
                if len(pending_line) > MAX_NDJSON_LINE_BYTES:
                    raise OllamaUnexpectedResponse(
                        "Ollama NDJSON record exceeded the safe line size."
                    )
            if not chunk and not lines:
                break
            for line in lines:
                if len(line) > MAX_NDJSON_LINE_BYTES:
                    raise OllamaUnexpectedResponse(
                        "Ollama NDJSON record exceeded the safe line size."
                    )
                raw_bytes += len(line) + 1
                if raw_bytes > MAX_RESPONSE_BYTES:
                    raise OllamaUnexpectedResponse(
                        "Ollama streamed response exceeded the safe size limit."
                    )
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise OllamaMalformedResponse(
                        f"Ollama returned invalid NDJSON ({type(exc).__name__}): {exc}",
                        cause_type=type(exc).__name__,
                        cause_message=str(exc),
                    ) from exc
                if not isinstance(record, dict):
                    raise OllamaUnexpectedResponse(
                        "Ollama stream record is not a JSON object."
                    )
                message = record.get("message")
                if message is not None:
                    if not isinstance(message, dict):
                        raise OllamaUnexpectedResponse(
                            "Ollama stream message is not an object."
                        )
                    content = message.get("content", "")
                    thinking = message.get("thinking", "")
                    if not isinstance(content, str) or not isinstance(thinking, str):
                        raise OllamaUnexpectedResponse(
                            "Ollama stream message content fields must be strings."
                        )
                    message_content.append(content)
                    thinking_content.append(thinking)
                    stream_chunks += 1
                if record.get("done") is True:
                    completed_record = record
                    done = True
                    break
                if clock() >= deadline:
                    raise OllamaRequestTimeout(
                        f"Ollama request exceeded its {timeout_seconds:g}-second deadline."
                    )
        if completed_record is None:
            raise OllamaUnexpectedResponse("Ollama stream ended without a done record.")
        if clock() >= deadline:
            raise OllamaRequestTimeout(
                f"Ollama request exceeded its {timeout_seconds:g}-second deadline."
            )
        assembled_message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(message_content),
        }
        if any(thinking_content):
            assembled_message["thinking"] = "".join(thinking_content)
        result = {
            **completed_record,
            "message": assembled_message,
            "stream_chunks": stream_chunks,
        }
        if not assembled_message["content"]:
            raise OllamaMissingContent(
                "Ollama stream completed without message content."
            )
        return result
    except OllamaRequestError:
        raise
    except (TimeoutError, socket.timeout) as exc:
        raise OllamaRequestTimeout(
            f"Ollama stream read timed out: {type(exc).__name__}: {exc}",
            cause_type=type(exc).__name__,
            cause_message=str(exc),
        ) from exc
    except (ConnectionError, OSError) as exc:
        if clock() >= deadline:
            raise OllamaRequestTimeout(
                f"Ollama request exceeded its {timeout_seconds:g}-second deadline: {exc}",
                cause_type=type(exc).__name__,
                cause_message=str(exc),
            ) from exc
        raise OllamaConnectionFailure(
            f"Ollama connection failed ({type(exc).__name__}): {exc}",
            cause_type=type(exc).__name__,
            cause_message=str(exc),
        ) from exc
    except http.client.HTTPException as exc:
        raise OllamaUnexpectedResponse(
            f"Ollama returned an invalid HTTP stream ({type(exc).__name__}): {exc}",
            cause_type=type(exc).__name__,
            cause_message=str(exc),
        ) from exc
    except Exception as exc:
        raise OllamaUnexpectedResponse(
            f"Ollama stream failed unexpectedly ({type(exc).__name__}): {exc}",
            cause_type=type(exc).__name__,
            cause_message=str(exc),
        ) from exc
    finally:
        if response is not None:
            try:
                response.close()
            except (OSError, ValueError):
                pass
        try:
            connection.close()
        except OSError:
            pass


def _chat_payload(
    case,
    *,
    model: str,
    max_output_tokens: int,
    temperature: float = 0.0,
    feedback: str | None = None,
) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
        {"role": "user", "content": case.prompt},
    ]
    if feedback:
        messages.append({"role": "user", "content": feedback})
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "think": False,
        "stream": True,
        "options": {"num_predict": max_output_tokens, "temperature": temperature},
    }
    if case.id == "json_profit_margin":
        payload["format"] = "json"
    return payload


def _select_cases(cases, case_ids: list[str] | tuple[str, ...] | None):
    if case_ids is None:
        return tuple(cases)
    if not case_ids:
        raise ValueError("At least one case ID must be selected.")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Case IDs must be unique.")
    by_id = {case.id: case for case in cases}
    unknown = [case_id for case_id in case_ids if case_id not in by_id]
    if unknown:
        raise ValueError("Unknown evaluation case ID(s): " + ", ".join(unknown))
    return tuple(by_id[case_id] for case_id in case_ids)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _generator(
    *,
    base_url: str,
    model: str,
    timeout_seconds: int,
    max_output_tokens: int | None,
    api_usage: dict[str, list[dict[str, Any]]],
    temperature: float = 0.0,
    max_output_tokens_by_case: dict[str, int] | None = None,
    http_connection_factory: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
):
    def generate(case, *, feedback: str | None = None) -> Generation:
        case_output_limit = (
            max_output_tokens
            if max_output_tokens is not None
            else (max_output_tokens_by_case or _token_limit_profile(model)[1]).get(
                case.id,
                DEFAULT_MAX_OUTPUT_TOKENS,
            )
        )
        started = clock()
        response = _request_stream_json(
            f"{base_url}/api/chat",
            payload=_chat_payload(
                case,
                model=model,
                max_output_tokens=case_output_limit,
                temperature=temperature,
                feedback=feedback,
            ),
            timeout_seconds=timeout_seconds,
            http_connection_factory=http_connection_factory,
            clock=clock,
        )
        elapsed_ms = (clock() - started) * 1000
        message = response.get("message")
        if not isinstance(message, dict) or not isinstance(
            message.get("content"), str
        ) or not message["content"]:
            raise OllamaMissingContent(
                "Ollama response is missing non-empty message.content."
            )
        text = message["content"]
        if len(text) > 64 * 1024:
            raise RuntimeError("Ollama output exceeded the safe size limit.")

        prompt_tokens = _nonnegative_int(response.get("prompt_eval_count"))
        output_tokens = _nonnegative_int(response.get("eval_count"))
        thinking = message.get("thinking")
        thinking_field_present = isinstance(thinking, str) and bool(thinking.strip())
        usage = {
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "total_duration_ns": _nonnegative_int(response.get("total_duration")),
            "load_duration_ns": _nonnegative_int(response.get("load_duration")),
            "prompt_eval_duration_ns": _nonnegative_int(
                response.get("prompt_eval_duration")
            ),
            "eval_duration_ns": _nonnegative_int(response.get("eval_duration")),
            "stream_chunks": _nonnegative_int(response.get("stream_chunks")),
            "separate_thinking_field_present": thinking_field_present,
            "separate_thinking_char_count": (
                len(thinking) if thinking_field_present else 0
            ),
        }
        api_usage.setdefault(case.id, []).append(usage)
        done_reason = response.get("done_reason")
        if done_reason == "length" or (
            output_tokens is not None and output_tokens >= case_output_limit
        ):
            termination_reason = "max_new_tokens"
        elif done_reason == "stop":
            termination_reason = "eos"
        else:
            termination_reason = "unknown"
        rate = (
            output_tokens / (elapsed_ms / 1000)
            if output_tokens is not None and elapsed_ms > 0
            else None
        )
        final_text = _final_answer_text(text)
        final_leak = _has_reasoning_leak(final_text)
        return Generation(
            text=text,
            generated_tokens=output_tokens,
            duration_ms=round(elapsed_ms, 2),
            tokens_per_second=round(rate, 3) if rate is not None else None,
            termination_reason=termination_reason,
            reasoning_leak=final_leak,
            raw_leak=_has_think_tags(text),
            final_leak=final_leak,
            truncation_stage=_truncation_stage(text, termination_reason),
            raw_text=text,
            final_text=final_text,
            max_output_tokens=case_output_limit,
            api_usage=usage,
        )

    return generate


def _status_counts(results: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = ("passed", "failed", "error", "skipped", "needs_review")
    generations = []
    for result in results:
        if result.get("trials"):
            generations.extend(
                attempt["generation"]
                for trial in result["trials"]
                for attempt in trial["attempts"]
                if isinstance(attempt.get("attempt"), int)
            )
        else:
            generations.extend(result["generations"])
    generations = [
        generation
        for generation in generations
        if generation.get("source", "model") == "model"
    ]
    raw_leak_count = sum(generation.get("raw_leak", False) for generation in generations)
    final_leak_count = sum(
        generation.get("final_leak", generation.get("reasoning_leak", False))
        for generation in generations
    )
    return {
        "total_cases": len(results),
        **{
            status: sum(result["status"] == status for result in results)
            for status in statuses
        },
        "raw_leak_generations": raw_leak_count,
        "raw_leak_rate": raw_leak_count / len(generations) if generations else 0.0,
        "final_leak_generations": final_leak_count,
        "final_leak_rate": final_leak_count / len(generations) if generations else 0.0,
        "reasoning_leak_generations": final_leak_count,
        "reasoning_leak_rate": final_leak_count / len(generations) if generations else 0.0,
    }


def _comparison(
    baseline_path: Path,
    ollama_results: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline = rescore_saved_report(baseline_path)
    original_report = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_cases = {item["id"]: item for item in baseline["cases"]}
    comparisons = []
    for result in ollama_results:
        previous = baseline_cases.get(result["id"])
        comparisons.append(
            {
                "case_id": result["id"],
                "ollama_status": result["status"],
                "hf_status_rescored_with_current_scorer": (
                    previous["rescored_status"] if previous is not None else None
                ),
                "hf_original_status": (
                    previous["original_status"] if previous is not None else None
                ),
            }
        )
    review_path = baseline_path.with_name(
        f"{baseline_path.stem}-human-review.json"
    )
    human_review_reconciliation = (
        comparison_reconciliation_summary(
            reconcile_report(baseline_path, review_path)
        )
        if review_path.is_file()
        else None
    )
    return {
        "baseline_report": str(baseline_path.resolve()),
        "baseline_run_id": baseline.get("source_run_id"),
        "baseline_original_scoring_version": original_report.get("scoring_version"),
        "baseline_original_prompt_version": (
            original_report.get("generation_settings", {})
            .get("system_prompt", {})
            .get("version")
        ),
        "baseline_original_counts": baseline.get("original_counts"),
        "baseline_rescored_counts": baseline.get("rescored_counts"),
        "baseline_rescoring_version": baseline.get("scoring_version"),
        "baseline_human_review_reconciliation": human_review_reconciliation,
        "per_case": comparisons,
        "limitations": [
            "The saved Hugging Face baseline used a different system-prompt version and was generated before the current scoring fixes.",
            "Rescoring applies the current scorer to saved Hugging Face outputs but cannot make the original generation settings equivalent to Ollama.",
            "The comparison is diagnostic and is not a controlled model-only comparison.",
        ],
    }


def _write_report(report: dict[str, Any], output: Path | None) -> Path:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = output or RESULTS_DIRECTORY / f"ollama-qwen3-4b-{report['run_id']}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return destination


def run_evaluation(
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_output_tokens: int | None = None,
    temperature: float = 0.0,
    max_attempts: int = 3,
    baseline_path: Path = DEFAULT_BASELINE,
    case_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    local_base_url = _validate_local_base_url(base_url)
    if timeout_seconds < 1 or timeout_seconds > 300:
        raise ValueError("Timeout must be between 1 and 300 seconds.")
    if max_output_tokens is not None and (
        max_output_tokens < 1 or max_output_tokens > 4096
    ):
        raise ValueError("Output-token limit must be between 1 and 4096.")
    if not 0 <= temperature <= 2:
        raise ValueError("Temperature must be between 0 and 2.")
    if max_attempts < 1 or max_attempts > 3:
        raise ValueError("The evaluation retry limit must be between 1 and 3.")
    if not baseline_path.is_file():
        raise FileNotFoundError("The saved Hugging Face baseline report is missing.")

    tags = _request_json(
        f"{local_base_url}/api/tags",
        timeout_seconds=min(timeout_seconds, 30),
    )
    models = tags.get("models")
    matching_models = [
        item
        for item in models
        if isinstance(item, dict) and model in {item.get("name"), item.get("model")}
    ] if isinstance(models, list) else []
    if not matching_models:
        raise RuntimeError(f"Required local Ollama model is not installed: {model}.")
    version_payload = _request_json(
        f"{local_base_url}/api/version",
        timeout_seconds=min(timeout_seconds, 30),
    )
    suite_version, cases = load_suite()
    cases = _select_cases(cases, case_ids)
    token_limit_profile, token_limits = _token_limit_profile(model)
    api_usage: dict[str, list[dict[str, Any]]] = {}
    results = evaluate_cases_with_retries(
        cases,
        _generator(
            base_url=local_base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            api_usage=api_usage,
            temperature=temperature,
            max_output_tokens_by_case=token_limits,
        ),
        max_attempts=max_attempts,
        stop_on_generation_error=True,
    )

    elapsed_completions = [
        attempt["generation"]["duration_ms"]
        for result in results
        for trial in result.get("trials", [])
        for attempt in trial["attempts"]
        if isinstance(attempt.get("attempt"), int)
    ]
    sorted_latencies = sorted(elapsed_completions)
    latency = {
        "completion_count": len(sorted_latencies),
        "median_ms": (
            sorted_latencies[len(sorted_latencies) // 2]
            if len(sorted_latencies) % 2
            else (
                (
                    sorted_latencies[len(sorted_latencies) // 2 - 1]
                    + sorted_latencies[len(sorted_latencies) // 2]
                )
                / 2
                if sorted_latencies
                else None
            )
        ),
        "maximum_ms": max(sorted_latencies) if sorted_latencies else None,
    }
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "suite_version": suite_version,
        "scoring_version": SCORING_VERSION,
        "run_id": uuid.uuid4().hex,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evaluator": "local_ollama",
        "repository": _git_metadata(),
        "model": {
            "name": model,
            "tag": model,
            "digest": matching_models[0].get("digest"),
            "ollama_version": version_payload.get("version"),
            "endpoint": local_base_url,
            "ollama_metadata": matching_models[0],
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "generation_settings": {
            "thinking_mode": False,
            "thinking_mode_request_field": "think=false",
            "max_output_tokens": max_output_tokens,
            "max_output_tokens_by_case": {
                case.id: (
                    max_output_tokens
                    if max_output_tokens is not None
                    else token_limits.get(case.id, DEFAULT_MAX_OUTPUT_TOKENS)
                )
                for case in cases
            },
            "token_limit_profile": token_limit_profile,
            "temperature": temperature,
            "json_mode_case_ids": ["json_profit_margin"],
            "stream": True,
            "timeout_seconds": timeout_seconds,
            "max_attempts_including_initial": max_attempts,
            "selected_case_ids": [case.id for case in cases],
            "deterministic_fallback_case_ids": [
                "repeat_exact_token",
                "json_profit_margin",
                "missing_churn_inputs",
            ],
            "system_prompt": {
                "version": OLLAMA_SYSTEM_PROMPT_VERSION,
                "sha256": hashlib.sha256(
                    OLLAMA_SYSTEM_PROMPT.encode("utf-8")
                ).hexdigest(),
            },
        },
        "counts": _status_counts(results),
        "latency": latency,
        "comparison": _comparison(baseline_path, results),
        "cases": results,
        "limitations": [
            "This is a seven-case synthetic diagnostic, not a general capability guarantee.",
            "Cases with pending semantic review are not automatically approved by machine checks.",
            "The saved HF baseline used a different system prompt and earlier scoring version; this is not a controlled model-only comparison.",
            "The analysis-plan checks are narrow lexical heuristics and semantic correctness requires human review.",
            "The local model may page heavily under current system memory pressure; latency is specific to this run and machine state.",
            "No tools are executed; textual planning is not evidence of tool-call correctness.",
            "The evaluator records only presence and character count for an Ollama message.thinking field; it never stores that field's contents.",
            "A concise final-answer-only system instruction may improve output focus, but only a fresh model run can establish whether it does.",
        ],
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate an installed Ollama model on the synthetic InsightOS suite."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        help="Optional override applied to every case; otherwise use case-specific limits.",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Evaluate only this case ID; may be repeated. Defaults to the full suite.",
    )
    parser.add_argument("--baseline-report", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = run_evaluation(
        model=args.model,
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
        max_output_tokens=args.max_output_tokens,
        temperature=args.temperature,
        max_attempts=args.max_attempts,
        baseline_path=args.baseline_report,
        case_ids=args.case_ids,
    )
    destination = _write_report(report, args.output)
    print(json.dumps(report["counts"], sort_keys=True))
    print(f"Report saved to {destination}")
    if any(
        isinstance(case.get("error"), dict)
        and case["error"].get("category")
        in {
            "timeout",
            "connection_error",
            "http_error",
            "malformed_json",
            "missing_content",
            "unexpected",
        }
        for case in report["cases"]
    ):
        return 2
    return 1 if report["counts"]["failed"] or report["counts"]["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
