"""
Tests for settings validation.
"""
import pytest
from pydantic import ValidationError


def test_settings_requires_github_auth(monkeypatch):
    """Settings should fail if no GitHub credentials are set."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake_token_for_testing_only_not_real")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "12345")
    # Clear any GitHub env vars
    for var in [
        "GITHUB_APP_ID", "GITHUB_INSTALLATION_ID", "GITHUB_PRIVATE_KEY_PATH", "GITHUB_TOKEN"
    ]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises((ValidationError, ValueError)):
        from importlib import reload

        import app.config.settings as m
        reload(m)
        m.Settings(_env_file=None)


def test_settings_accepts_pat(monkeypatch):
    """Settings should succeed with a PAT token."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake_token_for_testing_only_not_real")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "12345")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "A" * 36)
    for var in ["GITHUB_APP_ID", "GITHUB_INSTALLATION_ID", "GITHUB_PRIVATE_KEY_PATH"]:
        monkeypatch.delenv(var, raising=False)

    from app.config.settings import Settings
    s = Settings(_env_file=None)
    assert s.TELEGRAM_ALLOWED_USER_ID == 12345
    assert s.DEFAULT_AGENT == "opencode"


def test_default_values(monkeypatch):
    """Check default values for optional settings."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake_token_for_testing_only_not_real")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "99999")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "B" * 36)
    monkeypatch.setenv("DEFAULT_AGENT", "opencode")

    from app.config.settings import Settings
    s = Settings(_env_file=None)
    assert s.MAX_AGENT_RUNTIME_MINUTES == 60
    assert s.MAX_FIX_ITERATIONS == 5
    assert s.LOG_LEVEL == "INFO"
    assert s.OPENCODE_COMMAND == "opencode"
