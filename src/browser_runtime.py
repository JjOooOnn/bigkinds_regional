from __future__ import annotations

import os
import selectors
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager

from .config import RuntimeConfig, load_runtime_config


def chromium_launch_options(*, headed: bool, runtime: RuntimeConfig, channel: str | None = None) -> dict:
    options: dict = {"headless": not headed}
    if channel:
        options["channel"] = channel
    if runtime.is_server:
        options["args"] = ["--disable-dev-shm-usage"]
    return options


def verification_environments(runtime: RuntimeConfig) -> tuple[tuple[str, dict], ...]:
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return ()
    bundled = ("번들 Chromium headed", chromium_launch_options(headed=True, runtime=runtime))
    if runtime.is_server:
        return (bundled,)
    return (
        bundled,
        ("Microsoft Edge headed", chromium_launch_options(headed=True, runtime=runtime, channel="msedge")),
    )


def is_missing_browser_capability(error: BaseException) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in (
        "executable doesn't exist", "distribution 'msedge' is not found",
        "missing x server", "without having a xserver running",
    ))


def _wait_for_display(process: subprocess.Popen, read_fd: int, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    data = b""
    with selectors.DefaultSelector() as selector:
        selector.register(read_fd, selectors.EVENT_READ)
        while time.monotonic() < deadline and process.poll() is None:
            if not selector.select(timeout=min(0.1, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(read_fd, 32)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                number = data.strip()
                if number.isdigit():
                    return ":" + number.decode("ascii")
                break
    raise RuntimeError("Xvfb가 제한시간 안에 가상 화면을 준비하지 못했습니다.")


@contextmanager
def managed_display(runtime: RuntimeConfig | None = None):
    runtime = runtime or load_runtime_config()
    if not runtime.is_server:
        yield
        return
    if not sys.platform.startswith("linux"):
        raise RuntimeError("서버의 Xvfb 실행은 Linux 환경에서 지원합니다.")
    executable = shutil.which("Xvfb")
    if not executable:
        raise RuntimeError("서버 모드에는 Xvfb가 필요합니다. 런타임 이미지의 설치 상태를 확인해 주세요.")

    previous_display = os.environ.get("DISPLAY")
    read_fd, write_fd = os.pipe()
    process = None
    try:
        process = subprocess.Popen(
            [executable, "-displayfd", str(write_fd), "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
            pass_fds=(write_fd,), stdin=subprocess.DEVNULL,
        )
        os.close(write_fd)
        write_fd = -1
        os.environ["DISPLAY"] = _wait_for_display(process, read_fd)
        yield
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)
        if previous_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = previous_display
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
