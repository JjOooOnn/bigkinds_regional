from __future__ import annotations

from contextlib import nullcontext
from datetime import date

import pytest

import run_web


def test_web_runner_binds_only_to_loopback_and_preserves_daily_log(monkeypatch, tmp_path):
    called = {}

    def fake_run(app, **kwargs):
        called.update({"app": app, **kwargs})
        print("Authorization: Bearer test-secret")

    monkeypatch.setattr(run_web.uvicorn, "run", fake_run)
    monkeypatch.setattr(run_web, "SERVER_LOG_DIR", tmp_path)
    assert run_web.main(["--no-browser", "--skip-build", "--port", "8123"]) == 0
    assert called["app"] == "src.api.app:app"
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 8123
    assert called["workers"] == 1
    logs = list(tmp_path.glob("server_*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert '"component": "server_launcher"' in text
    assert '"event": "starting"' in text
    assert '"termination_reason": "uvicorn_returned"' in text
    assert "test-secret" not in text
    assert "[마스킹]" in text


def test_server_mode_uses_railway_settings_without_opening_browser_or_building(
    monkeypatch, tmp_path,
):
    called = {}

    def fake_run(app, **kwargs):
        called.update({"app": app, **kwargs})

    monkeypatch.setenv("BIGKINDS_RUNTIME", "server")
    monkeypatch.setattr(run_web, "managed_display", lambda runtime: nullcontext())
    monkeypatch.setenv("PORT", "9123")
    monkeypatch.setenv("BIGKINDS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(run_web.uvicorn, "run", fake_run)
    monkeypatch.setattr(run_web, "FRONTEND_INDEX", tmp_path / "index.html")
    (tmp_path / "index.html").write_text("built", encoding="utf-8")
    monkeypatch.setattr(run_web, "SERVER_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(
        run_web, "ensure_frontend_build",
        lambda: pytest.fail("서버 모드는 프런트엔드를 빌드하지 않아야 합니다."),
    )
    monkeypatch.setattr(
        run_web.webbrowser, "open",
        lambda _url: pytest.fail("서버 모드는 브라우저를 열지 않아야 합니다."),
    )

    assert run_web.main([]) == 0

    assert called == {
        "app": "src.api.app:app",
        "host": "0.0.0.0",
        "port": 9123,
        "workers": 1,
    }


def test_server_mode_fails_when_frontend_build_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("BIGKINDS_RUNTIME", "server")
    monkeypatch.setattr(run_web, "FRONTEND_INDEX", tmp_path / "missing" / "index.html")

    with pytest.raises(RuntimeError, match="frontend/dist"):
        run_web.main([])


def test_server_output_log_rotates_when_date_changes(tmp_path):
    current_day = [date(2026, 8, 4)]
    writer = run_web._DailyLogWriter(tmp_path, today=lambda: current_day[0])
    try:
        writer.write("first day\n")
        current_day[0] = date(2026, 8, 5)
        writer.write("second day\n")
    finally:
        writer.close()

    assert (tmp_path / "server_2026-08-04.log").read_text(encoding="utf-8") == "first day\n"
    assert (tmp_path / "server_2026-08-05.log").read_text(encoding="utf-8") == "second day\n"


@pytest.mark.parametrize("port", [0, 65536])
def test_web_runner_rejects_invalid_port(port):
    with pytest.raises(SystemExit, match="1부터 65535"):
        run_web.main(["--no-browser", "--skip-build", "--port", str(port)])
