"""
/status handler — show current or last agent job status.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.database.repository import JobStatus
from app.telegram.auth import auth_required

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


@auth_required
async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    executor = context.bot_data.get("executor")
    if executor is None:
        msg = "❌ Job executor not initialised."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return

    job = await executor.get_active_job()

    if job is None:
        # Try last completed job
        from app.database.repository import JobRepository
        db = context.bot_data.get("db")
        if db:
            repo = JobRepository(db)
            recent = await repo.list_recent_jobs(limit=1)
            job = recent[0] if recent else None

    if job is None:
        msg = "📊 No agent jobs found.\n\nUse /run to start one."
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return

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

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, parse_mode="Markdown")
