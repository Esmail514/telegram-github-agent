"""
Regression tests for photo and media uploading across Telegram handlers.
Verifies that photo uploads never hang, never get silently ignored,
and are properly saved or routed to prompts.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.telegram.handlers import newissue, personal, schedule


@pytest.mark.asyncio
async def test_personal_incoming_photo_handled_when_personal_mode_disabled():
    update = SimpleNamespace(
        message=SimpleNamespace(
            photo=[SimpleNamespace(file_id="photo123")],
            caption="Analyze this diagram",
            document=None,
            reply_text=AsyncMock(),
        ),
        effective_user=SimpleNamespace(id=123456789, username=None),
    )
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    # When personal mode is disabled, bot must reply with explanation instead of staying silent
    await personal.incoming_file_handler(update, context)

    update.message.reply_text.assert_called_once()
    reply_args = update.message.reply_text.call_args[0][0]
    assert "تم استلام الصورة" in reply_args


@pytest.mark.asyncio
async def test_personal_incoming_photo_saved_when_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(personal, "_check_personal_guard", lambda: (True, ""))
    saved_path = tmp_path / "photo_test.jpg"
    monkeypatch.setattr(personal, "save_incoming_file", lambda **kwargs: (True, "saved", saved_path))

    status_msg = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(
        message=SimpleNamespace(
            photo=[SimpleNamespace(file_id="photo456")],
            caption="Fix bug in this screen",
            document=None,
            reply_text=AsyncMock(return_value=status_msg),
        ),
        effective_user=SimpleNamespace(id=123456789, username=None),
    )
    file_mock = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"fake-image-bytes"))
    context = SimpleNamespace(
        user_data={},
        bot=SimpleNamespace(get_file=AsyncMock(return_value=file_mock)),
    )

    await personal.incoming_file_handler(update, context)

    status_msg.edit_text.assert_called_once()
    reply_text = status_msg.edit_text.call_args[0][0]
    assert "تم حفظ الصورة بنجاح على الجهاز" in reply_text
    assert "Fix bug in this screen" in reply_text


@pytest.mark.asyncio
async def test_schedule_prompt_receives_photo(tmp_path: Path):
    project_dir = tmp_path / "my_project"
    project_dir.mkdir()

    status_msg = SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())
    update = SimpleNamespace(
        message=SimpleNamespace(
            photo=[SimpleNamespace(file_id="photo789")],
            caption="Make UI match this screenshot",
            document=None,
            reply_text=AsyncMock(return_value=status_msg),
        ),
        effective_user=SimpleNamespace(id=123456789, username=None),
        callback_query=None,
    )
    file_mock = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"fake-image-data"))
    context = SimpleNamespace(
        user_data={
            "sched_mode": "local",
            "sched_project_name": "my_project",
            "sched_project_path": str(project_dir),
        },
        bot_data={},
        bot=SimpleNamespace(get_file=AsyncMock(return_value=file_mock)),
    )

    state = await schedule.sched_prompt_received(update, context)

    assert state == schedule.SCHED_ENTER_TIME
    assert "sched_prompt" in context.user_data
    assert "Make UI match this screenshot" in context.user_data["sched_prompt"]
    assert ".agent_assets" in context.user_data["sched_prompt"]
    assets_dir = project_dir / ".agent_assets"
    assert assets_dir.exists()
    assert any(assets_dir.glob("task_*.jpg"))


@pytest.mark.asyncio
async def test_newissue_photo_upload_timeout_fails_safely_without_hang(monkeypatch: pytest.MonkeyPatch):
    status_msg = SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())
    update = SimpleNamespace(
        message=SimpleNamespace(
            photo=[SimpleNamespace(file_id="slow_photo")],
            caption="",
            document=None,
            reply_text=AsyncMock(return_value=status_msg),
        ),
        effective_user=SimpleNamespace(id=123456789, username=None),
    )

    # Simulate get_file hanging / timing out
    async def slow_get_file(*args, **kwargs):
        raise TimeoutError("Simulated connection timeout")

    context = SimpleNamespace(
        user_data={"ni_repo": "owner/repo"},
        bot=SimpleNamespace(get_file=slow_get_file),
    )

    next_state = await newissue.ni_enter_body(update, context)

    # Must stay on NI_ENTER_BODY and notify user immediately rather than hanging
    assert next_state == newissue.NI_ENTER_BODY
    status_msg.edit_text.assert_called_once()
    assert "فشل رفع الصورة" in status_msg.edit_text.call_args[0][0]
