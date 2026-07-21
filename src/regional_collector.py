from __future__ import annotations

import re
import time
from datetime import date, datetime

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .checkpoint import CheckpointStore
from .config import ISSUE_CATEGORIES, SCREENSHOT_DIR, TARGET_URL
from .date_navigation import DateNavigator, inclusive_dates
from .link_checker import BrowserLinkChecker, LinkCheckResult
from .logging_utils import debug_entry, sanitize
from .models import AuditRow, SourceInfo
from .source_parser import parse_source_text
from .url_utils import analyze_url_structure, infer_external_url, normalize_url, resolve_url_reference
from .regions import REGION_DISPLAY_ORDER, validate_site_regions
from .application.progress import (
    AuditCancelled,
    CancellationToken,
    NeverCancelToken,
    NullProgressReporter,
    ProgressReporter,
)


AUTOMATION_VERIFICATION_ENVIRONMENTS = (
    ("번들 Chromium headed", {"headless": False}),
    ("Microsoft Edge headed", {"headless": False, "channel": "msedge"}),
)


class RegionalCollector:
    def __init__(
        self, *, start_date: date, end_date: date, regions: list[str] | None,
        headed: bool, max_issues: int | None, timeout_ms: int, retries: int,
        link_delay_ms: int, checkpoint: CheckpointStore, logger,
        debug: bool = False, progress_reporter: ProgressReporter | None = None,
        cancellation_token: CancellationToken | None = None,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.requested_regions = regions
        self.headed = headed
        self.max_issues = max_issues
        self.timeout_ms = timeout_ms
        self.retries = retries
        self.link_delay_ms = link_delay_ms
        self.checkpoint = checkpoint
        self.logger = logger
        self.debug = debug
        self.progress_reporter = progress_reporter or NullProgressReporter()
        self.cancellation_token = cancellation_token or NeverCancelToken()
        self.issue_keys: set[tuple[str, str, int]] = set()
        self.completed_region_names: set[str] = set()
        self.had_partial_failures = False
        existing_rows = list(getattr(checkpoint, "rows", []))
        self.known_links = len(existing_rows)
        self.processed_links = len(existing_rows)
        self.normal_count = sum(row.verdict == "정상" for row in existing_rows)
        self.error_count = self.processed_links - self.normal_count
        self.completed_region_units = 0
        self.status_by_page: dict[Page, int] = {}
        self.status_by_url: dict[str, int] = {}
        self.first_url_by_page: dict[Page, str] = {}
        self._playwright: Playwright | None = None
        self._verification_sessions: dict[str, tuple[Browser, BrowserContext]] = {}

    async def run(self) -> tuple[list[AuditRow], list]:
        async with async_playwright() as playwright:
            self._playwright = playwright
            browser = await playwright.chromium.launch(headless=not self.headed)
            context = await browser.new_context(locale="ko-KR")
            context.set_default_timeout(self.timeout_ms)
            self._track_navigation(context)
            page = await context.new_page()
            try:
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(4_000)
                region_names = await self._read_regions(page)
                selected_regions = self._select_requested_regions(region_names)
                navigator = DateNavigator(page, self.timeout_ms)
                checker = BrowserLinkChecker(self.timeout_ms, self.retries)
                requested_dates = list(inclusive_dates(self.start_date, self.end_date))
                total_region_units = len(requested_dates) * len(selected_regions)
                self.progress_reporter.emit(
                    "audit_started", "링크 점검을 시작합니다.",
                    total_regions=len(selected_regions),
                    total_region_units=total_region_units,
                    processed_links=self.processed_links,
                    normal_count=self.normal_count,
                    error_count=self.error_count,
                )
                for requested in requested_dates:
                    self._raise_if_cancelled()
                    self.progress_reporter.emit(
                        "date_started", f"{requested.isoformat()} 점검을 시작합니다.",
                        current_date=requested.isoformat(),
                    )
                    ok, displayed = await navigator.move_to(requested, retries=1)
                    if not ok:
                        self.had_partial_failures = True
                        self._debug("날짜이동", requested, displayed, event="화면표시일 불일치", details=f"요청 {requested}, 화면 {displayed}")
                        self.logger.warning("[%s] 화면표시일 불일치로 건너뜁니다: %s", requested, displayed)
                        continue
                    for region_order, region in enumerate(selected_regions):
                        self._raise_if_cancelled()
                        key = (requested.isoformat(), region)
                        if key in self.checkpoint.completed:
                            self.logger.info("[%s][%s] 체크포인트 완료 항목 건너뜀", requested, region)
                            self.completed_region_names.add(region)
                            self.completed_region_units += 1
                            self.progress_reporter.emit(
                                "region_completed", f"{region}의 완료된 체크포인트를 건너뜁니다.",
                                current_date=requested.isoformat(), current_region=region,
                                completed_regions=self.completed_region_units,
                                total_region_units=total_region_units,
                            )
                            continue
                        self.progress_reporter.emit(
                            "region_started", f"{region} 점검을 시작합니다.",
                            current_date=requested.isoformat(), current_region=region,
                            completed_regions=self.completed_region_units,
                            total_region_units=total_region_units,
                        )
                        completed = await self._audit_region(
                            page, context, checker, requested, displayed, region, region_order
                        )
                        if completed:
                            self.checkpoint.mark_completed(*key)
                            self.completed_region_names.add(region)
                            self.completed_region_units += 1
                            self.progress_reporter.emit(
                                "region_completed", f"{region} 점검이 완료되었습니다.",
                                current_date=requested.isoformat(), current_region=region,
                                completed_regions=self.completed_region_units,
                                total_region_units=total_region_units,
                                processed_links=self.processed_links,
                                normal_count=self.normal_count,
                                error_count=self.error_count,
                            )
                        else:
                            self.had_partial_failures = True
            finally:
                await self._close_verification_sessions()
                self._playwright = None
                # Chromium이 비정상 종료되었거나 사용자가 중단한 경우에도
                # 이미 닫힌 객체를 다시 닫다가 원래 결과 저장을 방해하지 않는다.
                try:
                    await context.close()
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass
        return self.checkpoint.rows, self.checkpoint.debug_entries

    async def _verification_context(
        self, environment: str, launch_options: dict[str, object],
    ) -> BrowserContext:
        existing = self._verification_sessions.get(environment)
        if existing is not None:
            return existing[1]
        if self._playwright is None:
            raise RuntimeError("대체 브라우저 검증에 사용할 Playwright 실행기가 없습니다.")
        browser = await self._playwright.chromium.launch(**launch_options)
        context = await browser.new_context(locale="ko-KR")
        context.set_default_timeout(self.timeout_ms)
        self._verification_sessions[environment] = (browser, context)
        return context

    async def _close_verification_sessions(self) -> None:
        sessions = list(self._verification_sessions.values())
        self._verification_sessions.clear()
        for browser, _ in reversed(sessions):
            try:
                await browser.close()
            except Exception:
                pass

    async def _verify_automation_environment_block(
        self, primary: LinkCheckResult, url: str, expected_title: str,
        debug_context: dict, started_at: float, source_type: str = "",
    ) -> LinkCheckResult:
        """headless 메인 문서만 403인 경우 headed 브라우저의 실제 DOM을 재확인한다."""
        should_verify = (
            not self.headed
            and primary.verdict == "접근제한"
            and primary.http_status in (401, 403)
            and primary.article_rendered_yn != "Y"
            and bool(url)
        )
        if not should_verify or self._playwright is None:
            return primary

        attempts: list[str] = []
        for environment, launch_options in AUTOMATION_VERIFICATION_ENVIRONMENTS:
            page: Page | None = None
            try:
                context = await self._verification_context(environment, launch_options)
                page = await context.new_page()
                verifier = BrowserLinkChecker(self.timeout_ms, retries=0)
                verified = await verifier.open_url(
                    page, url, started_at, {}, expected_title=expected_title,
                    source_type=source_type,
                )
                attempts.append(
                    f"{environment}: main_status={verified.http_status}, "
                    f"판정={verified.verdict}, 기사렌더링={verified.article_rendered_yn}"
                )
                if verified.verdict != "정상" or verified.article_rendered_yn != "Y":
                    continue

                verified.access_reason_code = "ARTICLE_RENDERED_AFTER_AUTOMATION_BLOCK"
                details = (
                    "기본환경=번들 Chromium headless; "
                    f"기본 main document HTTP={primary.http_status}; "
                    f"기본 document.title={primary.document_title}; 기본 h1={primary.visible_h1}; "
                    f"검증환경={environment}; 검증 main document HTTP={verified.http_status}; "
                    f"기사제목일치={verified.article_title_match_yn}; 기사렌더링={verified.article_rendered_yn}"
                )
                self.checkpoint.add_debug(debug_entry(
                    "브라우저환경비교", **debug_context,
                    original_url=url, final_url=verified.final_url,
                    event="자동화 환경 차단", details=details,
                    http_status=primary.http_status if primary.http_status is not None else "",
                    access_reason_code=verified.access_reason_code,
                    document_title=verified.document_title,
                    visible_h1=verified.visible_h1,
                    article_exists_yn=verified.article_exists_yn,
                    primary_text_length=verified.primary_text_length,
                    article_title_match_yn=verified.article_title_match_yn,
                    article_rendered_yn=verified.article_rendered_yn,
                    non_news_title_match_yn=verified.non_news_title_match_yn,
                    non_news_content_rendered_yn=verified.non_news_content_rendered_yn,
                    matched_title=verified.matched_title,
                    content_container_locator=verified.content_container_locator,
                    attachment_exists_yn=verified.attachment_exists_yn,
                ))
                self.logger.info(
                    "자동화 환경 차단 확인: headless HTTP %s, %s HTTP %s, 기사 DOM 정상",
                    primary.http_status, environment, verified.http_status,
                )
                return verified
            except Exception as exc:
                attempts.append(f"{environment}: {type(exc).__name__} {sanitize(exc)}")
            finally:
                try:
                    if page is not None and not page.is_closed():
                        await page.close()
                except Exception:
                    pass

        self.checkpoint.add_debug(debug_entry(
            "브라우저환경비교", **debug_context,
            original_url=url, final_url=primary.final_url,
            event="자동화 환경 재검증 실패",
            details="; ".join(attempts),
            http_status=primary.http_status if primary.http_status is not None else "",
            access_reason_code=primary.access_reason_code,
            document_title=primary.document_title,
            visible_h1=primary.visible_h1,
            article_exists_yn=primary.article_exists_yn,
            primary_text_length=primary.primary_text_length,
            article_title_match_yn=primary.article_title_match_yn,
            article_rendered_yn=primary.article_rendered_yn,
            non_news_title_match_yn=primary.non_news_title_match_yn,
            non_news_content_rendered_yn=primary.non_news_content_rendered_yn,
            matched_title=primary.matched_title,
            content_container_locator=primary.content_container_locator,
            attachment_exists_yn=primary.attachment_exists_yn,
        ))
        return primary

    def _track_navigation(self, context: BrowserContext) -> None:
        def on_page(page: Page):
            def on_request(request):
                try:
                    if request.is_navigation_request() and request.frame == page.main_frame:
                        self.first_url_by_page.setdefault(page, request.url)
                except Exception:
                    pass
            page.on("request", on_request)

        def on_response(response):
            try:
                request = response.request
                if not (request.is_navigation_request() and request.resource_type == "document"):
                    return
                try:
                    frame = response.frame
                    page = frame.page
                except Exception:
                    # popup의 첫 요청은 frame 객체보다 먼저 만들어질 수 있다.
                    # 이 경우에만 URL을 키로 상태를 먼저 보존한다.
                    self.status_by_url[normalize_url(response.url)] = response.status
                    return
                if frame == page.main_frame:
                    self.status_by_url[normalize_url(response.url)] = response.status
                    self.first_url_by_page.setdefault(page, response.url)
                    self.status_by_page[page] = response.status
            except Exception:
                pass
        context.on("page", on_page)
        context.on("response", on_response)

    @staticmethod
    def _region_combo(page: Page) -> Locator:
        """진단 DOM에서 확인한 지역 combobox 한 개만 선택한다."""
        return page.locator(
            '[role="combobox"][aria-controls="region-listbox"][aria-haspopup="listbox"]'
        )

    @staticmethod
    def _region_listbox(page: Page) -> Locator:
        return page.locator('#region-listbox[role="listbox"][aria-label="지역 선택"]')

    async def _dismiss_header_overlay(self, page: Page) -> None:
        """fixed 헤더 메뉴의 backdrop이 본문 컨트롤을 가로채지 않게 정리한다."""
        await page.keyboard.press("Escape")
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        await page.mouse.move(max(1, viewport["width"] - 2), max(1, viewport["height"] - 2))
        try:
            await page.wait_for_function(
                """() => {
                    const backdrop = document.querySelector('#header .backdrop');
                    if (!backdrop) return true;
                    const style = getComputedStyle(backdrop);
                    return style.display === 'none' || style.visibility === 'hidden' ||
                           style.pointerEvents === 'none';
                }""",
                timeout=min(self.timeout_ms, 3_000),
            )
        except Exception:
            # 아래의 elementFromPoint 검증과 click fallback에서 실제 가림 여부를
            # 다시 판단하므로 전환 대기 실패만으로 전체 실행을 중단하지 않는다.
            pass

    async def _open_region_listbox(self, page: Page) -> tuple[Locator, Locator]:
        combo = self._region_combo(page)
        if await combo.count() != 1:
            raise RuntimeError("aria-controls=region-listbox인 지역 combobox를 하나만 찾지 못했습니다.")
        await self._dismiss_header_overlay(page)
        if await combo.get_attribute("aria-expanded") != "true":
            try:
                await combo.click(timeout=min(self.timeout_ms, 8_000))
            except PlaywrightTimeoutError:
                # 진단 파일에서 #header .backdrop의 pointer-events:auto와 넓은
                # bounding box가 확인되었다. 숨김 전환 중 가로채는 경우에만
                # 실제 combobox의 DOM click handler를 호출한다.
                await combo.evaluate("e => e.click()")
        await page.wait_for_function(
            """() => {
                const combo = document.querySelector(
                    '[role="combobox"][aria-controls="region-listbox"][aria-haspopup="listbox"]'
                );
                const list = document.querySelector('#region-listbox[role="listbox"]');
                return combo?.getAttribute('aria-expanded') === 'true' &&
                       list && getComputedStyle(list).visibility !== 'hidden';
            }""",
            timeout=min(self.timeout_ms, 8_000),
        )
        return combo, self._region_listbox(page)

    async def _read_regions(self, page: Page) -> list[str]:
        combo, listbox = await self._open_region_listbox(page)
        options = listbox.locator(':scope > [role="option"]')
        names = [name.strip() for name in await options.all_inner_texts() if name.strip()]
        if not names:
            raise RuntimeError("지역 option 목록을 찾지 못했습니다.")
        await options.nth(0).click()
        await page.wait_for_function(
            "e => e.getAttribute('aria-expanded') === 'false'", arg=await combo.element_handle(),
            timeout=min(self.timeout_ms, 8_000),
        )
        self.checkpoint.add_debug(debug_entry(
            "지역목록검증", requested_date=self.start_date.isoformat(),
            event="사이트 지역 option 목록 확인",
            details=f"option 수={len(names)}; 목록={', '.join(names)}",
            locator='#region-listbox > [role="option"]',
        ))
        try:
            validate_site_regions(names, self.requested_regions or REGION_DISPLAY_ORDER)
        except ValueError as exc:
            self.logger.error("%s 실제 목록: %s", exc, ", ".join(names))
            raise RuntimeError(str(exc)) from exc
        self.logger.info("사이트 지역 option %d개 확인: %s", len(names), ", ".join(names))
        return names

    def _select_requested_regions(self, available: list[str]) -> list[str]:
        requested = self.requested_regions or list(REGION_DISPLAY_ORDER)
        try:
            return validate_site_regions(available, requested)
        except ValueError as exc:
            self.logger.error("%s", exc)
            raise

    async def _select_region(self, page: Page, region: str) -> bool:
        combo = self._region_combo(page)
        if (await combo.inner_text()).strip() == region:
            return True
        combo, listbox = await self._open_region_listbox(page)
        option = listbox.locator(':scope > [role="option"]').filter(has_text=re.compile(f"^{re.escape(region)}$"))
        if await option.count() != 1:
            raise RuntimeError(f"지역 option을 하나만 찾지 못했습니다: {region}")
        await option.click()
        try:
            await page.wait_for_function(
                """name => {
                    const combo = document.querySelector(
                        '[role="combobox"][aria-controls="region-listbox"]'
                    );
                    const regionLabels = [...document.querySelectorAll('#curation-root section > div > span')];
                    return combo?.innerText.trim() === name &&
                           combo.getAttribute('aria-expanded') === 'false' &&
                           regionLabels.some(e => e.innerText.trim() === name);
                }""",
                arg=region, timeout=min(self.timeout_ms, 15_000),
            )
        except Exception:
            return False
        return (await combo.inner_text()).strip() == region

    async def _issue_cards(self, page: Page) -> list[Locator]:
        sections = page.locator("#curation-root section").filter(
            has=page.get_by_text("전체 이슈", exact=True)
        )
        if not await sections.count():
            return []
        # 진단 HTML: section > 지역/건수 헤더 div + 카드목록 div > 카드 div.
        # 카드 자체는 분류 div, h3 제목, p 요약, 출처유형 div를 직접 자식으로 갖는다.
        candidates = sections.last.locator(":scope > div > div")
        cards: list[Locator] = []
        for index in range(await candidates.count()):
            card = candidates.nth(index)
            try:
                valid = await card.evaluate(
                    """e => {
                        const children = [...e.children];
                        return children.some(x => x.tagName === 'H3') &&
                               children.some(x => x.tagName === 'P') &&
                               children.filter(x => x.tagName === 'DIV').length >= 2;
                    }"""
                )
                if valid:
                    cards.append(card)
            except Exception:
                continue
        return cards

    async def _audit_region(
        self, page: Page, context: BrowserContext, checker: BrowserLinkChecker,
        requested: date, displayed: date | None, region: str, region_order: int,
    ) -> bool:
        req = requested.isoformat()
        self.logger.info("[%s][%s] 지역 점검 시작", req, region)
        try:
            if not await self._select_region(page, region):
                raise RuntimeError("선택된 지역명이 일치하지 않습니다.")
            cards = await self._issue_cards(page)
            if self.max_issues is not None:
                cards = cards[: self.max_issues]
            if not cards:
                self.logger.info("[%s][%s] 데이터 없음", req, region)
                return True
            for issue_index in range(len(cards)):
                self._raise_if_cancelled()
                self.logger.info("[%s][%s] 이슈 %d/%d 점검", req, region, issue_index + 1, len(cards))
                try:
                    await self._audit_issue(page, context, checker, requested, displayed, region, region_order, issue_index, len(cards))
                except AuditCancelled:
                    raise
                except Exception as exc:
                    self.had_partial_failures = True
                    self._debug("이슈순회", requested, displayed, region=region, issue_order=issue_index + 1,
                                event="이슈 처리 실패", exception_type=type(exc).__name__, details=exc)
            return True
        except AuditCancelled:
            raise
        except Exception as exc:
            self._debug("지역선택", requested, displayed, region=region, event="지역 처리 실패",
                        exception_type=type(exc).__name__, details=exc)
            self.logger.error("[%s][%s] 지역 처리 실패: %s", req, region, sanitize(exc))
            return False
        finally:
            rows = [r for r in self.checkpoint.rows if r.requested_date == req and r.region == region]
            normal = sum(r.verdict == "정상" for r in rows)
            self.logger.info("[%s][%s] 완료: 정상 %d, 오류 %d", req, region, normal, len(rows) - normal)

    async def _audit_issue(
        self, page: Page, context: BrowserContext, checker: BrowserLinkChecker,
        requested: date, displayed: date | None, region: str, region_order: int,
        issue_index: int, issue_total: int,
    ) -> None:
        cards = await self._issue_cards(page)
        if issue_index >= len(cards):
            raise RuntimeError("지역 변경 후 이슈 카드 수가 달라졌습니다.")
        card = cards[issue_index]
        issue = await card.evaluate("""e => {
          const h=e.querySelector(':scope > h3');
          const before=h ? h.previousElementSibling : null;
          return {title:h?.innerText?.trim()||'', categories:before ? [...before.querySelectorAll(':scope > span')].map(x=>x.innerText.trim()) : []};
        }""")
        self.progress_reporter.emit(
            "issue_started", f"이슈 {issue_index + 1}/{issue_total} 확인 중",
            current_date=requested.isoformat(), current_region=region,
            current_issue=issue["title"], current_issue_order=issue_index + 1,
            current_issue_total=issue_total,
        )
        self.issue_keys.add((requested.isoformat(), region, issue_index + 1))
        self.checkpoint.mark_issue(requested.isoformat(), region, issue_index + 1)
        categories = ", ".join(x for x in issue["categories"] if x in ISSUE_CATEGORIES)
        # 고정 헤더의 투명 backdrop이 카드 위 포인터 이벤트를 가로채는 경우가
        # 실제 사이트에서 확인되었다. 카드 자체의 click handler를 직접 실행한다.
        await card.click(force=True)
        close = page.get_by_role("button", name="닫기", exact=True)
        try:
            await close.wait_for(state="visible", timeout=min(self.timeout_ms, 10_000))
        except Exception as exc:
            self._debug("상세모달", requested, displayed, region=region, issue_order=issue_index + 1,
                        issue_title=issue["title"], event="모달 열기 실패", exception_type=type(exc).__name__, details=exc)
            screenshot = await self._save_screenshot(page, requested, region, issue_index + 1, "모달오류")
            if screenshot:
                self._debug(
                    "상세모달", requested, displayed, region=region, issue_order=issue_index + 1,
                    issue_title=issue["title"], event="모달 실패 화면 저장", screenshot_path=screenshot,
                )
            return
        modal = await self._modal_from_close(close)
        source_heading = modal.locator("h3").filter(
            has_text=re.compile(r"^출처\s*\(\s*\d+\s*\)$")
        ).first
        source_text = (await source_heading.inner_text()).strip() if await source_heading.count() else ""
        source_match = re.fullmatch(r"출처\s*\(\s*(\d+)\s*\)", source_text)
        source_count = int(source_match.group(1)) if source_match else 0
        sources, actual_card_count = await self._source_cards(source_heading)
        self.known_links = max(self.known_links, self.processed_links + len(sources))
        self.progress_reporter.emit(
            "links_discovered", f"출처 링크 {len(sources)}개를 확인합니다.",
            known_links=self.known_links,
        )
        if source_count != actual_card_count:
            self._debug("출처수집", requested, displayed, region=region, issue_order=issue_index + 1,
                        issue_title=issue["title"], event="출처수 불일치",
                        details=f"화면 {source_count}, 직접 자식 카드 {actual_card_count}")
        if actual_card_count != len(sources):
            self._debug("출처수집", requested, displayed, region=region, issue_order=issue_index + 1,
                        issue_title=issue["title"], event="출처 카드 파싱 불일치",
                        details=f"직접 자식 카드 {actual_card_count}, 파싱 성공 {len(sources)}")
        try:
            for source_order, (source_card, source_info) in enumerate(sources, 1):
                self._raise_if_cancelled()
                link_context = {
                    "requested_date": requested.isoformat(),
                    "displayed_date": displayed.isoformat() if displayed else "",
                    "region": region,
                    "issue_order": issue_index + 1,
                    "issue_title": issue["title"],
                }
                result = await self._click_and_check(
                    context, page, source_card, checker,
                    expected_title=source_info.title, debug_context=link_context,
                    source_type=source_info.source_type,
                )
                row = AuditRow(
                    requested_date=requested.isoformat(), displayed_date=displayed.isoformat() if displayed else "",
                    region=region, issue_order=issue_index + 1, issue_title=issue["title"], issue_categories=categories,
                    source_count=source_count, source_type=source_info.source_type, publisher=source_info.publisher,
                    article_date=source_info.article_date, article_title=source_info.title,
                    original_url=result.original_url, final_url=result.final_url, http_status=result.http_status,
                    browser_result=result.browser_result, link_working_yn=result.link_working_yn, verdict=result.verdict,
                    response_seconds=result.response_seconds, error_message=result.error_message,
                    checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                    region_order=region_order, source_order=source_order,
                )
                added = self.checkpoint.add_row(row)
                if added:
                    self.processed_links += 1
                    if result.verdict == "정상":
                        self.normal_count += 1
                    else:
                        self.error_count += 1
                self.checkpoint.add_debug(debug_entry(
                    "링크URL", **link_context,
                    source_href_raw=result.source_href_raw,
                    source_href_property=result.source_href_property,
                    click_target_raw=result.click_target_raw,
                    normalization_input=result.normalization_input,
                    original_url=result.original_url,
                    normalization_method=result.normalization_method,
                    click_before_url=result.click_before_url,
                    click_after_url=result.click_after_url,
                    first_opened_url=result.first_opened_url,
                    new_tab_yn=result.new_tab_yn,
                    current_tab_moved_yn=result.current_tab_moved_yn,
                    inferred_url=result.inferred_url,
                    url_structure_anomaly_yn=result.url_structure_anomaly_yn,
                    url_structure_anomaly_details=result.url_structure_anomaly_details,
                    locator=result.click_target,
                    final_url=result.final_url,
                    http_status=result.http_status if result.http_status is not None else "",
                    screenshot_path=result.screenshot_path,
                    event=f"{source_info.publisher} 출처 URL 진단",
                    details=f"기사제목={source_info.title}",
                    access_reason_code=result.access_reason_code,
                    detected_phrase=result.detected_phrase,
                    detected_locator=result.detected_locator,
                    detected_dom_area=result.detected_dom_area,
                    detected_visible_yn=result.detected_visible_yn,
                    document_title=result.document_title,
                    visible_h1=result.visible_h1,
                    article_exists_yn=result.article_exists_yn,
                    primary_text_length=result.primary_text_length,
                    article_title_match_yn=result.article_title_match_yn,
                    article_rendered_yn=result.article_rendered_yn,
                    non_news_title_match_yn=result.non_news_title_match_yn,
                    non_news_content_rendered_yn=result.non_news_content_rendered_yn,
                    matched_title=result.matched_title,
                    content_container_locator=result.content_container_locator,
                    attachment_exists_yn=result.attachment_exists_yn,
                ))
                self.logger.info("[%s][%s][이슈 %d] 출처 %d/%d %s", requested, region, issue_index + 1,
                                 source_order, len(sources), result.verdict)
                self.progress_reporter.emit(
                    "link_completed",
                    f"출처 링크 {source_order}/{len(sources)} {result.verdict}",
                    current_date=requested.isoformat(), current_region=region,
                    current_issue=issue["title"], current_issue_order=issue_index + 1,
                    current_issue_total=issue_total, known_links=self.known_links,
                    processed_links=self.processed_links,
                    normal_count=self.normal_count, error_count=self.error_count,
                    verdict=result.verdict,
                )
                if result.verdict != "정상":
                    self._debug(
                        "링크판정", requested, displayed, region=region,
                        issue_order=issue_index + 1, issue_title=issue["title"],
                        original_url=result.original_url, final_url=result.final_url,
                        http_status=result.http_status or "", event=result.verdict,
                        details=result.error_message, screenshot_path=result.screenshot_path,
                        access_reason_code=result.access_reason_code,
                        detected_phrase=result.detected_phrase,
                        detected_locator=result.detected_locator,
                        detected_dom_area=result.detected_dom_area,
                        detected_visible_yn=result.detected_visible_yn,
                        document_title=result.document_title,
                        visible_h1=result.visible_h1,
                        article_exists_yn=result.article_exists_yn,
                        primary_text_length=result.primary_text_length,
                        article_title_match_yn=result.article_title_match_yn,
                        article_rendered_yn=result.article_rendered_yn,
                        non_news_title_match_yn=result.non_news_title_match_yn,
                        non_news_content_rendered_yn=result.non_news_content_rendered_yn,
                        matched_title=result.matched_title,
                        content_container_locator=result.content_container_locator,
                        attachment_exists_yn=result.attachment_exists_yn,
                    )
                await page.wait_for_timeout(self.link_delay_ms)
        finally:
            if await close.count() and await close.is_visible():
                await close.click()
                await modal.wait_for(state="hidden", timeout=min(self.timeout_ms, 10_000))
                try:
                    await page.wait_for_function(
                        "() => getComputedStyle(document.body).overflow !== 'hidden'",
                        timeout=min(self.timeout_ms, 5_000),
                    )
                except Exception:
                    pass
                await self._dismiss_header_overlay(page)

    async def _modal_from_close(self, close: Locator) -> Locator:
        # 진단 HTML에는 role=dialog/aria-modal/id가 없다. 닫기 버튼에서 시작해
        # h2 이슈 제목과 '출처 (n)' h3를 함께 포함하는 가장 가까운 조상을 쓴다.
        modal = close.locator(
            "xpath=ancestor::div[.//h2 and .//h3[starts-with(normalize-space(.), '출처 (')]][1]"
        )
        if not await modal.count():
            raise RuntimeError("닫기 버튼 기준 상세 팝업 콘텐츠 컨테이너를 찾지 못했습니다.")
        return modal

    async def _source_cards(self, source_heading: Locator) -> tuple[list[tuple[Locator, SourceInfo]], int]:
        if not await source_heading.count():
            return [], 0
        # 진단 HTML: h3 '출처 (n)'의 바로 다음 형제 div가 목록이며,
        # 그 직접 자식 div 각각이 출처 한 건이다. 하위 메타/제목 div는 세지 않는다.
        container = source_heading.locator("xpath=following-sibling::div[1]")
        candidates = container.locator(":scope > div")
        actual_card_count = await candidates.count()
        result = []
        for index in range(actual_card_count):
            candidate = candidates.nth(index)
            try:
                valid = await candidate.evaluate(r"""e => {
                  const kids=[...e.children];
                  const type=kids.find(x=>x.tagName==='SPAN')?.innerText?.trim();
                  const divs=kids.filter(x=>x.tagName==='DIV');
                  const metadata=(divs[0]?.innerText||'').trim();
                  const title=(divs[1]?.innerText||'').trim();
                  return Boolean(type) && divs.length === 2 && Boolean(title) &&
                         /\b\d{4}[-.]\d{2}[-.]\d{2}\b/.test(metadata);
                }""")
                if valid:
                    info = parse_source_text(await candidate.inner_text(), len(result) + 1)
                    if info.source_type and info.article_date and info.title:
                        result.append((candidate, info))
            except Exception:
                continue
        return result, actual_card_count

    async def _click_and_check(
        self, context: BrowserContext, source_page: Page, source_card: Locator, checker: BrowserLinkChecker,
        *, expected_title: str, debug_context: dict, source_type: str = "",
    ) -> LinkCheckResult:
        """BigKinds 출처 카드를 실제 클릭하고 그 클릭이 연 URL만 판정한다.

        프로토콜이 빠져 보이는 문자열에서 언론사 URL을 추정할 수는 있지만,
        추정 URL은 진단 필드에만 저장하며 원본URL·최종URL·Y/N에 사용하지 않는다.
        """
        popup_timeout = max(250, min(self.timeout_ms, 7_000))
        source_href_raw, source_href_property = await self._source_href_values(source_card)
        anchor = source_card.locator("a[href]").first
        has_anchor = bool(await anchor.count())
        click_target = anchor if has_anchor else source_card
        click_target_label = "출처 카드 내부 첫 a[href]" if has_anchor else "출처 카드 DIV"
        inline_target = await self._inline_click_target(source_card)
        last_raw_target = source_href_raw or inline_target
        last_original_url = source_href_property
        last_before_url = source_page.url
        last_after_url = ""
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            before_url = source_page.url
            last_before_url = before_url
            target = None
            capture_key = await self._install_window_open_capture(source_page)
            captured_raw = ""
            try:
                try:
                    async with context.expect_page(timeout=popup_timeout) as page_info:
                        await click_target.click(force=True)
                    target = await page_info.value
                except PlaywrightTimeoutError:
                    await source_page.wait_for_timeout(500)
                    captured_raw = await self._take_window_open_capture(source_page, capture_key)
                    raw_target = captured_raw or source_href_raw or inline_target
                    after_url = source_page.url
                    resolved_target = await self._resolve_url_like_browser(
                        source_page, raw_target, before_url,
                    )
                    original_url = resolved_target or source_href_property
                    last_raw_target = raw_target
                    last_original_url = original_url
                    last_after_url = after_url if after_url != before_url else ""

                    if after_url != before_url:
                        original_url = original_url or after_url
                        last_original_url = original_url
                        await source_page.go_back(
                            wait_until="domcontentloaded", timeout=self.timeout_ms,
                        )
                        return await self._inspect_url(
                            context, checker, original_url,
                            expected_title=expected_title, debug_context=debug_context,
                            started=started, source_href_raw=source_href_raw,
                            source_href_property=source_href_property,
                            raw_target=raw_target, method="실제 클릭(현재 탭)",
                            click_target=click_target_label, click_before_url=before_url,
                            click_after_url=after_url, first_opened_url=original_url,
                            new_tab_yn="N", current_tab_moved_yn="Y",
                            source_type=source_type,
                        )

                    if original_url:
                        self.checkpoint.add_debug(debug_entry(
                            "링크클릭", **debug_context,
                            source_href_raw=source_href_raw,
                            source_href_property=source_href_property,
                            click_target_raw=raw_target,
                            normalization_input=raw_target,
                            original_url=original_url,
                            normalization_method="브라우저 해석 URL 검사(클릭 후 페이지 미생성)",
                            click_before_url=before_url,
                            locator=click_target_label,
                            event="popup/현재 탭 미생성: 실제 클릭 대상을 검사 전용 page에서 확인",
                            retry_count=attempt,
                        ))
                        return await self._inspect_url(
                            context, checker, original_url,
                            expected_title=expected_title, debug_context=debug_context,
                            started=started, source_href_raw=source_href_raw,
                            source_href_property=source_href_property,
                            raw_target=raw_target,
                            method="브라우저 해석 URL 검사(클릭 후 페이지 미생성)",
                            click_target=click_target_label, click_before_url=before_url,
                            click_after_url="", first_opened_url=original_url,
                            new_tab_yn="N", current_tab_moved_yn="N",
                            source_type=source_type,
                        )

                    if attempt < self.retries:
                        self.checkpoint.add_debug(debug_entry(
                            "링크클릭", **debug_context,
                            source_href_raw=source_href_raw,
                            source_href_property=source_href_property,
                            click_target_raw=raw_target,
                            click_before_url=before_url,
                            locator=click_target_label,
                            event="popup/현재 탭 변화 없음: 클릭 재시도",
                            retry_count=attempt + 1,
                        ))
                        continue
                    break

                captured_raw = await self._take_window_open_capture(source_page, capture_key)
                raw_target = captured_raw or source_href_raw or inline_target
                click_after_url = target.url
                resolved_target = await self._resolve_url_like_browser(
                    source_page, raw_target, before_url,
                )
                first_url = self.first_url_by_page.get(target, "")
                original_url = (
                    resolved_target or first_url or source_href_property
                    or (target.url if target.url.startswith(("http://", "https://")) else "")
                )
                last_raw_target = raw_target
                last_original_url = original_url
                last_after_url = click_after_url
                result = await checker.inspect_open_page(
                    target, original_url or target.url, started, self.status_by_page,
                    expected_title=expected_title, status_by_url=self.status_by_url,
                    source_type=source_type,
                )
                result = await self._verify_automation_environment_block(
                    result, original_url or target.url, expected_title,
                    debug_context, started, source_type=source_type,
                )
                self._attach_url_diagnostics(
                    result, original_url=original_url or result.original_url,
                    source_href_raw=source_href_raw,
                    source_href_property=source_href_property,
                    raw_target=raw_target, method="실제 클릭(새 탭)",
                    click_target=click_target_label, click_before_url=before_url,
                    click_after_url=click_after_url,
                    first_opened_url=first_url or original_url,
                    new_tab_yn="Y", current_tab_moved_yn="N",
                )
                if result.verdict != "정상":
                    result.screenshot_path = await self._save_screenshot(
                        target, date.fromisoformat(debug_context["requested_date"]),
                        debug_context["region"], int(debug_context["issue_order"]), result.verdict,
                    )
                return result
            except Exception as exc:
                captured_raw = captured_raw or await self._take_window_open_capture(
                    source_page, capture_key,
                )
                raw_target = captured_raw or source_href_raw or inline_target
                original_url = await self._resolve_url_like_browser(
                    source_page, raw_target, before_url,
                ) or source_href_property
                last_raw_target = raw_target
                last_original_url = original_url
                if original_url and source_page.url == before_url:
                    return await self._inspect_url(
                        context, checker, original_url,
                        expected_title=expected_title, debug_context=debug_context,
                        started=started, source_href_raw=source_href_raw,
                        source_href_property=source_href_property,
                        raw_target=raw_target,
                        method="브라우저 해석 URL 검사(클릭 예외 후)",
                        click_target=click_target_label, click_before_url=before_url,
                        click_after_url="", first_opened_url=original_url,
                        new_tab_yn="N", current_tab_moved_yn="N",
                        source_type=source_type,
                    )
                if attempt < self.retries:
                    self.checkpoint.add_debug(debug_entry(
                        "링크클릭", **debug_context,
                        source_href_raw=source_href_raw,
                        source_href_property=source_href_property,
                        click_target_raw=raw_target,
                        click_before_url=before_url,
                        click_after_url=source_page.url if source_page.url != before_url else "",
                        locator=click_target_label, event="클릭 예외 재시도",
                        exception_type=type(exc).__name__, details=exc,
                        retry_count=attempt + 1,
                    ))
                    continue
                break
            finally:
                await self._take_window_open_capture(source_page, capture_key)
                if target and not target.is_closed():
                    await target.close()

        result = checker.click_error((self.retries + 1) * popup_timeout / 1000)
        self._attach_url_diagnostics(
            result, original_url=last_original_url,
            source_href_raw=source_href_raw,
            source_href_property=source_href_property,
            raw_target=last_raw_target, method="실제 클릭 실패",
            click_target=click_target_label, click_before_url=last_before_url,
            click_after_url=last_after_url, first_opened_url="",
            new_tab_yn="N", current_tab_moved_yn="N",
        )
        result.screenshot_path = await self._save_screenshot(
            source_page, date.fromisoformat(debug_context["requested_date"]),
            debug_context["region"], int(debug_context["issue_order"]), result.verdict,
        )
        return result

    async def _inspect_url(
        self, context: BrowserContext, checker: BrowserLinkChecker, original_url: str,
        *, expected_title: str, debug_context: dict, source_href_raw: str,
        source_href_property: str, raw_target: str, method: str, click_target: str,
        click_before_url: str, click_after_url: str, first_opened_url: str,
        new_tab_yn: str, current_tab_moved_yn: str, source_type: str = "",
        started: float | None = None,
    ) -> LinkCheckResult:
        target = await context.new_page()
        started = started if started is not None else time.perf_counter()
        try:
            result = await checker.open_url(
                target, original_url, started, self.status_by_page,
                expected_title=expected_title, status_by_url=self.status_by_url,
                source_type=source_type,
            )
            result = await self._verify_automation_environment_block(
                result, original_url, expected_title, debug_context, started,
                source_type=source_type,
            )
            self._attach_url_diagnostics(
                result, original_url=original_url,
                source_href_raw=source_href_raw,
                source_href_property=source_href_property,
                raw_target=raw_target, method=method, click_target=click_target,
                click_before_url=click_before_url, click_after_url=click_after_url,
                first_opened_url=first_opened_url or original_url,
                new_tab_yn=new_tab_yn, current_tab_moved_yn=current_tab_moved_yn,
            )
            if result.verdict != "정상":
                result.screenshot_path = await self._save_screenshot(
                    target, date.fromisoformat(debug_context["requested_date"]),
                    debug_context["region"], int(debug_context["issue_order"]), result.verdict,
                )
            return result
        finally:
            if not target.is_closed():
                await target.close()

    @staticmethod
    def _attach_url_diagnostics(
        result: LinkCheckResult, *, original_url: str, source_href_raw: str,
        source_href_property: str, raw_target: str, method: str, click_target: str,
        click_before_url: str, click_after_url: str, first_opened_url: str,
        new_tab_yn: str, current_tab_moved_yn: str,
    ) -> None:
        result.source_href_raw = source_href_raw or ""
        result.source_href_property = source_href_property or ""
        result.click_target_raw = raw_target or ""
        result.normalization_input = raw_target or ""
        result.normalization_method = method
        result.click_target = click_target
        result.click_before_url = click_before_url
        result.click_after_url = click_after_url
        result.first_opened_url = first_opened_url
        result.new_tab_yn = new_tab_yn
        result.current_tab_moved_yn = current_tab_moved_yn
        if original_url:
            result.original_url = original_url
        structure = analyze_url_structure(result.original_url)
        result.url_structure_anomaly_yn = "Y" if structure.anomalous else "N"
        result.url_structure_anomaly_details = structure.details
        result.inferred_url = structure.inferred_url or infer_external_url(raw_target)

    @staticmethod
    async def _source_href_raw(source_card: Locator) -> str:
        raw, _ = await RegionalCollector._source_href_values(source_card)
        return raw

    @staticmethod
    async def _source_href_values(source_card: Locator) -> tuple[str, str]:
        anchor = source_card.locator("a[href]").first
        if not await anchor.count():
            return "", ""
        raw = (await anchor.get_attribute("href") or "").strip()
        try:
            href_property = (await anchor.evaluate("element => element.href") or "").strip()
        except Exception:
            href_property = ""
        return raw, href_property

    @staticmethod
    async def _resolve_url_like_browser(source_page: Page, value: str, base_url: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            return ""
        try:
            resolved = await source_page.evaluate(
                "([target, base]) => new URL(target, base).href", [candidate, base_url],
            )
        except Exception:
            resolved = resolve_url_reference(candidate, base_url)
        return resolved if str(resolved).startswith(("http://", "https://")) else ""

    @staticmethod
    async def _inline_click_target(source_card: Locator) -> str:
        onclick = await source_card.get_attribute("onclick") or ""
        patterns = [
            r"window\.open\(\s*(['\"])(.*?)\1",
            r"(?:window\.)?location(?:\.href)?\s*=\s*(['\"])(.*?)\1",
        ]
        for pattern in patterns:
            match = re.search(pattern, onclick)
            if match:
                return match.group(2)
        return ""

    @staticmethod
    async def _install_window_open_capture(source_page: Page) -> str:
        capture_key = "__bigkindsAuditOpenCapture"
        try:
            await source_page.evaluate(
                r"""key => {
                    const previous = window[key];
                    if (previous?.original) window.open = previous.original;
                    delete window[key];
                    const state = {original: window.open, calls: []};
                    Object.defineProperty(window, key, {value: state, configurable: true});
                    window.open = function(...args) {
                        const raw = args[0] == null ? '' : String(args[0]);
                        state.calls.push({raw});
                        // 관찰만 하고 모든 인자를 원형 그대로 전달한다. 이 wrapper가
                        // 실제 사용자의 클릭 URL을 수정해서는 안 된다.
                        return state.original.apply(window, args);
                    };
                }""",
                capture_key,
            )
            return capture_key
        except Exception:
            return ""

    @staticmethod
    async def _take_window_open_capture(source_page: Page, capture_key: str) -> str:
        if not capture_key:
            return ""
        try:
            calls = await source_page.evaluate("key => window[key]?.calls || []", capture_key)
        except Exception:
            calls = []
        finally:
            try:
                await source_page.evaluate(
                    """key => {
                        const state = window[key];
                        if (state?.original) window.open = state.original;
                        delete window[key];
                    }""",
                    capture_key,
                )
            except Exception:
                pass
        return next((str(call.get("raw") or "") for call in calls if call.get("raw")), "")

    async def _save_screenshot(self, page: Page, requested: date, region: str, issue_order: int, error_type: str) -> str:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        safe_region = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", region)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"{requested}_{safe_region}_{issue_order}_{error_type}_{stamp}.png"
        try:
            await page.screenshot(path=path, full_page=False)
            return str(path.resolve())
        except Exception:
            return ""

    def _debug(self, stage: str, requested: date, displayed: date | None, **kwargs) -> None:
        entry = debug_entry(stage, requested_date=requested.isoformat(),
                            displayed_date=displayed.isoformat() if displayed else "", **kwargs)
        self.checkpoint.add_debug(entry)

    def _raise_if_cancelled(self) -> None:
        if self.cancellation_token.is_cancel_requested():
            self.progress_reporter.emit("cancel_acknowledged", "중단 요청을 확인했습니다. 현재 결과를 정리합니다.")
            raise AuditCancelled("사용자가 점검 중단을 요청했습니다.")
