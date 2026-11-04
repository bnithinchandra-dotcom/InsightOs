import hashlib
import zipfile

import pytest

from insightos_training.artifacts import import_kaggle_experiment, list_experiments


def archive(path, entries):
    with zipfile.ZipFile(path, "w") as handle:
        for name, content in entries.items():
            handle.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_archive_rejected(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        import_kaggle_experiment(tmp_path / "missing.zip", "0" * 64, tmp_path / "experiments")


def test_archive_hash_mismatch_rejected(tmp_path):
    archive_path = tmp_path / "test.zip"
    digest = archive(archive_path, {"file.txt": "data"})
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        import_kaggle_experiment(archive_path, "0" * 64, tmp_path / "experiments")
    assert digest != "0" * 64


def test_invalid_zip_rejected(tmp_path):
    archive_path = tmp_path / "invalid.zip"
    archive_path.write_text("not a zip")
    with pytest.raises(ValueError, match="Invalid ZIP"):
        import_kaggle_experiment(
            archive_path, hashlib.sha256(archive_path.read_bytes()).hexdigest(), tmp_path / "experiments"
        )


def test_traversal_rejected(tmp_path):
    archive_path = tmp_path / "traversal.zip"
    digest = archive(archive_path, {"../outside.txt": "data"})
    with pytest.raises(ValueError, match="Unsafe archive path"):
        import_kaggle_experiment(archive_path, digest, tmp_path / "experiments")


def test_list_experiments_has_no_git_dependency(tmp_path):
    (tmp_path / "one").mkdir()
    assert list_experiments(tmp_path) == ["one"]
