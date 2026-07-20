from src.url_utils import (
    FULL_URL,
    INTERNAL_ABSOLUTE_PATH,
    INTERNAL_RELATIVE_PATH,
    PROTOCOL_RELATIVE,
    analyze_url_structure,
    infer_external_url,
    is_suspicious_embedded_external_url,
    normalize_url,
    normalize_url_with_method,
    resolve_url_reference,
)


def test_relative_url_becomes_absolute():
    assert normalize_url("/news/1", "https://Example.COM/base") == "https://example.com/news/1"


def test_fragment_removed_and_query_kept():
    assert normalize_url("HTTPS://Example.COM/a?q=1#section") == "https://example.com/a?q=1"


def test_hostname_case_and_default_port_normalized():
    assert normalize_url("https://EXAMPLE.com:443/a") == "https://example.com/a"


def test_complete_https_url_is_preserved():
    result = normalize_url_with_method("https://www.example.com/a", "https://www.bigkinds.or.kr/regional/")
    assert result.value == "https://www.example.com/a"
    assert result.method == FULL_URL


def test_complete_http_url_is_preserved():
    result = normalize_url_with_method("http://www.example.com/a", "https://www.bigkinds.or.kr/regional/")
    assert result.value == "http://www.example.com/a"
    assert result.method == FULL_URL


def test_protocol_relative_url_uses_current_page_scheme():
    result = normalize_url_with_method("//www.example.com/a", "http://internal.example/base")
    assert result.value == "http://www.example.com/a"
    assert result.method == PROTOCOL_RELATIVE


def test_protocol_less_domain_keeps_browser_resolved_bigkinds_click_url():
    raw = "www.dynews.co.kr/news/articleView.html?idxno=857155"
    base = "https://www.bigkinds.or.kr/regional/curation.do"
    expected = "https://www.bigkinds.or.kr/regional/www.dynews.co.kr/news/articleView.html?idxno=857155"
    result = normalize_url_with_method(raw, base)
    assert result.value == expected
    assert result.method == INTERNAL_RELATIVE_PATH
    assert resolve_url_reference(raw, base) == expected


def test_inferred_external_url_is_separate_diagnostic_value():
    raw = "www.dynews.co.kr/news/articleView.html?idxno=857155"
    assert infer_external_url(raw) == "https://www.dynews.co.kr/news/articleView.html?idxno=857155"


def test_site_absolute_path_uses_current_origin():
    result = normalize_url_with_method("/news/article/1", "https://www.example.com/regional/curation.do")
    assert result.value == "https://www.example.com/news/article/1"
    assert result.method == INTERNAL_ABSOLUTE_PATH


def test_domain_shaped_relative_value_is_not_rewritten_to_external_host():
    result = normalize_url(
        "example.com/article/1", "https://www.bigkinds.or.kr/regional/curation.do",
    )
    assert result == "https://www.bigkinds.or.kr/regional/example.com/article/1"


def test_html_entity_and_whitespace_are_the_only_text_cleanup():
    result = resolve_url_reference(
        "  /news?a=1&amp;b=2  ", "https://www.bigkinds.or.kr/regional/curation.do",
    )
    assert result == "https://www.bigkinds.or.kr/news?a=1&b=2"


def test_bigkinds_path_with_embedded_external_domain_is_suspicious():
    first = analyze_url_structure(
        "https://www.bigkinds.or.kr/regional/www.example.com/news/1?x=2"
    )
    second = analyze_url_structure(
        "https://www.bigkinds.or.kr/regional/example.com/news/2"
    )
    assert first.anomalous and second.anomalous
    assert first.details == "BigKinds 경로 내부에 외부 도메인 문자열이 포함됨"
    assert first.inferred_url == "https://www.example.com/news/1?x=2"
    assert second.inferred_url == "https://example.com/news/2"
    assert is_suspicious_embedded_external_url(
        "https://www.bigkinds.or.kr/regional/www.example.com/news/1"
    )
    assert not is_suspicious_embedded_external_url(
        "https://www.bigkinds.or.kr/regional/curation.do"
    )
