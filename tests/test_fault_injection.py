from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import os
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from src.api.app import create_app
from src.application.job_manager import JobManager, SqliteProgressReporter, _heartbeat_loop
from src.application.job_repository import JobRepository
from src.application.progress import AuditCancelled
from src.link_checker import BrowserLinkChecker
from src.logging_utils import configure_lifecycle_logging
from src.regional_collector import BrowserSessionFailure, RegionalCollector


def _config() -> dict:
    return {
        "start_date": "2026-07-30",
        "end_date": "2026-07-30",
        "regions": ["부산광역시"],
        "headed": False,
        "resume": False,
        "max_issues": 1,
        "timeout_seconds": 1,
        "retries": 0,
        "link_delay_seconds": 0,
        "debug": False,
    }


def _create_queued_job(repository: JobRepository, job_id: str = "fault-job") -> dict:
    return repository.create_job(
        job_id,
        _config(),
        repository.db_path.parent / f"{job_id}.jsonl",
    )


def _abrupt_worker(db_path: str, job_id: str) -> None:
    repository = JobRepository(Path(db_path))
    repository.mark_started(job_id, os.getpid())
    reporter = SqliteProgressReporter(repository, job_id)
    reporter.emit(
        "link_started",
        "강제 종료 대상 기사를 확인 중입니다.",
        current_operation="link_check:injected-worker-exit",
        browser_state="running",
    )
    repository.update_job(job_id, processed_links=1)
    os._exit(23)


def _uncooperative_worker(db_path: str, job_id: str, ready) -> None:
    repository = JobRepository(Path(db_path))
    repository.mark_started(job_id, os.getpid())
    reporter = SqliteProgressReporter(repository, job_id)
    reporter.emit(
        "link_started",
        "강제 종료 대기 작업을 확인 중입니다.",
        current_operation="link_check:uncooperative-worker",
        browser_state="running",
    )
    ready.set()
    while True:
        time.sleep(1)


def _run_api_lifespan(
    db_path: str,
    log_path: str,
    ready,
    stop,
) -> None:
    with Path(log_path).open("a", encoding="utf-8", buffering=1) as stream:
        configure_lifecycle_logging(stream)
        app = create_app(
            db_path=Path(db_path),
            frontend_dist=Path(db_path).parent / "missing-frontend",
        )
        with TestClient(app):
            ready.set()
            stop.wait(30)


class _StalledResponseHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        time.sleep(1)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"late response")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *args: object) -> None:
        return


def _read_lifecycle_events(path: Path) -> list[dict[str, str]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("LIFECYCLE "):
            events.append(json.loads(line.removeprefix("LIFECYCLE ")))
    return events


class _CollectorCheckpoint:
    rows = []
    debug_entries = []


class _RecordingReporter:
    def __init__(self):
        self.events = []

    def emit(self, event: str, message: str = "", **data) -> None:
        self.events.append((event, message, data))


class _MutableCancellationToken:
    requested = False

    def is_cancel_requested(self) -> bool:
        return self.requested


def _collector(*, timeout_ms=30_000, retries=2, reporter=None, cancellation_token=None):
    return RegionalCollector(
        start_date=date(2026, 7, 30),
        end_date=date(2026, 7, 30),
        regions=["부산광역시"],
        headed=False,
        max_issues=1,
        timeout_ms=timeout_ms,
        retries=retries,
        link_delay_ms=0,
        checkpoint=_CollectorCheckpoint(),
        logger=logging.getLogger("fault-injection"),
        progress_reporter=reporter,
        cancellation_token=cancellation_token,
    )


def test_browser_lifecycle_events_record_page_and_browser_states():
    reporter = _RecordingReporter()
    collector = _collector(reporter=reporter)
    callbacks = {}

    page = Mock()
    page.on.side_effect = lambda event, callback: callbacks.setdefault(f"page:{event}", callback)
    browser = Mock()
    browser.on.side_effect = lambda event, callback: callbacks.setdefault(
        f"browser:{event}", callback,
    )

    collector._track_page_lifecycle(page, "검사 page")
    collector._track_browser_lifecycle(browser)
    callbacks["page:close"](page)
    callbacks["browser:disconnected"](browser)

    assert page in collector._unexpected_closed_pages
    assert browser in collector._disconnected_browsers
    states = [data["browser_state"] for _, _, data in reporter.events]
    assert states == ["page_closed", "disconnected"]


def test_browser_failure_classification_uses_live_state_before_error_text():
    collector = _collector()
    browser = Mock()
    browser.is_connected.return_value = True
    context = Mock(browser=browser)
    source_page = Mock()
    source_page.is_closed.return_value = False
    inspection_page = Mock()
    inspection_page.is_closed.return_value = True

    page_failure = collector._browser_session_failure(
        source_page, context, RuntimeError("opaque failure"), target=inspection_page,
    )
    assert page_failure is not None
    assert page_failure.state == "page_closed"
    assert page_failure.inspection_page_only

    browser.is_connected.return_value = False
    disconnected = collector._browser_session_failure(
        source_page, context, RuntimeError("opaque failure"), target=inspection_page,
    )
    assert disconnected is not None
    assert disconnected.state == "disconnected"
    assert not disconnected.inspection_page_only


def test_recovery_is_limited_per_article_and_per_job():
    reporter = _RecordingReporter()
    collector = _collector(reporter=reporter)
    requested = date(2026, 7, 30)

    async def scenario():
        first_key = (requested.isoformat(), "부산광역시", 1, 1)
        first = BrowserSessionFailure(
            "page_closed", "inspection page closed",
            inspection_page_only=True, article_key=first_key,
        )
        assert await collector._recover_browser_session(first, requested, "부산광역시")
        assert not await collector._recover_browser_session(first, requested, "부산광역시")

        second = BrowserSessionFailure(
            "page_closed", "inspection page closed",
            inspection_page_only=True,
            article_key=(requested.isoformat(), "부산광역시", 1, 2),
        )
        assert await collector._recover_browser_session(second, requested, "부산광역시")

        third = BrowserSessionFailure(
            "page_closed", "inspection page closed",
            inspection_page_only=True,
            article_key=(requested.isoformat(), "부산광역시", 1, 3),
        )
        assert not await collector._recover_browser_session(third, requested, "부산광역시")

    asyncio.run(scenario())

    assert collector.browser_restart_count == 2
    assert [event for event, _, _ in reporter.events].count("browser_recovered") == 2
    assert [event for event, _, _ in reporter.events].count("browser_recovery_exhausted") == 2


def test_cancel_request_prevents_browser_recovery():
    token = _MutableCancellationToken()
    token.requested = True
    collector = _collector(cancellation_token=token)
    failure = BrowserSessionFailure(
        "disconnected", "browser disconnected",
        article_key=("2026-07-30", "부산광역시", 1, 1),
    )

    with pytest.raises(AuditCancelled):
        asyncio.run(
            collector._recover_browser_session(
                failure, date(2026, 7, 30), "부산광역시",
            )
        )
    assert collector.browser_restart_count == 0


def test_main_page_recovery_restores_requested_date_and_region():
    collector = _collector()
    requested = date(2026, 7, 30)
    browser = Mock()
    context = Mock()
    replacement_page = AsyncMock()
    context.new_page = AsyncMock(return_value=replacement_page)
    collector._session = SimpleNamespace(
        browser=browser, context=context, page=Mock(),
    )
    collector._track_page_lifecycle = Mock()
    collector._select_region = AsyncMock(return_value=True)
    navigator = Mock()
    navigator.move_to = AsyncMock(return_value=(True, requested))

    failure = BrowserSessionFailure(
        "page_closed", "BigKinds page closed",
        article_key=(requested.isoformat(), "부산광역시", 1, 1),
    )
    with patch("src.regional_collector.DateNavigator", return_value=navigator):
        recovered = asyncio.run(
            collector._recover_browser_session(failure, requested, "부산광역시")
        )

    assert recovered
    assert collector._session.page is replacement_page
    replacement_page.goto.assert_awaited_once()
    navigator.move_to.assert_awaited_once_with(requested, retries=1)
    collector._select_region.assert_awaited_once_with(replacement_page, "부산광역시")


def test_disconnected_browser_recovery_recreates_playwright_and_session():
    collector = _collector()
    requested = date(2026, 7, 30)
    old_playwright = Mock()
    old_playwright.stop = AsyncMock()
    old_session = SimpleNamespace(browser=Mock(), context=Mock(), page=Mock())
    replacement_session = SimpleNamespace(browser=Mock(), context=Mock(), page=Mock())
    new_playwright = Mock()
    starter = Mock()
    starter.start = AsyncMock(return_value=new_playwright)

    collector._playwright = old_playwright
    collector._session = old_session
    collector._cleanup_browser_resources = AsyncMock()
    async def bounded_cleanup(awaitable, _label):
        await awaitable
        return True

    collector._bounded_cleanup = AsyncMock(side_effect=bounded_cleanup)
    collector._create_browser_session = AsyncMock(return_value=replacement_session)
    collector._select_region = AsyncMock(return_value=True)
    navigator = Mock()
    navigator.move_to = AsyncMock(return_value=(True, requested))

    failure = BrowserSessionFailure(
        "disconnected", "browser disconnected",
        article_key=(requested.isoformat(), "부산광역시", 1, 1),
    )
    with (
        patch("src.regional_collector.async_playwright", return_value=starter),
        patch("src.regional_collector.DateNavigator", return_value=navigator),
    ):
        recovered = asyncio.run(
            collector._recover_browser_session(failure, requested, "부산광역시")
        )

    assert recovered
    assert collector._playwright is new_playwright
    assert collector._session is replacement_session
    collector._cleanup_browser_resources.assert_awaited_once_with(
        old_session.context, old_session.browser,
    )
    starter.start.assert_awaited_once()
    collector._create_browser_session.assert_awaited_once()


@pytest.mark.parametrize(
    ("timeout_ms", "retries", "expected"),
    [
        (1_000, 0, 45.0),
        (30_000, 2, 105.0),
        (300_000, 2, 180.0),
    ],
)
def test_article_deadline_uses_the_stage_three_policy(timeout_ms, retries, expected):
    assert _collector(timeout_ms=timeout_ms, retries=retries).article_deadline_seconds == expected


def test_article_deadline_returns_a_timeout_result_without_waiting_for_the_operation():
    collector = _collector()
    collector.article_deadline_seconds = 0.05
    collector.cleanup_timeout_seconds = 0.05

    async def never_finishes():
        await asyncio.sleep(10)

    started = time.perf_counter()
    result = asyncio.run(collector._run_article_with_controls(never_finishes()))
    assert time.perf_counter() - started < 0.5
    assert result.verdict == "타임아웃"
    assert result.link_working_yn == "N"
    assert result.access_reason_code == "ARTICLE_DEADLINE_EXCEEDED"


def test_article_check_observes_cancellation_and_acknowledges_before_cleanup():
    reporter = _RecordingReporter()
    cancellation = _MutableCancellationToken()
    collector = _collector(reporter=reporter, cancellation_token=cancellation)
    collector.cancel_poll_seconds = 0.01
    operation_cancelled = asyncio.Event()

    async def scenario():
        async def operation():
            try:
                await asyncio.sleep(10)
            finally:
                operation_cancelled.set()

        async def request_cancel():
            await asyncio.sleep(0.02)
            cancellation.requested = True

        requester = asyncio.create_task(request_cancel())
        with pytest.raises(AuditCancelled):
            await collector._run_article_with_controls(operation())
        await requester

    asyncio.run(scenario())
    assert operation_cancelled.is_set()
    assert reporter.events[0][0] == "cancel_acknowledged"


def test_cleanup_call_is_bounded():
    collector = _collector()
    collector.cleanup_timeout_seconds = 0.02

    async def slow_cleanup():
        await asyncio.sleep(10)

    started = time.perf_counter()
    cleaned = asyncio.run(collector._bounded_cleanup(slow_cleanup(), "느린 정리"))
    assert not cleaned
    assert time.perf_counter() - started < 0.5


def test_stalled_link_keeps_heartbeat_but_not_progress_or_cancel_acknowledgement(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    _create_queued_job(repository)
    assert repository.mark_started("fault-job", os.getpid())
    reporter = SqliteProgressReporter(repository, "fault-job")
    reporter.emit(
        "link_started",
        current_operation="link_check:2026-07-30:부산광역시:issue_5:source_8",
        browser_state="running",
    )
    before = repository.get_job("fault-job")
    assert before is not None

    heartbeat_stop = Event()
    heartbeat = Thread(
        target=_heartbeat_loop,
        args=(repository, "fault-job", heartbeat_stop),
        daemon=True,
    )
    heartbeat.start()
    try:
        heartbeat_deadline = time.monotonic() + 7
        while time.monotonic() < heartbeat_deadline:
            heartbeat_observation = repository.get_job("fault-job")
            if (
                heartbeat_observation
                and heartbeat_observation["heartbeat_at"] > before["heartbeat_at"]
            ):
                break
            time.sleep(0.05)
        else:
            pytest.fail("정지 연산 중 하트비트가 갱신되지 않았습니다.")
        assert repository.request_cancel("fault-job", requested_by="fault_injection")
        observed = repository.get_job("fault-job")
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)

    assert observed is not None
    assert observed["heartbeat_at"] > before["heartbeat_at"]
    assert observed["last_progress_at"] == ""
    assert observed["current_operation"].endswith("issue_5:source_8")
    assert observed["status"] == "cancel_requested"
    assert observed["cancel_requested_by"] == "fault_injection"


def test_slow_http_response_is_bounded_by_the_existing_playwright_call_timeout():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StalledResponseHandler)
    server.daemon_threads = True
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/stall"

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            checker = BrowserLinkChecker(
                timeout_ms=150,
                retries=0,
                render_recheck_timeout_ms=0,
            )
            started = time.perf_counter()
            result = await checker.open_url(page, url, started, {})
            elapsed = time.perf_counter() - started
            await page.close()
            await browser.close()
        return result, elapsed

    try:
        result, elapsed = asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert result.verdict == "타임아웃"
    assert elapsed < 1.5


@pytest.mark.parametrize(
    ("injection", "message_fragment"),
    [
        ("page", "closed"),
        ("browser", "closed"),
        ("driver", "closed"),
    ],
    ids=["inspection-page-closed", "chromium-closed", "playwright-driver-closed"],
)
def test_browser_layer_termination_is_reported_by_the_first_failed_call(
    injection: str,
    message_fragment: str,
):
    async def scenario() -> str:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("<title>fault injection</title>")
        if injection == "page":
            await page.close()
        elif injection == "browser":
            await browser.close()
        else:
            await playwright.stop()
        try:
            await page.title()
        except Exception as exc:
            message = str(exc)
        else:
            pytest.fail("종료된 브라우저 계층의 호출이 성공했습니다.")
        if injection == "page":
            await browser.close()
        if injection != "driver":
            await playwright.stop()
        return message

    assert message_fragment.lower() in asyncio.run(scenario()).lower()


def test_abrupt_worker_exit_preserves_last_operation_for_diagnosis(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    _create_queued_job(repository)
    manager = JobManager(repository, worker_launcher=lambda _job_id: None, recover_on_start=False)
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_abrupt_worker,
        args=(str(repository.db_path), "fault-job"),
    )
    process.start()
    manager._watch_process("fault-job", process)

    observed = repository.get_job("fault-job")
    assert observed is not None
    assert observed["worker_pid"] == process.pid
    assert observed["worker_exit_code"] == 23
    assert observed["status"] == "partial_failed"
    assert observed["termination_reason"] == "worker_exited_unexpectedly"
    assert observed["heartbeat_at"]
    assert observed["last_progress_at"] == ""
    assert observed["current_operation"] == "link_check:injected-worker-exit"
    assert observed["browser_state"] == "running"


def test_server_shutdown_terminates_then_kills_only_the_recorded_worker(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    _create_queued_job(repository)
    assert repository.mark_started("fault-job", 4242)

    class StillRunningProcess:
        pid = 4242
        exitcode = -9

        def __init__(self) -> None:
            self.join_timeouts = []
            self.terminated = False
            self.killed = False

        def is_alive(self) -> bool:
            return not self.killed

        def join(self, timeout: float) -> None:
            self.join_timeouts.append(timeout)

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    process = StillRunningProcess()
    manager = JobManager(
        repository,
        worker_launcher=lambda _job_id: None,
        recover_on_start=False,
        cancel_grace_seconds=0.01,
        terminate_grace_seconds=0.01,
    )
    manager._processes["fault-job"] = process  # type: ignore[assignment]
    manager.shutdown()

    observed = repository.get_job("fault-job")
    assert observed is not None
    assert observed["status"] == "force_terminating"
    assert observed["cancel_requested_by"] == "server_shutdown"
    assert process.join_timeouts == [0.01, 0.01]
    assert process.terminated
    assert process.killed
    assert manager._processes == {}


def test_force_termination_rejects_a_process_with_a_different_pid(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    _create_queued_job(repository)
    assert repository.mark_started("fault-job", 4242)
    assert repository.request_cancel("fault-job", requested_by="user")

    class DifferentProcess:
        pid = 4243

        def __init__(self) -> None:
            self.terminated = False

        @staticmethod
        def join(timeout: float) -> None:
            return None

        @staticmethod
        def is_alive() -> bool:
            return True

        def terminate(self) -> None:
            self.terminated = True

    process = DifferentProcess()
    manager = JobManager(
        repository,
        worker_launcher=lambda _job_id: None,
        recover_on_start=False,
        cancel_grace_seconds=0,
    )
    manager._processes["fault-job"] = process  # type: ignore[assignment]
    manager._enforce_cancel_deadline("fault-job", process)  # type: ignore[arg-type]

    assert not process.terminated
    assert repository.get_job("fault-job")["status"] == "cancel_requested"


def test_uncooperative_worker_is_force_terminated_and_diagnostic_state_is_preserved(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    _create_queued_job(repository)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(
        target=_uncooperative_worker,
        args=(str(repository.db_path), "fault-job", ready),
    )
    process.start()
    manager = JobManager(
        repository,
        worker_launcher=lambda _job_id: None,
        recover_on_start=False,
        cancel_grace_seconds=0.05,
        terminate_grace_seconds=0.05,
    )
    manager._processes["fault-job"] = process
    watcher = Thread(target=manager._watch_process, args=("fault-job", process), daemon=True)
    watcher.start()
    try:
        assert ready.wait(5)
        requested = manager.cancel_job("fault-job")
        assert requested["status"] == "cancel_requested"
        watcher.join(5)
        assert not watcher.is_alive()
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=2)

    observed = repository.get_job("fault-job")
    assert observed is not None
    assert observed["status"] == "cancelled"
    assert observed["termination_reason"] == "force_terminated"
    assert observed["worker_pid"] == process.pid
    assert observed["worker_exit_code"] is not None
    assert observed["current_operation"] == "link_check:uncooperative-worker"
    assert observed["operation_started_at"]
    assert observed["browser_state"] == "running"


def test_api_lifespan_normal_and_abrupt_process_exit_have_distinct_timelines(tmp_path):
    context = multiprocessing.get_context("spawn")

    normal_log = tmp_path / "api-normal.log"
    normal_ready = context.Event()
    normal_stop = context.Event()
    normal = context.Process(
        target=_run_api_lifespan,
        args=(str(tmp_path / "normal.sqlite3"), str(normal_log), normal_ready, normal_stop),
    )
    normal.start()
    assert normal_ready.wait(10)
    normal_stop.set()
    normal.join(timeout=10)
    assert normal.exitcode == 0

    abrupt_log = tmp_path / "api-abrupt.log"
    abrupt_ready = context.Event()
    abrupt_stop = context.Event()
    abrupt = context.Process(
        target=_run_api_lifespan,
        args=(str(tmp_path / "abrupt.sqlite3"), str(abrupt_log), abrupt_ready, abrupt_stop),
    )
    abrupt.start()
    assert abrupt_ready.wait(10)
    abrupt.terminate()
    abrupt.join(timeout=10)
    assert abrupt.exitcode not in (None, 0)

    normal_events = _read_lifecycle_events(normal_log)
    abrupt_events = _read_lifecycle_events(abrupt_log)
    assert [event["event"] for event in normal_events] == ["started", "stopping", "stopped"]
    assert {int(event["pid"]) for event in normal_events} == {normal.pid}
    assert [event["event"] for event in abrupt_events] == ["started"]
    assert int(abrupt_events[0]["pid"]) == abrupt.pid


def test_ui_cancel_records_request_while_stalled_operation_remains_active(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    manager = JobManager(
        repository,
        worker_launcher=lambda _job_id: None,
        recover_on_start=False,
    )
    app = create_app(
        job_manager=manager,
        frontend_dist=tmp_path / "missing-frontend",
    )
    payload = {
        "start_date": "2026-07-30",
        "end_date": "2026-07-30",
        "all_regions": False,
        "regions": ["부산광역시"],
        "headed": False,
        "max_issues": 1,
    }
    with TestClient(app) as client:
        created = client.post("/api/jobs", json=payload).json()
        job_id = created["job_id"]
        assert repository.mark_started(job_id, os.getpid())
        SqliteProgressReporter(repository, job_id).emit(
            "link_started",
            current_operation="link_check:injected-ui-cancel",
            browser_state="running",
        )
        response = client.post(f"/api/jobs/{job_id}/cancel")

    assert response.status_code == 200
    observed = response.json()
    assert observed["status"] == "cancel_requested"
    assert observed["cancel_requested_at"]
    assert observed["cancel_requested_by"] == "user"
    assert observed["current_operation"] == "link_check:injected-ui-cancel"
    assert observed["ended_at"] == ""
