"""
/schedule handler — schedule a GitHub Issue to be executed by an agent at a future time.

Flow:
    /schedule → Select Repo → Select Issue → Enter date/time → Confirm → Done
    /schedule list → Show all scheduled jobs (with delete buttons)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.github.client import github_client
from app.github.issues import issue_service
from app.telegram.auth import auth_required
from app.telegram.keyboards import (
    CB_SCHEDULE_DEL,
    cancel_keyboard,
    issues_keyboard,
    repos_keyboard,
    schedule_confirm_keyboard,
    scheduled_jobs_keyboard,
)

logger = logging.getLogger(__name__)

# Conversation states
(
    SCHED_SELECT_REPO,
    SCHED_SELECT_ISSUE,
    SCHED_ENTER_TIME,
    SCHED_CONFIRM,
) = range(20, 24)

_ISSUES_PER_PAGE = 10
_DATE_FORMAT_HINT = "أدخل موعد التنفيذ بالصيغة:\n`DD/MM HH:MM`\nمثال: `23/09 14:00` (التوقيت UTC)"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@auth_required
async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry: /schedule — show repo selector."""
    assert update.message
    await update.message.reply_text("⏳ جاري تحميل المستودعات...")
    return await _show_repo_select(update, context)


async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry from menu:schedule callback."""
    return await _show_repo_select(update, context)


async def _show_repo_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> int:
    try:
        repos = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.list_repos(page=page, per_page=8)
        )
    except Exception as exc:
        logger.error("Failed to list repos for schedule: %s", exc)
        msg = "❌ تعذّر تحميل المستودعات."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return ConversationHandler.END

    keyboard = repos_keyboard(
        repos,
        page=page,
        callback_prefix="sched_repo:",
        page_prefix="sched_repo_page:",
        include_cancel=True,
    )
    text = (
        f"📅 *جدولة Issue — اختيار المستودع* (صفحة {page + 1})\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "اختر المستودع الذي يحتوي على الـ Issue المطلوب جدولته:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return SCHED_SELECT_REPO


# ---------------------------------------------------------------------------
# Repo selected → show issues
# ---------------------------------------------------------------------------

@auth_required
async def sched_repo_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("sched_repo_page:", ""))
    return await _show_repo_select(update, context, page=page)


@auth_required
async def sched_repo_selected_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    full_name = (query.data or "").replace("sched_repo:", "")
    if context.user_data is not None:
        context.user_data["sched_repo"] = full_name

    return await _show_issue_select(update, context, full_name, page=0)


async def _show_issue_select(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    full_name: str,
    page: int = 0,
) -> int:
    try:
        issues = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: issue_service.list_issues(full_name, state="open", page=page, per_page=_ISSUES_PER_PAGE),
        )
    except Exception as exc:
        logger.error("Failed to list issues for schedule: %s", exc)
        if update.callback_query:
            await update.callback_query.edit_message_text("❌ تعذّر تحميل الـ Issues.")
        return ConversationHandler.END

    if not issues:
        msg = f"📋 لا توجد Issues مفتوحة في `{full_name}`."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
        return ConversationHandler.END

    keyboard = issues_keyboard(
        issues, full_name, page=page, include_cancel=True,
        callback_prefix="sched_issue:",
        page_prefix="sched_issue_page:",
    )
    text = f"📅 *جدولة مهمة — اختر Issue من {full_name}:*"
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    return SCHED_SELECT_ISSUE


@auth_required
async def sched_issue_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("sched_issue_page:", ""))
    full_name = (context.user_data or {}).get("sched_repo", "")
    return await _show_issue_select(update, context, full_name, page=page)


@auth_required
async def sched_issue_selected_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    raw = (query.data or "").replace("sched_issue:", "")
    issue_number = int(raw)

    if context.user_data is not None:
        context.user_data["sched_issue"] = issue_number

    await query.edit_message_text(
        f"📅 *جدولة Issue #{issue_number}*\n\n"
        f"{_DATE_FORMAT_HINT}\n\n"
        f"📌 السنة الحالية تُستخدم تلقائياً.\n"
        f"مثال للغد: `23/09 09:00`",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    return SCHED_ENTER_TIME


# ---------------------------------------------------------------------------
# Time input
# ---------------------------------------------------------------------------

@auth_required
async def sched_time_received(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    assert update.message
    text = (update.message.text or "").strip()

    now_utc = datetime.now(UTC)
    try:
        scheduled_dt = _parse_user_time(text, now_utc.year)
    except ValueError:
        await update.message.reply_text(
            f"⚠️ صيغة غير صحيحة.\n\n{_DATE_FORMAT_HINT}",
            parse_mode="Markdown",
        )
        return SCHED_ENTER_TIME

    if scheduled_dt <= now_utc:
        await update.message.reply_text(
            "⚠️ الموعد المدخل في الماضي. أدخل موعداً مستقبلياً.",
        )
        return SCHED_ENTER_TIME

    if context.user_data is not None:
        context.user_data["sched_dt"] = scheduled_dt.isoformat()

    repo = (context.user_data or {}).get("sched_repo", "?")
    issue = (context.user_data or {}).get("sched_issue", "?")
    display = scheduled_dt.strftime("%d/%m/%Y %H:%M UTC")

    await update.message.reply_text(
        f"📅 *تأكيد جدولة الـ Issue*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *المستودع:* `{repo}`\n"
        f"📌 *الـ Issue:* `#{issue}`\n"
        f"⏰ *موعد التشغيل:* `{display}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "هل تود تأكيد الجدولة؟",
        reply_markup=schedule_confirm_keyboard(),
        parse_mode="Markdown",
    )
    return SCHED_CONFIRM


def _parse_user_time(text: str, year: int) -> datetime:
    """Parse 'DD/MM HH:MM' and return a UTC datetime for the given year."""
    try:
        dt = datetime.strptime(f"{text.strip()} {year}", "%d/%m %H:%M %Y")
        return dt.replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"Cannot parse date/time: {text!r}") from exc


# ---------------------------------------------------------------------------
# Confirm → save to DB
# ---------------------------------------------------------------------------

@auth_required
async def sched_confirm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    ud = context.user_data or {}
    repo_full_name = ud.get("sched_repo", "")
    issue_number = ud.get("sched_issue")
    dt_iso = ud.get("sched_dt")

    if not repo_full_name or issue_number is None or not dt_iso:
        await query.edit_message_text("❌ بيانات ناقصة. ابدأ من /schedule مجدداً.")
        return ConversationHandler.END

    scheduled_dt = datetime.fromisoformat(dt_iso)
    chat_id = query.message.chat_id  # type: ignore[union-attr]
    from app.config.settings import settings
    agent = settings.DEFAULT_AGENT

    db = context.bot_data.get("db")
    if db is None:
        await query.edit_message_text("❌ قاعدة البيانات غير متاحة.")
        return ConversationHandler.END

    from app.database.schedule_repository import ScheduleRepository
    sched_repo = ScheduleRepository(db)
    sj = await sched_repo.create(
        repo_full_name=repo_full_name,
        issue_number=issue_number,
        agent=agent,
        scheduled_at=scheduled_dt,
        chat_id=chat_id,
    )

    display = scheduled_dt.strftime("%d/%m/%Y %H:%M UTC")
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    success_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 المهام المجدولة", callback_data="sched_list"),
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu:start"),
        ]
    ])
    await query.edit_message_text(
        f"✅ *تمت الجدولة بنجاح وحفظها في قاعدة البيانات!*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *المستودع:* `{repo_full_name}`\n"
        f"📌 *الـ Issue:* `#{issue_number}`\n"
        f"🕐 *موعد التنفيذ:* `{display}`\n"
        f"🆔 *رقم الجدولة:* `#{sj.id}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💾 سيبدأ الـ Agent تلقائياً عند حلول الموعد ✨",
        reply_markup=success_kb,
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# List & Delete scheduled jobs
# ---------------------------------------------------------------------------

@auth_required
async def schedule_list_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show all scheduled jobs for this chat."""
    db = context.bot_data.get("db")
    if db is None:
        msg = "❌ قاعدة البيانات غير متاحة."
        assert update.message
        await update.message.reply_text(msg)
        return

    from app.database.schedule_repository import ScheduleRepository
    sched_repo = ScheduleRepository(db)
    chat_id = update.effective_chat.id  # type: ignore[union-attr]
    jobs = await sched_repo.list_all(chat_id=chat_id)

    if not jobs:
        assert update.message
        await update.message.reply_text(
            "📅 لا توجد مهام مجدولة حالياً.\n\nاستخدم /schedule لإضافة مهمة جديدة."
        )
        return

    keyboard = scheduled_jobs_keyboard(jobs)
    assert update.message
    await update.message.reply_text(
        f"📅 *المهام المجدولة* ({len(jobs)} مهمة):\n\n"
        "⏳ = قيد الانتظار  |  ✅ = تم التشغيل\n"
        "اضغط 🗑 لحذف مهمة معلقة.",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@auth_required
async def sched_delete_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Delete a pending scheduled job."""
    query = update.callback_query
    assert query
    await query.answer()

    raw = (query.data or "").replace(CB_SCHEDULE_DEL, "")
    job_id = int(raw)

    db = context.bot_data.get("db")
    if db is None:
        await query.edit_message_text("❌ قاعدة البيانات غير متاحة.")
        return

    from app.database.schedule_repository import ScheduleRepository
    sched_repo = ScheduleRepository(db)
    cancelled = await sched_repo.cancel(job_id)

    if cancelled:
        await query.answer(f"✅ تم حذف المهمة #{job_id}", show_alert=True)
    else:
        await query.answer("⚠️ لا يمكن حذف هذه المهمة (ربما تم تشغيلها بالفعل).", show_alert=True)

    # Refresh the list
    chat_id = query.message.chat_id  # type: ignore[union-attr]
    jobs = await sched_repo.list_all(chat_id=chat_id)
    if not jobs:
        await query.edit_message_text(
            "📅 لا توجد مهام مجدولة حالياً.\n\nاستخدم /schedule لإضافة مهمة جديدة."
        )
        return
    await query.edit_message_reply_markup(reply_markup=scheduled_jobs_keyboard(jobs))


# ---------------------------------------------------------------------------
# Conversation handler builder
# ---------------------------------------------------------------------------

async def schedule_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data:
        for k in list(context.user_data.keys()):
            if k.startswith("sched_"):
                context.user_data.pop(k, None)
    if update.callback_query:
        await update.callback_query.answer()
        from app.telegram.handlers.start import WELCOME
        from app.telegram.keyboards import main_menu_keyboard
        await update.callback_query.edit_message_text(
            WELCOME, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
        )
    return ConversationHandler.END


def build_schedule_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("schedule", schedule_command),
            CallbackQueryHandler(schedule_start, pattern="^menu:schedule$"),
        ],
        states={
            SCHED_SELECT_REPO: [
                CallbackQueryHandler(sched_repo_page_callback, pattern="^sched_repo_page:"),
                CallbackQueryHandler(sched_repo_selected_callback, pattern="^sched_repo:"),
            ],
            SCHED_SELECT_ISSUE: [
                CallbackQueryHandler(sched_issue_page_callback, pattern="^sched_issue_page:"),
                CallbackQueryHandler(sched_issue_selected_callback, pattern="^sched_issue:\\d+$"),
            ],
            SCHED_ENTER_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, sched_time_received),
            ],
            SCHED_CONFIRM: [
                CallbackQueryHandler(sched_confirm_callback, pattern="^sched_confirm$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(
                lambda u, c: (u.callback_query.answer() or True) and u.callback_query.edit_message_text("❌ Cancelled."),
                pattern="^cancel$",
            ),
            CallbackQueryHandler(schedule_to_menu, pattern="^menu:start$"),
            CommandHandler("schedule", schedule_command),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
