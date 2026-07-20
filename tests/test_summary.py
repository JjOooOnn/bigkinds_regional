from conftest import make_row
from src.summary import calculate_summary


def test_summary_totals_and_groups():
    rows = [
        make_row(issue_order=1, verdict="정상", link_working_yn="Y"),
        make_row(issue_order=1, original_url="https://example.com/b", verdict="링크오류", link_working_yn="N"),
        make_row(issue_order=2, original_url="https://example.com/c", verdict="정상", link_working_yn="Y"),
    ]
    summary = calculate_summary(rows)
    assert (summary["total"], summary["normal"], summary["errors"], summary["rate"]) == (3, 2, 1, 2 / 3)
    assert summary["details"][0]["issues"] == 2
    assert summary["details"][0]["links"] == 3

