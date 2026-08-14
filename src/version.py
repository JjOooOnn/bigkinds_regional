from __future__ import annotations

import json
import re
from pathlib import Path

from src.config import ROOT_DIR


PACKAGE_MANIFEST = ROOT_DIR / "frontend" / "package.json"
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def read_app_version(package_manifest: Path = PACKAGE_MANIFEST) -> str:
    """Read the product version from the frontend package manifest."""
    try:
        manifest = json.loads(package_manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"버전 manifest를 찾을 수 없습니다: {package_manifest}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"버전 manifest 형식이 올바르지 않습니다: {package_manifest}") from exc

    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        raise RuntimeError(
            f"버전은 MAJOR.MINOR.PATCH 형식이어야 합니다: {package_manifest}"
        )
    return version
