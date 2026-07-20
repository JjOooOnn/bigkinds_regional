from src.logging_utils import sanitize


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
