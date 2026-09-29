from pathlib import Path

import pytest

from src.config import ROOT_DIR, load_runtime_config


def test_runtime_defaults_preserve_local_settings_and_paths():
    runtime = load_runtime_config({})

    assert runtime.mode == "local"
    assert runtime.host == "127.0.0.1"
    assert runtime.port == 8000
    assert runtime.open_browser is True
    assert runtime.data_dir == ROOT_DIR


def test_local_mode_ignores_railway_port():
    runtime = load_runtime_config({"PORT": "9123"})

    assert runtime.host == "127.0.0.1"
    assert runtime.port == 8000


def test_server_mode_uses_platform_host_port_and_data_directory():
    runtime = load_runtime_config({
        "BIGKINDS_RUNTIME": "server",
        "PORT": "9123",
        "BIGKINDS_DATA_DIR": "/data",
    })

    assert runtime.mode == "server"
    assert runtime.host == "0.0.0.0"
    assert runtime.port == 9123
    assert runtime.open_browser is False
    assert runtime.data_dir == Path("/data")
    assert runtime.work_dir == Path("/data/work")
    assert runtime.output_dir == Path("/data/output")
    assert runtime.screenshot_dir == Path("/data/artifacts/screenshots")
    assert runtime.trace_dir == Path("/data/artifacts/traces")


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({"BIGKINDS_RUNTIME": "railway"}, "BIGKINDS_RUNTIME"),
        ({"BIGKINDS_RUNTIME": "server", "PORT": "not-a-port"}, "PORT"),
        ({"BIGKINDS_RUNTIME": "server", "PORT": "0"}, "PORT"),
        ({"BIGKINDS_RUNTIME": "server", "PORT": "65536"}, "PORT"),
    ],
)
def test_invalid_runtime_settings_are_rejected(environ, message):
    with pytest.raises(ValueError, match=message):
        load_runtime_config(environ)
