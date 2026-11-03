from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.config import get_agent_settings, resolve_task_budget
from app.agent.business_intelligence import (
    BI_PROMPT_VERSION,
    PromptVariant,
    SPECIALIST_PROMPT_PROFILES,
    model_specialization_instruction,
)
from app.agent.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderMessage,
    ProviderHealth,
    RequestTelemetry,
)
from app.agent.config import AgentSettings
from app.agent.model_router import AgentModelRouter
from app.agent.model_registry import REGISTRY
from app.agent.providers import ModelProvider, create_provider
from app.agent.specialist_agents import (
    AnalyticsDecision,
    DataIntelligenceResult,
    PresentationResult,
    SpecialistAgentError,
    build_specialist_handoff,
    run_analytics_decision,
    run_data_intelligence,
    run_presentation,
)


OLLAMA_BASE_URL = "http://localhost:11434"
FIXTURE_PATH = Path(__file__).with_name("fixtures.json")
RESULTS_ROOT = BACKEND_ROOT / "artifacts" / "evaluation-results" / "qwen3-live-benchmark"
GIB = 1024**3
SEVERE_FREE_RAM_GB = 0.5
SEVERE_MEMORY_LOAD_PERCENT = 98
SEVERE_COMMIT_HEADROOM_GB = 1.0
SEVERE_PAGEFILE_USAGE_PERCENT = 95
MEMORY_SAFETY_RESERVE_GB = 2.0
NUMBER_PATTERN = re.compile(r"(?<![\w])(?:[$€£]\s*)?-?\d[\d,]*(?:\.\d+)?%?(?![\w])")
MODEL_TARGETS = (
    "qwen3:8b",
    "qwen3:4b-instruct",
    "qwen3:4b",
    "qwen3:0.6b",
)
DEFAULT_MODEL_TARGET = "qwen3:8b"


def _read_memory() -> dict[str, float] | None:
    if sys.platform == "win32":
        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        class _PerformanceInformation(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("CommitTotal", ctypes.c_ulonglong),
                ("CommitLimit", ctypes.c_ulonglong),
                ("CommitPeak", ctypes.c_ulonglong),
                ("PhysicalTotal", ctypes.c_ulonglong),
                ("PhysicalAvailable", ctypes.c_ulonglong),
                ("SystemCache", ctypes.c_ulonglong),
                ("KernelTotal", ctypes.c_ulonglong),
                ("KernelPaged", ctypes.c_ulonglong),
                ("KernelNonpaged", ctypes.c_ulonglong),
                ("PageSize", ctypes.c_ulong),
                ("HandleCount", ctypes.c_ulong),
                ("ProcessCount", ctypes.c_ulong),
                ("ThreadCount", ctypes.c_ulong),
            ]

        memory = {
            "total_gb": 0.0,
            "free_gb": 0.0,
            "memory_load_percent": None,
        }
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        memory.update(
            {
                "total_gb": round(status.ullTotalPhys / GIB, 3),
                "free_gb": round(status.ullAvailPhys / GIB, 3),
                "memory_load_percent": int(status.dwMemoryLoad),
                "pagefile_total_gb": round(status.ullTotalPageFile / GIB, 3),
                "pagefile_free_gb": round(status.ullAvailPageFile / GIB, 3),
                "pagefile_used_gb": round(
                    (status.ullTotalPageFile - status.ullAvailPageFile) / GIB,
                    3,
                ),
                "pagefile_usage_percent": (
                    round(
                        (status.ullTotalPageFile - status.ullAvailPageFile)
                        / status.ullTotalPageFile
                        * 100,
                        3,
                    )
                    if status.ullTotalPageFile > 0
                    else 0.0
                ),
            }
        )

        psapi = getattr(ctypes.windll, "psapi", None)
        if psapi is not None and hasattr(psapi, "GetPerformanceInfo"):
            perf = _PerformanceInformation()
            perf.cb = ctypes.sizeof(perf)
            if psapi.GetPerformanceInfo(ctypes.byref(perf), perf.cb):
                commit_total = perf.CommitTotal * perf.PageSize
                commit_limit = perf.CommitLimit * perf.PageSize
                memory.update(
                    {
                        "committed_gb": round(commit_total / GIB, 3),
                        "commit_limit_gb": round(commit_limit / GIB, 3),
                        "commit_headroom_gb": round((commit_limit - commit_total) / GIB, 3),
                        "committed_percent": (
                            round(commit_total / commit_limit * 100, 3) if commit_limit > 0 else 0.0
                        ),
                    }
                )
        return memory

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    return {
        "total_gb": round(total_pages * page_size / GIB, 3),
        "free_gb": round(available_pages * page_size / GIB, 3),
        "memory_load_percent": None,
    }


def _local_json(
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    parsed = urlsplit(OLLAMA_BASE_URL)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Benchmark Ollama requests must use a local HTTP endpoint.")
    body = (
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(
        f"{OLLAMA_BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST" if body else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Ollama metadata response exceeded the benchmark safety limit.")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Ollama returned an invalid metadata response.")
    return result


def _command_output(*args: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "error_type": type(error).__name__}
    return {
        "available": completed.returncode == 0,
        "return_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip()[:500],
    }


def _model_cli_metadata(tag: str) -> dict[str, Any]:
    result = _command_output("ollama", "show", "--verbose", tag)
    if not result["available"]:
        return {
            "family": None,
            "parameter_size": None,
            "context_length": None,
            "quantization": None,
            "temperature_default": None,
            "metadata_error_type": "ollama_show_failed",
        }
    sections: dict[str, dict[str, str]] = {}
    section = "Model"
    for line in result["stdout"].splitlines():
        stripped = line.strip()
        if stripped in {"Model", "Capabilities", "Parameters", "License"}:
            section = stripped
            sections.setdefault(section, {})
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9 _-]+?)\s{2,}(.+)$", stripped)
        if match:
            sections.setdefault(section, {})[match.group(1).strip().casefold()] = (
                match.group(2).strip()
            )
    model_section = sections.get("Model", {})
    parameter_section = sections.get("Parameters", {})
    return {
        "family": model_section.get("architecture"),
        "parameter_size": model_section.get("parameters"),
        "context_length": model_section.get("context length"),
        "quantization": model_section.get("quantization"),
        "temperature_default": parameter_section.get("temperature"),
        "default_parameters": parameter_section,
        "capabilities": sections.get("Capabilities", {}),
    }


def _powershell_video_controllers() -> list[str]:
    if sys.platform != "win32":
        return []
    result = _command_output(
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
    )
    if not result["available"]:
        return []
    return [line for line in result["stdout"].splitlines() if line.strip()]


def _powershell_processor_name() -> str | None:
    if sys.platform != "win32":
        return None
    result = _command_output(
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name",
    )
    return result["stdout"] if result["available"] and result["stdout"] else None


def _windows_telemetry_snapshot() -> dict[str, Any]:
    if sys.platform != "win32":
        return {
            "physical_memory": {},
            "committed_memory": {},
            "pagefile": {},
            "paging": {},
            "disk_activity": {},
            "availability": {"windows_metrics": False},
        }
    command = (
        "$mem = Get-CimInstance Win32_OperatingSystem | Select-Object "
        "TotalVisibleMemorySize,FreePhysicalMemory; "
        "$pagefileUsage = Get-CimInstance Win32_PageFileUsage | Select-Object "
        "Name,CurrentUsage,PeakUsage,AllocatedBaseSize; "
        "$pagefileSetting = Get-CimInstance Win32_PageFileSetting | Select-Object "
        "Name,InitialSize,MaximumSize; "
        "$counter = Get-Counter '\\Memory\\Committed Bytes','\\Memory\\Commit Limit','\\Memory\\Available MBytes','\\Memory\\Pages Input/sec','\\Memory\\Page Reads/sec','\\Memory\\Pages Output/sec','\\Memory\\Page Writes/sec','\\PhysicalDisk(_Total)\\Disk Reads/sec','\\PhysicalDisk(_Total)\\Disk Writes/sec','\\PhysicalDisk(_Total)\\Avg. Disk sec/Read','\\PhysicalDisk(_Total)\\Avg. Disk sec/Write','\\PhysicalDisk(_Total)\\% Disk Time' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty CounterSamples | Select-Object Path,CookedValue; "
        "$payload = [ordered]@{ physical_memory = $mem; pagefile_usage = $pagefileUsage; pagefile_setting = $pagefileSetting; counters = $counter }; $payload | ConvertTo-Json -Depth 10 -Compress"
    )
    result = _command_output(
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        command,
    )
    if not result["available"] or not result["stdout"]:
        return {
            "physical_memory": {},
            "committed_memory": {},
            "pagefile": {},
            "paging": {},
            "disk_activity": {},
            "availability": {"windows_metrics": False, "reason": result.get("stderr") or result.get("error_type")},
        }
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return {
            "physical_memory": {},
            "committed_memory": {},
            "pagefile": {},
            "paging": {},
            "disk_activity": {},
            "availability": {"windows_metrics": False, "reason": "invalid_powershell_json"},
        }
    physical = payload.get("physical_memory") or {}
    total_kb = physical.get("TotalVisibleMemorySize")
    available_kb = physical.get("FreePhysicalMemory")
    physical_memory = {
        "total_kb": total_kb,
        "available_kb": available_kb,
        "total_gb": None if total_kb is None else round(total_kb * 1024 / GIB, 3),
        "available_gb": None if available_kb is None else round(available_kb * 1024 / GIB, 3),
        "in_use_gb": None if total_kb is None or available_kb is None else round((total_kb - available_kb) * 1024 / GIB, 3),
        "memory_load_percent": None if total_kb is None or available_kb is None else round((1 - (available_kb / total_kb)) * 100, 3),
    }
    counter_map = {}
    for item in payload.get("counters") or []:
        path = str(item.get("Path") or "")
        normalized = path.replace("\\\\nithin\\", "\\").replace("\\\\localhost\\", "\\")
        counter_map[normalized.lower()] = item.get("CookedValue")
    committed_bytes = counter_map.get("\\memory\\committed bytes")
    commit_limit_bytes = counter_map.get("\\memory\\commit limit")
    available_mbytes = counter_map.get("\\memory\\available mbytes")
    pages_input = counter_map.get("\\memory\\pages input/sec")
    page_reads = counter_map.get("\\memory\\page reads/sec")
    pages_output = counter_map.get("\\memory\\pages output/sec")
    page_writes = counter_map.get("\\memory\\page writes/sec")
    disk_reads = counter_map.get("\\physicaldisk(_total)\\disk reads/sec")
    disk_writes = counter_map.get("\\physicaldisk(_total)\\disk writes/sec")
    disk_avg_read = counter_map.get("\\physicaldisk(_total)\\avg. disk sec/read")
    disk_avg_write = counter_map.get("\\physicaldisk(_total)\\avg. disk sec/write")
    disk_util = counter_map.get("\\physicaldisk(_total)\\% disk time")
    usage = payload.get("pagefile_usage") or {}
    setting = payload.get("pagefile_setting") or {}
    if not isinstance(usage, list):
        usage = [usage] if isinstance(usage, dict) else []
    if not isinstance(setting, list):
        setting = [setting] if isinstance(setting, dict) else []
    pagefile_entry = usage[0] if usage else {}
    pagefile_setting_entry = setting[0] if setting else {}
    return {
        "physical_memory": physical_memory,
        "committed_memory": {
            "committed_bytes": committed_bytes,
            "commit_limit_bytes": commit_limit_bytes,
            "available_mbytes": available_mbytes,
            "commit_headroom_gb": None if committed_bytes is None or commit_limit_bytes is None else round((commit_limit_bytes - committed_bytes) / GIB, 3),
            "committed_percent": None if committed_bytes is None or commit_limit_bytes in (None, 0) else round(committed_bytes / commit_limit_bytes * 100, 3),
        },
        "pagefile": {
            "name": pagefile_entry.get("Name"),
            "current_usage_mb": pagefile_entry.get("CurrentUsage"),
            "peak_usage_mb": pagefile_entry.get("PeakUsage"),
            "allocated_mb": pagefile_entry.get("AllocatedBaseSize"),
            "configured_initial_mb": pagefile_setting_entry.get("InitialSize"),
            "configured_maximum_mb": pagefile_setting_entry.get("MaximumSize"),
            "current_usage_gb": None if pagefile_entry.get("CurrentUsage") is None else round(pagefile_entry["CurrentUsage"] / 1024, 3),
            "peak_usage_gb": None if pagefile_entry.get("PeakUsage") is None else round(pagefile_entry["PeakUsage"] / 1024, 3),
            "allocated_gb": None if pagefile_entry.get("AllocatedBaseSize") is None else round(pagefile_entry["AllocatedBaseSize"] / 1024, 3),
        },
        "paging": {
            "pages_input_per_sec": pages_input,
            "page_reads_per_sec": page_reads,
            "pages_output_per_sec": pages_output,
            "page_writes_per_sec": page_writes,
        },
        "disk_activity": {
            "read_bytes_per_sec": disk_reads,
            "write_bytes_per_sec": disk_writes,
            "avg_disk_sec_per_read": disk_avg_read,
            "avg_disk_sec_per_write": disk_avg_write,
            "disk_utilization_percent": disk_util,
        },
        "availability": {"windows_metrics": True},
    }


def _run_telemetry_check() -> Path:
    output_dir = RESULTS_ROOT / ("telemetry-check-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    output_dir.mkdir(parents=True, exist_ok=False)
    snapshots = []
    for index in range(3):
        time.sleep(1)
        snapshot = _windows_telemetry_snapshot()
        snapshot["snapshot_index"] = index + 1
        snapshots.append(snapshot)
    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshots": snapshots,
        "validations": {
            "commit_headroom_is_distinct_from_commit_limit": all(
                (item.get("committed_memory") or {}).get("commit_headroom_gb") is not None
                or (item.get("committed_memory") or {}).get("commit_limit_bytes") is not None
                for item in snapshots
            ),
            "pagefile_usage_is_distinct_from_allocated_capacity": all(
                (item.get("pagefile") or {}).get("current_usage_mb") is not None
                or (item.get("pagefile") or {}).get("allocated_mb") is not None
                for item in snapshots
            ),
            "unavailable_metrics_are_explicit": all(
                "availability" in item for item in snapshots
            ),
        },
    }
    _write_json(output_dir / "telemetry_diagnostic.json", report)
    return output_dir


def _powershell_ollama_processes() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    result = _command_output(
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-Process | Where-Object {$_.ProcessName -like 'ollama*'} | "
        "Select-Object ProcessName,Id,WorkingSet64,CPU | ConvertTo-Json -Compress",
    )
    if not result["available"] or not result["stdout"]:
        return []
    try:
        parsed = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return []
    items = parsed if isinstance(parsed, list) else [parsed]
    return [
        {
            "process_name": item.get("ProcessName"),
            "pid": item.get("Id"),
            "working_set_bytes": item.get("WorkingSet64"),
            "cpu_seconds": item.get("CPU"),
        }
        for item in items
        if isinstance(item, dict)
    ]


def _powershell_top_processes(limit: int = 12) -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    result = _command_output(
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        f"Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First {limit} "
        "ProcessName,Id,WorkingSet64 | ConvertTo-Json -Compress",
    )
    if not result["available"] or not result["stdout"]:
        return []
    try:
        parsed = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return []
    items = parsed if isinstance(parsed, list) else [parsed]
    return [
        {
            "process_name": item.get("ProcessName"),
            "pid": item.get("Id"),
            "working_set_bytes": item.get("WorkingSet64"),
        }
        for item in items
        if isinstance(item, dict)
    ]


def _process_cpu_seconds(processes: list[dict[str, Any]]) -> float | None:
    values = [
        item.get("cpu_seconds")
        for item in processes
        if isinstance(item.get("cpu_seconds"), (int, float))
    ]
    return sum(values) if values else None


def _get_model_state() -> dict[str, Any]:
    try:
        tags_response = _local_json("/api/tags")
        raw_models = tags_response.get("models")
        if not isinstance(raw_models, list):
            raise ValueError("Ollama model inventory is invalid.")
        qwen_models = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if not isinstance(name, str) or not name.startswith("qwen3:"):
                continue
            model_info = _model_cli_metadata(name)
            qwen_models.append(
                {
                    "tag": name,
                    "digest": item.get("digest"),
                    "size_bytes": item.get("size"),
                    "modified_at": item.get("modified_at"),
                    **model_info,
                }
            )
        ps_response = _local_json("/api/ps")
        loaded_models = ps_response.get("models")
        if not isinstance(loaded_models, list):
            loaded_models = []
        return {
            "available": True,
            "models": qwen_models,
            "loaded_models": [
                {
                    "name": item.get("name") or item.get("model"),
                    "size_bytes": item.get("size"),
                    "size_vram_bytes": item.get("size_vram"),
                    "context_length": item.get("context_length"),
                    "expires_at": item.get("expires_at"),
                }
                for item in loaded_models
                if isinstance(item, dict)
            ],
        }
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        return {
            "available": False,
            "models": [],
            "loaded_models": [],
            "error_type": type(error).__name__,
        }


def capture_environment(commit_sha: str | None) -> dict[str, Any]:
    memory = _read_memory()
    model_state = _get_model_state()
    ollama_version = _command_output("ollama", "--version")
    ollama_ps = _command_output("ollama", "ps")
    settings = get_agent_settings()
    profile_settings = {
        profile: {
            "max_output_tokens": resolve_task_budget(profile).max_output_tokens,
            "context_tokens": resolve_task_budget(profile).context_tokens,
            "timeout_seconds": min(
                settings.timeout_seconds,
                resolve_task_budget(profile).timeout_seconds,
            ),
            "reasoning_allowed": resolve_task_budget(profile).reasoning_allowed,
        }
        for profile in (
            "routing",
            "lightweight",
            "presentation",
            "analytical",
            "complex_reasoning",
        )
    }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": commit_sha,
        "operating_system": platform.platform(),
        "python_version": platform.python_version(),
        "processor": platform.processor() or None,
        "processor_name": _powershell_processor_name(),
        "logical_cpu_count": os.cpu_count(),
        "video_controllers": _powershell_video_controllers(),
        "system_memory": memory,
        "major_memory_consumers_initial": _powershell_top_processes(),
        "ollama_processes_initial": _powershell_ollama_processes(),
        "ollama_version": ollama_version,
        "ollama_ps_initial": ollama_ps,
        "ollama_api_initial_state": model_state,
        "fixtures_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "inference_configuration": {
            "provider": "ollama",
            "base_url": OLLAMA_BASE_URL,
            "model_selection": "explicit per request; production environment defaults unchanged",
            "configured_max_output_tokens": settings.max_output_tokens,
            "configured_num_ctx": settings.num_ctx,
            "workflow_token_budget": settings.workflow_token_budget,
            "workflow_final_reserve_tokens": settings.workflow_final_reserve_tokens,
            "reasoning_enabled_in_settings": settings.reasoning_enabled,
            "benchmark_keep_alive_seconds": 86_400,
            "resident_load_keep_alive": "indefinite (Ollama keep_alive=-1)",
            "temperature": "model tag default; the InsightOS provider does not override it",
            "thinking": "Qwen3 receives an explicit think value from the request policy; benchmark requests disable reasoning",
            "ollama_keep_alive_seconds": settings.ollama_keep_alive_seconds,
            "task_profiles": profile_settings,
        },
        "privacy": {
            "environment_secrets_recorded": False,
            "api_keys_recorded": False,
            "only_loopback_ollama_endpoint_used": True,
        },
    }


def _git_commit_sha() -> str | None:
    result = _command_output("git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD")
    return result["stdout"] if result["available"] else None


def _number_tokens(value: Any) -> set[str]:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    tokens = set()
    for match in NUMBER_PATTERN.findall(text):
        normalized = re.sub(r"[$€£\s,%]", "", match).replace(",", "")
        try:
            number = str(float(normalized)).rstrip("0").rstrip(".")
        except ValueError:
            continue
        tokens.add(number)
    return tokens


def _expected_number_set(values: list[int | float]) -> set[str]:
    return {str(value) for value in values}


def _load_object(content: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None, "invalid_json"
    if not isinstance(parsed, dict):
        return None, "top_level_not_object"
    return parsed, None


def _exact_keys(value: Any, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def _validate_workload(
    workload: dict[str, Any],
    content: str,
    *,
    handoff_valid: bool | None = None,
) -> dict[str, Any]:
    parsed, parse_error = _load_object(content)
    if parse_error:
        return {
            "json_valid": False,
            "schema_valid": False,
            "factual_accuracy": False,
            "instruction_following": False,
            "completeness": False,
            "hallucination_control": False,
            "errors": [parse_error],
        }

    errors: list[str] = []
    schema_valid = False
    factual_accuracy = False
    completeness = False
    workload_id = workload["id"]
    rendered = json.dumps(parsed, ensure_ascii=False).casefold()
    if workload_id == "task_classification":
        schema_valid = (
            set(parsed) == {"routes"}
            and isinstance(parsed.get("routes"), list)
            and all(
                _exact_keys(
                    item,
                    {
                        "request_type",
                        "task_category",
                        "required_agent_stages",
                        "required_capabilities",
                        "dataset_context_required",
                        "clarification_required",
                    },
                )
                and isinstance(item.get("required_agent_stages"), list)
                and isinstance(item.get("required_capabilities"), list)
                and isinstance(item.get("dataset_context_required"), bool)
                and isinstance(item.get("clarification_required"), bool)
                for item in parsed["routes"]
            )
        )
        factual_accuracy = parsed.get("routes") == workload["expected_routes"]
        completeness = schema_valid and len(parsed.get("routes", [])) == 7
        if not factual_accuracy:
            errors.append("route_answer_key_mismatch")
    elif workload_id == "business_summary":
        schema_valid = (
            set(parsed)
            == {"executive_summary", "trends", "exceptions", "limitations"}
            and isinstance(parsed.get("executive_summary"), str)
            and isinstance(parsed.get("trends"), list)
            and all(isinstance(item, str) for item in parsed.get("trends", []))
            and isinstance(parsed.get("exceptions"), list)
            and all(isinstance(item, str) for item in parsed.get("exceptions", []))
            and isinstance(parsed.get("limitations"), str)
        )
        numbers = _number_tokens(parsed)
        factual_accuracy = _expected_number_set(workload["required_numbers"]).issubset(numbers)
        exception_text = " ".join(parsed.get("exceptions", [])).casefold()
        completeness = (
            all(term in rendered for term in workload["required_terms"])
            and bool(parsed.get("executive_summary", "").strip())
            and bool(parsed.get("trends"))
            and "march" in exception_text
            and "returns" in exception_text
        )
        if not factual_accuracy:
            errors.append("required_source_values_missing")
    elif workload_id == "structured_presentation":
        highlights = parsed.get("highlights")
        schema_valid = (
            set(parsed) == {"title", "summary", "highlights", "warnings"}
            and isinstance(parsed.get("title"), str)
            and isinstance(parsed.get("summary"), str)
            and isinstance(highlights, list)
            and all(
                _exact_keys(item, {"label", "value"})
                and isinstance(item.get("label"), str)
                and isinstance(item.get("value"), str)
                for item in highlights
            )
            and isinstance(parsed.get("warnings"), list)
            and all(isinstance(item, str) for item in parsed.get("warnings", []))
        )
        numbers = _number_tokens(parsed)
        factual_accuracy = (
            _expected_number_set(workload["required_numbers"]).issubset(numbers)
            and numbers.issubset(_expected_number_set(workload["allowed_numbers"]))
        )
        completeness = (
            all(term in rendered for term in workload["required_terms"])
            and bool(highlights)
            and bool(parsed.get("warnings"))
        )
        if not factual_accuracy:
            errors.append("presentation_value_or_hallucination_check_failed")
    elif workload_id == "analytics_planning":
        array_fields = {
            "relevant_columns",
            "data_quality_concerns",
            "analysis_methods",
            "deterministic_engines",
            "validation_steps",
            "expected_outputs",
        }
        schema_valid = set(parsed) == array_fields and all(
            isinstance(parsed.get(field), list)
            and all(isinstance(item, str) for item in parsed[field])
            for field in array_fields
        )
        relevant = {str(item).casefold() for item in parsed.get("relevant_columns", [])}
        engines = {str(item).casefold() for item in parsed.get("deterministic_engines", [])}
        methods_text = " ".join(parsed.get("analysis_methods", [])).casefold()
        validation_text = " ".join(parsed.get("validation_steps", [])).casefold()
        factual_accuracy = (
            {"month", "revenue", "expenses", "region"}.issubset(relevant)
            and "12" in _number_tokens(parsed.get("data_quality_concerns", []))
            and "1" in _number_tokens(parsed.get("data_quality_concerns", []))
        )
        completeness = (
            {"run_quality_analysis", "run_eda_analysis", "run_monthly_forecast"}.issubset(engines)
            and all(term in rendered for term in workload["required_terms"])
            and "margin" in methods_text
            and "month" in methods_text
            and "holdout" in validation_text
            and bool(parsed.get("expected_outputs"))
        )
        numbers = _number_tokens(parsed)
        factual_accuracy = factual_accuracy and numbers.issubset(
            _expected_number_set(workload["allowed_numbers"])
        )
        if not factual_accuracy:
            errors.append("planning_facts_or_supported_tools_check_failed")
    elif workload_id == "complex_business_reasoning":
        array_fields = {
            "verified_facts",
            "business_implications",
            "conflicts_or_gaps",
            "assumptions",
            "next_actions",
        }
        schema_valid = set(parsed) == array_fields and all(
            isinstance(parsed.get(field), list)
            and all(isinstance(item, str) for item in parsed[field])
            for field in array_fields
        )
        numbers = _number_tokens(parsed)
        factual_accuracy = (
            _expected_number_set(workload["required_numbers"]).issubset(numbers)
            and numbers.issubset(_expected_number_set(workload["allowed_numbers"]))
        )
        completeness = (
            all(bool(parsed.get(field)) for field in array_fields)
            and bool(parsed.get("conflicts_or_gaps"))
            and bool(parsed.get("next_actions"))
            and all(term in rendered for term in workload["required_terms"])
        )
        if not factual_accuracy:
            errors.append("business_reasoning_fact_check_failed")
    elif workload_id == "specialist_handoff":
        schema_valid = handoff_valid is True
        factual_accuracy = handoff_valid is True
        completeness = handoff_valid is True
        if handoff_valid is not True:
            errors.append("typed_specialist_handoff_validation_failed")
    if not schema_valid:
        errors.append("schema_invalid")
    if not completeness:
        errors.append("required_content_missing")
    hallucination_control = (
        _number_tokens(parsed).issubset(_expected_number_set(workload.get("allowed_numbers", [])))
        if workload.get("allowed_numbers") is not None
        else True
    )
    if workload_id == "task_classification":
        hallucination_control = factual_accuracy
    if workload_id == "specialist_handoff":
        hallucination_control = handoff_valid is True
    if not hallucination_control:
        errors.append("unsupported_numeric_claim")
    return {
        "json_valid": True,
        "schema_valid": schema_valid,
        "factual_accuracy": factual_accuracy,
        "instruction_following": schema_valid and completeness,
        "completeness": completeness,
        "hallucination_control": hallucination_control,
        "errors": errors,
    }


class _CapturingProvider:
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider
        self.capabilities = provider.capabilities
        self.responses: list[str] = []
        self.telemetries: list[RequestTelemetry | None] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._provider.complete(request)
        self.responses.append(response.message.content)
        self.telemetries.append(response.telemetry)
        return response

    def check_health(self) -> ProviderHealth:
        return self._provider.check_health()


class _MemoryMonitor:
    def __init__(self) -> None:
        self.samples: list[dict[str, float | int | None]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.is_set():
            memory = _read_memory()
            if memory is not None:
                self.samples.append(memory)
            self._stop.wait(0.5)

    def __enter__(self) -> "_MemoryMonitor":
        memory = _read_memory()
        if memory is not None:
            self.samples.append(memory)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    @property
    def minimum_free_gb(self) -> float | None:
        return min(
            (sample["free_gb"] for sample in self.samples),
            default=None,
        )

    @property
    def severe_pressure(self) -> bool:
        return any(_memory_pressure_is_severe(sample) for sample in self.samples)

    @property
    def pressure_warning(self) -> bool:
        return bool(self.samples) and any(
            sample["free_gb"] < MEMORY_SAFETY_RESERVE_GB
            for sample in self.samples
        )


def _memory_pressure_is_severe(memory: dict[str, Any] | None) -> bool:
    if memory is None:
        return False
    free_gb = memory.get("free_gb")
    memory_load = memory.get("memory_load_percent")
    committed_gb = memory.get("committed_gb")
    commit_limit_gb = memory.get("commit_limit_gb")
    commit_headroom_gb = memory.get("commit_headroom_gb")
    pagefile_used_gb = memory.get("pagefile_used_gb")
    pagefile_total_gb = memory.get("pagefile_total_gb")
    pagefile_usage_percent = memory.get("pagefile_usage_percent")

    if (
        isinstance(free_gb, (int, float))
        and free_gb < SEVERE_FREE_RAM_GB
    ) or (
        isinstance(memory_load, (int, float))
        and memory_load >= SEVERE_MEMORY_LOAD_PERCENT
    ):
        return True

    if isinstance(commit_headroom_gb, (int, float)):
        if commit_headroom_gb <= SEVERE_COMMIT_HEADROOM_GB:
            return True
    elif (
        isinstance(committed_gb, (int, float))
        and isinstance(commit_limit_gb, (int, float))
        and commit_limit_gb > 0
        and (commit_limit_gb - committed_gb) <= SEVERE_COMMIT_HEADROOM_GB
    ):
        return True

    if isinstance(committed_gb, (int, float)) and isinstance(commit_limit_gb, (int, float)):
        if commit_limit_gb > 0 and committed_gb >= commit_limit_gb * 0.95:
            return True

    if isinstance(pagefile_usage_percent, (int, float)):
        if pagefile_usage_percent >= SEVERE_PAGEFILE_USAGE_PERCENT:
            return True
    elif (
        isinstance(pagefile_used_gb, (int, float))
        and isinstance(pagefile_total_gb, (int, float))
        and pagefile_total_gb > 0
        and (pagefile_used_gb / pagefile_total_gb) >= 0.95
    ):
        return True

    return False


def _model_is_loaded(tag: str, loaded_models: list[dict[str, Any]]) -> bool:
    return any(
        item.get("name") == tag or item.get("model") == tag
        for item in loaded_models
    )


def _only_model_is_loaded(tag: str, loaded_models: list[dict[str, Any]]) -> bool:
    loaded_names = {
        item.get("name") or item.get("model")
        for item in loaded_models
        if isinstance(item.get("name") or item.get("model"), str)
    }
    return loaded_names == {tag}


def _workloads_for_model(
    workloads: list[dict[str, Any]],
    model_tag: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entry = REGISTRY.require(model_tag)
    eligible_roles = entry.eligible_roles
    specialist_roles = {
        "data_intelligence",
        "analytics_modelling",
        "insights_visualization",
    }
    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for workload in workloads:
        supported = (
            specialist_roles.issubset(eligible_roles)
            if workload["id"] == "specialist_handoff"
            else workload["role"] in eligible_roles
        )
        (eligible if supported else skipped).append(workload)
    return eligible, skipped


def _required_free_gb(model: dict[str, Any], *, already_loaded: bool) -> float:
    if already_loaded:
        return SEVERE_FREE_RAM_GB
    size_bytes = model.get("size_bytes")
    weight_gb = size_bytes / GIB if isinstance(size_bytes, (int, float)) else 0.0
    return max(SEVERE_FREE_RAM_GB, weight_gb + MEMORY_SAFETY_RESERVE_GB)


def _load_model_resident(tag: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        payload = _local_json(
            "/api/generate",
            {
                "model": tag,
                "prompt": "",
                "stream": False,
                "keep_alive": -1,
            },
            timeout_seconds=300,
        )
    except urllib.error.HTTPError as error:
        raw = error.read(2 * 1024 * 1024 + 1)
        details: Any = None
        if len(raw) <= 2 * 1024 * 1024:
            try:
                details = json.loads(raw)
            except json.JSONDecodeError:
                details = None
        message = (
            details.get("error")
            if isinstance(details, dict) and isinstance(details.get("error"), str)
            else f"Ollama returned HTTP {error.code} while loading the model."
        )
        return {
            "loaded": False,
            "error": message,
            "http_status": error.code,
            "wall_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as error:
        return {
            "loaded": False,
            "error": str(error)[:500] or type(error).__name__,
            "error_type": type(error).__name__,
            "wall_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    error_message = payload.get("error")
    if isinstance(error_message, str) and error_message:
        return {
            "loaded": False,
            "error": error_message[:500],
            "wall_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    load_duration = payload.get("load_duration")
    return {
        "loaded": True,
        "wall_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "load_duration_ms": (
            round(load_duration / 1_000_000, 3)
            if isinstance(load_duration, (int, float)) and load_duration >= 0
            else None
        ),
        "total_duration_ms": (
            round(payload["total_duration"] / 1_000_000, 3)
            if isinstance(payload.get("total_duration"), (int, float))
            and payload["total_duration"] >= 0
            else None
        ),
    }


def _ollama_loaded_models() -> list[dict[str, Any]]:
    try:
        response = _local_json("/api/ps")
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return []
    models = response.get("models")
    return [item for item in models if isinstance(item, dict)] if isinstance(models, list) else []


def _unload_benchmark_model(tag: str) -> None:
    response = _local_json("/api/generate", {"model": tag, "keep_alive": 0})
    if isinstance(response.get("error"), str) and response["error"]:
        raise ValueError("Ollama rejected the benchmark model unload request.")


def _route_completion(
    router: AgentModelRouter,
    *,
    model: str,
    role: str,
    category: str,
    prompt: str,
    prompt_variant: PromptVariant,
) -> tuple[str, str]:
    system_instruction = (
        "You are being evaluated on a controlled InsightOS benchmark. "
        "Return exactly the requested JSON object, use only supplied facts, "
        "and do not claim to execute tools."
    )
    if prompt_variant == "optimized":
        profile = SPECIALIST_PROMPT_PROFILES[role]
        model_instruction = model_specialization_instruction(model, role)
        system_instruction = " ".join(
            item
            for item in (profile.instruction, model_instruction, system_instruction)
            if item
        )
    request = ModelRequest(
        model=model,
        messages=(
            ProviderMessage(
                role="system",
                content=system_instruction,
            ),
            ProviderMessage(role="user", content=prompt),
        ),
        tools=(),
        max_output_tokens=resolve_task_budget(category).max_output_tokens,
        response_format="json",
        task_category=category,
        reasoning_enabled=False,
        is_final=True,
    )
    completion = router.complete(role, request)
    response = completion.response
    return response.message.content, response.finish_reason


def _run_handoff(
    router: AgentModelRouter,
    *,
    workflow_id: str,
    prompt_variant: PromptVariant,
) -> tuple[dict[str, Any], dict[str, Any]]:
    operation = "run_quality_analysis"
    arguments = {"dataset_id": 501, "file_id": 601, "rules": "default"}
    evidence = [{"type": "benchmark_fixture", "id": "synthetic-inspection-01"}]
    inspection = {
        "dataset_id": 501,
        "file_id": 601,
        "source_fingerprint": "benchmark-fixture-v1",
        "file": {"file_name": "synthetic_sales.csv"},
        "profile": {
            "tables": [
                {
                    "row_count": 1200,
                    "column_count": 3,
                    "columns": [
                        {"name": "month", "physical_types": ["date"], "missing_value_count": 0},
                        {"name": "revenue", "physical_types": ["float"], "missing_value_count": 0},
                        {"name": "expenses", "physical_types": ["float"], "missing_value_count": 12},
                    ],
                }
            ]
        },
    }
    objective = "Evaluate the synthetic fixture's quality and present only supported results."
    data_result, data_model = run_data_intelligence(
        router,
        objective=objective,
        workflow_id=workflow_id,
        conversation_id=None,
        inspection=inspection,
        operation=operation,
        evidence_references=evidence,
        prompt_variant=prompt_variant,
    )
    data_payload = data_result.model_dump(mode="json")
    analytics_result, analytics_model = run_analytics_decision(
        router,
        objective=objective,
        workflow_id=workflow_id,
        conversation_id=None,
        inspection=inspection,
        data_intelligence=data_payload,
        tool_name=operation,
        arguments=arguments,
        prompt_variant=prompt_variant,
    )
    analytics_payload = analytics_result.model_dump(mode="json")
    engine_result = {
        "status": "not_executed",
        "summary": "Benchmark simulation only; no deterministic engine was called.",
        "data": {"fixture": "synthetic", "execution_performed": False},
    }
    presentation_result, presentation_model = run_presentation(
        router,
        objective=objective,
        workflow_id=workflow_id,
        conversation_id=None,
        data_intelligence=data_payload,
        engine_result=engine_result,
        allowed_references=evidence,
        prompt_variant=prompt_variant,
    )
    presentation_payload = presentation_result.model_dump(mode="json")
    outputs = [
        (data_result, data_model, "data_intelligence", operation),
        (analytics_result, analytics_model, "analytics_modelling", operation),
        (presentation_result, presentation_model, "insights_visualization", operation),
    ]
    handoffs = []
    for result, model_name, stage, requested_operation in outputs:
        _, envelope, _ = build_specialist_handoff(
            stage=stage,
            operation=requested_operation,
            source_fingerprint="benchmark-fixture-v1",
            dataset_id=501,
            file_id=601,
            dependency_task_ids=[],
            output=result,
            model_name=model_name,
            parameters=arguments if stage == "analytics_modelling" else {},
            actual_operations=[],
            warnings=["No deterministic engine was executed in this benchmark simulation."],
        )
        handoffs.append(envelope)
    stages_valid = (
        isinstance(data_result, DataIntelligenceResult)
        and data_result.recommended_operation == operation
        and set(data_result.observed_fields).issubset({"month", "revenue", "expenses"})
        and data_result.evidence_references == evidence
        and isinstance(analytics_result, AnalyticsDecision)
        and analytics_result.status == "approved"
        and analytics_result.tool_name == operation
        and analytics_result.arguments == arguments
        and analytics_result.evidence_references == evidence
        and isinstance(presentation_result, PresentationResult)
        and presentation_result.evidence_references == evidence
        and presentation_result.visualization_specification is None
        and len(handoffs) == 3
        and [item["handoff"]["stage"] for item in handoffs]
        == ["data_intelligence", "analytics_modelling", "insights_visualization"]
    )
    return (
        {
            "data_intelligence": data_payload,
            "analytics_modelling": analytics_payload,
            "insights_visualization": presentation_payload,
        },
        {
            "valid": stages_valid,
            "engine_executed": False,
            "handoffs": handoffs,
            "models_returned": [data_model, analytics_model, presentation_model],
        },
    )


def _new_case_result(
    model: str,
    workload: dict[str, Any],
    repetition: int,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "model": model,
        "workload": workload["id"],
        "repetition": repetition,
        "status": status,
        "skip_reason": reason,
        "cold_or_warm": None,
        "response": None,
        "actual_stage_responses": None,
        "validation": None,
        "quality_scores": None,
        "measurements": {
            "first_token_latency_ms": None,
            "request_latency_ms": None,
            "cold_start_latency_ms": None,
            "load_duration_ms": None,
            "prompt_eval_duration_ms": None,
            "generation_duration_ms": None,
            "prompt_tokens": None,
            "generated_tokens": None,
            "total_tokens": None,
            "tokens_per_second": None,
            "finish_reason": None,
            "truncated": None,
            "system_memory_before_gb": None,
            "system_memory_after_gb": None,
            "system_memory_minimum_during_gb": None,
            "system_memory_load_percent_before": None,
            "system_memory_load_percent_after": None,
            "system_memory_load_percent_maximum_during": None,
            "system_memory_samples_during": None,
            "memory_estimate_warning": None,
            "memory_pressure_severe": None,
            "loaded_model_state_before": None,
            "ollama_process_state_before": None,
            "ollama_process_state_after": None,
            "ollama_process_memory_before": None,
            "ollama_process_memory_after": None,
            "major_memory_consumers_before": None,
            "major_memory_consumers_after": None,
            "ollama_process_cpu_percent_of_machine": None,
            "loaded_model_state_after": None,
        },
        "inference_settings": None,
        "error": None,
    }


def _mark_truncated_result(result: dict[str, Any]) -> None:
    result["status"] = "failed"
    result["error"] = {
        "code": "response_truncated",
        "type": "IncompleteModelResponse",
    }
    validation = result.get("validation")
    if not isinstance(validation, dict):
        validation = {
            "json_valid": False,
            "factual_accuracy": False,
            "hallucination_control": False,
            "errors": [],
        }
    validation.update(
        {
            "schema_valid": False,
            "instruction_following": False,
            "completeness": False,
        }
    )
    validation.setdefault("errors", []).append("response_truncated")
    result["validation"] = validation


def _record_failed_attempt(
    result: dict[str, Any],
    *,
    error_code: str | None,
    error_type: str,
    error_telemetry: RequestTelemetry | None,
    workload: dict[str, Any],
    provider: _CapturingProvider,
    memory_before: dict[str, Any] | None,
    process_before: list[dict[str, Any]],
    loaded_models_before: list[dict[str, Any]],
    ollama_state_before: dict[str, Any],
    consumers_before: list[dict[str, Any]],
    monitor: _MemoryMonitor | None,
    started: float,
    was_loaded: bool,
) -> None:
    result["status"] = "failed"
    result["error"] = {"type": error_type}
    if error_code:
        result["error"]["code"] = (
            "response_truncated"
            if error_code == "agent_response_incomplete"
            else error_code
        )
    if provider.responses:
        if workload["id"] == "specialist_handoff":
            result["actual_stage_responses"] = list(provider.responses)
        else:
            result["response"] = provider.responses[-1]
    telemetry_records = [item for item in provider.telemetries if item is not None]
    if not telemetry_records and error_telemetry is not None:
        telemetry_records = [error_telemetry]

    def sum_metric(name: str) -> float | int | None:
        values = [getattr(item, name) for item in telemetry_records]
        if not values or any(value is None for value in values):
            return None
        return sum(values)

    measurements = result["measurements"]
    elapsed_ms = (time.perf_counter() - started) * 1000
    latest = telemetry_records[-1] if telemetry_records else error_telemetry
    request_duration = sum_metric("request_duration_ms")
    measurements["request_latency_ms"] = round(
        elapsed_ms
        if workload["id"] == "specialist_handoff" and len(telemetry_records) > 1
        else request_duration
        if request_duration is not None
        else elapsed_ms,
        3,
    )
    measurements["load_duration_ms"] = sum_metric("model_load_duration_ms")
    measurements["prompt_eval_duration_ms"] = sum_metric("prompt_eval_duration_ms")
    measurements["generation_duration_ms"] = sum_metric("generation_duration_ms")
    measurements["prompt_tokens"] = sum_metric("input_tokens")
    measurements["generated_tokens"] = sum_metric("output_tokens")
    measurements["total_tokens"] = (
        measurements["prompt_tokens"] + measurements["generated_tokens"]
        if measurements["prompt_tokens"] is not None
        and measurements["generated_tokens"] is not None
        else None
    )
    if (
        measurements["generated_tokens"] is not None
        and measurements["generation_duration_ms"] is not None
        and measurements["generation_duration_ms"] > 0
    ):
        measurements["tokens_per_second"] = (
            measurements["generated_tokens"]
            / (measurements["generation_duration_ms"] / 1000)
        )
    measurements["finish_reason"] = (
        latest.finish_reason if latest is not None else None
    )
    measurements["truncated"] = (
        error_code == "agent_response_incomplete"
        or any(item.incomplete for item in telemetry_records)
    )
    if (
        not was_loaded
        and telemetry_records
        and telemetry_records[0].model_load_duration_ms is not None
        and telemetry_records[0].model_load_duration_ms > 0
    ):
        measurements["cold_start_latency_ms"] = measurements["request_latency_ms"]
    measurements["system_memory_before_gb"] = (
        memory_before["free_gb"] if memory_before else None
    )
    measurements["system_memory_load_percent_before"] = (
        memory_before.get("memory_load_percent") if memory_before else None
    )
    memory_after = _read_memory()
    measurements["system_memory_after_gb"] = (
        memory_after["free_gb"] if memory_after else None
    )
    measurements["system_memory_load_percent_after"] = (
        memory_after.get("memory_load_percent") if memory_after else None
    )
    measurements["system_memory_minimum_during_gb"] = (
        monitor.minimum_free_gb if monitor is not None else None
    )
    measurements["system_memory_samples_during"] = (
        monitor.samples if monitor is not None else None
    )
    measurements["system_memory_load_percent_maximum_during"] = (
        max(
            (
                sample["memory_load_percent"]
                for sample in monitor.samples
                if isinstance(sample.get("memory_load_percent"), (int, float))
            ),
            default=None,
        )
        if monitor is not None
        else None
    )
    measurements["memory_pressure_severe"] = (
        monitor.severe_pressure if monitor is not None else False
    )
    measurements["loaded_model_state_before"] = loaded_models_before
    measurements["loaded_model_state_after"] = _ollama_loaded_models()
    measurements["ollama_process_state_before"] = ollama_state_before
    measurements["ollama_process_state_after"] = _command_output("ollama", "ps")
    measurements["ollama_process_memory_before"] = process_before
    process_after = _powershell_ollama_processes()
    measurements["ollama_process_memory_after"] = process_after
    measurements["major_memory_consumers_before"] = consumers_before
    measurements["major_memory_consumers_after"] = _powershell_top_processes()
    cpu_before = _process_cpu_seconds(process_before)
    cpu_after = _process_cpu_seconds(process_after)
    if (
        cpu_before is not None
        and cpu_after is not None
        and cpu_after >= cpu_before
        and measurements["request_latency_ms"] > 0
        and os.cpu_count()
    ):
        measurements["ollama_process_cpu_percent_of_machine"] = round(
            (cpu_after - cpu_before)
            / (measurements["request_latency_ms"] / 1000)
            / os.cpu_count()
            * 100,
            2,
        )
    if measurements["truncated"]:
        _mark_truncated_result(result)
    if monitor is not None and monitor.severe_pressure:
        result["status"] = "aborted_memory_pressure"
        result["skip_reason"] = (
            f"Severe system memory pressure observed; minimum available RAM was "
            f"{monitor.minimum_free_gb} GB."
        )


def _run_one(
    model_tag: str,
    workload: dict[str, Any],
    repetition: int,
    settings: AgentSettings,
    model_metadata: dict[str, Any],
    provider: _CapturingProvider,
    router: AgentModelRouter,
    prompt_variant: PromptVariant,
) -> dict[str, Any]:
    result = _new_case_result(model_tag, workload, repetition, "failed")
    provider.responses.clear()
    provider.telemetries.clear()
    memory_before = _read_memory()
    process_before = _powershell_ollama_processes()
    state_before = _ollama_loaded_models()
    ollama_state_before = _command_output("ollama", "ps")
    consumers_before = _powershell_top_processes()
    was_loaded = _model_is_loaded(model_tag, state_before)
    profile = resolve_task_budget(workload["category"])
    result["cold_or_warm"] = "warm" if was_loaded else "cold_candidate"
    result["inference_settings"] = {
        "provider": "ollama",
        "model": model_tag,
        "token_profile": workload["category"],
        "profile_output_token_limit": min(profile.max_output_tokens, settings.max_output_tokens),
        "effective_output_token_limit": min(
            profile.max_output_tokens,
            settings.max_output_tokens,
            settings.workflow_token_budget,
        ),
        "workflow_token_budget_tokens": settings.workflow_token_budget,
        "profile_context_token_limit": profile.context_tokens,
        "configured_num_ctx": settings.num_ctx,
        "effective_ollama_num_ctx": (
            min(settings.num_ctx, profile.context_tokens)
            if settings.num_ctx is not None
            else None
        ),
        "ollama_num_ctx_option": (
            min(settings.num_ctx, profile.context_tokens)
            if settings.num_ctx is not None
            else "omitted; Ollama model/server default applies"
        ),
        "timeout_seconds": min(settings.timeout_seconds, profile.timeout_seconds),
        "temperature": model_metadata.get("temperature_default")
        or "model tag default; not overridden",
        "other_model_default_parameters": model_metadata.get("default_parameters"),
        "thinking": "false (explicit Qwen3 request policy; reasoning disabled)",
        "prompt_variant": prompt_variant,
        "prompt_version": (
            "baseline"
            if prompt_variant == "baseline"
            else BI_PROMPT_VERSION
        ),
        "reasoning_allowed_by_profile": profile.reasoning_allowed,
        "repetition_count": repetition,
        "request_format": "json",
    }
    started = time.perf_counter()
    monitor: _MemoryMonitor | None = None
    try:
        with _MemoryMonitor() as monitor:
            if workload["id"] == "specialist_handoff":
                workflow_id = uuid.uuid4().hex
                response, handoff = _run_handoff(
                    router,
                    workflow_id=workflow_id,
                    prompt_variant=prompt_variant,
                )
                content = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
                validation = _validate_workload(
                    workload,
                    content,
                    handoff_valid=handoff["valid"],
                )
                result["handoff"] = handoff
                result["response"] = response
                result["actual_stage_responses"] = list(provider.responses)
                result["validation"] = validation
                finish_reason = (
                    provider.telemetries[-1].finish_reason
                    if provider.telemetries and provider.telemetries[-1]
                    else None
                )
            else:
                content, finish_reason = _route_completion(
                    router,
                    model=model_tag,
                    role=workload["role"],
                    category=workload["category"],
                    prompt=workload["prompt"],
                    prompt_variant=prompt_variant,
                )
                validation = _validate_workload(workload, content)
                result["response"] = content
                result["validation"] = validation
            elapsed_ms = (time.perf_counter() - started) * 1000.0
        telemetry_records = [
            item for item in provider.telemetries if item is not None
        ]
        telemetry = telemetry_records[-1] if telemetry_records else None

        def sum_metric(name: str) -> float | int | None:
            values = [getattr(item, name) for item in telemetry_records]
            if not values or any(value is None for value in values):
                return None
            return sum(values)

        measurements = result["measurements"]
        measurements["request_latency_ms"] = round(
            elapsed_ms
            if workload["id"] == "specialist_handoff"
            else telemetry.request_duration_ms
            if telemetry and telemetry.request_duration_ms is not None
            else elapsed_ms,
            3,
        )
        measurements["first_token_latency_ms"] = None
        measurements["cold_start_latency_ms"] = (
            measurements["request_latency_ms"]
            if not was_loaded
            and telemetry_records
            and telemetry_records[0].model_load_duration_ms is not None
            and telemetry_records[0].model_load_duration_ms > 0
            else None
        )
        if telemetry_records:
            input_tokens = sum_metric("input_tokens")
            output_tokens = sum_metric("output_tokens")
            generation_duration_ms = sum_metric("generation_duration_ms")
            measurements.update(
                {
                    "load_duration_ms": sum_metric("model_load_duration_ms"),
                    "prompt_eval_duration_ms": sum_metric("prompt_eval_duration_ms"),
                    "generation_duration_ms": generation_duration_ms,
                    "prompt_tokens": input_tokens,
                    "generated_tokens": output_tokens,
                    "tokens_per_second": (
                        output_tokens / (generation_duration_ms / 1000)
                        if output_tokens is not None
                        and generation_duration_ms is not None
                        and generation_duration_ms > 0
                        else None
                    ),
                    "finish_reason": telemetry.finish_reason or finish_reason,
                    "truncated": any(item.incomplete for item in telemetry_records),
                }
            )
            if input_tokens is not None and output_tokens is not None:
                measurements["total_tokens"] = input_tokens + output_tokens
        measurements["system_memory_before_gb"] = (
            memory_before["free_gb"] if memory_before else None
        )
        measurements["system_memory_load_percent_before"] = (
            memory_before.get("memory_load_percent") if memory_before else None
        )
        memory_after = _read_memory()
        measurements["system_memory_after_gb"] = (
            memory_after["free_gb"] if memory_after else None
        )
        measurements["system_memory_load_percent_after"] = (
            memory_after.get("memory_load_percent") if memory_after else None
        )
        measurements["system_memory_minimum_during_gb"] = monitor.minimum_free_gb
        measurements["system_memory_load_percent_maximum_during"] = max(
            (
                sample["memory_load_percent"]
                for sample in monitor.samples
                if isinstance(sample.get("memory_load_percent"), (int, float))
            ),
            default=None,
        )
        measurements["system_memory_samples_during"] = monitor.samples
        measurements["memory_estimate_warning"] = (
            f"Available memory dipped below the model-size estimate of "
            f"{_required_free_gb(model_metadata, already_loaded=True):.2f} GB; "
            "the estimate is advisory and did not block the request."
            if monitor.minimum_free_gb is not None
            and monitor.minimum_free_gb
            < _required_free_gb(model_metadata, already_loaded=False)
            else None
        )
        measurements["memory_pressure_severe"] = monitor.severe_pressure
        measurements["loaded_model_state_after"] = _ollama_loaded_models()
        measurements["loaded_model_state_before"] = state_before
        measurements["ollama_process_state_before"] = ollama_state_before
        measurements["ollama_process_state_after"] = _command_output("ollama", "ps")
        process_after = _powershell_ollama_processes()
        measurements["ollama_process_memory_before"] = process_before
        measurements["ollama_process_memory_after"] = process_after
        measurements["major_memory_consumers_before"] = consumers_before
        measurements["major_memory_consumers_after"] = _powershell_top_processes()
        cpu_before = _process_cpu_seconds(process_before)
        cpu_after = _process_cpu_seconds(process_after)
        elapsed_seconds = elapsed_ms / 1000
        if (
            cpu_before is not None
            and cpu_after is not None
            and cpu_after >= cpu_before
            and elapsed_seconds > 0
            and os.cpu_count()
        ):
            measurements["ollama_process_cpu_percent_of_machine"] = round(
                (cpu_after - cpu_before) / elapsed_seconds / os.cpu_count() * 100,
                2,
            )
        if monitor.severe_pressure:
            result["status"] = "aborted_memory_pressure"
            result["skip_reason"] = (
                f"Severe system memory pressure observed; minimum available RAM was "
                f"{monitor.minimum_free_gb} GB."
            )
        elif measurements["truncated"]:
            _mark_truncated_result(result)
        else:
            result["status"] = "completed"
    except (ProviderError, SpecialistAgentError) as error:
        _record_failed_attempt(
            result,
            error_code=error.code,
            error_type=type(error).__name__,
            error_telemetry=error.telemetry if isinstance(error, ProviderError) else None,
            workload=workload,
            provider=provider,
            memory_before=memory_before,
            process_before=process_before,
            loaded_models_before=state_before,
            ollama_state_before=ollama_state_before,
            consumers_before=consumers_before,
            monitor=monitor,
            started=started,
            was_loaded=was_loaded,
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as error:
        _record_failed_attempt(
            result,
            error_code=None,
            error_type=type(error).__name__,
            error_telemetry=None,
            workload=workload,
            provider=provider,
            memory_before=memory_before,
            process_before=process_before,
            loaded_models_before=state_before,
            ollama_state_before=ollama_state_before,
            consumers_before=consumers_before,
            monitor=monitor,
            started=started,
            was_loaded=was_loaded,
        )
    return result


def _apply_quality_scores(results: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault((result["model"], result["workload"]), []).append(result)
    for cases in grouped.values():
        completed = [item for item in cases if item["status"] == "completed"]
        consistent: bool | None = None
        if len(completed) >= 2:
            outputs = [
                json.dumps(item["response"], ensure_ascii=False, sort_keys=True)
                for item in completed
            ]
            consistent = len(set(outputs)) == 1
        for item in cases:
            validation = item.get("validation") or {}
            item_consistency = consistent if item["status"] == "completed" else None
            validation["consistency"] = item_consistency
            item["validation"] = validation if item.get("validation") is not None else None
            measurements = item["measurements"]
            latency = measurements.get("request_latency_ms")
            timeout = (item.get("inference_settings") or {}).get("timeout_seconds")
            latency_score = None
            if (
                item["status"] in {"completed", "failed"}
                and (item.get("error") or {}).get("code") != "provider_timeout"
                and latency is not None
                and timeout is not None
            ):
                latency_score = float(latency <= timeout * 1000)
            elif (item.get("error") or {}).get("code") == "provider_timeout":
                latency_score = 0.0
            minimum_free = measurements.get("system_memory_minimum_during_gb")
            resource_score = None
            if item["status"] == "completed" and minimum_free is not None:
                resource_score = float(minimum_free >= SEVERE_FREE_RAM_GB)
            elif (
                item["status"] == "aborted_memory_pressure"
                and measurements.get("request_latency_ms") is not None
            ):
                resource_score = 0.0
            item["quality_scores"] = {
                "factual_accuracy": (
                    float(validation["factual_accuracy"])
                    if "factual_accuracy" in validation
                    else None
                ),
                "instruction_following": (
                    float(validation["instruction_following"])
                    if "instruction_following" in validation
                    else None
                ),
                "structured_output": (
                    float(validation["schema_valid"])
                    if "schema_valid" in validation
                    else None
                ),
                "completeness": (
                    float(validation["completeness"])
                    if "completeness" in validation
                    else None
                ),
                "hallucination_control": (
                    float(validation["hallucination_control"])
                    if "hallucination_control" in validation
                    else None
                ),
                "consistency": (
                    float(item_consistency) if item_consistency is not None else None
                ),
                "latency": latency_score,
                "resource_efficiency": resource_score,
            }


def _role_recommendations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = (
        {
            "model": "qwen3:8b",
            "intended_role": "Complex reasoning, analytics planning, business interpretation, and evidence synthesis",
            "workloads": [
                "analytics_planning",
                "complex_business_reasoning",
                "specialist_handoff",
            ],
        },
        {
            "model": "qwen3:4b",
            "intended_role": "Task classification, routing, workflow decisions, and handoff preparation",
            "workloads": [
                "task_classification",
                "analytics_planning",
                "structured_presentation",
                "specialist_handoff",
            ],
        },
        {
            "model": "qwen3:4b-instruct",
            "intended_role": "Instruction-tuned 4B comparison for classification, planning, and handoff",
            "workloads": [
                "task_classification",
                "analytics_planning",
                "structured_presentation",
                "specialist_handoff",
            ],
        },
        {
            "model": "qwen3:0.6b",
            "intended_role": "Formatting, lightweight summaries, and presentation assistance",
            "workloads": [
                "business_summary",
                "structured_presentation",
                "specialist_handoff",
            ],
        },
    )
    recommendations = []
    for role in roles:
        cases = [
            item
            for item in results
            if item["model"] == role["model"]
            and item["workload"] in role["workloads"]
        ]
        completed = [item for item in cases if item["status"] == "completed"]
        critical_failure = any(
            item.get("status") == "failed"
            and (item.get("error") or {}).get("code") == "response_truncated"
            for item in cases
        ) or any(
            item["validation"]
            and (
                not item["validation"]["schema_valid"]
                or not item["validation"]["hallucination_control"]
                or not item["validation"]["factual_accuracy"]
            )
            for item in completed
        )
        if not completed:
            classification = "Insufficient evidence"
        elif critical_failure:
            classification = "Not suitable"
        elif (
            len(completed) < len(role["workloads"]) * 3
            or any(item["validation"].get("consistency") is None for item in completed)
        ):
            classification = "Insufficient evidence"
        elif all(
            item["validation"]["schema_valid"]
            and item["validation"]["factual_accuracy"]
            and item["validation"]["instruction_following"]
            and item["validation"]["completeness"]
            and item["validation"]["hallucination_control"]
            and item["validation"].get("consistency") is True
            for item in completed
        ):
            classification = "Suitable"
        else:
            classification = "Conditionally suitable"
        recommendations.append(
            {
                **role,
                "classification": classification,
                "completed_role_workloads": sorted({item["workload"] for item in completed}),
                "reason": (
                    "At least one required workload failed a factual, schema, or unsupported-claim check."
                    if critical_failure
                    else "Live coverage and repeated consistency evidence are not yet sufficient."
                    if classification == "Insufficient evidence"
                    else "The tested role workloads passed deterministic checks."
                ),
            }
        )
    return recommendations


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _score_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        validation = result.get("validation") or {}
        measurements = result["measurements"]
        rows.append(
            {
                "model": result["model"],
                "workload": result["workload"],
                "repetition": result["repetition"],
                "status": result["status"],
                "factual_accuracy": validation.get("factual_accuracy"),
                "instruction_following": validation.get("instruction_following"),
                "structured_output": validation.get("schema_valid"),
                "completeness": validation.get("completeness"),
                "hallucination_control": validation.get("hallucination_control"),
                "consistency": validation.get("consistency"),
                **{
                    f"score_{name}": value
                    for name, value in (result.get("quality_scores") or {}).items()
                },
                "request_latency_ms": measurements.get("request_latency_ms"),
                "first_token_latency_ms": measurements.get("first_token_latency_ms"),
                "cold_start_latency_ms": measurements.get("cold_start_latency_ms"),
                "load_duration_ms": measurements.get("load_duration_ms"),
                "prompt_eval_duration_ms": measurements.get("prompt_eval_duration_ms"),
                "generation_duration_ms": measurements.get("generation_duration_ms"),
                "prompt_tokens": measurements.get("prompt_tokens"),
                "generated_tokens": measurements.get("generated_tokens"),
                "total_tokens": measurements.get("total_tokens"),
                "tokens_per_second": measurements.get("tokens_per_second"),
                "finish_reason": measurements.get("finish_reason"),
                "truncated": measurements.get("truncated"),
                "system_memory_before_gb": measurements.get("system_memory_before_gb"),
                "system_memory_minimum_during_gb": measurements.get(
                    "system_memory_minimum_during_gb"
                ),
                "system_memory_after_gb": measurements.get("system_memory_after_gb"),
                "system_memory_load_percent_before": measurements.get(
                    "system_memory_load_percent_before"
                ),
                "system_memory_load_percent_after": measurements.get(
                    "system_memory_load_percent_after"
                ),
                "system_memory_load_percent_maximum_during": measurements.get(
                    "system_memory_load_percent_maximum_during"
                ),
                "system_memory_samples_during": json.dumps(
                    measurements.get("system_memory_samples_during"),
                    ensure_ascii=False,
                ),
                "memory_pressure_severe": measurements.get("memory_pressure_severe"),
                "memory_estimate_warning": measurements.get("memory_estimate_warning"),
                "loaded_model_state_before": json.dumps(
                    measurements.get("loaded_model_state_before"),
                    ensure_ascii=False,
                ),
                "loaded_model_state_after": json.dumps(
                    measurements.get("loaded_model_state_after"),
                    ensure_ascii=False,
                ),
                "ollama_process_state_before": json.dumps(
                    measurements.get("ollama_process_state_before"),
                    ensure_ascii=False,
                ),
                "ollama_process_state_after": json.dumps(
                    measurements.get("ollama_process_state_after"),
                    ensure_ascii=False,
                ),
                "ollama_process_memory_after": json.dumps(
                    measurements.get("ollama_process_memory_after"),
                    ensure_ascii=False,
                ),
                "major_memory_consumers_after": json.dumps(
                    measurements.get("major_memory_consumers_after"),
                    ensure_ascii=False,
                ),
                "ollama_process_cpu_percent_of_machine": measurements.get(
                    "ollama_process_cpu_percent_of_machine"
                ),
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _markdown_summary(
    run_id: str,
    environment: dict[str, Any],
    results: list[dict[str, Any]],
    workload_repetitions: int,
    role_recommendations: list[dict[str, Any]],
    models_requested: tuple[str, ...],
    prompt_variant: PromptVariant,
) -> str:
    memory = environment.get("system_memory") or {}
    inventory = environment["ollama_api_initial_state"]
    models = inventory.get("models", [])
    lines = [
        "# InsightOS Qwen3 Live Benchmark",
        "",
        f"- Run ID: `{run_id}`",
        f"- Timestamp (UTC): `{environment['captured_at']}`",
        f"- Git SHA: `{environment.get('git_commit_sha') or 'unavailable'}`",
        f"- Environment: `{environment['operating_system']}`; CPU `{environment.get('processor_name') or environment.get('processor') or 'unavailable'}`; {environment.get('logical_cpu_count') or 'unknown'} logical CPUs",
        f"- System memory at preflight: {memory.get('free_gb', 'unavailable')} GB free / {memory.get('total_gb', 'unavailable')} GB total",
        f"- Ollama version: `{(environment.get('ollama_version') or {}).get('stdout') or 'unavailable'}`",
        "",
        "## Reproduce",
        "",
        f"From `backend`, run one installed model at a time with `python scripts\\benchmarks\\qwen3_live_benchmark.py --model {models_requested[0]} --prompt-variant {prompt_variant} --screen`. Artifacts are written to a unique run directory under `backend\\artifacts\\evaluation-results\\qwen3-live-benchmark\\`; no models are downloaded.",
        "",
        "## Objective and methodology",
        "",
        f"Measure `{models_requested[0]}` against controlled InsightOS workloads using the real Ollama provider and router. Prompt variant: `{prompt_variant}`. Production assignments and defaults are unchanged. Calls are sequential and use deterministic JSON/schema/answer-key checks; subjective quality is not inferred from fluent text.",
        f"Requested repetitions per workload: {workload_repetitions}. Only one model may be resident; the run stops if another model appears in Ollama. Model-size-based memory estimates are advisory. The run stops on Ollama load/inference failure or severe memory pressure (available RAM below {SEVERE_FREE_RAM_GB:.1f} GB or Windows memory load at least {SEVERE_MEMORY_LOAD_PERCENT}%).",
        "",
        "## Model inventory",
        "",
        "| Exact tag | Family / parameters | Quantization | Model size | Context metadata | Loaded at start |",
        "|---|---|---|---:|---|---|",
    ]
    loaded = inventory.get("loaded_models", [])
    loaded_names = {item.get("name") for item in loaded}
    for model in models:
        context_text = model.get("context_length") or "unavailable"
        size = model.get("size_bytes")
        size_text = f"{size / GIB:.2f} GiB" if isinstance(size, (int, float)) else "unavailable"
        lines.append(
            f"| `{model['tag']}` | {model.get('family')} / {model.get('parameter_size')} | {model.get('quantization')} | {size_text} | {context_text} | {'yes' if model['tag'] in loaded_names else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Primary model residency",
            "",
            "| Model | Load status | Ollama load duration (ms) | Wall load duration (ms) | Free RAM before / minimum / after (GB) | Memory warning |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for item in environment.get("model_loading_observations", []):
        memory_before_load = item.get("memory_before") or {}
        memory_after_load = item.get("memory_after") or {}
        lines.append(
            f"| `{item.get('model')}` | {item.get('status')} | "
            f"{item.get('ollama_load_duration_ms') or '-'} | "
            f"{item.get('wall_duration_ms') or '-'} | "
            f"{memory_before_load.get('free_gb', '-')} / "
            f"{item.get('memory_minimum_during_load_gb') or '-'} / "
            f"{memory_after_load.get('free_gb', '-')} | "
            f"{item.get('memory_estimate_warning') or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Workloads and results",
            "",
            "| Model | Workload | Completed / skipped / failed / stopped | Factual | Instruction | JSON/schema | Complete | Hallucination control | Consistency | Latency | Resource efficiency | Cold-start / warm mean latency (ms) | Median / worst latency (ms) | Mean prompt / output tokens | Mean tokens/s | Mean Ollama CPU (% machine) | Lowest free RAM (GB) |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    workload_titles = {
        "task_classification": "Task classification and routing",
        "business_summary": "Lightweight business summarization",
        "structured_presentation": "Structured presentation output",
        "analytics_planning": "Analytics planning",
        "complex_business_reasoning": "Complex business reasoning",
        "specialist_handoff": "Sequential specialist handoff",
    }
    dimensions = (
        "factual_accuracy",
        "instruction_following",
        "structured_output",
        "completeness",
        "hallucination_control",
        "consistency",
    )
    for model_tag in models_requested:
        for workload_id, title in workload_titles.items():
            cases = [
                item
                for item in results
                if item["model"] == model_tag and item["workload"] == workload_id
            ]
            completed = [item for item in cases if item["status"] == "completed"]
            status = (
                f"{len(completed)} / "
                f"{sum(item['status'] == 'skipped' for item in cases)} / "
                f"{sum(item['status'] == 'failed' for item in cases)} / "
                f"{sum(item['status'] == 'aborted_memory_pressure' for item in cases)}"
            )

            def dimension_percent(name: str) -> str:
                values = [
                    item["quality_scores"].get(name)
                    for item in cases
                    if item.get("quality_scores")
                    and item["quality_scores"].get(name) is not None
                ]
                return f"{100 * sum(values) / len(values):.0f}%" if values else "-"

            cold_latencies = [
                item["measurements"]["cold_start_latency_ms"]
                for item in completed
                if item["measurements"]["cold_start_latency_ms"] is not None
            ]
            warm_latencies = [
                item["measurements"]["request_latency_ms"]
                for item in completed
                if item["cold_or_warm"] == "warm"
                and item["measurements"]["request_latency_ms"] is not None
            ]
            latencies = [
                item["measurements"]["request_latency_ms"]
                for item in completed
                if item["measurements"]["request_latency_ms"] is not None
            ]
            rates = [
                item["measurements"]["tokens_per_second"]
                for item in completed
                if item["measurements"]["tokens_per_second"] is not None
            ]
            cpu_percent = [
                item["measurements"]["ollama_process_cpu_percent_of_machine"]
                for item in completed
                if item["measurements"]["ollama_process_cpu_percent_of_machine"]
                is not None
            ]
            prompt_tokens = [
                item["measurements"]["prompt_tokens"]
                for item in completed
                if item["measurements"]["prompt_tokens"] is not None
            ]
            output_tokens = [
                item["measurements"]["generated_tokens"]
                for item in completed
                if item["measurements"]["generated_tokens"] is not None
            ]
            free_memory = [
                item["measurements"]["system_memory_minimum_during_gb"]
                for item in completed
                if item["measurements"]["system_memory_minimum_during_gb"] is not None
            ]
            latency_text = (
                f"{statistics.median(latencies):.0f} / {max(latencies):.0f}"
                if latencies
                else "-"
            )
            cold_warm_text = (
                f"{statistics.mean(cold_latencies):.0f} / "
                f"{statistics.mean(warm_latencies):.0f}"
                if cold_latencies and warm_latencies
                else "-"
            )
            rate_text = f"{statistics.mean(rates):.2f}" if rates else "-"
            cpu_text = f"{statistics.mean(cpu_percent):.2f}%" if cpu_percent else "-"
            token_text = (
                f"{statistics.mean(prompt_tokens):.0f} / {statistics.mean(output_tokens):.0f}"
                if prompt_tokens and output_tokens
                else "-"
            )
            memory_text = f"{min(free_memory):.2f}" if free_memory else "-"
            lines.append(
                f"| `{model_tag}` | {title} | {status} | "
                + " | ".join(dimension_percent(name) for name in dimensions)
                + f" | {dimension_percent('latency')} | "
                + f"{dimension_percent('resource_efficiency')} | {cold_warm_text} | "
                + f"{latency_text} | "
                + f"{token_text} | {rate_text} | {cpu_text} | {memory_text} |"
            )

    lines.extend(["", "## Benchmark status", ""])
    for workload_id, title in (
        ("task_classification", "Task classification and routing"),
        ("business_summary", "Lightweight business summarization"),
        ("structured_presentation", "Structured presentation output"),
        ("analytics_planning", "Analytics planning"),
        ("complex_business_reasoning", "Complex business reasoning"),
        ("specialist_handoff", "Sequential specialist handoff"),
    ):
        cases = [item for item in results if item["workload"] == workload_id]
        completed = sum(item["status"] == "completed" for item in cases)
        skipped = sum(item["status"] == "skipped" for item in cases)
        failed = sum(item["status"] == "failed" for item in cases)
        aborted = sum(item["status"] == "aborted_memory_pressure" for item in cases)
        lines.append(
            f"- **{title}:** {completed} completed; {skipped} skipped; "
            f"{failed} failed; {aborted} stopped for memory pressure."
        )
        reasons = sorted({item.get("skip_reason") for item in cases if item.get("skip_reason")})
        if reasons:
            lines.append(f"  - Reason: {'; '.join(str(reason) for reason in reasons)}")
    lines.extend(
        [
            "",
            "No benchmark inference response was generated; see the primary-model load outcomes."
            if not any(item.get("response") is not None for item in results)
            else "Responses and deterministic validation outcomes are in `benchmark_results.json`.",
            "",
            "## Performance and quality",
            "",
            "No inference latency, token counts, throughput, output-quality, CPU, or inference-memory measurements exist because no inference requests were sent; model load timing and memory are reported separately. First-token latency is unavailable through the current non-streaming provider. When live requests run, Ollama-reported counts and durations are kept distinct from unavailable values; cold-start latency is only populated when the provider reports a nonzero model-load duration for a model not already loaded. Ollama process CPU use is derived from process CPU-time deltas over request wall time and normalized by logical CPU count; the Ollama `ps` processor field is retained separately.",
            "",
            f"Scoring rubric: factual accuracy is workload answer-key/value validation; instruction following and structured output require exact JSON keys/types (plus the production typed specialist contracts for handoffs); completeness checks required facts, fields, and workflow stages; hallucination control rejects unsupported numeric claims; consistency is exact normalized-output agreement across at least two completed repetitions. Latency scores pass only when the measured request finishes within the applied task-profile timeout. Resource-efficiency scores pass when measured free RAM remains above the severe-pressure stop threshold ({SEVERE_FREE_RAM_GB:.1f} GB); advisory model-size estimates never determine success or failure. Unrun scores remain null, never zero.",
            "",
            "Workload-specific checks: routing compares all seven structured route objects against exact expected values; summaries and dashboard presentation validate supplied figures and numeric allowlists; planning checks relevant columns, known missingness, approved deterministic engines, and required validation steps; complex reasoning checks the supplied/derived financial values and explicit conflict/assumption/action fields; handoff runs each production specialist contract in sequence against synthetic context, validates exact approved operations/evidence, and confirms no deterministic engine ran.",
            "",
            "## Memory observations and safety",
            "",
            f"Memory immediately before model loading: `{json.dumps(environment.get('system_memory_before_loading'), ensure_ascii=False)}`.",
            f"Major memory consumers before model loading: `{json.dumps(environment.get('major_memory_consumers_before_loading', []), ensure_ascii=False)}`.",
            f"Per-model load and residency observations: `{json.dumps(environment.get('model_loading_observations', []), ensure_ascii=False)}`.",
            f"Memory and Ollama state after the run: `{json.dumps(environment.get('system_memory_after_run'), ensure_ascii=False)}`; `{json.dumps(environment.get('ollama_api_final_state'), ensure_ascii=False)}`.",
            f"Initial `ollama ps`: `{(environment.get('ollama_ps_initial') or {}).get('stdout', 'unavailable') or '(no models loaded)'}`.",
            f"Initial Ollama process working sets: `{json.dumps(environment.get('ollama_processes_initial', []), ensure_ascii=False)}`.",
            "Ollama model file size is not treated as inference memory usage. Models loaded before the run are protected; only benchmark-loaded models are unloaded during final cleanup. No models are downloaded or updated. The integrated Intel UHD adapter is recorded as inventory only; this run makes no GPU-acceleration claim.",
            "",
            "## Role-specific recommendations",
            "",
            "| Intended role | Evidence-based classification | Recommendation |",
            "|---|---|---|",
        ]
    )
    for item in role_recommendations:
        recommendation = (
            "Keep the current production assignment; do not activate candidates."
            if item["classification"] == "Insufficient evidence"
            else item["reason"]
        )
        lines.append(
            f"| {item['model']}: {item['intended_role']} | **{item['classification']}** | {recommendation} |"
        )
    lines.extend(
        [
            "",
            "## Limitations and conclusion",
            "",
            "A model counts as benchmarked only when its requested workload inference completed and was validated; successful loading alone is reported only as residency evidence. The current provider is non-streaming, so true first-token latency is unavailable. The 4B instruct tag is an optional additional comparison and was not substituted for the primary `qwen3:4b` run. The orchestration handoff workload uses the production specialist contracts and router with a synthetic fixture; no deterministic engine is run. When `INSIGHTOS_OLLAMA_NUM_CTX` is unset, the router still places the profile context allocation on the request, but the Ollama provider omits its `num_ctx` option; this is reported, not silently treated as a measured context limit.",
            "",
            "**Recommendation: conduct further testing before making any model-assignment changes. Retain the current production hierarchy and validated assignments; do not promote candidate models based on this evidence.**",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark(
    *,
    repetitions: int = 3,
    screening: bool = False,
    model_tag: str = DEFAULT_MODEL_TARGET,
    prompt_variant: PromptVariant = "optimized",
) -> Path:
    if repetitions < 1 or repetitions > 5:
        raise ValueError("Repetitions must be between 1 and 5.")
    if model_tag not in MODEL_TARGETS:
        raise ValueError(f"Unsupported benchmark model: {model_tag!r}.")
    if prompt_variant not in {"baseline", "optimized"}:
        raise ValueError(f"Unsupported prompt variant: {prompt_variant!r}.")
    models_requested = (model_tag,)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output_dir = RESULTS_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    environment = capture_environment(_git_commit_sha())
    environment_path = output_dir / "benchmark_environment.json"
    _write_json(environment_path, environment)
    all_workloads = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["workloads"]
    workloads, role_ineligible_workloads = _workloads_for_model(
        all_workloads,
        model_tag,
    )
    requested_repetitions = 1 if screening else repetitions
    inventory = environment["ollama_api_initial_state"]
    installed = {item["tag"]: item for item in inventory.get("models", [])}
    initial_loaded = inventory.get("loaded_models", [])
    results: list[dict[str, Any]] = []
    for workload in role_ineligible_workloads:
        for repetition in range(1, (1 if screening else repetitions) + 1):
            results.append(
                _new_case_result(
                    model_tag,
                    workload,
                    repetition,
                    "skipped",
                    f"{model_tag} is not approved for the {workload['role']} role.",
                )
            )
    errors: list[dict[str, Any]] = []
    load_observations: list[dict[str, Any]] = []
    safety_abort = False
    load_failure: str | None = None
    settings = get_agent_settings()
    loaded_by_benchmark: set[str] = set()
    environment["system_memory_before_loading"] = _read_memory()
    environment["major_memory_consumers_before_loading"] = _powershell_top_processes()
    environment["benchmark_model"] = model_tag
    environment["benchmark_prompt_variant"] = prompt_variant
    environment["benchmark_prompt_version"] = (
        "baseline" if prompt_variant == "baseline" else BI_PROMPT_VERSION
    )
    _write_json(environment_path, environment)

    try:
        if not inventory.get("available"):
            load_failure = "Ollama model inventory is unavailable; no models were loaded."
            errors.append({"code": "ollama_inventory_unavailable", "message": load_failure})

        initial_names = {
            item.get("name") or item.get("model")
            for item in initial_loaded
            if isinstance(item.get("name") or item.get("model"), str)
        }
        if initial_names - {model_tag}:
            load_failure = (
                "Another Ollama model was already resident; the benchmark will not "
                "evict or coexist with a model managed outside this run."
            )
            errors.append({"code": "other_model_already_resident", "message": load_failure})

        for model_tag in models_requested:
            if load_failure:
                break
            model = installed.get(model_tag)
            if model is None:
                load_failure = (
                    f"Exact requested model tag {model_tag} is not installed; downloads are disabled."
                )
                errors.append(
                    {
                        "model": model_tag,
                        "code": "model_not_installed",
                        "message": load_failure,
                    }
                )
                break

            memory_before = _read_memory()
            estimate_gb = _required_free_gb(
                model,
                already_loaded=_model_is_loaded(model_tag, initial_loaded),
            )
            estimated_warning = (
                f"Available memory {memory_before['free_gb']:.2f} GB is below the "
                f"advisory {estimate_gb:.2f} GB model-size estimate."
                if memory_before is not None
                and memory_before["free_gb"] < estimate_gb
                else None
            )
            if _memory_pressure_is_severe(memory_before):
                safety_abort = True
                load_failure = (
                    "Severe memory pressure was already present immediately before "
                    f"loading {model_tag}; no Ollama load request was sent."
                )
                load_observations.append(
                    {
                        "model": model_tag,
                        "status": "not_attempted_severe_memory_pressure",
                        "memory_before": memory_before,
                        "memory_estimate_warning": estimated_warning,
                    }
                )
                break

            loaded_before = _ollama_loaded_models()
            already_loaded = _model_is_loaded(model_tag, loaded_before)
            load_started = time.perf_counter()
            monitor: _MemoryMonitor | None = None
            if already_loaded:
                load_result = {
                    "loaded": True,
                    "already_loaded": True,
                    "wall_duration_ms": 0.0,
                    "load_duration_ms": None,
                }
            else:
                with _MemoryMonitor() as monitor:
                    load_result = _load_model_resident(model_tag)
                load_result["monitor_minimum_free_gb"] = (
                    monitor.minimum_free_gb if monitor is not None else None
                )
                load_result["memory_pressure_severe"] = (
                    monitor.severe_pressure if monitor is not None else False
                )

            memory_after = _read_memory()
            loaded_after = _ollama_loaded_models()
            if not already_loaded and _model_is_loaded(model_tag, loaded_after):
                loaded_by_benchmark.add(model_tag)
            load_succeeded = (
                load_result.get("loaded") is True
                and _model_is_loaded(model_tag, loaded_after)
            )
            observation = {
                "model": model_tag,
                "status": "loaded" if load_succeeded else "load_failed",
                "already_loaded_before_request": already_loaded,
                "memory_before": memory_before,
                "memory_after": memory_after,
                "memory_estimate_gb": round(estimate_gb, 3),
                "memory_estimate_warning": estimated_warning,
                "wall_duration_ms": load_result.get(
                    "wall_duration_ms",
                    round((time.perf_counter() - load_started) * 1000, 3),
                ),
                "ollama_load_duration_ms": load_result.get("load_duration_ms"),
                "load_error": load_result.get("error"),
                "load_error_type": load_result.get("error_type"),
                "http_status": load_result.get("http_status"),
                "memory_minimum_during_load_gb": load_result.get(
                    "monitor_minimum_free_gb"
                ),
                "memory_samples_during_load": (
                    monitor.samples if monitor is not None else []
                ),
                "memory_pressure_severe_during_load": load_result.get(
                    "memory_pressure_severe", False
                ),
                "loaded_models_before": loaded_before,
                "loaded_models_after": loaded_after,
                "ollama_ps_after": _command_output("ollama", "ps"),
                "ollama_processes_after": _powershell_ollama_processes(),
                "major_memory_consumers_after": _powershell_top_processes(),
            }
            load_observations.append(observation)
            environment["model_loading_observations"] = load_observations
            _write_json(environment_path, environment)

            pressure_during_load = (
                _memory_pressure_is_severe(memory_after)
                or load_result.get("memory_pressure_severe") is True
            )
            if pressure_during_load:
                safety_abort = True
                load_failure = (
                    f"Severe system memory pressure occurred while loading {model_tag}; "
                    "the remaining models were not loaded."
                )
                errors.append(
                    {
                        "model": model_tag,
                        "code": "severe_memory_pressure_during_load",
                        "message": load_failure,
                        "memory_minimum_during_load_gb": load_result.get(
                            "monitor_minimum_free_gb"
                        ),
                        "memory_after_load": memory_after,
                    }
                )

            if not load_succeeded:
                load_failure = load_failure or (
                    f"Ollama did not leave {model_tag} resident: "
                    f"{load_result.get('error') or 'model absent from ollama ps'}"
                )
                memory_allocation_failure = bool(
                    isinstance(load_result.get("error"), str)
                    and re.search(
                        r"out of memory|memory allocation|allocate memory|not enough memory|failed to allocate|requires more system memory|insufficient memory",
                        load_result["error"],
                        re.IGNORECASE,
                    )
                )
                errors.append(
                    {
                        "model": model_tag,
                        "code": "ollama_model_load_failed",
                        "message": load_failure,
                        "memory_allocation_failure": memory_allocation_failure,
                        "http_status": load_result.get("http_status"),
                    }
                )
                break
            if pressure_during_load:
                break

        loaded_now = _ollama_loaded_models()
        selected_model_resident = _only_model_is_loaded(model_tag, loaded_now)
        environment["selected_model_resident_before_inference"] = selected_model_resident
        environment["loaded_models_before_inference"] = loaded_now
        environment["ollama_ps_before_inference"] = _command_output("ollama", "ps")
        _write_json(environment_path, environment)

        if selected_model_resident and not safety_abort and not load_failure:
            for model_tag in models_requested:
                model = installed[model_tag]
                model_settings = replace(
                    settings,
                    provider="ollama",
                    base_url=OLLAMA_BASE_URL,
                    model=model_tag,
                    api_key=None,
                    ollama_keep_alive_seconds=86_400,
                )
                provider = _CapturingProvider(create_provider(model_settings))
                router = AgentModelRouter(
                    settings=model_settings,
                    provider=provider,
                    model_overrides={
                        "orchestrator": model_tag,
                        "data_intelligence": model_tag,
                        "analytics_modelling": model_tag,
                        "insights_visualization": model_tag,
                    },
                )
                model_stopped = False
                for workload in workloads:
                    for repetition in range(1, requested_repetitions + 1):
                        memory_now = _read_memory()
                        if _memory_pressure_is_severe(memory_now):
                            result = _new_case_result(
                                model_tag,
                                workload,
                                repetition,
                                "aborted_memory_pressure",
                                "Severe system memory pressure observed before inference.",
                            )
                            result["measurements"]["system_memory_before_gb"] = (
                                memory_now.get("free_gb") if memory_now else None
                            )
                            result["measurements"]["system_memory_load_percent_before"] = (
                                memory_now.get("memory_load_percent") if memory_now else None
                            )
                            result["measurements"]["memory_pressure_severe"] = True
                            results.append(result)
                            safety_abort = True
                            model_stopped = True
                            break
                        current = _ollama_loaded_models()
                        if not _only_model_is_loaded(model_tag, current):
                            reason = (
                                "The selected model was not the only resident Ollama "
                                "model before inference; stopping without loading or "
                                "evicting models."
                            )
                            results.append(
                                _new_case_result(
                                    model_tag, workload, repetition, "failed", reason
                                )
                            )
                            errors.append(
                                {
                                    "model": model_tag,
                                    "workload": workload["id"],
                                    "code": "primary_model_residency_lost",
                                    "message": reason,
                                }
                            )
                            load_failure = reason
                            model_stopped = True
                            break
                        result = _run_one(
                            model_tag,
                            workload,
                            repetition,
                            model_settings,
                            model,
                            provider,
                            router,
                        )
                        results.append(result)
                        if result["status"] == "aborted_memory_pressure":
                            safety_abort = True
                            model_stopped = True
                        elif result["status"] == "failed":
                            errors.append(
                                {
                                    "model": model_tag,
                                    "workload": workload["id"],
                                    "repetition": repetition,
                                    **(result.get("error") or {"type": "unknown"}),
                                }
                            )
                        if model_stopped:
                            break
                    if model_stopped:
                        break
                if safety_abort or load_failure:
                    break

        if not selected_model_resident:
            reason = load_failure or (
                "The full primary model cohort was not resident at the same time; "
                "inference was not started."
            )
            for model_tag in models_requested:
                for workload in workloads:
                    for repetition in range(1, requested_repetitions + 1):
                        if any(
                            item["model"] == model_tag
                            and item["workload"] == workload["id"]
                            and item["repetition"] == repetition
                            for item in results
                        ):
                            continue
                        status = (
                            "aborted_memory_pressure"
                            if safety_abort
                            else "failed"
                            if any(
                                error.get("model") == model_tag
                                and error.get("code") == "ollama_model_load_failed"
                                for error in errors
                            )
                            else "skipped"
                        )
                        results.append(
                            _new_case_result(
                                model_tag,
                                workload,
                                repetition,
                                status,
                                reason,
                            )
                        )
        elif safety_abort or load_failure:
            reason = load_failure or "Benchmark stopped after severe memory pressure."
            for model_tag in models_requested:
                for workload in workloads:
                    for repetition in range(1, requested_repetitions + 1):
                        if any(
                            item["model"] == model_tag
                            and item["workload"] == workload["id"]
                            and item["repetition"] == repetition
                            for item in results
                        ):
                            continue
                        results.append(
                            _new_case_result(
                                model_tag,
                                workload,
                                repetition,
                                "aborted_memory_pressure"
                                if safety_abort
                                else "skipped",
                                reason,
                            )
                        )
    finally:
        for tag in sorted(loaded_by_benchmark):
            if _model_is_loaded(tag, initial_loaded):
                continue
            try:
                _unload_benchmark_model(tag)
            except (OSError, urllib.error.URLError, TimeoutError, ValueError):
                errors.append(
                    {
                        "model": tag,
                        "workload": "cleanup",
                        "type": "benchmark_model_unload_failed",
                    }
                )
        environment["model_loading_observations"] = load_observations
        environment["system_memory_after_run"] = _read_memory()
        environment["major_memory_consumers_after_run"] = _powershell_top_processes()
        environment["ollama_processes_after_run"] = _powershell_ollama_processes()
        environment["ollama_api_final_state"] = {
            "loaded_models": _ollama_loaded_models(),
            "ollama_ps": _command_output("ollama", "ps"),
        }
        _write_json(environment_path, environment)

    results_payload = {
        "schema_version": 3,
        "run_id": run_id,
        "timestamp_utc": environment["captured_at"],
        "git_commit_sha": environment.get("git_commit_sha"),
        "screening_run": screening,
        "requested_repetitions": requested_repetitions,
        "models_requested": list(models_requested),
        "prompt_variant": prompt_variant,
        "prompt_version": (
            "baseline" if prompt_variant == "baseline" else BI_PROMPT_VERSION
        ),
        "installed_model_tags": [item["tag"] for item in installed.values()],
        "model_loading_observations": load_observations,
        "selected_model_resident_before_inference": selected_model_resident,
        "workloads": [
            {
                "id": workload["id"],
                "title": workload["title"],
                "token_profile": workload["category"],
                "role": workload["role"],
            }
            for workload in all_workloads
        ],
        "fixture_file": str(FIXTURE_PATH.relative_to(REPOSITORY_ROOT)),
        "fixture_sha256": environment["fixtures_sha256"],
        "results": results,
        "errors": errors,
        "safety_abort": safety_abort,
        "quality_scoring": {
            "method": "deterministic fixture answer keys, JSON shape checks, allowed-number checks, and production specialist contract validation",
            "llm_judge_used": False,
            "unrun_scores_are_null": True,
        },
    }
    _apply_quality_scores(results)
    role_recommendations = _role_recommendations(results)
    results_payload["role_recommendations"] = role_recommendations
    _write_json(output_dir / "benchmark_results.json", results_payload)
    _write_csv(output_dir / "benchmark_comparison.csv", _score_rows(results))
    summary = _markdown_summary(
        run_id,
        environment,
        results,
        requested_repetitions,
        role_recommendations,
        models_requested,
        prompt_variant,
    )
    (output_dir / "benchmark_summary.md").write_text(summary, encoding="utf-8")
    return output_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely benchmark installed Qwen3 models through InsightOS.")
    parser.add_argument(
        "--screen",
        action="store_true",
        help="Run one repetition per workload; memory estimates are advisory and never gate loading.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=3,
        help="Repetitions per workload for a full run (1-5; default: 3).",
    )
    parser.add_argument(
        "--model",
        choices=MODEL_TARGETS,
        default=DEFAULT_MODEL_TARGET,
        help="One installed model to evaluate; models are never loaded as a cohort.",
    )
    parser.add_argument(
        "--prompt-variant",
        choices=("baseline", "optimized"),
        default="optimized",
        help="Use the captured pre-specialization prompt or the versioned BI role prompts.",
    )
    parser.add_argument(
        "--telemetry-check",
        action="store_true",
        help="Capture three Windows memory snapshots and write a telemetry-only diagnostic report without loading models.",
    )
    args = parser.parse_args(argv)
    if args.telemetry_check:
        output_dir = _run_telemetry_check()
        print(f"Telemetry diagnostic artifacts: {output_dir}")
        return 0
    output_dir = run_benchmark(
        repetitions=args.repetitions,
        screening=args.screen,
        model_tag=args.model,
        prompt_variant=args.prompt_variant,
    )
    print(f"Benchmark artifacts: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
