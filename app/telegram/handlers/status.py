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

from app.config.settings import get_platform_info, settings
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
        pr_line = f"\n*Pull Request:* [#{job.pr_number}]({job.pr_url})"

    error_line = ""
    if job.error:
        error_line = f"\n*Error:* `{job.error[:200]}`"

    text = (
        f"{emoji} *Agent Status*\n\n"
        f"*Job:* `{job.job_id}`\n"
        f"*Repository:* `{job.repo_full_name}`\n"
        f"*Issue:* #{job.issue_number}\n"
        f"*Branch:* `{job.branch}`\n"
        f"*Status:* {job.status}\n"
        f"*Phase:* {job.current_phase or '—'}\n"
        f"*Elapsed:* {elapsed}\n"
        f"*Files changed:* {job.files_changed}\n"
        f"*Fix iterations:* {job.fix_iterations}"
        f"{pr_line}"
        f"{error_line}"
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
        msg = "📊 No agent jobs found.\n\nUse /run to start one."
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
    """Handle /sysinfo — display platform details and workspace path."""
    assert update.message

    info = get_platform_info()
    workspace = settings.WORKSPACE_DIR.resolve()
    workspace_exists = workspace.exists()
    workspace_icon = "✅" if workspace_exists else "⚠️"

    # Map OS name to a friendly emoji
    os_emoji = {
        "Windows": "🪟",
        "Darwin": "🍎",
        "Linux": "🐧",
    }.get(info["os"], "💻")

    text = (
        f"{os_emoji} *System Information*\n\n"
        f"*OS:* `{info['os']} {info['os_release']}`\n"
        f"*Platform:* `{info['platform']}`\n"
        f"*Machine:* `{info['machine']}`\n"
        f"*Python:* `{info['python']}`\n"
        f"\n"
        f"📂 *Workspace Directory*\n"
        f"{workspace_icon} `{workspace}`\n"
        f"*Exists:* {'Yes' if workspace_exists else 'No — will be created on first run'}\n"
        f"\n"
        f"💡 _Override with_ `WORKSPACE_DIR=<path>` _in your_ `.env`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
