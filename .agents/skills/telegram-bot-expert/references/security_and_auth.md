# Telegram Bot Security & Authorization Guide

## 1. Single-User Authorization
To prevent unauthorized users from interacting with the bot (which executes code and Git commands):

```python
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from app.config.settings import settings

def auth_required(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or user.id != settings.TELEGRAM_ALLOWED_USER_ID:
            if update.callback_query:
                await update.callback_query.answer("⛔ Unauthorized access.", show_alert=True)
            elif update.message:
                await update.message.reply_text("⛔ Unauthorized access.")
            return None
        return await func(update, context, *args, **kwargs)
    return wrapper
```

## 2. Token Masking in Logs
Always type sensitive tokens (bot tokens, PATs, API keys) as `pydantic.SecretStr`:
- Masked automatically when printed or logged (`'**********'`).
- Retrieved explicitly via `.get_secret_value()` only at the point of making an external request.
