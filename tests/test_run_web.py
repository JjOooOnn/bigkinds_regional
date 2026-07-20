from __future__ import annotations

import pytest

import run_web


def test_web_runner_binds_only_to_loopback(monkeypatch):
    called = {}

    def fake_run(app, **kwargs):
        called.update({"app": app, **kwargs})

    monkeypatch.setattr(run_web.uvicorn, "run", fake_run)
    assert run_web.main(["--no-browser", "--skip-build", "--port", "8123"]) == 0
    assert called["app"] == "src.api.app:app"
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 8123
    assert called["workers"] == 1


@pytest.mark.parametrize("port", [0, 65536])
def test_web_runner_rejects_invalid_port(port):
    with pytest.raises(SystemExit, match="1부터 65535"):
        run_web.main(["--no-browser", "--skip-build", "--port", str(port)])
