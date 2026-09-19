"""
Telegram authentication middleware.

Only the configured TELEGRAM_ALLOWED_USER_ID may interact with the bot.
Unauthorized requests are silently dropped (no reply leaks bot existence).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from app.utils.security import is_allowed_user

logger = logging.getLogger(__name__)


def auth_required(
    func: Callable[..., Coroutine[Any, Any, Any]]
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """
    Decorator for Telegram handler functions.
    Drops requests from users not in the allow-list.
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        user = update.effective_user
        if user is None:
            return None
        if not is_allowed_user(user.id):
            logger.warning(
                "Unauthorized access attempt from user_id=%d username=%s",
                user.id,
                user.username or "<unknown>",
            )
            return None  # Silently ignore
        return await func(update, context)

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper
