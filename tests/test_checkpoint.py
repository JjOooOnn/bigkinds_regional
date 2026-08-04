from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from unittest.mock import AsyncMock

from src.checkpoint import CheckpointStore
from src.models import AuditRow
from src.regional_collector import RegionalCollector


def _row(original_url: str = "https://example.com/article") -> AuditRow:
    return AuditRow(
        requested_date="2026-08-04",
        displayed_date="2026-08-04",
        region="test-region",
        issue_order=1,
        issue_title="test issue",
        issue_categories="",
        source_count=1,
        source_type="news",
        publisher="test publisher",
        article_date="2026-08-04",
        article_title="test article",
        original_url=original_url,
        final_url=original_url,
        http_status=200,
        browser_result="ok",
        link_working_yn="Y",
        verdict="정상",
        response_seconds=0.1,
        error_message="",
        checked_at="2026-08-04T09:00:00+09:00",
    )


def test_checkpoint_reads_legacy_issue_as_started_and_preserves_terminal_status(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    records = [
        {"type": "issue", "requested_date": "2026-08-04", "region": "test-region", "issue_order": 1},
        {"type": "issue_started", "requested_date": "2026-08-04", "region": "test-region", "issue_order": 2},
        {"type": "issue_completed", "requested_date": "2026-08-04", "region": "test-region", "issue_order": 2},
        {"type": "issue_started", "requested_date": "2026-08-04", "region": "test-region", "issue_order": 3},
        {"type": "issue_failed", "requested_date": "2026-08-04", "region": "test-region", "issue_order": 3},
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n{broken",
        encoding="utf-8",
    )

    checkpoint = CheckpointStore(path, resume=True)

    assert checkpoint.issue_status("2026-08-04", "test-region", 1) == "started"
    assert checkpoint.issue_status("2026-08-04", "test-region", 2) == "completed"
    assert checkpoint.issue_status("2026-08-04", "test-region", 3) == "failed"
    assert len(checkpoint.issue_keys) == 3


def test_started_issue_with_partial_rows_remains_resumable_and_rows_stay_deduplicated(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    checkpoint = CheckpointStore(path)
    row = _row()
    checkpoint.mark_issue_started("2026-08-04", "test-region", 1)
    assert checkpoint.add_row(row)
    assert not checkpoint.add_row(row)

    resumed = CheckpointStore(path, resume=True)

    assert resumed.issue_status("2026-08-04", "test-region", 1) == "started"
    assert len(resumed.rows) == 1
    assert not resumed.add_row(row)
    resumed.mark_issue_completed("2026-08-04", "test-region", 1)
    assert resumed.issue_status("2026-08-04", "test-region", 1) == "completed"


def test_only_started_issues_are_retried_and_failed_issue_blocks_region_completion(tmp_path):
    async def scenario() -> None:
        checkpoint = CheckpointStore(tmp_path / "checkpoint.jsonl")
        checkpoint.mark_issue_started("2026-08-04", "test-region", 1)
        collector = RegionalCollector(
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 4),
            regions=["test-region"],
            headed=False,
            max_issues=None,
            timeout_ms=1_000,
            retries=0,
            link_delay_ms=0,
            checkpoint=checkpoint,
            logger=logging.getLogger("test_checkpoint"),
        )
        collector._select_region = AsyncMock(return_value=True)
        collector._issue_cards = AsyncMock(return_value=[object()])
        collector._audit_issue = AsyncMock(return_value=True)

        assert await collector._audit_region(
            AsyncMock(), AsyncMock(), AsyncMock(), date(2026, 8, 4),
            date(2026, 8, 4), "test-region", 0,
        )
        collector._audit_issue.assert_awaited_once()

        checkpoint.mark_issue_completed("2026-08-04", "test-region", 1)
        collector._audit_issue.reset_mock()

        assert await collector._audit_region(
            AsyncMock(), AsyncMock(), AsyncMock(), date(2026, 8, 4),
            date(2026, 8, 4), "test-region", 0,
        )
        collector._audit_issue.assert_not_awaited()

        failed_checkpoint = CheckpointStore(tmp_path / "failed-checkpoint.jsonl")
        failed_checkpoint.mark_issue_started("2026-08-04", "test-region", 1)
        failed_checkpoint.mark_issue_failed("2026-08-04", "test-region", 1)
        collector.checkpoint = failed_checkpoint

        assert not await collector._audit_region(
            AsyncMock(), AsyncMock(), AsyncMock(), date(2026, 8, 4),
            date(2026, 8, 4), "test-region", 0,
        )
        collector._audit_issue.assert_not_awaited()

    asyncio.run(scenario())
