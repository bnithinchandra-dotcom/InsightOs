from insightos_training.hardware import inspect_hardware
from insightos_training.smoke_test import run_smoke_test


def test_hardware_report_has_required_sections(tmp_path):
    report = inspect_hardware(tmp_path)
    assert "cpu" in report
    assert "gpu" in report
    assert "wsl2" in report


def test_hardware_cpu_is_model_name(tmp_path):
    report = inspect_hardware(tmp_path)
    assert report["cpu"]["model"] not in {"", "unknown", "x86_64"}


def test_cpu_smoke_is_non_training(tmp_path):
    report = run_smoke_test()
    assert report["passed"]
    assert report["mode"] == "synthetic_cpu"
    assert report["training_executed"] is False
