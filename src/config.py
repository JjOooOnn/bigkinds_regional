from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

TARGET_URL = "https://www.bigkinds.or.kr/regional/curation.do"
ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RuntimeConfig:
    """Web-server settings with the original local behavior as the default."""

    mode: str
    host: str
    port: int
    data_dir: Path

    @property
    def is_server(self) -> bool:
        return self.mode == "server"

    @property
    def open_browser(self) -> bool:
        return not self.is_server

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def screenshot_dir(self) -> Path:
        return self.data_dir / "artifacts" / "screenshots"

    @property
    def trace_dir(self) -> Path:
        return self.data_dir / "artifacts" / "traces"


def load_runtime_config(environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Read the small set of settings needed to start the web service."""
    env = os.environ if environ is None else environ
    mode = env.get("BIGKINDS_RUNTIME", "local").strip().lower() or "local"
    if mode not in {"local", "server"}:
        raise ValueError("BIGKINDS_RUNTIME은 'local' 또는 'server'여야 합니다.")

    if mode == "server":
        raw_port = env.get("PORT", "8000").strip() or "8000"
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("PORT는 1부터 65535 사이의 정수여야 합니다.") from exc
        host = "0.0.0.0"
    else:
        # PORT is a platform setting; ignore it unless server mode was selected.
        port = 8000
        host = "127.0.0.1"

    if not 1 <= port <= 65535:
        raise ValueError("PORT는 1부터 65535 사이의 정수여야 합니다.")

    configured_data_dir = env.get("BIGKINDS_DATA_DIR", "").strip()
    data_dir = Path(configured_data_dir) if configured_data_dir else ROOT_DIR
    return RuntimeConfig(mode=mode, host=host, port=port, data_dir=data_dir)


RUNTIME_CONFIG = load_runtime_config()
DATA_DIR = RUNTIME_CONFIG.data_dir
OUTPUT_DIR = RUNTIME_CONFIG.output_dir
WORK_DIR = RUNTIME_CONFIG.work_dir
SCREENSHOT_DIR = RUNTIME_CONFIG.screenshot_dir
TRACE_DIR = RUNTIME_CONFIG.trace_dir

RESULT_COLUMNS = [
    "순번", "조회요청일", "화면표시일", "지역명", "이슈순번", "이슈제목", "이슈분류",
    "출처수", "출처구분", "언론사명", "기사일자", "기사제목", "원본URL", "최종URL",
    "HTTP상태", "브라우저표시결과", "링크작동여부_YN", "최종판정", "응답시간_초",
    "오류내용", "점검일시",
]

DEBUG_COLUMNS = [
    "로그시각", "실행단계", "조회요청일", "화면표시일", "지역명", "이슈순번", "이슈제목",
    "href속성원문", "href프로퍼티값", "클릭대상URL원문", "URL처리전", "원본URL", "URL처리방식",
    "클릭직전URL", "클릭직후URL", "새페이지최초URL", "새탭생성여부_YN", "현재탭이동여부_YN",
    "추정정상URL", "URL구조이상여부", "URL구조이상내용", "DOM또는Locator",
    "이벤트", "예외유형", "상세내용", "HTTP상태", "최종URL", "스크린샷경로", "재시도횟수",
    "접근제한판정근거코드", "감지문구", "감지문구Locator", "감지문구DOM영역",
    "감지문구Visible_YN", "document.title", "visible h1", "article존재여부_YN",
    "주요콘텐츠텍스트길이", "기사제목일치여부_YN", "기사렌더링근거여부_YN",
]

VERDICTS = ["정상", "접근제한", "링크오류", "서버오류", "타임아웃", "클릭오류", "빈화면", "확인필요"]
ISSUE_CATEGORIES = {"정치", "경제", "사회", "문화", "국제", "IT과학"}
