"""
/status handler — show current or last agent job status.

Includes inline buttons:
  🛑 Stop Job   (only when a job is active)
  🔄 Refresh    (re-fetch and update the status message in-place)
  🏠 Main Menu
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.config.settings import settings
from app.database.repository import JobStatus
from app.telegram.auth import auth_required
from app.telegram.keyboards import status_keyboard
from app.utils.security import sanitize_for_telegram

logger = logging.getLogger(__name__)

_STATUS_EMOJI = {
    JobStatus.QUEUED: "⏳",
    JobStatus.CLONING: "📥",
    JobStatus.INSPECTING: "🔍",
    JobStatus.PLANNING: "📝",
    JobStatus.IMPLEMENTING: "🛠",
    JobStatus.TESTING: "🧪",
    JobStatus.FIXING: "🔧",
    JobStatus.COMMITTING: "📦",
    JobStatus.PUSHING: "⬆️",
    JobStatus.CREATING_PR: "🔀",
    JobStatus.COMPLETED: "✅",
    JobStatus.FAILED: "❌",
    JobStatus.STOPPED: "🛑",
}

_ACTIVE_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.CLONING,
    JobStatus.INSPECTING,
    JobStatus.PLANNING,
    JobStatus.IMPLEMENTING,
    JobStatus.TESTING,
    JobStatus.FIXING,
    JobStatus.COMMITTING,
    JobStatus.PUSHING,
    JobStatus.CREATING_PR,
}


def _build_status_text(job) -> tuple[str, bool]:
    """Return (message_text, is_active)."""
    is_active = job.status in _ACTIVE_STATUSES
    emoji = _STATUS_EMOJI.get(job.status, "❓")
    elapsed = job.elapsed_display() if job.started_at else "—"

    pr_line = ""
    if job.pr_url:
        pr_line = f"\n🔀 *Pull Request:* [#{job.pr_number}]({job.pr_url})"

    error_line = ""
    if job.error:
        error_line = f"\n⚠️ *الخطأ:* `{job.error[:200]}`"

    text = (
        f"{emoji} *حالة الـ Agent | Agent Status*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *العملية:* `{job.job_id}`\n"
        f"📦 *المستودع:* `{job.repo_full_name}`\n"
        f"📌 *الـ Issue:* `#{job.issue_number}`\n"
        f"🌿 *الفرع:* `{job.branch}`\n"
        f"⚡️ *الحالة:* `{job.status}`\n"
        f"🔄 *المرحلة:* `{job.current_phase or '—'}`\n"
        f"⏱ *الوقت:* `{elapsed}`\n"
        f"📝 *الملفات:* `{job.files_changed}`\n"
        f"🔧 *محاولات الإصلاح:* `{job.fix_iterations}`"
        f"{pr_line}"
        f"{error_line}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    return text, is_active


async def _get_job(context: ContextTypes.DEFAULT_TYPE):
    """Return active job, or most recent job if no active one."""
    executor = context.bot_data.get("executor")
    if executor is None:
        return None

    job = await executor.get_active_job()
    if job is None:
        from app.database.repository import JobRepository
        db = context.bot_data.get("db")
        if db:
            repo = JobRepository(db)
            recent = await repo.list_recent_jobs(limit=1)
            job = recent[0] if recent else None
    return job


@auth_required
async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: /status command or menu:status callback."""
    job = await _get_job(context)

    if job is None:
        msg = (
            "📊 *حالة الـ Agent | Agent Status*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💤 لا توجد عمليات جارية حالياً.\n\n"
            "💡 يمكنك بدء تشغيل عملية جديدة عبر /run أو القائمة الرئيسية.\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        keyboard = status_keyboard(is_active=False)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(msg, reply_markup=keyboard)
        elif update.message:
            await update.message.reply_text(msg, reply_markup=keyboard)
        return

    text, is_active = _build_status_text(job)
    keyboard = status_keyboard(is_active=is_active)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )


@auth_required
async def status_refresh_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """🔄 Refresh — re-fetch and edit the status message in-place."""
    query = update.callback_query
    assert query
    await query.answer("Refreshed ✅")

    job = await _get_job(context)
    if job is None:
        await query.edit_message_text(
            "📊 No agent jobs found.\n\nUse /run to start one.",
            reply_markup=status_keyboard(is_active=False),
        )
        return

    text, is_active = _build_status_text(job)
    await query.edit_message_text(
        text, reply_markup=status_keyboard(is_active=is_active), parse_mode="Markdown"
    )


@auth_required
async def stop_job_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """🛑 Stop Job — cancel the running agent and update the message."""
    query = update.callback_query
    assert query
    await query.answer("Stopping…")

    executor = context.bot_data.get("executor")
    if executor is None:
        await query.edit_message_text("❌ Job executor not initialised.")
        return

    active = await executor.get_active_job()
    if active is None:
        await query.edit_message_text(
            "📊 No active job to stop.",
            reply_markup=status_keyboard(is_active=False),
        )
        return

    chat_id = query.message.chat_id if query.message else 0

    async def notify(msg: str) -> None:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=sanitize_for_telegram(msg),
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.warning("Notify failed in stop_job_callback: %s", exc)

    job = await executor.stop_active_job(notify_fn=notify)

    if job:
        text, is_active = _build_status_text(job)
        await query.edit_message_text(
            text, reply_markup=status_keyboard(is_active=False), parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            "🛑 Job stopped.", reply_markup=status_keyboard(is_active=False)
        )


@auth_required
async def sysinfo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sysinfo — display platform details, resource usage, and workspace info."""
    from app.telegram.keyboards import sysinfo_keyboard

    text = _build_sysinfo_text()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=sysinfo_keyboard(), parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=sysinfo_keyboard(), parse_mode="Markdown"
        )


def _build_sysinfo_text() -> str:
    """Build the sysinfo message text with live resource data."""
    from app.config.settings import get_platform_info
    from app.utils.sysmonitor import get_resource_snapshot

    info = get_platform_info()
    snap = get_resource_snapshot()
    workspace = settings.WORKSPACE_DIR.resolve()
    workspace_exists = workspace.exists()
    workspace_icon = "✅" if workspace_exists else "⚠️"

    os_emoji = {
        "Windows": "🪟",
        "Darwin": "🍎",
        "Linux": "🐧",
    }.get(info["os"], "💻")

    # ── Platform block ──────────────────────────────────────────────────
    lines = [
        f"{os_emoji} *معلومات النظام | System Info*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"*OS:* `{info['os']} {info['os_release']}`",
        f"*Platform:* `{info['platform']}`",
        f"*Python:* `{info['python']}`",
        "",
    ]

    if snap.get("psutil_available"):
        ram = snap.get("ram", {})
        cpu = snap.get("cpu", {})
        disk_root = snap.get("disk_root", {})
        disk_ws = snap.get("disk_workspace", {})

        def _pbar(pct: float) -> str:
            filled = int(pct / 10)
            return "█" * filled + "░" * (10 - filled)

        # ── RAM ──────────────────────────────────────────────────────────
        ram_pct = ram.get("percent", 0.0)
        lines += [
            "🧠 *الذاكرة العشوائية (RAM)*",
            f"`{_pbar(ram_pct)}` {ram_pct}%",
            f"• المستخدم: `{ram.get('used_gb', 0)} GB` / الإجمالي: `{ram.get('total_gb', 0)} GB`",
            f"• المتاح: `{ram.get('available_gb', 0)} GB`",
            "",
        ]

        # ── CPU ──────────────────────────────────────────────────────────
        cpu_pct = cpu.get("percent", 0.0)
        lines += [
            f"⚡ *المعالج (CPU)* — {cpu.get('count', '?')} نواة",
            f"`{_pbar(cpu_pct)}` {cpu_pct}%",
            "",
        ]

        # ── Disk ─────────────────────────────────────────────────────────
        disk_pct = disk_root.get("percent", 0.0)
        lines += [
            "💾 *التخزين (Drive الرئيسي)*",
            f"`{_pbar(disk_pct)}` {disk_pct}%",
            f"• المستخدم: `{disk_root.get('used_gb', 0)} GB` / الإجمالي: `{disk_root.get('total_gb', 0)} GB`",
            f"• المتاح: `{disk_root.get('free_gb', 0)} GB`",
            "",
        ]

        # Workspace disk (only if different from root)
        if disk_ws and disk_ws != disk_root:
            ws_pct = disk_ws.get("percent", 0.0)
            lines += [
                "📂 *مساحة مجلد Workspace*",
                f"`{_pbar(ws_pct)}` {ws_pct}%",
                f"• متاح: `{disk_ws.get('free_gb', 0)} GB`",
                "",
            ]

        # ── Bot process ──────────────────────────────────────────────────
        bot_ram = snap.get("bot_ram_mb", 0.0)
        ws_size = snap.get("workspace_size_mb", 0.0)
        db_size = snap.get("db_size_kb", 0.0)
        lines += [
            "🤖 *استهلاك البوت*",
            f"• RAM الخاص بالعملية: `{bot_ram} MB`",
            f"• حجم مجلد Workspaces: `{ws_size} MB`",
            f"• حجم قاعدة البيانات: `{db_size} KB`",
            "",
        ]
    else:
        lines.append("⚠️ _لا يمكن قراءة موارد الجهاز — تأكد من تثبيت_ `psutil`")
        lines.append("")

    # ── Workspace path ────────────────────────────────────────────────
    lines += [
        "📁 *مجلد العمل (Workspace)*",
        f"{workspace_icon} `{workspace}`",
        f"*موجود:* {'نعم ✅' if workspace_exists else 'لا — سيُنشأ عند أول تشغيل'}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "💡 _اضغط 🔄 تحديث للقراءة الآنية · 🧹 تنظيف DB لحذف السجلات القديمة_",
    ]

    return "\n".join(lines)


@auth_required
async def sysinfo_refresh_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """🔄 Refresh the sysinfo display with fresh resource readings."""
    from app.telegram.keyboards import sysinfo_keyboard

    query = update.callback_query
    assert query
    await query.answer("جارٍ التحديث... ✅")
    text = _build_sysinfo_text()
    try:
        await query.edit_message_text(
            text, reply_markup=sysinfo_keyboard(), parse_mode="Markdown"
        )
    except Exception:
        pass  # Ignore "message not modified" errors


@auth_required
async def sysinfo_cleanup_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """🧹 Clean up old terminal-state jobs from the database."""
    from app.telegram.keyboards import sysinfo_keyboard

    query = update.callback_query
    assert query
    await query.answer("جارٍ التنظيف...")

    db = context.bot_data.get("db")
    if db is None:
        await query.answer("❌ قاعدة البيانات غير متاحة", show_alert=True)
        return

    from app.database.repository import JobRepository
    repo = JobRepository(db)
    deleted = await repo.cleanup_old_jobs(keep_last=50)

    if deleted:
        await query.answer(f"✅ تم حذف {deleted} سجل قديم", show_alert=True)
    else:
        await query.answer("✅ لا يوجد سجلات قديمة للحذف", show_alert=True)

    # Refresh the display
    text = _build_sysinfo_text()
    try:
        await query.edit_message_text(
            text, reply_markup=sysinfo_keyboard(), parse_mode="Markdown"
        )
    except Exception:
        pass

