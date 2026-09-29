"""Offline release gate, executed with the bundled interpreter before zipping."""
from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def check_worker() -> None:
    # A real spawn child must import native extensions and the actual job entrypoint.
    import greenlet
    import openpyxl
    import pydantic_core
    import sqlite3
    from src.application.job_manager import run_audit_job_worker
    from playwright.async_api import async_playwright

    assert callable(run_audit_job_worker)
    assert greenlet.greenlet and pydantic_core.SchemaValidator
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "report.xlsx"
        openpyxl.Workbook().save(report)
        workbook = openpyxl.load_workbook(report)
        workbook.close()

    async def browsers():
        async with async_playwright() as playwright:
            # Both are used by the application, including headed re-verification.
            for headless in (True, False):
                browser = await playwright.chromium.launch(headless=headless)
                try:
                    page = await browser.new_page()
                    await page.goto("data:text/html,<main>portable-ready</main>")
                    assert await page.locator("main").inner_text() == "portable-ready"
                finally:
                    await browser.close()

    asyncio.run(browsers())


def check_server(root: Path, data_dir: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, BIGKINDS_DATA_DIR=str(data_dir))
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [sys.executable, "-B", "-X", "utf8", str(root / "run_web.py"),
             "--skip-build", "--no-browser", "--port", str(port)],
            cwd=data_dir, env=env, stdout=log, stderr=log,
        )
        try:
            deadline = time.monotonic() + 30
            base = f"http://127.0.0.1:{port}"
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("번들 서버가 준비되기 전에 종료되었습니다.")
                try:
                    with opener.open(base + "/api/health", timeout=1) as response:
                        health = json.load(response)
                    assert health["status"] == "ok" and health["local_only"] is True
                    break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("번들 서버 준비 시간이 초과되었습니다.")
            with opener.open(base + "/", timeout=5) as response:
                html = response.read().decode("utf-8")
            assets = re.findall(r'(?:src|href)="(/assets/[^"?#]+)', html)
            if not assets:
                raise RuntimeError("빌드된 화면의 자산을 찾을 수 없습니다.")
            for asset in assets:
                with opener.open(base + asset, timeout=5) as response:
                    assert response.status == 200 and response.read()
            with opener.open(base + "/api/jobs", timeout=5) as response:
                assert json.load(response)["jobs"] == []
        except BaseException:
            log.seek(0)
            print(log.read().decode("utf-8", errors="replace"), file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    os.environ["BIGKINDS_RUNTIME"] = "local"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root / "browsers")
    from portable_launcher import check_browser, check_files

    check_files(root)
    check_browser(root)
    if Path(sys.executable).resolve() != root / "runtime" / "python.exe":
        raise RuntimeError("검증에는 배포 폴더의 Python을 사용해야 합니다.")
    with tempfile.TemporaryDirectory(prefix="bigkinds-smoke-") as directory:
        data_dir = Path(directory)
        os.environ["BIGKINDS_DATA_DIR"] = str(data_dir)
        worker = multiprocessing.get_context("spawn").Process(target=check_worker)
        worker.start()
        worker.join(timeout=60)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=10)
            raise RuntimeError("번들 worker 검증 시간이 초과되었습니다.")
        if worker.exitcode != 0:
            raise RuntimeError(f"번들 worker 검증 실패: exit={worker.exitcode}")
        check_server(root, data_dir)
    print("Portable smoke passed: spawn, Chromium (headless/headed), SQLite, Excel, API, assets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
