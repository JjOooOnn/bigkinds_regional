from __future__ import annotations

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
