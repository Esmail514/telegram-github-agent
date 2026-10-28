from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.telegram.handlers import personal


@pytest.mark.asyncio
async def test_ls_denies_sibling_prefix_outside_allowed_root(tmp_path, monkeypatch):
    allowed = tmp_path / "Allowed"
    denied = tmp_path / "Allowed-Private"
    allowed.mkdir()
    denied.mkdir()
    (denied / "private-name.txt").write_text("private", encoding="utf-8")
    monkeypatch.setenv("PERSONAL_ALLOWED_DIRS", str(allowed))
    monkeypatch.setattr(personal, "_check_personal_guard", lambda: (True, ""))
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=123456789, username=None))

    await personal.ls_handler(update, SimpleNamespace(args=[str(denied)]))

    text = message.reply_text.call_args.args[0]
    assert "غير مسموح" in text
    assert "private-name" not in text


@pytest.mark.asyncio
async def test_ls_preserves_regular_listing_inside_allowed_root(tmp_path, monkeypatch):
    allowed = tmp_path / "Allowed"
    allowed.mkdir()
    (allowed / "data.txt").write_text("safe", encoding="utf-8")
    monkeypatch.setenv("PERSONAL_ALLOWED_DIRS", str(allowed))
    monkeypatch.setattr(personal, "_check_personal_guard", lambda: (True, ""))
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=123456789, username=None))

    await personal.ls_handler(update, SimpleNamespace(args=[str(allowed)]))

    assert "data.txt" in message.reply_text.call_args.args[0]
