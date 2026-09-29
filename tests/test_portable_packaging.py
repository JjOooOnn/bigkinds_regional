from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL or sys.platform != "win32", reason="Windows packaging checks")


def package(archive, digest, output):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts/package_portable.ps1"), "-PythonArchive", str(archive),
         "-PythonArchiveSha256", digest, "-BuildPython", sys.executable,
         "-OutputDirectory", str(output)],
        capture_output=True, timeout=30,
    )


def test_wrong_hash_fails_before_creating_output(tmp_path):
    archive = tmp_path / "python.zip"
    archive.write_bytes(b"invalid")
    output = tmp_path / "output"
    result = package(archive, "0" * 64, output)
    assert result.returncode != 0
    assert b"SHA-256 mismatch" in result.stderr
    assert not output.exists()


def test_incomplete_runtime_is_rejected_and_only_own_stage_is_removed(tmp_path):
    archive = tmp_path / "python.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("readme.txt", "not a Python runtime")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    output = tmp_path / "한글 release"
    output.mkdir()
    preserved = output / "existing.txt"
    preserved.write_text("keep", encoding="utf-8")
    result = package(archive, digest, output)
    assert result.returncode != 0
    assert b"Not a Python 3.12 embeddable ZIP" in result.stderr
    assert list(output.iterdir()) == [preserved]
    assert preserved.read_text(encoding="utf-8") == "keep"


def test_packaging_script_parses_in_windows_powershell():
    script = str(ROOT / "scripts/package_portable.ps1").replace("'", "''")
    command = (
        f"$tokens=$null; $errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }"
    )
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_batch_from_other_directory_reports_missing_files(tmp_path):
    folder = tmp_path / "한글 배포"
    folder.mkdir()
    batch = folder / "실행.bat"
    source = (ROOT / "scripts/start_portable.bat").read_text(encoding="utf-8")
    batch.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(batch)], cwd=tmp_path,
        input=b"\r\n", capture_output=True, timeout=15,
    )
    assert result.returncode == 1
    assert "필수 실행 파일이 없습니다" in result.stdout.decode("utf-8", errors="replace")
