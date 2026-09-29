from __future__ import annotations

import asyncio
import gc
import os
import subprocess
import sys
import weakref
from unittest.mock import AsyncMock, Mock

import pytest

from src import browser_runtime
from src.config import load_runtime_config
from src.link_checker import LinkCheckResult
from test_fault_injection import _collector


def test_local_launch_and_fallback_order_are_preserved(monkeypatch):
    monkeypatch.setattr(browser_runtime.sys, "platform", "win32")
    runtime = load_runtime_config({})
    assert browser_runtime.chromium_launch_options(headed=False, runtime=runtime) == {"headless": True}
    assert [options for _, options in browser_runtime.verification_environments(runtime)] == [
        {"headless": False}, {"headless": False, "channel": "msedge"},
    ]
    with browser_runtime.managed_display(runtime):
        pass


def test_server_uses_shared_memory_workaround_and_bundled_headed_only(monkeypatch):
    monkeypatch.setattr(browser_runtime.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":99")
    runtime = load_runtime_config({"BIGKINDS_RUNTIME": "server"})
    assert browser_runtime.verification_environments(runtime) == (
        ("번들 Chromium headed", {"headless": False, "args": ["--disable-dev-shm-usage"]}),
    )
    monkeypatch.delenv("DISPLAY")
    assert browser_runtime.verification_environments(runtime) == ()


@pytest.mark.parametrize("body_fails", [False, True])
def test_managed_display_restores_environment_and_stops_xvfb(monkeypatch, body_fails):
    monkeypatch.setattr(browser_runtime.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":old")
    monkeypatch.setattr(browser_runtime.shutil, "which", lambda _: "/usr/bin/Xvfb")
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("Xvfb", 5), 0]
    launch = Mock(return_value=process)
    monkeypatch.setattr(browser_runtime.subprocess, "Popen", launch)
    monkeypatch.setattr(browser_runtime, "_wait_for_display", lambda *_: ":42")
    runtime = load_runtime_config({"BIGKINDS_RUNTIME": "server"})
    try:
        with browser_runtime.managed_display(runtime):
            assert os.environ["DISPLAY"] == ":42"
            if body_fails:
                raise ValueError("server failed")
    except ValueError:
        assert body_fails
    assert os.environ["DISPLAY"] == ":old"
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert "-nolisten" in launch.call_args.args[0]


def test_missing_xvfb_fails_before_server_start(monkeypatch):
    monkeypatch.setattr(browser_runtime.sys, "platform", "linux")
    monkeypatch.setattr(browser_runtime.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="Xvfb"):
        with browser_runtime.managed_display(load_runtime_config({"BIGKINDS_RUNTIME": "server"})):
            pytest.fail("server must not start")


def _browser(context=None):
    browser = Mock(version="123.0")
    browser.close = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.is_connected.return_value = True
    return browser


@pytest.mark.parametrize("verification", [False, True])
def test_partial_session_failure_closes_browser(verification):
    async def check():
        collector = _collector()
        browser = _browser()
        browser.new_context.side_effect = RuntimeError("context failed")
        collector._playwright = Mock()
        collector._playwright.chromium.launch = AsyncMock(return_value=browser)
        with pytest.raises(RuntimeError, match="context failed"):
            if verification:
                await collector._verification_context("test", {"headless": False})
            else:
                await collector._create_browser_session()
        if not verification:
            assert browser.new_context.await_args.kwargs["timezone_id"] == "Asia/Seoul"
        browser.close.assert_awaited_once()
        assert not collector._verification_sessions
    asyncio.run(check())


@pytest.mark.parametrize("disconnected", [False, True])
def test_stale_verification_session_is_closed_and_replaced(disconnected):
    async def check():
        collector = _collector()
        old_context = Mock(pages=[], close=AsyncMock())
        old_browser = _browser(old_context)
        old_browser.is_connected.return_value = not disconnected
        if not disconnected:
            collector._closed_verification_contexts.add(old_context)
        collector._verification_sessions["test"] = (old_browser, old_context)
        new_context = Mock(pages=[], close=AsyncMock())
        browser = _browser(new_context)
        collector._playwright = Mock()
        collector._playwright.chromium.launch = AsyncMock(return_value=browser)
        assert await collector._verification_context("test", {}) is new_context
        old_context.close.assert_awaited_once()
        old_browser.close.assert_awaited_once()
        assert await collector._verification_context("test", {}) is new_context
        collector._playwright.chromium.launch.assert_awaited_once()
        events = []
        new_context.close.side_effect = lambda: events.append("context")
        browser.close.side_effect = lambda: events.append("browser")
        await collector._close_verification_sessions()
        assert events == ["context", "browser"]
    asyncio.run(check())


def test_unavailable_fallback_is_attempted_only_once_per_job(monkeypatch):
    monkeypatch.setattr(browser_runtime.sys, "platform", "win32")
    async def check():
        collector = _collector()
        collector.checkpoint = Mock()
        collector._playwright = Mock()
        launch = AsyncMock(side_effect=RuntimeError("Executable doesn't exist"))
        collector._playwright.chromium.launch = launch
        primary = LinkCheckResult(verdict="접근제한", http_status=403)
        for _ in range(2):
            result = await collector._verify_automation_environment_block(primary, "https://example.test", "", {}, 0)
            assert result is primary
        assert launch.await_count == 2  # Chromium and Edge, each once.
        assert len(collector._unavailable_verification_environments) == 2
    asyncio.run(check())


def test_page_diagnostics_do_not_retain_closed_pages():
    collector = _collector()
    page = Mock()
    ref = weakref.ref(page)
    collector.status_by_page[page] = 200
    collector.first_url_by_page[page] = "https://example.test"
    collector._crashed_pages.add(page)
    del page
    gc.collect()
    assert ref() is None
    assert not collector.status_by_page
    assert not collector._crashed_pages


def test_navigation_failure_closes_context_before_browser():
    async def check():
        collector = _collector()
        page = Mock(goto=AsyncMock(side_effect=RuntimeError("navigation failed")))
        context = Mock(pages=[page], new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = _browser(context)
        collector._playwright = Mock()
        collector._playwright.chromium.launch = AsyncMock(return_value=browser)
        events = []
        context.close.side_effect = lambda: events.append("context")
        browser.close.side_effect = lambda: events.append("browser")
        with pytest.raises(RuntimeError, match="navigation failed"):
            await collector._create_browser_session()
        assert events == ["context", "browser"]
    asyncio.run(check())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires real Linux Xvfb")
@pytest.mark.parametrize("headed", [False, True])
def test_linux_xvfb_supports_real_server_chromium(headed):
    from playwright.sync_api import sync_playwright

    runtime = load_runtime_config({"BIGKINDS_RUNTIME": "server"})
    previous_display = os.environ.get("DISPLAY")
    with browser_runtime.managed_display(runtime), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            **browser_runtime.chromium_launch_options(headed=headed, runtime=runtime)
        )
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto("data:text/html,<h1>server runtime</h1>")
            assert page.locator("h1").inner_text() == "server runtime"
        finally:
            context.close()
            browser.close()
    assert os.environ.get("DISPLAY") == previous_display
