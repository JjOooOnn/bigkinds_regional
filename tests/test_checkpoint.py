from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.checkpoint import CheckpointStore, RowUpsertStatus
from src.link_checker import LinkCheckResult
from src.models import AuditRow, SourceInfo
from src.regional_collector import BrowserSessionFailure, RegionalCollector


def _row(
    original_url: str = "https://example.com/article", **overrides,
) -> AuditRow:
    data = dict(
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
    data.update(overrides)
    return AuditRow(**data)


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
    assert checkpoint.add_row(row).status is RowUpsertStatus.INSERTED
    assert checkpoint.add_row(row).status is RowUpsertStatus.UNCHANGED

    resumed = CheckpointStore(path, resume=True)

    assert resumed.issue_status("2026-08-04", "test-region", 1) == "started"
    assert len(resumed.rows) == 1
    assert resumed.add_row(row).status is RowUpsertStatus.UNCHANGED
    resumed.mark_issue_completed("2026-08-04", "test-region", 1)
    assert resumed.issue_status("2026-08-04", "test-region", 1) == "completed"


def test_checkpoint_upserts_latest_source_result_and_reloads_it(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    checkpoint = CheckpointStore(path)
    timed_out = _row(
        "", source_order=1, final_url="", http_status=None,
        browser_result="시간 초과", link_working_yn="N", verdict="타임아웃",
        error_message="deadline", checked_at="2026-08-04T09:00:00+09:00",
    )
    succeeded = _row(
        "https://example.com/retry", source_order=1,
        final_url="https://example.com/retry", browser_result="정상 표시",
        link_working_yn="Y", verdict="정상", error_message="",
        checked_at="2026-08-04T09:01:00+09:00",
    )

    inserted = checkpoint.add_row(timed_out)
    replaced = checkpoint.add_row(succeeded)

    assert inserted.status is RowUpsertStatus.INSERTED
    assert replaced.status is RowUpsertStatus.REPLACED
    assert replaced.previous == timed_out
    assert checkpoint.rows == [succeeded]
    row_records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("type") == "row"
    ]
    assert [record["data"]["verdict"] for record in row_records] == ["타임아웃", "정상"]

    resumed = CheckpointStore(path, resume=True)

    assert resumed.rows == [succeeded]


def test_collector_replacement_recalculates_counts_and_records_retry_debug(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "checkpoint.jsonl")
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
        logger=logging.getLogger("test_checkpoint_upsert"),
    )
    timed_out = _row(
        "", source_order=1, final_url="", http_status=None,
        browser_result="시간 초과", link_working_yn="N", verdict="타임아웃",
        error_message="deadline", checked_at="2026-08-04T09:00:00+09:00",
    )
    succeeded = _row(
        "https://example.com/retry", source_order=1,
        final_url="https://example.com/retry", browser_result="정상 표시",
        link_working_yn="Y", verdict="정상", error_message="",
        checked_at="2026-08-04T09:01:00+09:00",
    )

    collector._upsert_audit_row(timed_out)
    collector._upsert_audit_row(succeeded)

    assert checkpoint.rows == [succeeded]
    assert (
        collector.processed_links, collector.normal_count, collector.error_count,
    ) == (1, 1, 0)
    retry_entries = [
        entry for entry in checkpoint.debug_entries
        if entry.stage == "링크재시도" and entry.event == "이전 판정 대체"
    ]
    assert len(retry_entries) == 1
    assert "이전 판정=타임아웃" in retry_entries[0].details
    assert "최종 판정=정상" in retry_entries[0].details
    assert "최종 결과에서 제외되었음" in retry_entries[0].details


def test_retry_flow_keeps_failure_success_and_replacement_debug(tmp_path):
    async def scenario() -> None:
        checkpoint = CheckpointStore(tmp_path / "checkpoint.jsonl")
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
            logger=logging.getLogger("test_checkpoint_retry_debug"),
        )
        issue_card = AsyncMock()
        issue_card.evaluate.return_value = {"title": "test issue", "categories": []}
        collector._issue_cards = AsyncMock(return_value=[issue_card])
        close = AsyncMock()
        page = Mock()
        page.get_by_role.return_value = close
        page.wait_for_timeout = AsyncMock()
        source_heading = AsyncMock()
        source_heading.count.return_value = 1
        source_heading.inner_text.return_value = "출처 (1)"
        filtered = Mock(first=source_heading)
        title_locator = Mock()
        title_locator.filter.return_value = filtered
        modal = Mock()
        modal.locator.return_value = title_locator
        collector._modal_from_close = AsyncMock(return_value=modal)
        source_info = SourceInfo(
            source_type="뉴스", publisher="test publisher",
            article_date="2026-08-04", title="test article", card_order=1,
        )
        collector._source_cards = AsyncMock(
            return_value=([(Mock(), source_info)], 1),
        )
        collector._click_and_check = Mock(return_value=None)
        collector._cleanup_issue_modal = AsyncMock()
        collector._run_article_with_controls = AsyncMock(side_effect=[
            LinkCheckResult(
                browser_result="시간 초과", link_working_yn="N", verdict="타임아웃",
                error_message="deadline", access_reason_code="ARTICLE_DEADLINE_EXCEEDED",
            ),
            LinkCheckResult(
                original_url="https://example.com/retry",
                final_url="https://example.com/retry", http_status=200,
                browser_result="정상 표시", link_working_yn="Y", verdict="정상",
                access_reason_code="ARTICLE_RENDERED",
            ),
        ])

        for _ in range(2):
            assert await collector._audit_issue(
                page, AsyncMock(), AsyncMock(), date(2026, 8, 4),
                date(2026, 8, 4), "test-region", 0, 0, 1,
            )

        assert len(checkpoint.rows) == 1
        assert checkpoint.rows[0].verdict == "정상"
        assert (
            collector.processed_links, collector.normal_count, collector.error_count,
        ) == (1, 1, 0)
        assert any(
            entry.stage == "링크판정" and entry.event == "타임아웃"
            for entry in checkpoint.debug_entries
        )
        assert any(
            entry.stage == "링크URL"
            and entry.final_url == "https://example.com/retry"
            for entry in checkpoint.debug_entries
        )
        assert any(
            entry.stage == "링크재시도" and entry.event == "이전 판정 대체"
            for entry in checkpoint.debug_entries
        )

    asyncio.run(scenario())


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


def test_browser_recovery_retries_the_same_started_issue(tmp_path):
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
            logger=logging.getLogger("test_checkpoint_recovery"),
        )
        page, context = AsyncMock(), AsyncMock()
        collector._session = SimpleNamespace(
            browser=AsyncMock(), context=context, page=page,
        )
        collector._select_region = AsyncMock(return_value=True)
        collector._issue_cards = AsyncMock(return_value=[object()])
        failure = BrowserSessionFailure(
            "page_closed", "inspection page closed",
            inspection_page_only=True,
            article_key=("2026-08-04", "test-region", 1, 1),
        )

        async def audit_issue(*_args):
            if collector._audit_issue.await_count == 1:
                raise failure
            checkpoint.mark_issue_completed("2026-08-04", "test-region", 1)
            return True

        collector._audit_issue = AsyncMock(side_effect=audit_issue)

        assert await collector._audit_region(
            page, context, AsyncMock(), date(2026, 8, 4),
            date(2026, 8, 4), "test-region", 0,
        )
        assert collector._audit_issue.await_count == 2
        assert checkpoint.issue_status("2026-08-04", "test-region", 1) == "completed"
        assert collector.browser_restart_count == 1

    asyncio.run(scenario())


def test_exhausted_browser_recovery_keeps_started_issue_retryable(tmp_path):
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
            logger=logging.getLogger("test_checkpoint_exhausted_recovery"),
        )
        page, context = AsyncMock(), AsyncMock()
        collector._session = SimpleNamespace(
            browser=AsyncMock(), context=context, page=page,
        )
        collector._select_region = AsyncMock(return_value=True)
        collector._issue_cards = AsyncMock(return_value=[object()])
        failure = BrowserSessionFailure(
            "page_crashed", "Target crashed",
            article_key=("2026-08-04", "test-region", 1, 1),
        )
        collector._audit_issue = AsyncMock(side_effect=failure)
        collector._recover_browser_session = AsyncMock(return_value=False)

        with pytest.raises(BrowserSessionFailure):
            await collector._audit_region(
                page, context, AsyncMock(), date(2026, 8, 4),
                date(2026, 8, 4), "test-region", 0,
            )

        assert checkpoint.issue_status("2026-08-04", "test-region", 1) == "started"

    asyncio.run(scenario())
