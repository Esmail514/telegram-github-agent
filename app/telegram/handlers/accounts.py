"""
Antigravity Accounts Handler.

Allows operators to switch between multiple Google Antigravity account profiles,
save current active sessions, and resume paused/token-exhausted tasks seamlessly.
"""
from __future__ import annotations

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.agents.antigravity_account_manager import AntigravityAccountManager
from app.database.repository import JobRepository
from app.runner.recovery import is_resumable, is_token_limit
from app.telegram.auth import auth_required
from app.telegram.keyboards import accounts_keyboard, main_menu_button
from app.utils.security import sanitize_for_telegram

logger = logging.getLogger(__name__)


async def _find_paused_token_job(db: Any) -> Any:
    """Finds the most recent job that was paused due to token exhaustion."""
    repo = JobRepository(db)
    recent = await repo.list_recent_jobs(limit=10)
    for job in recent:
        if is_token_limit(job):
            return job
    return None


@auth_required
async def accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /accounts command."""
    db = context.bot_data.get("db")
    mgr = AntigravityAccountManager()
    profiles = mgr.list_profiles()
    active = mgr.get_active_profile()

    paused_job = await _find_paused_token_job(db) if db else None
    paused_job_id = paused_job.job_id if paused_job else None

    text = (
        "👥 *إدارة حسابات Antigravity CLI*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"الحساب النشط حالياً: *{active or 'جلسة غير محفوظة'}*\n\n"
    )

    if paused_job:
        text += (
            "⚠️ *يوجد عمل متوقف بسبب نفاد الـ Tokens!*\n"
            f"📦 المستودع: `{paused_job.repo_full_name}`\n"
            f"📌 المهمة: #{paused_job.issue_number}\n\n"
            "اختر حساباً جديداً للتبديل إليه واستئناف العمل من حيث توقف دون إعادة العمل من الصفر.\n\n"
        )
    else:
        text += "اختر الحساب المطلوب للتبديل إليه فوراً:\n\n"

    kb = accounts_keyboard(profiles, paused_job_id=paused_job_id)
    if update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")


@auth_required
async def accounts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles callbacks for accounts menu, switching, and resuming."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    data = query.data
    db = context.bot_data.get("db")
    executor = context.bot_data.get("executor")
    mgr = AntigravityAccountManager()

    if data == "menu:accounts":
        profiles = mgr.list_profiles()
        active = mgr.get_active_profile()
        paused_job = await _find_paused_token_job(db) if db else None
        paused_job_id = paused_job.job_id if paused_job else None

        text = (
            "👥 *إدارة حسابات Antigravity CLI*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"الحساب النشط حالياً: *{active or 'جلسة غير محفوظة'}*\n\n"
        )
        if paused_job:
            text += (
                "⚠️ *يوجد عمل متوقف بسبب نفاد الـ Tokens!*\n"
                f"📦 المستودع: `{paused_job.repo_full_name}`\n"
                f"📌 المهمة: #{paused_job.issue_number}\n\n"
                "اختر حساباً للتبديل واستئناف العمل فوراً:\n\n"
            )
        else:
            text += "اختر الحساب المطلوب للتبديل إليه:\n\n"

        kb = accounts_keyboard(profiles, paused_job_id=paused_job_id)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("acc_switch:"):
        target_name = data.split(":", 1)[1]
        ok = mgr.activate_profile(target_name)
        if not ok:
            await query.edit_message_text(
                f"❌ فشل التبديل إلى الحساب `{target_name}`. تأكد من سلامة ملف التعريف.",
                reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
                parse_mode="Markdown",
            )
            return

        paused_job = await _find_paused_token_job(db) if db else None
        if paused_job:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ استئناف العمل فوراً بالحساب الجديد", callback_data=f"acc_resume:{paused_job.job_id}")],
                [InlineKeyboardButton("👥 قائمة الحسابات", callback_data="menu:accounts")],
                [main_menu_button()],
            ])
            await query.edit_message_text(
                f"✅ *تم التبديل بنجاح إلى الحساب:* `{target_name}`\n\n"
                f"📌 يوجد عمل معلق بسبب نفاد التوكنز:\n"
                f"📦 *المستودع:* `{paused_job.repo_full_name}`\n"
                f"📌 *المهمة:* #{paused_job.issue_number}\n\n"
                "اضغط أدناه لاستئناف العمل ليكمل الـ Agent التعديل من حيث توقف مباشرة.",
                reply_markup=kb,
                parse_mode="Markdown",
            )
        else:
            profiles = mgr.list_profiles()
            kb = accounts_keyboard(profiles, paused_job_id=None)
            await query.edit_message_text(
                f"✅ *تم التبديل بنجاح إلى الحساب:* `{target_name}`\n\n"
                "ستعمل جميع مهام Antigravity القادمة بهذا الحساب تلقائياً.",
                reply_markup=kb,
                parse_mode="Markdown",
            )

    elif data.startswith("acc_resume:"):
        job_id = data.split(":", 1)[1]
        if not executor:
            await query.edit_message_text("❌ نظام تشغيل المهام (Job Executor) غير مهيأ.")
            return

        chat_id = update.effective_chat.id if update.effective_chat else 0

        async def notify(msg: str, reply_markup=None) -> None:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=sanitize_for_telegram(msg),
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )
            except Exception as e:
                logger.warning("Failed to send Telegram notification: %s", e)

        try:
            await executor.resume_job(job_id, notify_fn=notify)
            await query.edit_message_text(
                f"🚀 *تم استئناف المهمة `{job_id}` بنجاح!*\n\n"
                "يقوم الـ Agent الآن بمراجعة التعديلات المتبقية ومتابعة العمل...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 متابعة الحالة", callback_data="menu:status")],
                    [main_menu_button()],
                ]),
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error("Failed to resume job %s: %s", job_id, exc)
            await query.edit_message_text(
                f"❌ تعذر استئناف المهمة: {exc}",
                reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
            )

    elif data == "acc_save":
        active = mgr.get_active_profile()
        help_text = (
            "💾 *حفظ الجلسة الحالية كـ Profile*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"الجلسة الحالية في ويندوز: *{active or 'جلسة غير محفوظة'}*\n\n"
            "لحفظ هذه الجلسة وتسميتها، أرسل الأمر التالي في المحادثة:\n"
            "`/saveaccount <اسم_الحساب>`\n\n"
            "أمثلة:\n"
            "• `/saveaccount work`\n"
            "• `/saveaccount personal`\n"
            "• `/saveaccount account2`\n"
        )
        await query.edit_message_text(
            help_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 العودة للحسابات", callback_data="menu:accounts")],
                [main_menu_button()],
            ]),
            parse_mode="Markdown",
        )


@auth_required
async def saveaccount_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /saveaccount <name> command."""
    if not update.message or not update.message.text:
        return

    parts = update.message.text.strip().split()
    if len(parts) < 2:
        await update.message.reply_text(
            "⚠️ يرجى تحديد اسم للحساب.\n"
            "الاستخدام:\n`/saveaccount <اسم_الحساب>`\n\n"
            "مثال: `/saveaccount work`",
            parse_mode="Markdown",
        )
        return

    name = parts[1].strip()
    mgr = AntigravityAccountManager()
    ok = mgr.save_current_profile(name)

    if ok:
        await update.message.reply_text(
            f"✅ تم حفظ جلسة Antigravity الحالية بنجاح باسم:\n`{name}`\n\n"
            "يمكنك الآن التبديل بين الحسابات في أي وقت عبر الأمر `/accounts` أو من القائمة الرئيسية.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 عرض الحسابات", callback_data="menu:accounts")],
                [main_menu_button()],
            ]),
        )
    else:
        await update.message.reply_text(
            "❌ تعذر قراءة بيانات الجلسة من Windows Credential Manager.\n"
            "تأكد من أنك قمت بتسجيل الدخول في Antigravity CLI (`agy`) أولاً.",
        )


@auth_required
async def deleteaccount_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /deleteaccount <name> command."""
    if not update.message or not update.message.text:
        return

    parts = update.message.text.strip().split()
    if len(parts) < 2:
        await update.message.reply_text(
            "⚠️ يرجى تحديد اسم الحساب المراد حذفه.\n"
            "الاستخدام:\n`/deleteaccount <اسم_الحساب>`\n\n"
            "مثال: `/deleteaccount account2`",
            parse_mode="Markdown",
        )
        return

    name = parts[1].strip()
    mgr = AntigravityAccountManager()
    ok = mgr.delete_profile(name)

    if ok:
        await update.message.reply_text(
            f"🗑️ تم حذف ملف الحساب `{name}` بنجاح.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 عرض الحسابات", callback_data="menu:accounts")],
                [main_menu_button()],
            ]),
        )
    else:
        await update.message.reply_text(
            f"❌ لم يتم العثور على الحساب `{name}`.\n"
            "تأكد من كتابة الاسم بشكل صحيح أو استعرض الحسابات عبر `/accounts`.",
            parse_mode="Markdown",
        )


@auth_required
async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /resume [job_id] command."""
    if not update.message:
        return

    db = context.bot_data.get("db")
    executor = context.bot_data.get("executor")
    if not db or not executor:
        await update.message.reply_text("❌ نظام تشغيل المهام غير مهيأ.")
        return

    parts = (update.message.text or "").strip().split()
    repo = JobRepository(db)

    target_job_id = None
    if len(parts) >= 2:
        target_job_id = parts[1].strip()
    else:
        # Auto-detect latest resumable job
        recent = await repo.list_recent_jobs(limit=10)
        for job in recent:
            if is_resumable(job):
                target_job_id = job.job_id
                break

    if not target_job_id:
        await update.message.reply_text(
            "ℹ️ لا توجد مهام معلقة قابلة للاستئناف حالياً.",
        )
        return

    chat_id = update.effective_chat.id if update.effective_chat else 0

    async def notify(msg: str, reply_markup=None) -> None:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=sanitize_for_telegram(msg),
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning("Failed to send Telegram notification: %s", e)

    try:
        await executor.resume_job(target_job_id, notify_fn=notify)
        await update.message.reply_text(
            f"🔄 جاري استئناف المهمة `{target_job_id}`...",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("Failed to resume job: %s", exc)
        await update.message.reply_text(f"❌ فشل استئناف المهمة: {exc}")
