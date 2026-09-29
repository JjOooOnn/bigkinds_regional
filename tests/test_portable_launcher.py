from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

from scripts import portable_launcher as launcher


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "한글 배포 폴더"
    for name in (
        "runtime/python.exe", "runtime/python312._pth", "run_web.py", "src/api/app.py",
        "frontend/package.json", "frontend/dist/index.html", "frontend/dist/assets/app.js",
        "runtime/Lib/site-packages/dependency.py", "browsers/chromium/chrome.exe",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    return root


def test_launch_overrides_host_settings_and_never_builds(bundle, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("BIGKINDS_RUNTIME", "BIGKINDS_DATA_DIR", "PLAYWRIGHT_BROWSERS_PATH", "PYTHONDONTWRITEBYTECODE"):
        monkeypatch.setenv(key, "host-value")
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    calls = []
    monkeypatch.setattr(launcher, "check_port", lambda port: calls.append(port))
    monkeypatch.setattr(launcher, "check_browser", lambda root: calls.append(root))

    def run_server(args):
        assert os.environ["BIGKINDS_RUNTIME"] == "local"
        assert os.environ["BIGKINDS_DATA_DIR"] == str(bundle)
        assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(bundle / "browsers")
        calls.append(args)
        return 0

    monkeypatch.setitem(sys.modules, "run_web", SimpleNamespace(main=run_server))
    assert launcher.main(["--port", "8123", "--no-browser"], root=bundle) == 0
    assert calls == [8123, bundle, ["--skip-build", "--port", "8123", "--no-browser"]]
    assert (bundle / "work/server_logs").is_dir()
    assert not list(bundle.rglob("*.xlsx"))
    assert not list(bundle.rglob("*.sqlite3"))


@pytest.mark.parametrize("relative", ["runtime/python.exe", "frontend/dist/index.html", "frontend/package.json"])
def test_missing_files_explain_full_extraction(bundle, relative):
    (bundle / relative).unlink()
    with pytest.raises(RuntimeError, match="전체 압축"):
        launcher.check_files(bundle)


def test_empty_browser_folder_is_rejected(bundle):
    (bundle / "browsers/chromium/chrome.exe").unlink()
    (bundle / "browsers/chromium").rmdir()
    with pytest.raises(RuntimeError, match="비어 있습니다"):
        launcher.check_files(bundle)


def test_unwritable_folder_has_actionable_error(bundle, monkeypatch):
    def denied(**_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(launcher.tempfile, "TemporaryFile", denied)
    with pytest.raises(RuntimeError, match="쓰기 가능한"):
        launcher.check_writable(bundle)


def test_occupied_port_has_actionable_error():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        with pytest.raises(RuntimeError, match="이미 실행한"):
            launcher.check_port(port)


@pytest.mark.parametrize("port", [0, 65536])
def test_invalid_port_is_rejected_before_preflight(bundle, port):
    with pytest.raises(SystemExit) as caught:
        launcher.main(["--port", str(port)], root=bundle)
    assert caught.value.code == 2


@pytest.mark.parametrize("external", [False, True])
def test_chromium_must_exist_inside_bundle(bundle, tmp_path, monkeypatch, external):
    import playwright.sync_api

    executable = tmp_path / "external.exe" if external else bundle / "browsers/missing.exe"
    if external:
        executable.touch()

    class Playwright:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path=str(executable)))

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", Playwright)
    with pytest.raises(RuntimeError, match="Chromium"):
        launcher.check_browser(bundle)


def test_startup_failure_returns_error_without_starting_server(bundle, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "configure_environment", lambda _: None)
    (bundle / "run_web.py").unlink()
    assert launcher.main([], root=bundle) == 1
    assert "ZIP 안에서 실행하지 말고" in capsys.readouterr().err
