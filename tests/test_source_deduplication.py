from src.source_parser import parse_source_text
from src.url_utils import deduplicate_rows
from conftest import make_row


def test_same_url_from_nested_dom_is_one_row():
    url = "https://Example.com/news/1#title"
    rows = [
        make_row(article_title="부모 div", original_url=url),
        make_row(article_title="언론사 div", original_url="https://example.com/news/1"),
        make_row(article_title="제목 div", original_url="https://example.com/news/1#other"),
    ]
    assert len(deduplicate_rows(rows)) == 1


def test_same_url_in_other_issue_is_retained():
    rows = [make_row(issue_order=1), make_row(issue_order=2)]
    assert len(deduplicate_rows(rows)) == 2


def test_parse_news_text():
    info = parse_source_text("뉴스\n데일리안 | 2026-07-01\n기사 제목")
    assert (info.source_type, info.publisher, info.article_date, info.title) == ("뉴스", "데일리안", "2026-07-01", "기사 제목")


def test_parse_research_report_text():
    info = parse_source_text("연구보고서\n전남연구원 | 2026-07-20\n연구보고서 제목")
    assert (info.source_type, info.publisher, info.article_date, info.title) == (
        "연구보고서", "전남연구원", "2026-07-20", "연구보고서 제목",
    )


def test_parse_notice_and_missing_fields():
    notice = parse_source_text("공지사항\n서울시 | 2026.07.01\n공지 제목")
    missing = parse_source_text("뉴스\n제목만 있음")
    assert (notice.source_type, notice.publisher, notice.article_date, notice.title) == (
        "공지사항", "서울시", "2026-07-01", "공지 제목",
    )
    assert missing.publisher == "" and missing.article_date == "" and missing.title == "제목만 있음"
