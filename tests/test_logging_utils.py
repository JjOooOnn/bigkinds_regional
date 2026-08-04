from io import StringIO

import pytest

from src.logging_utils import configure_lifecycle_logging, log_lifecycle_event, sanitize


def test_url_credentials_and_sensitive_query_values_are_masked():
    raw = (
        "https://user:secret@example.com/a?sessionid=abc123&access_token=token123&ok=1"
    )
    cleaned = sanitize(raw)
    assert "user:secret" not in cleaned
    assert "abc123" not in cleaned
    assert "token123" not in cleaned
    assert "[마스킹]" in cleaned
    assert "ok=1" in cleaned


def test_lifecycle_log_uses_timestamp_and_masks_secrets():
    stream = StringIO()
    logger = configure_lifecycle_logging(stream)
    log_lifecycle_event(
        logger, "worker", "started",
        pid=123, authorization="Authorization: Bearer secret-value",
    )
    text = stream.getvalue()
    assert text.startswith("LIFECYCLE ")
    assert '"timestamp":' in text
    assert '"component": "worker"' in text
    assert "secret-value" not in text
    assert "[마스킹]" in text


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("api_key=supersecret", "supersecret"),
        ("password: supersecret", "supersecret"),
        ('{"api_key": "json-secret"}', "json-secret"),
        ('{"password":"json-password"}', "json-password"),
        ('{"password":"secret with spaces"}', "with spaces"),
        ("api_key='quoted secret value'", "secret value"),
        ("Bearer bearer-secret", "bearer-secret"),
    ],
)
def test_common_secret_formats_are_masked(raw, secret):
    cleaned = sanitize(raw)
    assert secret not in cleaned
    assert "[마스킹]" in cleaned
