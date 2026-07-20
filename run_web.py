from __future__ import annotations

import argparse
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path

import uvicorn

from src.config import ROOT_DIR


FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "dist" / "index.html"


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
    if not args.skip_build:
        ensure_frontend_build()

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"로컬 사용자 페이지: {url}")
    print("종료하려면 이 창에서 Ctrl+C를 누르세요.")
    uvicorn.run("src.api.app:app", host="127.0.0.1", port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
