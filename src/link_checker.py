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
    VerdictDecision,
    classify_verdict_detailed,
    working_yn,
)


SPECIFIC_ARTICLE_SELECTOR = (
    "[itemprop='articleBody']:visible, .article-body:visible, .article_body:visible, "
    ".article-view:visible, .article_view:visible, #articleBodyContents:visible, "
    "#article-view-content-div:visible, .news_body:visible"
)
GENERIC_ARTICLE_SELECTOR = "article:visible"
NON_NEWS_TITLE_SELECTOR = (
    "h1, h2, h3, h4, h5, h6, "
    "[class*='title' i], [id*='title' i], "
    "[class*='subject' i], [id*='subject' i], "
    "[class*='heading' i], [id*='heading' i], "
    "[class*='view-title' i], [id*='view-title' i], "
    "[class*='board-title' i], [id*='board-title' i], "
    "[class*='report-title' i], [id*='report-title' i], "
    "[class*='tit' i], [id*='tit' i], "
    "table[summary*='상세'] thead th, main thead th, [role='main'] thead th, "
    "#content thead th, .content thead th"
)
TEXT_ACCESS_REASON_CODES = {
    "ACCESS_STRONG_TEXT_PRIMARY",
    "ACCESS_STRONG_TEXT_NO_ARTICLE",
    "ACCESS_LOGIN_REQUIRED_NO_ARTICLE",
}


@dataclass
class LinkCheckResult:
    original_url: str = ""
    inspection_url: str = ""
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
    url_html_entity_unescaped_yn: str = "N"
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
    non_news_title_match_yn: str = "N"
    non_news_content_rendered_yn: str = "N"
    matched_title: str = ""
    content_container_locator: str = ""
    attachment_exists_yn: str = "N"
    body_text_length: int = 0
    render_recheck_yn: str = "N"
    initial_body_text_length: int = 0
    rechecked_body_text_length: int = 0
    initial_document_title: str = ""
    rechecked_document_title: str = ""
    initial_verdict: str = ""
    rechecked_verdict: str = ""
    render_recheck_wait_seconds: float = 0.0


@dataclass(frozen=True)
class NonNewsDetailEvidence:
    title_match: bool = False
    content_rendered: bool = False
    matched_title: str = ""
    content_container_locator: str = ""
    content_text_length: int = 0
    attachment_exists: bool = False


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
    def __init__(
        self, timeout_ms: int = 30_000, retries: int = 2,
        render_recheck_timeout_ms: int = 4_000,
    ):
        self.timeout_ms = timeout_ms
        self.retries = retries
        self.render_recheck_timeout_ms = max(0, render_recheck_timeout_ms)

    async def inspect_open_page(
        self, page: Page, original_url: str, started_at: float,
        status_by_page: dict[Page, int], expected_title: str = "",
        status_by_url: dict[str, int] | None = None,
        source_type: str = "",
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
                    source_type=source_type,
                )
                result.inspection_url = original_url or page.url
                if self._is_render_recheck_candidate(result):
                    return await self._reinspect_after_render_wait(
                        page, result, original_url, started_at, status_by_page,
                        expected_title=expected_title, status_by_url=status_by_url,
                        source_type=source_type,
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
            inspection_url=original_url or page.url,
        )

    def _is_render_recheck_candidate(self, result: LinkCheckResult) -> bool:
        status_allows_wait = result.http_status is None or result.http_status == 200
        return (
            self.render_recheck_timeout_ms > 0
            and status_allows_wait
            and result.verdict in {"빈화면", "확인필요"}
            and result.final_url.startswith(("http://", "https://"))
        )

    async def _reinspect_after_render_wait(
        self, page: Page, initial: LinkCheckResult, original_url: str,
        started_at: float, status_by_page: dict[Page, int], *,
        expected_title: str, status_by_url: dict[str, int] | None,
        source_type: str,
    ) -> LinkCheckResult:
        wait_started = time.perf_counter()
        await self._wait_for_render_signal(page, expected_title)
        wait_seconds = round(time.perf_counter() - wait_started, 3)
        try:
            rechecked = await self.inspect_rendered_page(
                page, original_url, started_at, status_by_page,
                expected_title=expected_title, status_by_url=status_by_url,
                source_type=source_type,
            )
        except Exception:
            rechecked = initial

        rechecked.inspection_url = original_url or page.url
        rechecked.render_recheck_yn = "Y"
        rechecked.initial_body_text_length = initial.body_text_length
        rechecked.rechecked_body_text_length = rechecked.body_text_length
        rechecked.initial_document_title = initial.document_title
        rechecked.rechecked_document_title = rechecked.document_title
        rechecked.initial_verdict = initial.verdict
        rechecked.rechecked_verdict = rechecked.verdict
        rechecked.render_recheck_wait_seconds = wait_seconds
        return rechecked

    async def _wait_for_render_signal(self, page: Page, expected_title: str) -> None:
        deadline = time.perf_counter() + self.render_recheck_timeout_ms / 1000
        while True:
            remaining_ms = int((deadline - time.perf_counter()) * 1000)
            if remaining_ms <= 0:
                return
            await page.wait_for_timeout(min(250, remaining_ms))
            try:
                body = await page.locator("body").inner_text(timeout=min(500, remaining_ms))
                title = await page.title()
                headings = await page.locator(
                    "h1:visible, h2:visible, h3:visible, h4:visible, h5:visible, h6:visible"
                ).all_inner_texts()
                content_texts = await page.locator(
                    f"{SPECIFIC_ARTICLE_SELECTOR}, article:visible, main:visible, [role='main']:visible"
                ).all_inner_texts()
            except Exception:
                continue

            body_length = len(re.sub(r"\s+", "", body or ""))
            content_length = max(
                [len(re.sub(r"\s+", "", text)) for text in content_texts] + [0]
            )
            if (
                body_length >= 300
                or article_title_matches(expected_title, [title, *headings])
                or content_length >= 80
            ):
                return

    async def inspect_rendered_page(
        self, page: Page, original_url: str, started_at: float,
        status_by_page: dict[Page, int], expected_title: str = "",
        status_by_url: dict[str, int] | None = None,
        source_type: str = "",
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
        body_text_length = len(re.sub(r"\s+", "", body or ""))
        decision = classify_verdict_detailed(
            http_status=status, final_url=page.url, title=title, body_text=body,
            article_rendered=article_rendered,
            primary_text=primary_text,
        )
        marker_evidence = await self._locate_marker(page, decision.detected_marker)
        non_news_evidence = NonNewsDetailEvidence()
        if self._should_inspect_non_news_detail(
            source_type, status, decision, marker_evidence,
        ):
            non_news_evidence = await self._inspect_non_news_detail(page, expected_title)
            if non_news_evidence.content_rendered:
                decision = VerdictDecision(
                    "정상", "정상 표시", "", "NON_NEWS_DETAIL_RENDERED",
                    decision.detected_marker,
                )
        return LinkCheckResult(
            original_url=original_url or page.url,
            inspection_url=original_url or page.url, final_url=page.url,
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
            non_news_title_match_yn="Y" if non_news_evidence.title_match else "N",
            non_news_content_rendered_yn="Y" if non_news_evidence.content_rendered else "N",
            matched_title=non_news_evidence.matched_title,
            content_container_locator=non_news_evidence.content_container_locator,
            attachment_exists_yn="Y" if non_news_evidence.attachment_exists else "N",
            body_text_length=body_text_length,
        )

    async def open_url(
        self, page: Page, url: str, started_at: float,
        status_by_page: dict[Page, int], expected_title: str = "",
        status_by_url: dict[str, int] | None = None,
        source_type: str = "",
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
                result = await self.inspect_open_page(
                    page, url, started_at, status_by_page,
                    expected_title=expected_title, status_by_url=status_by_url,
                    source_type=source_type,
                )
                result.inspection_url = url
                return result
            except PlaywrightTimeoutError:
                last_error = f"{self.timeout_ms // 1000}초 내 응답 없음"
                try:
                    rendered = await self.inspect_rendered_page(
                        page, url, started_at, status_by_page,
                        expected_title=expected_title, status_by_url=status_by_url,
                        source_type=source_type,
                    )
                    if rendered.verdict in {"정상", "접근제한", "링크오류", "서버오류"}:
                        rendered.inspection_url = url
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
            original_url=url, inspection_url=url, final_url=page.url,
            http_status=status, browser_result=decision.display,
            link_working_yn=working_yn(decision.verdict), verdict=decision.verdict,
            response_seconds=round(time.perf_counter() - started_at, 3),
            error_message=last_error or decision.error,
            access_reason_code=decision.reason_code,
        )

    @staticmethod
    def _should_inspect_non_news_detail(
        source_type: str, status: int | None, decision: VerdictDecision,
        marker_evidence: dict[str, str],
    ) -> bool:
        normalized_type = (source_type or "").strip()
        if not normalized_type or normalized_type == "뉴스":
            return False
        if status is not None and status >= 400:
            return False
        if decision.verdict == "확인필요":
            return True
        return (
            decision.verdict == "접근제한"
            and decision.reason_code in TEXT_ACCESS_REASON_CODES
            and marker_evidence.get("auxiliary_yn") == "Y"
        )

    @staticmethod
    async def _inspect_non_news_detail(
        page: Page, expected_title: str,
    ) -> NonNewsDetailEvidence:
        if not expected_title:
            return NonNewsDetailEvidence()

        candidate_data = await page.evaluate(
            r"""({selector, expectedLength}) => {
                const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                };
                const label = element => [
                    element.id || '',
                    typeof element.className === 'string' ? element.className : '',
                    element.getAttribute('role') || '',
                    element.getAttribute('aria-label') || '',
                ].join(' ').trim();
                const auxiliary = element => {
                    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
                        if (['HEADER', 'NAV', 'FOOTER', 'ASIDE', 'LI'].includes(node.tagName)) return true;
                        const value = label(node);
                        const detailLike = /detail|view|board|bbs|report|post|content|write/i.test(value);
                        if (/comment|reply|share|social|subscribe|subscription|advert|related|prev|next|bookmark|favorite|banner|hero|carousel|slider/i.test(value)) return true;
                        if (!detailLike && /(?:^|[\s_-])(list|search|result|menu|item)(?:$|[\s_-])/i.test(value)) return true;
                    }
                    return false;
                };
                const path = element => {
                    const parts = [];
                    for (let node = element; node && node.nodeType === 1 && parts.length < 10; node = node.parentElement) {
                        let part = node.tagName.toLowerCase();
                        if (node.id) {
                            part += '#' + CSS.escape(node.id);
                            parts.unshift(part);
                            break;
                        }
                        const siblings = node.parentElement
                            ? [...node.parentElement.children].filter(value => value.tagName === node.tagName)
                            : [];
                        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
                        parts.unshift(part);
                    }
                    return parts.join(' > ');
                };
                const minimumLength = Math.min(8, Math.max(4, expectedLength));
                const candidates = [...document.querySelectorAll(selector)].flatMap(element => {
                    if (!visible(element) || auxiliary(element)) return [];
                    const text = (element.innerText || '').replace(/\s+/g, ' ').trim();
                    const compactLength = text.replace(/\s+/g, '').length;
                    if (compactLength < minimumLength || compactLength > 300) return [];
                    return [{
                        text,
                        tag: element.tagName,
                        label: label(element),
                        locator: path(element),
                    }];
                });
                return {
                    documentTitle: document.title || '',
                    ogTitle: document.querySelector('meta[property="og:title"]')?.content || '',
                    twitterTitle: document.querySelector('meta[name="twitter:title"]')?.content || '',
                    candidates,
                };
            }""",
            {
                "selector": NON_NEWS_TITLE_SELECTOR,
                "expectedLength": len(_normalized_title(expected_title)),
            },
        )
        metadata_titles = [
            candidate_data.get("documentTitle", ""),
            candidate_data.get("ogTitle", ""),
            candidate_data.get("twitterTitle", ""),
        ]
        metadata_match = article_title_matches(expected_title, metadata_titles)
        matching = [
            candidate for candidate in candidate_data.get("candidates", [])
            if article_title_matches(expected_title, [candidate.get("text", "")])
        ]
        if not matching:
            matched_metadata = next(
                (value for value in metadata_titles if article_title_matches(expected_title, [value])),
                "",
            )
            return NonNewsDetailEvidence(
                title_match=metadata_match,
                matched_title=matched_metadata,
            )

        expected_length = len(_normalized_title(expected_title))
        tag_priority = {f"H{level}": level for level in range(1, 7)}
        tag_priority["TH"] = 7
        matching.sort(key=lambda candidate: (
            abs(len(_normalized_title(candidate.get("text", ""))) - expected_length),
            tag_priority.get(candidate.get("tag", ""), 8),
            len(candidate.get("text", "")),
        ))
        matched = matching[0]
        container = await page.evaluate(
            r"""({titleLocator, titleText}) => {
                const title = document.querySelector(titleLocator);
                if (!title) return {};
                const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                };
                const label = element => [
                    element.id || '',
                    typeof element.className === 'string' ? element.className : '',
                    element.getAttribute('role') || '',
                    element.getAttribute('aria-label') || '',
                ].join(' ').trim();
                const auxiliaryNode = element => {
                    if (['HEADER', 'NAV', 'FOOTER', 'ASIDE', 'LI'].includes(element.tagName)) return true;
                    const value = label(element);
                    const detailLike = /detail|view|board|bbs|report|post|content|write/i.test(value);
                    if (/comment|reply|share|social|subscribe|subscription|advert|related|prev|next|bookmark|favorite|banner|hero|carousel|slider/i.test(value)) return true;
                    return !detailLike && /(?:^|[\s_-])(list|search|result|menu|item)(?:$|[\s_-])/i.test(value);
                };
                const path = element => {
                    const parts = [];
                    for (let node = element; node && node.nodeType === 1 && parts.length < 10; node = node.parentElement) {
                        let part = node.tagName.toLowerCase();
                        if (node.id) {
                            part += '#' + CSS.escape(node.id);
                            parts.unshift(part);
                            break;
                        }
                        const siblings = node.parentElement
                            ? [...node.parentElement.children].filter(value => value.tagName === node.tagName)
                            : [];
                        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
                        parts.unshift(part);
                    }
                    return parts.join(' > ');
                };
                const attachmentLinks = root => [...root.querySelectorAll('a[href]')].filter(anchor => {
                    if (!visible(anchor)) return false;
                    const value = [
                        anchor.getAttribute('href') || '',
                        anchor.getAttribute('download') || '',
                        anchor.getAttribute('title') || '',
                        anchor.innerText || '',
                    ].join(' ');
                    return anchor.hasAttribute('download') ||
                        /\.(pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|zip)\b/i.test(value);
                });
                const contentText = root => {
                    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                    const values = [];
                    while (walker.nextNode()) {
                        const textNode = walker.currentNode;
                        const parent = textNode.parentElement;
                        if (!parent || !visible(parent) || title.contains(parent)) continue;
                        if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'LABEL'].includes(parent.tagName)) continue;
                        let excluded = false;
                        for (let node = parent; node && node !== root; node = node.parentElement) {
                            if (auxiliaryNode(node)) {
                                excluded = true;
                                break;
                            }
                        }
                        if (!excluded) values.push(textNode.nodeValue || '');
                    }
                    const normalized = values.join(' ').replace(/\s+/g, ' ').trim();
                    const withoutTitle = normalized.replace(titleText, '').trim();
                    return {
                        text: withoutTitle,
                        length: withoutTitle.replace(/\s+/g, '').length,
                    };
                };

                let depth = 0;
                for (let node = title.parentElement; node && node.nodeType === 1; node = node.parentElement) {
                    if (['BODY', 'HTML'].includes(node.tagName) || depth++ > 8) break;
                    if (auxiliaryNode(node)) continue;
                    const attachments = attachmentLinks(node);
                    const content = contentText(node);
                    if (content.length > 12000) continue;
                    if (attachments.length || content.length >= 80) {
                        return {
                            locator: path(node),
                            contentTextLength: content.length,
                            attachmentExists: attachments.length > 0,
                        };
                    }
                }
                return {};
            }""",
            {
                "titleLocator": matched.get("locator", ""),
                "titleText": matched.get("text", ""),
            },
        )
        container_locator = container.get("locator", "")
        return NonNewsDetailEvidence(
            title_match=True,
            content_rendered=bool(container_locator),
            matched_title=matched.get("text", ""),
            content_container_locator=container_locator,
            content_text_length=int(container.get("contentTextLength", 0)),
            attachment_exists=bool(container.get("attachmentExists", False)),
        )

    @staticmethod
    async def _locate_marker(page: Page, marker: str) -> dict[str, str]:
        if not marker:
            return {}
        return await page.evaluate(
            r"""marker => {
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
                const context = element => {
                    const ancestors = [];
                    for (let node = element; node && node.nodeType === 1; node = node.parentElement) ancestors.push(node);
                    const auxiliary = ancestors.find(node => {
                        if (['HEADER', 'NAV', 'FOOTER', 'ASIDE'].includes(node.tagName)) return true;
                        const label = `${node.id || ''} ${typeof node.className === 'string' ? node.className : ''} ${node.getAttribute('role') || ''} ${node.getAttribute('aria-label') || ''}`;
                        const id = (node.id || '').toLowerCase();
                        const pageChrome = ['header', 'gnb', 'global-header', 'site-header'].includes(id) ||
                            /site-header|global-header|header-wrap|header_wrap|top-nav|top-menu/i.test(label);
                        const auxiliaryFeature = /comment|reply|subscribe|subscription|share|social|advert|\bad\b|related|prev|next|bookmark|favorite|login[-_ ]?(?:widget|menu|tools)|member[-_ ]?(?:widget|menu|tools)/i.test(label);
                        return pageChrome || auxiliaryFeature;
                    });
                    const semantic = auxiliary || ancestors.find(node =>
                        ['HEADER', 'NAV', 'FOOTER', 'ASIDE', 'MAIN', 'ARTICLE'].includes(node.tagName) ||
                        node.getAttribute('role') === 'main'
                    );
                    return {auxiliary, semantic};
                };
                let inspected = candidates.map(element => ({
                    element,
                    visible: visible(element),
                    ...context(element),
                }));
                inspected.sort((left, right) =>
                    Number(right.visible) - Number(left.visible) ||
                    Number(Boolean(left.auxiliary)) - Number(Boolean(right.auxiliary))
                );
                let selected = inspected[0];
                if (!selected) {
                    candidates = elements.filter(element => {
                        const text = (element.textContent || '').trim().toLowerCase();
                        if (!text.includes(needle)) return false;
                        return ![...element.children].some(child => (child.textContent || '').toLowerCase().includes(needle));
                    });
                    const element = candidates[0];
                    if (element) selected = {element, visible: false, ...context(element)};
                }
                if (!selected) return {};
                const visibleMatches = inspected.filter(item => item.visible);
                const auxiliaryOnly = visibleMatches.length > 0 &&
                    visibleMatches.every(item => Boolean(item.auxiliary));
                const {element, semantic} = selected;
                return {
                    text: ((element.innerText || element.textContent || '').trim()).slice(0, 1000),
                    locator: path(element),
                    area: semantic ? path(semantic) : 'body',
                    visible_yn: selected.visible ? 'Y' : 'N',
                    auxiliary_yn: auxiliaryOnly ? 'Y' : 'N',
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
