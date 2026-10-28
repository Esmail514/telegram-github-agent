import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.database.repository import JobRepository, JobStatus
from app.runner.recovery import is_interrupted


@pytest.mark.asyncio
async def test_startup_recovers_before_polling_and_scheduler(temp_db, tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.database.repository import Database
    from app.main import _async_main
    from app.runner.scheduler import job_scheduler

    repo = JobRepository(temp_db)
    await repo.create_job("orphan", "owner/repo", 1, "ai/issue-1", "codex", 123)
    await repo.set_status("orphan", JobStatus.IMPLEMENTING)
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(settings, "NOTIFY_ON_STARTUP", False)
    monkeypatch.setattr(settings, "NOTIFY_ON_SHUTDOWN", False)
    monkeypatch.setattr(Database, "create", AsyncMock(return_value=temp_db))

    async def check_recovered(**kwargs):
        assert await repo.get_active_job() is None
        assert is_interrupted(await repo.get_job("orphan"))

    app = SimpleNamespace(
        bot_data={}, bot=Mock(), initialize=AsyncMock(), start=AsyncMock(),
        stop=AsyncMock(), shutdown=AsyncMock(),
        updater=SimpleNamespace(start_polling=AsyncMock(side_effect=check_recovered), stop=AsyncMock()),
    )
    monkeypatch.setattr("app.telegram.bot.build_application", Mock(return_value=app))
    monkeypatch.setattr(job_scheduler, "start", Mock())
    monkeypatch.setattr(job_scheduler, "stop", Mock())
    def trigger_shutdown(sig, callback):
        if sig == signal.SIGINT and getattr(callback, "__name__", "") == "_on_signal":
            callback()
    monkeypatch.setattr(signal, "signal", trigger_shutdown)

    assert await _async_main() is False
    app.updater.start_polling.assert_awaited_once()
    job_scheduler.start.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["recovery", "build", "initialize", "start"])
async def test_startup_failure_releases_database(temp_db, tmp_path, monkeypatch, failure):
    from app.config.settings import settings
    from app.database.repository import Database
    from app.main import _async_main

    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(Database, "create", AsyncMock(return_value=temp_db))
    close = AsyncMock(wraps=temp_db.close)
    monkeypatch.setattr(temp_db, "close", close)
    app = SimpleNamespace(
        bot_data={}, initialize=AsyncMock(), start=AsyncMock(), stop=AsyncMock(),
        shutdown=AsyncMock(), updater=SimpleNamespace(running=False),
    )
    builder = Mock(return_value=app)
    if failure == "recovery":
        monkeypatch.setattr(
            "app.runner.recovery.RecoveryService.recover",
            AsyncMock(side_effect=RuntimeError("startup failed")),
        )
    elif failure == "build":
        builder.side_effect = RuntimeError("startup failed")
    else:
        getattr(app, failure).side_effect = RuntimeError("startup failed")
    monkeypatch.setattr("app.telegram.bot.build_application", builder)

    with pytest.raises(RuntimeError, match="startup failed"):
        await _async_main()
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_notification_uses_persistent_six_button_keyboard(temp_db, tmp_path, monkeypatch):
    from telegram import ReplyKeyboardMarkup

    from app.config.settings import settings
    from app.database.repository import Database
    from app.main import _async_main
    from app.runner.scheduler import job_scheduler
    from app.telegram.keyboards import main_menu_keyboard

    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(settings, "NOTIFY_ON_STARTUP", True)
    monkeypatch.setattr(settings, "NOTIFY_ON_SHUTDOWN", False)
    monkeypatch.setattr(Database, "create", AsyncMock(return_value=temp_db))
    app = SimpleNamespace(
        bot_data={}, bot=SimpleNamespace(send_message=AsyncMock()), initialize=AsyncMock(),
        start=AsyncMock(), stop=AsyncMock(), shutdown=AsyncMock(),
        updater=SimpleNamespace(start_polling=AsyncMock(), stop=AsyncMock()),
    )
    monkeypatch.setattr("app.telegram.bot.build_application", Mock(return_value=app))
    monkeypatch.setattr(job_scheduler, "start", Mock())

    def trigger_shutdown(sig, callback):
        if sig == signal.SIGINT and getattr(callback, "__name__", "") == "_on_signal":
            callback()

    monkeypatch.setattr(signal, "signal", trigger_shutdown)
    await _async_main()
    keyboard = app.bot.send_message.call_args.kwargs["reply_markup"]
    assert isinstance(keyboard, ReplyKeyboardMarkup)
    assert keyboard.to_dict() == main_menu_keyboard().to_dict()
    assert sum(len(row) for row in keyboard.keyboard) == 6
