from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from modometa_community_data.ldc import main
from modometa_community_data.ldc import validate_all_legacy_challenges_yamls
from modometa_community_data.ldc import validate_legacy_challenges_yaml


def test_repo_legacy_challenges_yamls_valid():
    """Verify that all legacy_challenges.yaml files in the repository are valid and have required keys ('date', 'name', 'uri')."""
    total_entries, errors = validate_all_legacy_challenges_yamls()
    assert len(errors) == 0, f"Found validation errors: {errors}"
    assert total_entries > 0, "Expected at least one challenge entry to be validated"


def test_validate_legacy_challenges_yaml_valid(tmp_path: Path):
    """Test validating a valid legacy_challenges.yaml file."""
    yaml_content = {
        "legacy-challenge-32-2025-01-01": {
            "date": "2025-01-01",
            "name": "Legacy Challenge 32",
            "uri": "https://www.mtgo.com/decklist/legacy-challenge-32-2025-01-01",
            "sheet_id": None,
            "tab": "Match Up Input",
        }
    }
    file_path = tmp_path / "legacy_challenges.yaml"
    file_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

    errors = validate_legacy_challenges_yaml(file_path)
    assert errors == []


@pytest.mark.parametrize("missing_key", ["date", "name", "uri"])
def test_validate_legacy_challenges_yaml_missing_keys(tmp_path: Path, missing_key: str):
    """Test that missing required keys ('date', 'name', 'uri') trigger validation errors."""
    entry = {
        "date": "2025-01-01",
        "name": "Legacy Challenge 32",
        "uri": "https://www.mtgo.com/decklist/legacy-challenge-32-2025-01-01",
    }
    del entry[missing_key]
    yaml_content = {"legacy-challenge-32-2025-01-01": entry}
    file_path = tmp_path / "legacy_challenges.yaml"
    file_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

    errors = validate_legacy_challenges_yaml(file_path)
    assert len(errors) == 1
    assert f"missing required key '{missing_key}'" in errors[0]


def test_validate_legacy_challenges_yaml_empty_key(tmp_path: Path):
    """Test that empty required key values trigger validation errors."""
    yaml_content = {
        "legacy-challenge-32-2025-01-01": {
            "date": "",
            "name": "Legacy Challenge 32",
            "uri": "   ",
        }
    }
    file_path = tmp_path / "legacy_challenges.yaml"
    file_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

    errors = validate_legacy_challenges_yaml(file_path)
    assert len(errors) == 2
    assert "empty required key 'date'" in errors[0]
    assert "empty required key 'uri'" in errors[1]


def test_validate_legacy_challenges_yaml_invalid_structure(tmp_path: Path):
    """Test that non-dict entries and top-level lists trigger validation errors."""
    list_path = tmp_path / "list.yaml"
    list_path.write_text("- item1\n- item2\n", encoding="utf-8")
    assert (
        "Top-level content must be a mapping"
        in validate_legacy_challenges_yaml(list_path)[0]
    )

    entry_not_dict_path = tmp_path / "not_dict.yaml"
    entry_not_dict_path.write_text("slug: not-a-dict\n", encoding="utf-8")
    assert (
        "must be a mapping" in validate_legacy_challenges_yaml(entry_not_dict_path)[0]
    )


def test_validate_legacy_challenges_yaml_missing_file(tmp_path: Path):
    """Test that non-existent file path returns an error."""
    missing_path = tmp_path / "nonexistent.yaml"
    errors = validate_legacy_challenges_yaml(missing_path)
    assert len(errors) == 1
    assert "File not found" in errors[0]


def test_validate_legacy_challenges_yaml_empty_file(tmp_path: Path):
    """Test that an empty YAML file returns an error."""
    empty_path = tmp_path / "empty.yaml"
    empty_path.write_text("", encoding="utf-8")
    errors = validate_legacy_challenges_yaml(empty_path)
    assert len(errors) == 1
    assert "File is empty" in errors[0]


def test_validate_all_legacy_challenges_yamls_empty_dir(tmp_path: Path):
    """Test that directory without valid year legacy_challenges.yaml returns an error."""
    count, errors = validate_all_legacy_challenges_yamls(tmp_path)
    assert count == 0
    assert len(errors) == 1
    assert "No legacy_challenges.yaml files found" in errors[0]


def test_main_validate_success(monkeypatch):
    """Test that pull-ldc --validate CLI option exits cleanly on success."""
    monkeypatch.setattr("sys.argv", ["pull-ldc", "--validate"])
    with patch(
        "modometa_community_data.ldc.validate_all_legacy_challenges_yamls",
        return_value=(42, []),
    ):
        main()


def test_main_validate_failure(monkeypatch):
    """Test that pull-ldc --validate CLI option exits with code 1 on errors."""
    monkeypatch.setattr("sys.argv", ["pull-ldc", "--validate"])
    with (
        patch(
            "modometa_community_data.ldc.validate_all_legacy_challenges_yamls",
            return_value=(0, ["error 1"]),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 1
