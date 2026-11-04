from pathlib import Path

from .visualization import (
    load_registry,
    validate_dataset_splits,
    validate_evaluation_cases,
    validate_registry,
)


def validate_dataset(dataset_dir: Path, require_approved: bool = False) -> dict:
    registry = load_registry()
    validate_registry(registry)
    splits = validate_dataset_splits(dataset_dir / "corpus")
    evaluation = validate_evaluation_cases(dataset_dir / "evaluation_cases.json")
    if require_approved:
        raise ValueError("No approved visualization training dataset exists")
    return {
        "valid": True,
        "require_approved": require_approved,
        "dataset_directory": str(dataset_dir),
        "corpus": splits,
        "evaluation_cases": evaluation["count"],
    }
