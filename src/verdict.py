from __future__ import annotations

import re
from dataclasses import dataclass

STRONG_BLOCK_MARKERS = [
    "access denied", "captcha", "robot check", "로봇이 아닙니다", "보안 차단",
    "인증서 오류", "privacy error", "your connection is not private",
    "접근 제한", "접근이 제한", "접근 권한이 없", "권한이 없습니다",
]
LOGIN_REQUIRED_MARKERS = ["로그인이 필요", "로그인 후 이용"]
# 기존 외부 import와 진단 코드의 호환성을 유지한다.
BLOCK_MARKERS = [*STRONG_BLOCK_MARKERS, *LOGIN_REQUIRED_MARKERS]
NOT_FOUND_MARKERS = [
    "404 not found", "page not found", "페이지를 찾을 수 없", "파일 또는 디렉터리를 찾을 수 없",
    "요청하신 페이지를 찾을 수 없", "요청하신 페이지가 존재하지 않", "페이지가 존재하지 않",
    "존재하지 않는 페이지", "삭제된 기사", "삭제된 뉴스", "삭제되었거나 존재하지 않",
    "잘못된 접근입니다",
    "not found",
]
SERVER_MARKERS = ["internal server error", "bad gateway", "service unavailable", "서버 오류"]
BROWSER_ERROR_MARKERS = [
    "chrome-error://", "err_name_not_resolved", "err_connection_", "사이트에 연결할 수 없",
    "this site can’t be reached", "this site can't be reached",
]


@dataclass(frozen=True)
class VerdictDecision:
    verdict: str
    display: str
    error: str
    reason_code: str
    detected_marker: str = ""


def working_yn(verdict: str) -> str:
    return "Y" if verdict == "정상" else "N"


def classify_verdict(
    *, http_status: int | None, final_url: str, title: str, body_text: str,
    timed_out: bool = False, click_error: bool = False,
    article_rendered: bool = False, primary_text: str = "",
) -> tuple[str, str, str]:
    decision = classify_verdict_detailed(
        http_status=http_status, final_url=final_url, title=title, body_text=body_text,
        timed_out=timed_out, click_error=click_error, article_rendered=article_rendered,
        primary_text=primary_text,
    )
    return decision.verdict, decision.display, decision.error


def classify_verdict_detailed(
    *, http_status: int | None, final_url: str, title: str, body_text: str,
    timed_out: bool = False, click_error: bool = False,
    article_rendered: bool = False, primary_text: str = "",
) -> VerdictDecision:
    if click_error:
        return VerdictDecision(
            "클릭오류", "표시 실패", "링크 클릭 후 새 페이지가 열리지 않음",
            "CLICK_DID_NOT_OPEN_PAGE",
        )
    if timed_out:
        return VerdictDecision(
            "타임아웃", "표시 실패", "설정된 제한시간 내 응답 없음",
            "NAVIGATION_TIMEOUT",
        )
    combined = re.sub(r"\s+", " ", f"{title} {body_text}").lower()
    primary = re.sub(r"\s+", " ", f"{title} {primary_text}").lower()
    lowered_url = (final_url or "").lower()
    browser_error = any(marker in lowered_url or marker in combined for marker in BROWSER_ERROR_MARKERS)
    if browser_error:
        marker = next((marker for marker in BROWSER_ERROR_MARKERS if marker in lowered_url or marker in combined), "")
        return VerdictDecision(
            "링크오류", "표시 실패", "브라우저 오류 URL 또는 화면 표시",
            "BROWSER_ERROR_PAGE", marker,
        )
    if not final_url or lowered_url == "about:blank" or "about:blank" in lowered_url:
        return VerdictDecision("빈화면", "빈 화면", "빈 페이지가 열림", "BLANK_PAGE")

    file_not_found = "파일 또는 디렉터리를 찾을 수 없" in combined
    explicit_not_found = any(marker in primary or marker in combined for marker in NOT_FOUND_MARKERS)
    explicit_not_found = explicit_not_found or file_not_found
    explicit_not_found = explicit_not_found or bool(re.search(r"(?:^|\s)404(?:\s|$)", primary))
    not_found_error = (
        "404 페이지 표시 - 파일 또는 디렉터리를 찾을 수 없음"
        if file_not_found else "404 또는 페이지 없음 화면 표시"
    )

    # 상태 코드가 화면 문구보다 우선한다. 다만 구체적인 화면 문구가 있으면
    # 오류내용에는 사용자가 확인할 수 있는 문구를 남긴다.
    if http_status in (404, 410):
        return VerdictDecision(
            "링크오류", "표시 실패",
            not_found_error if explicit_not_found else f"HTTP {http_status} - 기사 페이지를 찾을 수 없음",
            "HTTP_NOT_FOUND",
        )
    if http_status is not None and http_status >= 500:
        return VerdictDecision(
            "서버오류", "표시 실패", "서버 오류 화면 표시", "HTTP_SERVER_ERROR",
        )
    if explicit_not_found and not article_rendered:
        marker = next((marker for marker in NOT_FOUND_MARKERS if marker in primary or marker in combined), "404")
        return VerdictDecision(
            "링크오류", "표시 실패", not_found_error, "NOT_FOUND_TEXT_MATCH", marker,
        )
    if any(marker in combined for marker in SERVER_MARKERS):
        marker = next(marker for marker in SERVER_MARKERS if marker in combined)
        return VerdictDecision(
            "서버오류", "표시 실패", "서버 오류 화면 표시", "SERVER_ERROR_TEXT_MATCH", marker,
        )

    strong_marker = next((marker for marker in STRONG_BLOCK_MARKERS if marker in combined), "")
    primary_strong_marker = next((marker for marker in STRONG_BLOCK_MARKERS if marker in primary), "")
    if http_status in (401, 403):
        return VerdictDecision(
            "접근제한", "접근 제한", f"HTTP {http_status} - 접근 또는 인증이 제한됨",
            "ACCESS_HTTP_STATUS", strong_marker,
        )
    # 정상 기사에 딸린 댓글·구독·로그인 같은 보조 기능도 권한/CAPTCHA 문구를
    # 표시할 수 있다. 실제 기사 렌더링 근거가 있으면 주요 콘텐츠 안에서 확인된
    # 문구만 페이지 전체의 접근 제한 근거로 사용한다.
    if strong_marker and (primary_strong_marker or not article_rendered):
        reason = "CAPTCHA 또는 접근 제한 화면 표시" if strong_marker == "captcha" else "접근 또는 인증이 제한됨"
        code = "ACCESS_STRONG_TEXT_PRIMARY" if primary_strong_marker else "ACCESS_STRONG_TEXT_NO_ARTICLE"
        return VerdictDecision("접근제한", "접근 제한", reason, code, primary_strong_marker or strong_marker)
    # 댓글·북마크·구독 기능의 로그인 안내는 정상 기사에도 표시된다. 실제
    # 기사 제목과 충분한 본문이 렌더링된 경우에는 페이지 전체 접근 제한으로
    # 해석하지 않는다.
    login_marker = next((marker for marker in LOGIN_REQUIRED_MARKERS if marker in combined), "")
    if login_marker and not article_rendered:
        return VerdictDecision(
            "접근제한", "접근 제한", "로그인이 필요한 화면 표시",
            "ACCESS_LOGIN_REQUIRED_NO_ARTICLE", login_marker,
        )
    if article_rendered:
        code = "ARTICLE_RENDERED_AUXILIARY_ACCESS_TEXT_IGNORED" if strong_marker else "ARTICLE_RENDERED"
        return VerdictDecision("정상", "정상 표시", "", code, strong_marker)

    meaningful = len(re.sub(r"\s+", "", body_text or "")) >= 40
    if not meaningful:
        return VerdictDecision(
            "빈화면", "빈 화면", "의미 있는 화면 내용이 없음", "NO_MEANINGFUL_CONTENT",
        )
    status_label = "없음" if http_status is None else str(http_status)
    return VerdictDecision(
        "확인필요", "확인 필요",
        f"실제 기사 제목 또는 본문 확인 근거 부족 (HTTP 상태 {status_label})",
        "INSUFFICIENT_ARTICLE_EVIDENCE",
    )
