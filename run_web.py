from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import date
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable, TextIO

import uvicorn

from src.config import ROOT_DIR, WORK_DIR
from src.logging_utils import configure_lifecycle_logging, log_lifecycle_event, sanitize


FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "dist" / "index.html"
SERVER_LOG_DIR = WORK_DIR / "server_logs"


class _DailyLogWriter:
    def __init__(self, log_dir: Path, *, today: Callable[[], date] = date.today):
        self.log_dir = Path(log_dir)
        self.today = today
        self.lock = threading.Lock()
        self._day: date | None = None
        self._stream: TextIO | None = None

    def _rotate_if_needed(self) -> Path:
        current_day = self.today()
        path = self.log_dir / f"server_{current_day.isoformat()}.log"
        if self._day != current_day or self._stream is None:
            if self._stream is not None:
                self._stream.close()
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("a", encoding="utf-8", buffering=1)
            self._day = current_day
        return path

    @property
    def current_path(self) -> Path:
        with self.lock:
            return self._rotate_if_needed()

    def write(self, data: str) -> int:
        with self.lock:
            self._rotate_if_needed()
            assert self._stream is not None
            self._stream.write(sanitize(data))
        return len(data)

    def flush(self) -> None:
        with self.lock:
            if self._stream is not None:
                self._stream.flush()

    def close(self) -> None:
        with self.lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None


class _SanitizedTee:
    def __init__(self, console: TextIO, log_writer: _DailyLogWriter):
        self.console = console
        self.log_writer = log_writer

    def write(self, data: str) -> int:
        written = self.console.write(data)
        self.log_writer.write(data)
        return written

    def flush(self) -> None:
        self.console.flush()
        self.log_writer.flush()

    def isatty(self) -> bool:
        return self.console.isatty()


@contextmanager
def preserve_console_output(log_dir: Path):
    log_writer = _DailyLogWriter(log_dir)
    try:
        stdout = _SanitizedTee(sys.stdout, log_writer)
        stderr = _SanitizedTee(sys.stderr, log_writer)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            yield log_writer
    finally:
        log_writer.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="빅카인즈 링크 점검 로컬 사용자 페이지 실행")
    parser.add_argument("--port", type=int, default=8000, help="로컬 포트(기본 8000)")
    parser.add_argument("--no-browser", action="store_true", help="기본 브라우저를 자동으로 열지 않음")
    parser.add_argument("--skip-build", action="store_true", help="프런트엔드 빌드 확인을 건너뜀")
    return parser


def ensure_frontend_build() -> None:
    if FRONTEND_INDEX.is_file():
        return
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError(
            "프런트엔드 빌드가 없고 npm을 찾을 수 없습니다. Node.js 설치 후 frontend에서 npm install을 실행해 주세요."
        )
    if not (FRONTEND_DIR / "node_modules").is_dir():
        raise RuntimeError(
            "프런트엔드 패키지가 설치되지 않았습니다. frontend에서 npm install을 먼저 실행해 주세요."
        )
    subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR, check=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port는 1부터 65535 사이여야 합니다.")
    with preserve_console_output(SERVER_LOG_DIR) as server_output:
        log_path = server_output.current_path
        lifecycle_logger = configure_lifecycle_logging()
        log_lifecycle_event(
            lifecycle_logger, "server_launcher", "starting",
            pid=os.getpid(), port=args.port, log_path=log_path,
        )
        if not args.skip_build:
            ensure_frontend_build()

        url = f"http://127.0.0.1:{args.port}"
        if not args.no_browser:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"로컬 사용자 페이지: {url}")
        print(f"서버 로그: {log_path}")
        print("종료하려면 이 창에서 Ctrl+C를 누르세요.")
        try:
            uvicorn.run("src.api.app:app", host="127.0.0.1", port=args.port, workers=1)
        except BaseException as exc:
            log_lifecycle_event(
                lifecycle_logger, "server_launcher", "stopped",
                pid=os.getpid(), termination_reason=type(exc).__name__, error=exc,
            )
            raise
        else:
            log_lifecycle_event(
                lifecycle_logger, "server_launcher", "stopped",
                pid=os.getpid(), termination_reason="uvicorn_returned",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
