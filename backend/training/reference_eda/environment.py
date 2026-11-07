from __future__ import annotations

import importlib.metadata
import importlib.util
import platform
import sys
from typing import Any

REQUIRED_PACKAGES = (
    "accelerate",
    "bitsandbytes",
    "peft",
    "safetensors",
    "torch",
    "transformers",
    "datasets",
    "trl",
)


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in REQUIRED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def inspect_environment() -> dict[str, Any]:
    report: dict[str, Any] = {
        "python": platform.python_version(),
        "python_supported": (3, 10, 0) <= tuple(sys.version_info[:3]) < (3, 13, 0),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": package_versions(),
        "torch": None,
        "cuda": {
            "driver_available": False,
            "available": False,
            "runtime_version": None,
            "devices": [],
            "bf16_supported": False,
            "fp16_supported": False,
            "allocated_gib": None,
            "reserved_gib": None,
        },
        "bitsandbytes_available": importlib.util.find_spec("bitsandbytes") is not None,
        "unsloth_available": importlib.util.find_spec("unsloth") is not None,
    }

    if importlib.util.find_spec("torch") is None:
        return report

    import torch

    devices = []
    cuda_available = torch.cuda.is_available()
    report["torch"] = {
        "version": torch.__version__,
        "cuda_build": torch.version.cuda,
        "cuda_available": cuda_available,
    }
    report["cuda"]["available"] = cuda_available
    if cuda_available:
        report["cuda"]["driver_available"] = True
        report["cuda"]["runtime_version"] = torch.version.cuda
        report["cuda"]["bf16_supported"] = bool(torch.cuda.is_bf16_supported())
        report["cuda"]["fp16_supported"] = all(
            torch.cuda.get_device_capability(index)[0] >= 6
            for index in range(torch.cuda.device_count())
        )
        report["cuda"]["allocated_gib"] = round(
            torch.cuda.memory_allocated() / 1024**3, 3
        )
        report["cuda"]["reserved_gib"] = round(
            torch.cuda.memory_reserved() / 1024**3, 3
        )
        for index in range(torch.cuda.device_count()):
            properties = torch.cuda.get_device_properties(index)
            devices.append(
                {
                    "index": index,
                    "name": properties.name,
                    "total_memory_gib": round(properties.total_memory / 1024**3, 2),
                    "free_memory_gib": round(
                        (
                            properties.total_memory - torch.cuda.memory_reserved(index)
                        )
                        / 1024**3,
                        2,
                    ),
                }
            )
    report["cuda"]["devices"] = devices
    return report


def classify_environment(report: dict[str, Any]) -> str:
    if not report["python_supported"]:
        return "unsupported-python"
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        return "cpu-only-development"
    if report["cuda"]["devices"]:
        return "cuda-compatible" if report["cuda"]["available"] else "gpu-cuda-unavailable"
    return "gpu-unavailable"
