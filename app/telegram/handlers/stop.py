"""
/stop handler — gracefully stop the running agent job.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.telegram.auth import auth_required
from app.utils.security import sanitize_for_telegram

logger = logging.getLogger(__name__)


@auth_required
async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    executor = context.bot_data.get("executor")

    if executor is None:
        await update.message.reply_text("❌ Job executor not initialised.")
        return

    active = await executor.get_active_job()
    if active is None:
        await update.message.reply_text("📊 No active agent job to stop.")
        return

    await update.message.reply_text(
        f"🛑 Stopping agent...\n\n"
        f"Repository: `{active.repo_full_name}`\n"
        f"Issue: #{active.issue_number}",
        parse_mode="Markdown",
    )

    chat_id = update.message.chat_id

    async def notify(msg: str) -> None:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=sanitize_for_telegram(msg),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Notify failed in stop: %s", e)

    await executor.stop_active_job(notify_fn=notify)
