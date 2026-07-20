from __future__ import annotations

from datetime import datetime

from src.models import AuditRow


def make_row(**overrides) -> AuditRow:
    data = dict(
        requested_date="2026-07-01", displayed_date="2026-07-01", region="서울특별시",
        issue_order=1, issue_title="테스트 이슈", issue_categories="사회", source_count=2,
        source_type="뉴스", publisher="테스트일보", article_date="2026-07-01",
        article_title="테스트 기사", original_url="https://example.com/a", final_url="https://example.com/a",
        http_status=200, browser_result="정상 표시", link_working_yn="Y", verdict="정상",
        response_seconds=0.5, error_message="", checked_at=datetime.now().astimezone().isoformat(),
        region_order=0, source_order=1,
    )
    data.update(overrides)
    return AuditRow(**data)

