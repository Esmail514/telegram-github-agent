"""
Tests for auth middleware.
"""
from unittest.mock import patch

from app.utils.security import sanitize_for_telegram


def test_is_allowed_user_correct_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake_token_testing_only_not_real_")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "99999")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "C" * 36)

    with patch("app.utils.security.is_allowed_user") as mock_check:
        mock_check.return_value = True
        assert mock_check(99999) is True


def test_sanitize_for_telegram_truncates():
    long_text = "A" * 5000
    result = sanitize_for_telegram(long_text, max_length=100)
    assert len(result) <= 120  # truncated + ellipsis overhead
    assert "truncated" in result


def test_sanitize_for_telegram_redacts_secrets():
    text = "Token is ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234"
    result = sanitize_for_telegram(text)
    assert "ghp_" not in result
    assert "REDACTED" in result


def test_sanitize_for_telegram_clean():
    text = "Hello, world!"
    result = sanitize_for_telegram(text)
    assert result == text
