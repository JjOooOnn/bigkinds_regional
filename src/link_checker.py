from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .url_utils import normalize_url
from .verdict import (
    LOGIN_REQUIRED_MARKERS,
    STRONG_BLOCK_MARKERS,
    classify_verdict_detailed,
    working_yn,
)


SPECIFIC_ARTICLE_SELECTOR = (
    "[itemprop='articleBody']:visible, .article-body:visible, .article_body:visible, "
    ".article-view:visible, .article_view:visible, #articleBodyContents:visible, "
    "#article-view-content-div:visible, .news_body:visible"
)
GENERIC_ARTICLE_SELECTOR = "article:visible"


@dataclass
class LinkCheckResult:
    original_url: str = ""
    final_url: str = ""
    http_status: int | None = None
    browser_result: str = "표시 실패"
    link_working_yn: str = "N"
    verdict: str = "확인필요"
    response_seconds: float = 0.0
    error_message: str = ""
    screenshot_path: str = ""
    source_href_raw: str = ""
    source_href_property: str = ""
    click_target_raw: str = ""
    normalization_input: str = ""
    normalization_method: str = ""
    click_target: str = ""
    click_before_url: str = ""
    click_after_url: str = ""
    first_opened_url: str = ""
    new_tab_yn: str = ""
    current_tab_moved_yn: str = ""
    inferred_url: str = ""
    url_structure_anomaly_yn: str = "N"
    url_structure_anomaly_details: str = ""
    access_reason_code: str = ""
    detected_phrase: str = ""
    detected_locator: str = ""
    detected_dom_area: str = ""
    detected_visible_yn: str = ""
    document_title: str = ""
    visible_h1: str = ""
    article_exists_yn: str = "N"
    primary_text_length: int = 0
    article_title_match_yn: str = "N"
    article_rendered_yn: str = "N"


def _normalized_title(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (value or "").lower())


def _title_tokens(value: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[0-9A-Za-z가-힣]+", value or "") if len(token) >= 2}


def article_title_matches(expected_title: str, candidates: list[str]) -> bool:
    if not expected_title:
        return False
    expected_normalized = _normalized_title(expected_title)
    expected_tokens = _title_tokens(expected_title)
    for candidate in candidates:
        if not candidate:
            continue
        actual_normalized = _normalized_title(candidate)
        direct_match = bool(
            expected_normalized and actual_normalized and
            (expected_normalized in actual_normalized or actual_normalized in expected_normalized)
        )
        similarity = SequenceMatcher(None, expected_normalized, actual_normalized).ratio()
        actual_tokens = _title_tokens(candidate)
        common = expected_tokens & actual_tokens
        token_ratio = len(common) / min(len(expected_tokens), len(actual_tokens)) if expected_tokens and actual_tokens else 0
        if direct_match or similarity >= 0.52 or (len(common) >= 3 and token_ratio >= 0.45):
            return True
    return False


def article_rendered_evidence(
    expected_title: str, page_title: str, body_text: str, paragraph_text_length: int,
    heading_texts: list[str] | None = None, article_text_length: int = 0,
) -> bool:
    """출처 제목과 실제 표시 텍스트를 조합해 기사 렌더링 여부를 판단한다."""
    if not expected_title or len(re.sub(r"\s+", "", body_text or "")) < 300:
        return False
    title_matches = article_title_matches(expected_title, [page_title, *(heading_texts or [])])
    compact_body_length = len(re.sub(r"\s+", "", body_text or ""))
    body_confirmed = paragraph_text_length >= 160 or article_text_length >= 160 or compact_body_length >= 800
    # 언론사가 송고 뒤 제목을 수정한 경우에도 명시적인 article 본문 후보와
    # 충분한 문단이 함께 있으면 실제 기사 본문이 표시된 근거로 인정한다.
    structured_article_body = article_text_length >= 400 and paragraph_text_length >= 160
    return (title_matches and body_confirmed) or structured_article_body


class BrowserLinkChecker:
    def __init__(self, timeout_ms: int = 30_000, retries: int = 2):
        self.timeout_ms = timeout_ms
        self.retries = retries

    async def inspect_open_page(
        self, page: Page, original_url: str, started_at: float,
        status_by_page: dict[Page, int], expected_title: str = "",
        status_by_url: dict[str, int] | None = None,
    ) -> LinkCheckResult:
        timed_out = False
        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                if attempt == 0:
                    await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
                else:
                    await page.reload(wait_until="domcontentloaded", timeout=self.timeout_ms)
                await page.wait_for_timeout(1_000)
                result = await self.inspect_rendered_page(
                    page, original_url, started_at, status_by_page,
                    expected_title=expected_title, status_by_url=status_by_url,
                )
                verdict = result.verdict
                if verdict == "정상" or attempt == self.retries:
                    return result
            except PlaywrightTimeoutError:
                timed_out = True
                last_error = f"{self.timeout_ms // 1000}초 내 응답 없음"
            except Exception as exc:
                last_error = str(exc).splitlines()[0][:300]
            await asyncio.sleep(0.3)
        status = self._status_for(page, status_by_page, status_by_url)
        decision = classify_verdict_detailed(
            http_status=status, final_url=page.url, title="", body_text="", timed_out=timed_out,
        )
        return LinkCheckResult(
            original_url=original_url or page.url, final_url=page.url,
            http_status=status, browser_result=decision.display,
            link_working_yn=working_yn(decision.verdict), verdict=decision.verdict,
            response_seconds=round(time.perf_counter() - started_at, 3),
            error_message=last_error or decision.error,
            access_reason_code=decision.reason_code,
        )

    async def inspect_rendered_page(
        self, page: Page, original_url: str, started_at: float,
        status_by_page: dict[Page, int], expected_title: str = "",
        status_by_url: dict[str, int] | None = None,
    ) -> LinkCheckResult:
        """추가 navigation 대기 없이 현재 Chromium 화면 자체를 판정한다."""
        title = await page.title()
        body = await page.locator("body").inner_text(timeout=min(self.timeout_ms, 10_000))
        paragraphs = await page.locator("p:visible").all_inner_texts()
        paragraph_text_length = sum(len(re.sub(r"\s+", "", text)) for text in paragraphs)
        headings = await page.locator("h1:visible, h2:visible").all_inner_texts()
        visible_h1s = await page.locator("h1:visible").all_inner_texts()
        main_texts = await page.locator("main:visible, [role='main']:visible").all_inner_texts()
        specific_article_texts = await page.locator(SPECIFIC_ARTICLE_SELECTOR).all_inner_texts()
        generic_article_texts = await page.locator(GENERIC_ARTICLE_SELECTOR).all_inner_texts()
        article_texts = [*specific_article_texts, *generic_article_texts]
        article_text_length = max(
            [len(re.sub(r"\s+", "", text)) for text in article_texts] + [0]
        )
        article_exists = bool(await page.locator(
            "article, [itemprop='articleBody'], .article-body, .article_body, .article-view, "
            ".article_view, #articleBodyContents, #article-view-content-div, .news_body"
        ).count())
        title_matches = article_title_matches(expected_title, [title, *headings])
        article_rendered = article_rendered_evidence(
            expected_title, title, body, paragraph_text_length,
            heading_texts=headings, article_text_length=article_text_length,
        )
        status = self._status_for(page, status_by_page, status_by_url)
        primary_article_texts = [text for text in specific_article_texts if text.strip()]
        if primary_article_texts:
            primary_components = [*headings, *primary_article_texts]
        elif main_texts:
            primary_components = [*headings, *main_texts]
        else:
            largest_generic = max(generic_article_texts, key=lambda text: len(re.sub(r"\s+", "", text)), default="")
            primary_components = [*headings, largest_generic]
        primary_text = "\n".join(text for text in primary_components if text)
        primary_text_length = len(re.sub(r"\s+", "", primary_text))
        decision = classify_verdict_detailed(
            http_status=status, final_url=page.url, title=title, body_text=body,
            article_rendered=article_rendered,
            primary_text=primary_text,
        )
        marker_evidence = await self._locate_marker(page, decision.detected_marker)
        return LinkCheckResult(
            original_url=original_url or page.url, final_url=page.url,
            http_status=status, browser_result=decision.display,
            link_working_yn=working_yn(decision.verdict), verdict=decision.verdict,
            response_seconds=round(time.perf_counter() - started_at, 3),
            error_message=decision.error,
            access_reason_code=decision.reason_code,
            detected_phrase=marker_evidence.get("text", ""),
            detected_locator=marker_evidence.get("locator", ""),
            detected_dom_area=marker_evidence.get("area", ""),
            detected_visible_yn=marker_evidence.get("visible_yn", ""),
            document_title=title,
            visible_h1=" | ".join(text.strip() for text in visible_h1s if text.strip()),
            article_exists_yn="Y" if article_exists else "N",
            primary_text_length=primary_text_length,
            article_title_match_yn="Y" if title_matches else "N",
            article_rendered_yn="Y" if article_rendered else "N",
        )

    async def open_url(
        self, page: Page, url: str, started_at: float,
        status_by_page: dict[Page, int], expected_title: str = "",
        status_by_url: dict[str, int] | None = None,
    ) -> LinkCheckResult:
        """검사 전용 page 이동을 재시도하고, timeout 때도 렌더링 결과를 우선한다."""
        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout_ms,
                )
                if response:
                    status_by_page[page] = response.status
                return await self.inspect_open_page(
                    page, url, started_at, status_by_page,
                    expected_title=expected_title, status_by_url=status_by_url,
                )
            except PlaywrightTimeoutError:
                last_error = f"{self.timeout_ms // 1000}초 내 응답 없음"
                try:
                    rendered = await self.inspect_rendered_page(
                        page, url, started_at, status_by_page,
                        expected_title=expected_title, status_by_url=status_by_url,
                    )
                    if rendered.verdict in {"정상", "접근제한", "링크오류", "서버오류"}:
                        return rendered
                except Exception:
                    pass
            except Exception as exc:
                last_error = str(exc).splitlines()[0][:300]
            if attempt < self.retries:
                await asyncio.sleep(0.3)
        status = self._status_for(page, status_by_page, status_by_url)
        decision = classify_verdict_detailed(
            http_status=status, final_url=page.url,
            title="", body_text="", timed_out=True,
        )
        return LinkCheckResult(
            original_url=url, final_url=page.url,
            http_status=status, browser_result=decision.display,
            link_working_yn=working_yn(decision.verdict), verdict=decision.verdict,
            response_seconds=round(time.perf_counter() - started_at, 3),
            error_message=last_error or decision.error,
            access_reason_code=decision.reason_code,
        )

    @staticmethod
    async def _locate_marker(page: Page, marker: str) -> dict[str, str]:
        if not marker:
            return {}
        return await page.evaluate(
            """marker => {
                const needle = String(marker || '').toLowerCase();
                const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                };
                const path = element => {
                    const parts = [];
                    for (let node = element; node && node.nodeType === 1 && parts.length < 8; node = node.parentElement) {
                        let part = node.tagName.toLowerCase();
                        if (node.id) {
                            part += '#' + CSS.escape(node.id);
                            parts.unshift(part);
                            break;
                        }
                        part += [...node.classList].slice(0, 3).map(value => '.' + CSS.escape(value)).join('');
                        parts.unshift(part);
                    }
                    return parts.join(' > ');
                };
                const elements = [...document.querySelectorAll('body *')];
                let candidates = elements.filter(element => {
                    const text = (element.innerText || '').trim().toLowerCase();
                    if (!text.includes(needle)) return false;
                    return ![...element.children].some(child => (child.innerText || '').toLowerCase().includes(needle));
                });
                candidates.sort((left, right) => Number(visible(right)) - Number(visible(left)));
                let element = candidates[0];
                let isVisible = element ? visible(element) : false;
                if (!element) {
                    candidates = elements.filter(element => {
                        const text = (element.textContent || '').trim().toLowerCase();
                        if (!text.includes(needle)) return false;
                        return ![...element.children].some(child => (child.textContent || '').toLowerCase().includes(needle));
                    });
                    element = candidates[0];
                    isVisible = false;
                }
                if (!element) return {};
                const ancestors = [];
                for (let node = element; node && node.nodeType === 1; node = node.parentElement) ancestors.push(node);
                const auxiliary = ancestors.find(node => {
                    const label = `${node.id || ''} ${node.className || ''} ${node.getAttribute('role') || ''} ${node.getAttribute('aria-label') || ''}`;
                    return /comment|reply|login|member|account|subscribe|subscription|share|social|advert|\bad\b/i.test(label);
                });
                const semantic = auxiliary || ancestors.find(node =>
                    ['HEADER', 'NAV', 'FOOTER', 'ASIDE', 'MAIN', 'ARTICLE'].includes(node.tagName) ||
                    node.getAttribute('role') === 'main'
                );
                return {
                    text: ((element.innerText || element.textContent || '').trim()).slice(0, 1000),
                    locator: path(element),
                    area: semantic ? path(semantic) : 'body',
                    visible_yn: isVisible ? 'Y' : 'N',
                };
            }""",
            marker,
        )

    @staticmethod
    def _status_for(
        page: Page, status_by_page: dict[Page, int], status_by_url: dict[str, int] | None,
    ) -> int | None:
        status = status_by_page.get(page)
        if status is None and status_by_url is not None:
            status = status_by_url.get(normalize_url(page.url))
            if status is not None:
                status_by_page[page] = status
        return status

    @staticmethod
    def click_error(seconds: float) -> LinkCheckResult:
        decision = classify_verdict_detailed(
            http_status=None, final_url="", title="", body_text="", click_error=True,
        )
        return LinkCheckResult(
            browser_result=decision.display, link_working_yn=working_yn(decision.verdict),
            verdict=decision.verdict, response_seconds=round(seconds, 3),
            error_message=decision.error, access_reason_code=decision.reason_code,
        )
