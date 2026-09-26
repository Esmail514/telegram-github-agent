"""
Global test configuration and fixtures.
"""
import tempfile
from pathlib import Path

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


@pytest.fixture
async def temp_db():
    """Fresh SQLite database on a temp path with a reset test singleton."""
    from app.database.repository import Database

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        Database._instance = None
        db = await Database.create(db_path)
        yield db
        await db.close()
        Database._instance = None
