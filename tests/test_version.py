from __future__ import annotations

import pytest

from src.version import read_app_version


def test_read_app_version_returns_manifest_version(tmp_path):
    manifest = tmp_path / "package.json"
    manifest.write_text('{"version": "1.0.1"}', encoding="utf-8")

    assert read_app_version(manifest) == "1.0.1"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"version": "1.0"}', "MAJOR.MINOR.PATCH"),
        ('{"version": 1}', "MAJOR.MINOR.PATCH"),
        ('{}', "MAJOR.MINOR.PATCH"),
        ('not-json', "manifest 형식"),
    ],
)
def test_read_app_version_rejects_invalid_manifest(tmp_path, content, message):
    manifest = tmp_path / "package.json"
    manifest.write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        read_app_version(manifest)


def test_read_app_version_rejects_missing_manifest(tmp_path):
    with pytest.raises(RuntimeError, match="찾을 수 없습니다"):
        read_app_version(tmp_path / "missing.json")
