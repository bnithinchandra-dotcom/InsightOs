import os
import platform
from pathlib import Path


def inspect_hardware(package_root: Path) -> dict:
    cpu = platform.processor() or "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    try:
        mem_kib = next(
            int(line.split()[1])
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal:")
        )
        ram_gib = round(mem_kib / (1024 * 1024), 2)
    except (OSError, StopIteration, ValueError):
        ram_gib = None
    try:
        disk = os.statvfs(package_root)
        disk_gib = round((disk.f_blocks * disk.f_frsize) / (1024**3), 2)
    except OSError:
        disk_gib = None
    cores = os.cpu_count()
    return {
        "cpu": {"model": cpu, "architecture": platform.machine(), "logical_cores": cores},
        "memory": {"ram_gib": ram_gib},
        "disk": {"total_gib": disk_gib},
        "gpu": {"cuda_available": False, "xpu_available": False},
        "wsl2": "microsoft" in Path("/proc/version").read_text().lower(),
        "state": "cpu_development_ready",
    }
