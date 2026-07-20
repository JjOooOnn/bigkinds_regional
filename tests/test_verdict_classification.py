import pytest

from src.verdict import classify_verdict, classify_verdict_detailed, working_yn


def classify(status=200, url="https://example.com/a", title="기사", body="의미 있는 기사 내용 " * 10, **kwargs):
    return classify_verdict(http_status=status, final_url=url, title=title, body_text=body, **kwargs)


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"article_rendered": True}, "정상"),
        ({"status": 403}, "접근제한"),
        ({"status": 200, "body": "CAPTCHA 로봇 확인" * 10}, "접근제한"),
        ({"status": 404}, "링크오류"),
        ({"status": 500}, "서버오류"),
        ({"timed_out": True}, "타임아웃"),
        ({"url": "about:blank", "body": ""}, "빈화면"),
        ({"status": 418}, "확인필요"),
        ({"click_error": True}, "클릭오류"),
    ],
)
def test_classifications(kwargs, expected):
    assert classify(**kwargs)[0] == expected


def test_direct_request_failure_does_not_override_browser_success():
    # 상태를 읽지 못해도 실제 기사 제목과 본문 근거가 확인되면 정상이다.
    verdict, _, _ = classify(status=None, article_rendered=True)
    assert verdict == "정상"
    assert working_yn(verdict) == "Y"


def test_http_404_is_link_error_and_not_working():
    verdict, display, _ = classify(status=404, article_rendered=False)
    assert (verdict, display, working_yn(verdict)) == ("링크오류", "표시 실패", "N")


@pytest.mark.parametrize("status", [200, None])
def test_rendered_file_not_found_screen_is_link_error_even_without_reliable_status(status):
    verdict, display, error = classify_verdict(
        http_status=status,
        final_url="https://example.com/missing",
        title="404",
        body_text="404 / 파일 또는 디렉터리를 찾을 수 없습니다.",
        primary_text="404\n파일 또는 디렉터리를 찾을 수 없습니다.",
        article_rendered=False,
    )
    assert (verdict, display, working_yn(verdict)) == ("링크오류", "표시 실패", "N")
    assert error == "404 페이지 표시 - 파일 또는 디렉터리를 찾을 수 없음"


def test_status_none_without_article_evidence_is_not_normal():
    verdict, display, _ = classify(status=None, article_rendered=False)
    assert verdict == "확인필요"
    assert display == "확인 필요"
    assert working_yn(verdict) == "N"


def test_url_structure_anomaly_does_not_override_a_real_rendered_article():
    verdict, display, _ = classify_verdict(
        http_status=200,
        final_url="https://www.bigkinds.or.kr/regional/www.example.com/news/1",
        title="실제 기사 제목",
        body_text="실제 기사 본문 " * 100,
        article_rendered=True,
    )
    assert (verdict, display, working_yn(verdict)) == ("정상", "정상 표시", "Y")


@pytest.mark.parametrize(
    "body, expected",
    [
        ("요청하신 페이지가 존재하지 않습니다.", "링크오류"),
        ("삭제된 기사입니다.", "링크오류"),
        ("서버 오류가 발생했습니다.", "서버오류"),
        ("접근 제한: 이 페이지에 접근 권한이 없습니다.", "접근제한"),
    ],
)
def test_rendered_error_phrases_are_not_normal_even_with_http_200(body, expected):
    verdict, display, _ = classify_verdict(
        http_status=200, final_url="https://example.com/problem",
        title="오류", body_text=body, article_rendered=False,
    )
    assert verdict == expected
    assert display != "정상 표시"
    assert working_yn(verdict) == "N"


def test_browser_error_page_is_link_error_and_not_working():
    verdict, display, _ = classify_verdict(
        http_status=None, final_url="chrome-error://chromewebdata/",
        title="", body_text="ERR_NAME_NOT_RESOLVED", article_rendered=False,
    )
    assert (verdict, display, working_yn(verdict)) == ("링크오류", "표시 실패", "N")


def test_auxiliary_login_message_does_not_override_rendered_article():
    verdict, _, _ = classify_verdict(
        http_status=200,
        final_url="https://example.com/article",
        title="정상 기사 제목",
        body_text=("정상 기사 본문입니다. " * 30) + "댓글은 로그인 후 이용 가능합니다.",
        article_rendered=True,
    )
    assert verdict == "정상"


def test_auxiliary_strong_permission_message_does_not_override_rendered_article():
    decision = classify_verdict_detailed(
        http_status=200,
        final_url="https://example.com/article",
        title="정상 기사 제목",
        body_text=("정상 기사 본문입니다. " * 30) + "댓글입력 권한이 없습니다.",
        primary_text="정상 기사 제목\n" + ("정상 기사 본문입니다. " * 30),
        article_rendered=True,
    )
    assert (decision.verdict, working_yn(decision.verdict)) == ("정상", "Y")
    assert decision.reason_code == "ARTICLE_RENDERED_AUXILIARY_ACCESS_TEXT_IGNORED"
    assert decision.detected_marker == "권한이 없습니다"


def test_login_required_page_without_article_remains_blocked():
    verdict, _, _ = classify_verdict(
        http_status=200, final_url="https://example.com/login",
        title="로그인", body_text="이 기사는 로그인이 필요합니다.",
        article_rendered=False,
    )
    assert verdict == "접근제한"


@pytest.mark.parametrize("status, body", [(403, "정상 기사 본문 " * 30), (200, "CAPTCHA " * 30)])
def test_strong_access_signal_stays_blocked_even_with_article_evidence(status, body):
    verdict, _, _ = classify_verdict(
        http_status=status, final_url="https://example.com/article",
        title="기사", body_text=body, primary_text=body, article_rendered=True,
    )
    assert verdict == "접근제한"


@pytest.mark.parametrize("verdict", ["접근제한", "링크오류", "서버오류", "타임아웃", "클릭오류", "빈화면", "확인필요"])
def test_working_mapping(verdict):
    assert working_yn(verdict) == "N"


def test_mapping_only_y_or_n():
    values = {working_yn(value) for value in ["정상", "접근제한", "링크오류", "서버오류", "타임아웃", "클릭오류", "빈화면", "확인필요"]}
    assert values == {"Y", "N"}
