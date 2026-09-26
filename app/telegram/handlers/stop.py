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

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if executor is None:
        await update.message.reply_text("❌ محرك العمليات غير متصل.")
        return

    active = await executor.get_active_job()
    if active is None:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu:start")]])
        await update.message.reply_text(
            "🛑 *إيقاف العملية | Stop Agent*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💤 لا توجد عملية نشطة حالياً لإيقافها.\n"
            "━━━━━━━━━━━━━━━━━━━━",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        "🛑 *جاري إيقاف الـ Agent...*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *المستودع:* `{active.repo_full_name}`\n"
        f"📌 *الـ Issue:* `#{active.issue_number}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ جاري إنهاء العمليات بأمان...",
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
