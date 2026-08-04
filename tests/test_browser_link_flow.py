import asyncio
import logging
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import AsyncMock, Mock

from playwright.async_api import async_playwright

from src.link_checker import BrowserLinkChecker, LinkCheckResult, article_rendered_evidence
from src.regional_collector import RegionalCollector
from src.verdict import classify_verdict


ARTICLE_TITLE = "지역 현안 정상 기사 제목"
ARTICLE_BODY = "실제 Chromium에 표시되는 정상적인 기사 본문입니다. " * 30
RESEARCH_TITLE = "[제73호] 지역통합과 정부 메가프로젝트 연계 전략"
NOTICE_TITLE = "2026년 지역정책 연구지원 사업 공고"
NOTICE_BODY = "지역정책 연구지원 사업의 신청 대상과 제출 방법을 안내하는 공지사항 본문입니다. " * 12


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/article")
            self.end_headers()
            return
        if self.path == "/slow-rendered":
            payload = (
                f"<!doctype html><html><head><title>{ARTICLE_TITLE}</title></head>"
                f"<body><h1>{ARTICLE_TITLE}</h1><p>{ARTICLE_BODY}</p>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload) + 20))
            self.end_headers()
            try:
                self.wfile.write(payload)
                self.wfile.flush()
                time.sleep(1)
                self.wfile.write(b"</body></html>       ")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        routes = {
            "/article": (200, f"<title>{ARTICLE_TITLE}</title><h1>{ARTICLE_TITLE}</h1><p>{ARTICLE_BODY}</p>"),
            "/article?ncd=1&amp;ref=DA": (
                200, f"<title>{ARTICLE_TITLE}</title><h1>{ARTICLE_TITLE}</h1><p>{ARTICLE_BODY}</p>",
            ),
            "/article?ncd=1&ref=DA": (
                200, f"<title>{ARTICLE_TITLE}</title><h1>{ARTICLE_TITLE}</h1><p>{ARTICLE_BODY}</p>",
            ),
            "/blank": (200, ""),
            "/delayed-dom": (
                200,
                "<script>setTimeout(() => {"
                f"document.title={ARTICLE_TITLE!r};"
                f"document.body.innerHTML='<main><article><h1>{ARTICLE_TITLE}</h1>"
                f"<p>{ARTICLE_BODY}</p></article></main>';"
                "}, 1200);</script>",
            ),
            "/article-comment-permission": (
                200,
                f"<title>{ARTICLE_TITLE}</title><h1>{ARTICLE_TITLE}</h1>"
                f"<main><article itemprop='articleBody'><p>{ARTICLE_BODY}</p></article>"
                "<article id='reply' class='article-reply'><header class='comment-header'>"
                "<button><span class='text'>댓글입력 권한이 없습니다.</span></button>"
                "</header></article></main>",
            ),
            "/article-ad-403": (
                200,
                f"<title>{ARTICLE_TITLE}</title><h1>{ARTICLE_TITLE}</h1>"
                f"<main><article itemprop='articleBody'><p>{ARTICLE_BODY}</p></article></main>"
                "<img src='/ad-403' alt='광고'>",
            ),
            "/ad-403": (403, "forbidden advertisement"),
            "/404": (404, "<title>404 Not Found</title><h1>404 Not Found</h1>"),
            "/soft-404": (200, "<title>404</title><h1>404</h1><p>파일 또는 디렉터리를 찾을 수 없습니다.</p>"),
            "/403": (403, "<title>Access Denied</title><h1>Access Denied</h1>"),
            "/login": (200, "<title>로그인</title><h1>로그인</h1><p>기사를 보려면 로그인이 필요합니다.</p>"),
            "/captcha": (200, "<title>CAPTCHA</title><main><h1>CAPTCHA</h1><p>Robot check CAPTCHA</p></main>"),
            "/research-attachment": (
                200,
                f"<title>{RESEARCH_TITLE} | 연구원</title><main><section class='report-detail'>"
                f"<h3 class='report-title'>{RESEARCH_TITLE}</h3>"
                "<table><tbody><tr><td>연구책임자: 홍길동</td></tr><tr><td>첨부파일: "
                "<a href=\"javascript:file_download('/download?id=1', 'report.pdf')\" "
                "title='report.pdf 다운로드'><span>report.pdf</span></a></td></tr></tbody></table>"
                "</section></main>",
            ),
            "/notice-detail": (
                200,
                f"<title>{NOTICE_TITLE}</title><main><div class='board-detail'>"
                f"<div id='board-title'>{NOTICE_TITLE}</div>"
                f"<table><tbody><tr><td class='view-content'>{NOTICE_BODY}</td></tr></tbody></table>"
                "</div></main>",
            ),
            "/notice-comment-permission": (
                200,
                f"<title>{NOTICE_TITLE}</title><main><div class='board-detail'>"
                f"<div class='view-title'>{NOTICE_TITLE}</div>"
                f"<div class='view-content'>{NOTICE_BODY}</div>"
                "<section class='comment-reply'><span>댓글입력 권한이 없습니다.</span></section>"
                "</div></main>",
            ),
            "/source-popup": (200, "<div id='card' onclick=\"window.open('/article','_blank')\">popup</div>"),
            "/source-entity-popup": (
                200,
                "<div id='card'>entity popup</div>"
                "<script>document.getElementById('card').addEventListener('click', () => "
                "window.open('/article?ncd=1&amp;ref=DA', '_blank'));</script>",
            ),
            "/source-research": (
                200,
                "<div id='card' onclick=\"window.open('/research-attachment','_blank')\">research</div>",
            ),
            "/source-current": (200, "<div id='card' onclick=\"location.href='/article'\">current</div>"),
            "/source-href": (200, "<div id='card'><a href='/article' target='_blank'>href</a></div>"),
            "/source-absolute-href": (
                200,
                f"<div id='card'><a href='http://{self.headers['Host']}/article' target='_blank'>absolute</a></div>",
            ),
            "/source-redirect": (200, "<div id='card'><a href='/redirect' target='_blank'>redirect</a></div>"),
            "/source-domain-popup": (
                200,
                "<div id='card' onclick=\"window.open('www.dynews.co.kr/news/articleView.html?idxno=857155','_blank')\">domain popup</div>",
            ),
            "/source-domain-href": (
                200,
                "<div id='card'><a href='www.dynews.co.kr/news/articleView.html?idxno=857155' target='_blank'>domain href</a></div>",
            ),
            "/www.dynews.co.kr/news/articleView.html?idxno=857155": (
                404,
                "<title>404</title><h1>404</h1><p>파일 또는 디렉터리를 찾을 수 없습니다.</p>",
            ),
            "/source-blocked-popup": (
                200,
                "<script>window.open=function(){return null}</script>"
                "<div id='card' onclick=\"window.open('/article','_blank')\">blocked popup</div>",
            ),
            "/source-none": (200, "<div id='card' onclick='void 0'>no url</div>"),
        }
        status, body = routes.get(self.path, (404, "not found"))
        payload = ("<!doctype html><html><body>" + body + "</body></html>").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class _Checkpoint:
    def __init__(self):
        self.debug_entries = []
        self.rows = []

    def add_debug(self, entry):
        self.debug_entries.append(entry)

    def mark_issue(self, *_args):
        return None


class _Reporter:
    def __init__(self):
        self.events = []

    def emit(self, event, message="", **data):
        self.events.append((event, message, data))


def test_headless_context_uses_desktop_chromium_user_agent():
    user_agent = RegionalCollector._desktop_chromium_user_agent("149.0.7827.55")
    assert "Chrome/149.0.7827.55" in user_agent
    assert "HeadlessChrome" not in user_agent


def test_browser_url_resolution_fallback_unescapes_entity_once():
    async def scenario():
        source_page = AsyncMock()
        source_page.evaluate.side_effect = RuntimeError("page unavailable")
        result = await RegionalCollector._resolve_url_like_browser(
            source_page,
            "https://example.com/article?x=1&amp;amp;ref=DA",
            "https://www.bigkinds.or.kr/regional/curation.do",
        )
        assert result == "https://example.com/article?x=1&amp;ref=DA"

    asyncio.run(scenario())


def test_modal_open_failure_is_not_reported_as_issue_completed():
    async def scenario():
        checkpoint = _Checkpoint()
        reporter = _Reporter()
        collector = RegionalCollector(
            start_date=date(2026, 7, 21), end_date=date(2026, 7, 21),
            regions=["서울특별시"], headed=False, max_issues=None,
            timeout_ms=1_000, retries=0, link_delay_ms=0,
            checkpoint=checkpoint, logger=logging.getLogger("test"),
            progress_reporter=reporter,
        )
        card = AsyncMock()
        card.evaluate.return_value = {"title": "모달 실패 이슈", "categories": []}
        close = AsyncMock()
        close.wait_for.side_effect = RuntimeError("modal did not open")
        page = Mock()
        page.get_by_role.return_value = close
        collector._issue_cards = AsyncMock(return_value=[card])
        collector._save_screenshot = AsyncMock(return_value="")

        issue_completed = await collector._audit_issue(
            page, AsyncMock(), AsyncMock(), date(2026, 7, 21),
            date(2026, 7, 21), "서울특별시", 0, 0, 1,
        )
        assert issue_completed is False

        collector._select_region = AsyncMock(return_value=True)
        collector._issue_cards = AsyncMock(return_value=[card])
        collector._audit_issue = AsyncMock(return_value=False)
        await collector._audit_region(
            page, AsyncMock(), AsyncMock(), date(2026, 7, 21),
            date(2026, 7, 21), "서울특별시", 0,
        )
        assert collector.had_partial_failures is True
        assert all(event != "issue_completed" for event, _, _ in reporter.events)

    asyncio.run(scenario())


def test_article_rendered_evidence_matches_changed_but_equivalent_title():
    expected = "인천 추석전 인천e음 캐시백 부활 캐시백 10% 및 한도 30만원 유력"
    actual = "추석전 인천e음 캐시백 부활…10%·한도 30만원 부활 시동"
    assert article_rendered_evidence(expected, actual, ARTICLE_BODY, len(ARTICLE_BODY))
    assert not article_rendered_evidence(expected, "로그인", "로그인이 필요합니다.", 10)


def test_structured_article_body_can_confirm_article_after_headline_edit():
    assert article_rendered_evidence(
        "수집 당시 기사 제목", "편집 후 전혀 달라진 기사 제목",
        ARTICLE_BODY, len(ARTICLE_BODY), article_text_length=600,
    )


def test_source_cards_use_structure_instead_of_source_type_allowlist():
    async def scenario():
        checkpoint = _Checkpoint()
        collector = RegionalCollector(
            start_date=date(2026, 7, 21), end_date=date(2026, 7, 21), regions=None,
            headed=False, max_issues=None, timeout_ms=1_000, retries=0,
            link_delay_ms=0, checkpoint=checkpoint, logger=logging.getLogger("test"),
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content("""
                <h3 id="source-heading">출처 (7)</h3>
                <div id="source-list">
                  <div><span>뉴스</span><div>언론사 | 2026-07-21</div><div>뉴스 제목</div></div>
                  <div><span>공지사항</span><div>기관 | 2026.07.21</div><div>공지 제목</div></div>
                  <div><span>연구보고서</span><div>연구원 | 2026-07-20</div><div>연구보고서 제목</div></div>
                  <div><span>새 출처 유형</span><div>새 기관 | 2026-07-19</div><div>새 유형 제목</div></div>
                  <div><span></span><div>기관 | 2026-07-21</div><div>유형 없는 제목</div></div>
                  <div><span>새 출처</span><div>기관</div><div>날짜 없는 제목</div></div>
                  <div><span>새 출처</span><div>기관 | 2026-07-21</div><div> </div></div>
                </div>
            """)
            sources, actual_card_count = await collector._source_cards(page.locator("#source-heading"))
            assert actual_card_count == 7
            assert [info.source_type for _, info in sources] == [
                "뉴스", "공지사항", "연구보고서", "새 출처 유형",
            ]
            assert [info.title for _, info in sources] == [
                "뉴스 제목", "공지 제목", "연구보고서 제목", "새 유형 제목",
            ]
            await browser.close()

    asyncio.run(scenario())


def test_non_news_detail_fallback_preserves_news_and_http_errors():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    async def scenario():
        checker = BrowserLinkChecker(timeout_ms=1_000, retries=0)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(locale="ko-KR")

            async def inspect(path, expected_title, source_type):
                page = await context.new_page()
                result = await checker.open_url(
                    page, base + path, time.perf_counter(), {},
                    expected_title=expected_title, source_type=source_type,
                )
                await page.close()
                return result

            research = await inspect("/research-attachment", RESEARCH_TITLE, "연구보고서")
            notice = await inspect("/notice-detail", NOTICE_TITLE, "공지사항")
            notice_with_comment = await inspect(
                "/notice-comment-permission", NOTICE_TITLE, "공지사항",
            )
            news = await inspect("/article", ARTICLE_TITLE, "뉴스")
            not_found = await inspect("/404", RESEARCH_TITLE, "연구보고서")
            forbidden = await inspect("/403", RESEARCH_TITLE, "연구보고서")
            full_login = await inspect("/login", NOTICE_TITLE, "공지사항")

            assert (research.verdict, research.link_working_yn) == ("정상", "Y")
            assert research.access_reason_code == "NON_NEWS_DETAIL_RENDERED"
            assert research.article_rendered_yn == "N"
            assert research.non_news_title_match_yn == "Y"
            assert research.non_news_content_rendered_yn == "Y"
            assert research.attachment_exists_yn == "Y"
            assert research.matched_title == RESEARCH_TITLE
            assert research.content_container_locator

            assert (notice.verdict, notice.link_working_yn) == ("정상", "Y")
            assert notice.access_reason_code == "NON_NEWS_DETAIL_RENDERED"
            assert notice.attachment_exists_yn == "N"
            assert notice.non_news_content_rendered_yn == "Y"

            assert (notice_with_comment.verdict, notice_with_comment.link_working_yn) == ("정상", "Y")
            assert notice_with_comment.access_reason_code == "NON_NEWS_DETAIL_RENDERED"
            assert notice_with_comment.detected_phrase == "댓글입력 권한이 없습니다."
            assert "comment" in notice_with_comment.detected_dom_area

            assert (news.verdict, news.link_working_yn, news.article_rendered_yn) == ("정상", "Y", "Y")
            assert news.non_news_content_rendered_yn == "N"
            assert not_found.verdict == "링크오류" and not_found.link_working_yn == "N"
            assert forbidden.verdict == "접근제한" and forbidden.link_working_yn == "N"
            assert forbidden.access_reason_code == "ACCESS_HTTP_STATUS"
            assert full_login.verdict == "접근제한" and full_login.link_working_yn == "N"

            await context.close()
            await browser.close()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_browser_link_flows_and_verdicts():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    async def scenario():
        checkpoint = _Checkpoint()
        collector = RegionalCollector(
            start_date=date(2026, 7, 16), end_date=date(2026, 7, 16), regions=None,
            headed=False, max_issues=None, timeout_ms=1_000, retries=0,
            link_delay_ms=0, checkpoint=checkpoint, logger=logging.getLogger("test"),
        )
        collector._save_screenshot = AsyncMock(return_value="")
        checker = BrowserLinkChecker(timeout_ms=1_000, retries=0)
        debug_context = {
            "requested_date": "2026-07-16", "displayed_date": "2026-07-16",
            "region": "서울특별시", "issue_order": 1, "issue_title": "테스트 이슈",
        }
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(locale="ko-KR")
            context.set_default_timeout(1_000)
            collector._track_navigation(context)

            async def activate(path, expected_title=ARTICLE_TITLE, source_type=""):
                source = await context.new_page()
                await source.goto(base + path, wait_until="domcontentloaded")
                result = await collector._click_and_check(
                    context, source, source.locator("#card"), checker,
                    expected_title=expected_title, debug_context=debug_context,
                    source_type=source_type,
                )
                await source.close()
                return result

            popup = await activate("/source-popup")
            entity_popup = await activate("/source-entity-popup")
            research_popup = await activate("/source-research", RESEARCH_TITLE, "연구보고서")
            current = await activate("/source-current")
            href = await activate("/source-href")
            absolute = await activate("/source-absolute-href")
            redirect = await activate("/source-redirect")
            domain_popup = await activate("/source-domain-popup")
            domain_href = await activate("/source-domain-href")
            fallback = await activate("/source-blocked-popup")
            missing = await activate("/source-none")
            normal_results = [popup, entity_popup, current, href, absolute, redirect, fallback]
            assert [item.verdict for item in normal_results] == ["정상"] * len(normal_results)
            assert all(item.link_working_yn == "Y" for item in normal_results)
            assert all(item.http_status == 200 for item in normal_results)
            assert (research_popup.verdict, research_popup.link_working_yn) == ("정상", "Y")
            assert research_popup.access_reason_code == "NON_NEWS_DETAIL_RENDERED"
            assert fallback.original_url == base + "/article"
            assert popup.source_href_raw == ""
            assert popup.source_href_property == ""
            assert popup.click_target_raw == "/article"
            assert entity_popup.click_target_raw == "/article?ncd=1&amp;ref=DA"
            assert entity_popup.first_opened_url == base + "/article?ncd=1&amp;ref=DA"
            assert entity_popup.original_url == base + "/article?ncd=1&ref=DA"
            assert entity_popup.inspection_url == entity_popup.original_url
            assert entity_popup.final_url == entity_popup.original_url
            assert entity_popup.url_html_entity_unescaped_yn == "Y"
            assert href.source_href_raw == "/article"
            assert href.source_href_property == base + "/article"
            assert href.original_url == base + "/article"
            assert absolute.source_href_raw == base + "/article"
            assert absolute.source_href_property == base + "/article"
            assert absolute.original_url == base + "/article"
            assert redirect.original_url == base + "/redirect"
            assert redirect.final_url == base + "/article"
            assert domain_popup.original_url == base + "/www.dynews.co.kr/news/articleView.html?idxno=857155"
            assert domain_popup.click_target_raw == "www.dynews.co.kr/news/articleView.html?idxno=857155"
            assert domain_popup.inferred_url == "https://www.dynews.co.kr/news/articleView.html?idxno=857155"
            assert domain_popup.verdict == "링크오류" and domain_popup.link_working_yn == "N"
            assert domain_href.source_href_raw == "www.dynews.co.kr/news/articleView.html?idxno=857155"
            assert domain_href.source_href_property == base + "/www.dynews.co.kr/news/articleView.html?idxno=857155"
            assert domain_href.original_url == domain_href.source_href_property
            assert domain_href.verdict == "링크오류" and domain_href.link_working_yn == "N"
            assert missing.verdict == "클릭오류" and missing.link_working_yn == "N"

            async def inspect(path, expected=ARTICLE_TITLE):
                page = await context.new_page()
                response = await page.goto(base + path, wait_until="domcontentloaded")
                result = await checker.inspect_open_page(
                    page, page.url, time.perf_counter(), {page: response.status}, expected_title=expected,
                )
                await page.close()
                return result

            not_found = await inspect("/404")
            soft_not_found = await inspect("/soft-404")
            forbidden = await inspect("/403")
            login = await inspect("/login")
            captcha = await inspect("/captcha")
            article_with_auxiliary_permission = await inspect("/article-comment-permission")
            assert not_found.verdict == "링크오류"
            assert soft_not_found.verdict == "링크오류"
            assert soft_not_found.link_working_yn == "N"
            assert soft_not_found.browser_result == "표시 실패"
            assert forbidden.verdict == "접근제한"
            assert login.verdict == "접근제한"
            assert captcha.verdict == "접근제한"
            assert article_with_auxiliary_permission.verdict == "정상"
            assert article_with_auxiliary_permission.link_working_yn == "Y"
            assert article_with_auxiliary_permission.access_reason_code == "ARTICLE_RENDERED_AUXILIARY_ACCESS_TEXT_IGNORED"
            assert article_with_auxiliary_permission.detected_phrase == "댓글입력 권한이 없습니다."
            assert article_with_auxiliary_permission.detected_visible_yn == "Y"
            assert "reply" in article_with_auxiliary_permission.detected_dom_area
            assert article_with_auxiliary_permission.article_exists_yn == "Y"
            assert article_with_auxiliary_permission.article_title_match_yn == "Y"
            assert article_with_auxiliary_permission.render_recheck_yn == "N"
            assert forbidden.access_reason_code == "ACCESS_HTTP_STATUS"
            assert captcha.access_reason_code == "ACCESS_STRONG_TEXT_PRIMARY"

            delayed_checker = BrowserLinkChecker(
                timeout_ms=1_000, retries=0, render_recheck_timeout_ms=800,
            )

            async def inspect_delayed(path):
                page = await context.new_page()
                result = await delayed_checker.open_url(
                    page, base + path, time.perf_counter(), {}, expected_title=ARTICLE_TITLE,
                )
                await page.close()
                return result

            delayed = await inspect_delayed("/delayed-dom")
            blank = await inspect_delayed("/blank")
            assert (delayed.verdict, delayed.link_working_yn) == ("정상", "Y")
            assert delayed.render_recheck_yn == "Y"
            assert delayed.initial_body_text_length == 0
            assert delayed.rechecked_body_text_length >= 300
            assert delayed.initial_document_title == ""
            assert delayed.rechecked_document_title == ARTICLE_TITLE
            assert delayed.initial_verdict == "빈화면"
            assert delayed.rechecked_verdict == "정상"
            assert 0 < delayed.render_recheck_wait_seconds <= 1.0
            assert (blank.verdict, blank.link_working_yn) == ("빈화면", "N")
            assert blank.render_recheck_yn == "Y"
            assert blank.initial_body_text_length == 0
            assert blank.rechecked_body_text_length == 0
            assert blank.initial_verdict == blank.rechecked_verdict == "빈화면"
            assert 0.7 <= blank.render_recheck_wait_seconds <= 1.0

            # 하위 광고 응답의 403은 context response 이벤트에 보이더라도
            # 메인 document 상태와 기사 판정을 덮어쓰면 안 된다.
            ad_page = await context.new_page()
            failed_responses = []

            def record_failed_response(failed_response):
                if failed_response.status >= 400:
                    failed_responses.append({
                        "status": failed_response.status,
                        "url": failed_response.url,
                        "resource_type": failed_response.request.resource_type,
                        "navigation": failed_response.request.is_navigation_request(),
                        "main_frame": failed_response.frame == ad_page.main_frame,
                    })

            ad_page.on("response", record_failed_response)
            ad_main_response = await ad_page.goto(base + "/article-ad-403", wait_until="domcontentloaded")
            await ad_page.wait_for_timeout(100)
            assert ad_main_response.status == 200
            assert collector.status_by_page[ad_page] == 200
            assert failed_responses == [{
                "status": 403,
                "url": base + "/ad-403",
                "resource_type": "image",
                "navigation": False,
                "main_frame": True,
            }]
            ad_result = await checker.inspect_open_page(
                ad_page, ad_page.url, time.perf_counter(), collector.status_by_page,
                expected_title=ARTICLE_TITLE, status_by_url=collector.status_by_url,
            )
            assert (ad_result.http_status, ad_result.verdict, ad_result.link_working_yn) == (200, "정상", "Y")
            await ad_page.close()

            slow_page = await context.new_page()
            fast_checker = BrowserLinkChecker(timeout_ms=300, retries=1)
            slow_result = await fast_checker.open_url(
                slow_page, base + "/slow-rendered", time.perf_counter(), {},
                expected_title=ARTICLE_TITLE,
            )
            assert slow_result.verdict == "정상"
            await slow_page.close()

            no_response = await context.new_page()
            await no_response.goto(base + "/article", wait_until="domcontentloaded")
            no_response_result = await checker.inspect_open_page(
                no_response, no_response.url, time.perf_counter(), {},
                expected_title=ARTICLE_TITLE,
            )
            assert no_response_result.verdict == "정상"
            await no_response.close()
            await context.close()
            await browser.close()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_headless_main_403_can_be_reclassified_after_article_dom_verification():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/article"

    async def scenario():
        checkpoint = _Checkpoint()
        collector = RegionalCollector(
            start_date=date(2026, 7, 3), end_date=date(2026, 7, 3), regions=None,
            headed=False, max_issues=None, timeout_ms=1_000, retries=0,
            link_delay_ms=0, checkpoint=checkpoint, logger=logging.getLogger("test"),
        )
        primary = LinkCheckResult(
            original_url=url, final_url=url, http_status=403,
            browser_result="접근 제한", link_working_yn="N", verdict="접근제한",
            error_message="HTTP 403 - 접근 또는 인증이 제한됨",
            access_reason_code="ACCESS_HTTP_STATUS",
            document_title="Attention Required! | Cloudflare",
            visible_h1="Sorry, you have been blocked",
            article_rendered_yn="N",
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(locale="ko-KR")
            collector._playwright = playwright
            collector._verification_context = AsyncMock(return_value=context)
            verified = await collector._verify_automation_environment_block(
                primary, url, ARTICLE_TITLE,
                {
                    "requested_date": "2026-07-03", "displayed_date": "2026-07-03",
                    "region": "전북특별자치도", "issue_order": 1, "issue_title": "테스트 이슈",
                },
                time.perf_counter(),
            )
            assert (verified.http_status, verified.verdict, verified.link_working_yn) == (200, "정상", "Y")
            assert verified.article_rendered_yn == "Y"
            assert verified.access_reason_code == "ARTICLE_RENDERED_AFTER_AUTOMATION_BLOCK"
            assert checkpoint.debug_entries[-1].stage == "브라우저환경비교"
            assert checkpoint.debug_entries[-1].event == "자동화 환경 차단"
            assert "기본 main document HTTP=403" in checkpoint.debug_entries[-1].details
            await context.close()
            await browser.close()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_successful_inferred_url_cannot_override_actual_bigkinds_failure():
    actual_url = "https://www.bigkinds.or.kr/regional/www.dynews.co.kr/news/articleView.html?idxno=857155"
    actual = LinkCheckResult(
        original_url=actual_url, final_url=actual_url, http_status=404,
        browser_result="표시 실패", link_working_yn="N", verdict="링크오류",
        error_message="404 페이지 표시",
    )
    RegionalCollector._attach_url_diagnostics(
        actual, original_url=actual_url, source_href_raw="", source_href_property="",
        raw_target="www.dynews.co.kr/news/articleView.html?idxno=857155",
        method="실제 클릭(새 탭)", click_target="출처 카드 DIV",
        click_before_url="https://www.bigkinds.or.kr/regional/curation.do",
        click_after_url=actual_url, first_opened_url=actual_url,
        new_tab_yn="Y", current_tab_moved_yn="N",
    )
    inferred_verdict, _, _ = classify_verdict(
        http_status=200,
        final_url="https://www.dynews.co.kr/news/articleView.html?idxno=857155",
        title=ARTICLE_TITLE, body_text=ARTICLE_BODY, article_rendered=True,
    )
    assert inferred_verdict == "정상"
    assert actual.inferred_url == "https://www.dynews.co.kr/news/articleView.html?idxno=857155"
    assert actual.url_structure_anomaly_yn == "Y"
    assert actual.url_structure_anomaly_details == "BigKinds 경로 내부에 외부 도메인 문자열이 포함됨"
    assert (actual.original_url, actual.verdict, actual.link_working_yn) == (
        actual_url, "링크오류", "N",
    )
