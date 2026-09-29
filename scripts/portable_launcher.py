"""Standard-library bootstrap for the Windows portable distribution."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys
import tempfile
import traceback


def configure_environment(root: Path) -> None:
    # Set these before importing src.config; spawned workers inherit them.
    os.environ["BIGKINDS_RUNTIME"] = "local"
    os.environ["BIGKINDS_DATA_DIR"] = str(root)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root / "browsers")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True


def check_files(root: Path) -> None:
    for relative in (
        "runtime/python.exe", "runtime/python312._pth", "run_web.py",
        "src/api/app.py", "frontend/package.json", "frontend/dist/index.html",
    ):
        if not (root / relative).is_file():
            raise RuntimeError(
                f"필수 파일이 없습니다: {relative}\n"
                "ZIP 안에서 실행하지 말고 전체 압축을 새 폴더에 풀어 주세요."
            )
    for relative in ("runtime/Lib/site-packages", "frontend/dist/assets", "browsers"):
        directory = root / relative
        if not directory.is_dir() or not any(directory.iterdir()):
            raise RuntimeError(f"배포 폴더가 없거나 비어 있습니다: {relative}. 전체 압축을 다시 풀어 주세요.")


def check_writable(root: Path) -> None:
    for relative in (".", "output", "work", "work/server_logs", "artifacts/screenshots", "artifacts/traces"):
        directory = root / relative
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory) as probe:
                probe.write(b"write check")
                probe.flush()
        except OSError as exc:
            raise RuntimeError(
                f"파일을 저장할 수 없습니다: {directory}\n"
                "문서 폴더 등 쓰기 가능한 위치에 압축을 풀어 주세요."
            ) from exc


def check_port(port: int) -> None:
    try:
        with socket.socket() as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise RuntimeError(
            f"로컬 포트 {port}를 사용할 수 없습니다.\n"
            "이미 실행한 점검기 창이 있다면 그 창을 사용하거나 Ctrl+C로 종료해 주세요.\n"
            "다른 프로그램이 사용 중이면 실행.bat --port 8080 으로 실행할 수 있습니다."
        ) from exc


def check_browser(root: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path).resolve()
        if not executable.is_relative_to((root / "browsers").resolve()) or not executable.is_file():
            raise RuntimeError("포함된 Chromium을 찾을 수 없습니다. 배포 ZIP 전체를 다시 풀어 주세요.")


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="빅카인즈 링크 점검 실행")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1부터 65535 사이여야 합니다.")
    # The packaging script places this launcher at the distribution root.
    root = (root or Path(__file__).resolve().parent).resolve()
    try:
        configure_environment(root)
        check_files(root)
        check_writable(root)
        check_port(args.port)
        check_browser(root)
        from run_web import main as run_server

        server_args = ["--skip-build", "--port", str(args.port)]
        if args.no_browser:
            server_args.append("--no-browser")
        return run_server(server_args)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"\n[실행 오류] {exc}", file=sys.stderr)
        print("문제가 계속되면 이 창의 오류와 work/server_logs의 로그를 전달해 주세요.", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
