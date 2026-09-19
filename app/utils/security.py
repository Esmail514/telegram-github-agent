"""
General security utilities.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def sanitize_for_telegram(text: str, max_length: int = 4096) -> str:
    """
    Sanitize text before sending to Telegram:
    - Truncate to Telegram's message length limit.
    - Remove any accidentally included secret-like patterns.
    """
    from app.utils.secrets import _redact_line
    text = _redact_line(text)
    if len(text) > max_length:
        text = text[: max_length - 20] + "\n\n...[truncated]"
    return text


def is_allowed_user(user_id: int) -> bool:
    """Return True if the user is in the allow-list."""
    from app.config.settings import settings
    return user_id == settings.TELEGRAM_ALLOWED_USER_ID
