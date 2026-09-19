"""
Global test configuration and fixtures.
"""
import pytest

from app.config import settings as settings_module


@pytest.fixture(autouse=True)
def default_env(monkeypatch):
    """Set mock environment variables so settings can be instantiated during tests."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123456789")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_mocktokenforautomatedtestingsuite00000")
    monkeypatch.setenv("DEFAULT_AGENT", "opencode")
    settings_module._settings_instance = None
    yield
    settings_module._settings_instance = None
