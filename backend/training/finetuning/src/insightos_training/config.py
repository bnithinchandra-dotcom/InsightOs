from pathlib import Path

import yaml

MODEL_NAMES = ("qwen3_0_6b",)


def validate_model_config(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        "model_id": data["model"]["model_id"],
        "revision": data["model"]["revision"],
        "role": data["model"]["role"],
        "hardware": data["hardware"],
    }
