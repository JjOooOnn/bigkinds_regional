import asyncio
import logging
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright

from src.link_checker import BrowserLinkChecker, LinkCheckResult, article_rendered_evidence
from src.regional_collector import RegionalCollector
from src.verdict import classify_verdict


ARTICLE_TITLE = "지역 현안 정상 기사 제목"
ARTICLE_BODY = "실제 Chromium에 표시되는 정상적인 기사 본문입니다. " * 30


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
            "/source-popup": (200, "<div id='card' onclick=\"window.open('/article','_blank')\">popup</div>"),
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

    def add_debug(self, entry):
        self.debug_entries.append(entry)


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

            async def activate(path):
                source = await context.new_page()
                await source.goto(base + path, wait_until="domcontentloaded")
                result = await collector._click_and_check(
                    context, source, source.locator("#card"), checker,
                    expected_title=ARTICLE_TITLE, debug_context=debug_context,
                )
                await source.close()
                return result

            popup = await activate("/source-popup")
            current = await activate("/source-current")
            href = await activate("/source-href")
            absolute = await activate("/source-absolute-href")
            redirect = await activate("/source-redirect")
            domain_popup = await activate("/source-domain-popup")
            domain_href = await activate("/source-domain-href")
            fallback = await activate("/source-blocked-popup")
            missing = await activate("/source-none")
            normal_results = [popup, current, href, absolute, redirect, fallback]
            assert [item.verdict for item in normal_results] == ["정상"] * len(normal_results)
            assert all(item.link_working_yn == "Y" for item in normal_results)
            assert all(item.http_status == 200 for item in normal_results)
            assert fallback.original_url == base + "/article"
            assert popup.source_href_raw == ""
            assert popup.source_href_property == ""
            assert popup.click_target_raw == "/article"
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
            assert forbidden.access_reason_code == "ACCESS_HTTP_STATUS"
            assert captcha.access_reason_code == "ACCESS_STRONG_TEXT_PRIMARY"

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
