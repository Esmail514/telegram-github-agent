"""
Telegram bot assembly — registers all handlers and builds the Application.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

if TYPE_CHECKING:
    from telegram.ext import CallbackContext

from app.telegram.handlers.accounts import (
    accounts_callback,
    accounts_command,
    deleteaccount_command,
    resume_command,
    saveaccount_command,
)
from app.telegram.handlers.help import help_handler
from app.telegram.handlers.issues import (
    issue_page_callback,
    issue_selected_callback,
    issues_browse_github_callback,
    issues_for_repo_callback,
    issues_handler,
    issues_proj_page_callback,
    issues_proj_selected_callback,
    issues_repo_page_callback,
)
from app.telegram.handlers.newissue import build_newissue_handler
from app.telegram.handlers.polishissue import (
    polish_apply_callback,
    polish_issue_start,
    polish_newissue_start,
    polish_regen_callback,
)
from app.telegram.handlers.projects import (
    build_setdir_handler,
    project_page_callback,
    project_refresh_callback,
    project_selected_callback,
    projects_handler,
    setdir_command,
)
from app.telegram.handlers.prs import (
    merge_pr_execute_callback,
    merge_pr_start_callback,
    pr_page_callback,
    pr_selected_callback,
    prs_browse_github_callback,
    prs_for_repo_callback,
    prs_handler,
    prs_proj_page_callback,
    prs_proj_selected_callback,
    prs_repo_page_callback,
)
from app.telegram.handlers.repos import (
    repo_page_callback,
    repo_selected_callback,
    repos_handler,
)
from app.telegram.handlers.run import build_run_handler
from app.telegram.handlers.schedule import (
    build_schedule_handler,
    sched_delete_callback,
    schedule_list_command,
)
from app.telegram.handlers.start import menu_callback, start_handler
from app.telegram.handlers.status import (
    status_handler,
    status_refresh_callback,
    stop_job_callback,
    sysinfo_cleanup_callback,
    sysinfo_handler,
    sysinfo_refresh_callback,
)
from app.telegram.handlers.stop import stop_handler

logger = logging.getLogger(__name__)


async def _error_handler(update: object, context: CallbackContext) -> None:
    """Global error handler — logs transient network errors quietly."""
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)):
        # These are normal when the connection is unstable; log at WARNING, not ERROR
        logger.warning("Transient Telegram network error (will retry automatically): %s", err)
        return
    # Unexpected errors — log with full traceback
    logger.error("Unhandled exception while processing update", exc_info=context.error)


def build_application(token: str) -> Application:
    """
    Build and return the PTB Application with all handlers registered.
    The executor and db are injected into bot_data after build.
    """
    from telegram.request import HTTPXRequest

    from app.config.settings import settings

    req_kwargs: dict = {
        "connect_timeout": settings.TELEGRAM_REQUEST_TIMEOUT,
        "read_timeout": settings.TELEGRAM_REQUEST_TIMEOUT,
        "write_timeout": settings.TELEGRAM_REQUEST_TIMEOUT,
        "connection_pool_size": 8,  # extra connections for concurrent polling + sends
    }
    if settings.TELEGRAM_PROXY_URL:
        req_kwargs["proxy_url"] = settings.TELEGRAM_PROXY_URL
        logger.info("Using Telegram proxy: %s", settings.TELEGRAM_PROXY_URL)

    request = HTTPXRequest(**req_kwargs)
    app = ApplicationBuilder().token(token).request(request).build()

    # Register global error handler
    app.add_error_handler(_error_handler)

    # ------------------------------------------------------------------
    # Conversation handlers (must be registered first — they have priority)
    # ------------------------------------------------------------------
    app.add_handler(build_setdir_handler())
    app.add_handler(build_newissue_handler())
    app.add_handler(build_run_handler())
    app.add_handler(build_schedule_handler())

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("projects", projects_handler))
    app.add_handler(CommandHandler("setdir", setdir_command))
    app.add_handler(CommandHandler("repos", repos_handler))
    app.add_handler(CommandHandler("issues", issues_handler))
    app.add_handler(CommandHandler("prs", prs_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("stop", stop_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("sysinfo", sysinfo_handler))
    app.add_handler(CommandHandler("scheduled", schedule_list_command))
    app.add_handler(CommandHandler("accounts", accounts_command))
    app.add_handler(CommandHandler("saveaccount", saveaccount_command))
    app.add_handler(CommandHandler("deleteaccount", deleteaccount_command))
    app.add_handler(CommandHandler("resume", resume_command))

    # ------------------------------------------------------------------
    # Personal use mode commands & callbacks
    # ------------------------------------------------------------------
    from app.telegram.handlers.personal import (
        getfile_handler,
        incoming_file_handler,
        ls_handler,
        personal_killswitch_confirm_callback,
        personal_menu_handler,
        personal_off_handler,
        personal_on_rejection_handler,
        pkill_cancel_callback,
        pkill_confirm_callback,
        pkill_handler,
        ps_handler,
        shell_handler,
        task_handler,
        task_stop_handler,
    )
    app.add_handler(CommandHandler("personal", personal_menu_handler))
    app.add_handler(CommandHandler("personal_off", personal_off_handler))
    app.add_handler(CommandHandler("personal_on", personal_on_rejection_handler))
    app.add_handler(CommandHandler("task", task_handler))
    app.add_handler(CommandHandler("task_stop", task_stop_handler))
    app.add_handler(CommandHandler("getfile", getfile_handler))
    app.add_handler(CommandHandler("download", getfile_handler))
    app.add_handler(CommandHandler("shell", shell_handler))
    app.add_handler(CommandHandler("ls", ls_handler))
    app.add_handler(CommandHandler("ps", ps_handler))
    app.add_handler(CommandHandler("pkill", pkill_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, incoming_file_handler))

    app.add_handler(CallbackQueryHandler(personal_menu_handler, pattern="^personal:menu$"))
    app.add_handler(CallbackQueryHandler(personal_off_handler, pattern="^personal:killswitch_ask$"))
    app.add_handler(CallbackQueryHandler(personal_killswitch_confirm_callback, pattern="^personal:killswitch_confirm$"))
    app.add_handler(CallbackQueryHandler(task_stop_handler, pattern="^personal:task_stop$"))
    app.add_handler(CallbackQueryHandler(pkill_confirm_callback, pattern="^pkill:confirm$"))
    app.add_handler(CallbackQueryHandler(pkill_cancel_callback, pattern="^pkill:cancel$"))

    # ------------------------------------------------------------------
    # Callback query handlers (inline keyboard buttons)
    # ------------------------------------------------------------------

    # Main menu buttons
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu:"))

    # Local Projects
    app.add_handler(CallbackQueryHandler(project_page_callback, pattern="^proj_page:"))
    app.add_handler(CallbackQueryHandler(project_refresh_callback, pattern="^proj_refresh$"))
    app.add_handler(CallbackQueryHandler(project_selected_callback, pattern="^proj:\\d+$"))

    # Repos
    app.add_handler(CallbackQueryHandler(repo_page_callback, pattern="^repo_page:"))
    app.add_handler(CallbackQueryHandler(repo_selected_callback, pattern="^repo:"))

    # Issues
    app.add_handler(CallbackQueryHandler(issues_proj_page_callback, pattern="^issues_proj_page:"))
    app.add_handler(CallbackQueryHandler(issues_proj_selected_callback, pattern="^issues_proj:"))
    app.add_handler(CallbackQueryHandler(issues_browse_github_callback, pattern="^issues_browse_github$"))
    app.add_handler(CallbackQueryHandler(issues_repo_page_callback, pattern="^issues_repo_page:"))
    app.add_handler(CallbackQueryHandler(issues_for_repo_callback, pattern="^issues_for:"))
    app.add_handler(CallbackQueryHandler(issue_page_callback, pattern="^issue_page:"))
    app.add_handler(CallbackQueryHandler(issue_selected_callback, pattern="^issue:\\d+$"))

    # Cancel fallback
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: u.callback_query.answer() and u.callback_query.edit_message_text(
                "❌ Cancelled."
            ),
            pattern="^cancel$",
        )
    )

    # Status page: refresh & stop
    app.add_handler(CallbackQueryHandler(status_refresh_callback, pattern="^status:refresh$"))
    app.add_handler(CallbackQueryHandler(stop_job_callback, pattern="^stop_job$"))

    # Sysinfo: refresh & DB cleanup
    app.add_handler(CallbackQueryHandler(sysinfo_refresh_callback, pattern="^sysinfo:refresh$"))
    app.add_handler(CallbackQueryHandler(sysinfo_cleanup_callback, pattern="^sysinfo:cleanup$"))

    # Polish with AI
    app.add_handler(CallbackQueryHandler(polish_issue_start, pattern="^polish_issue:\\d+$"))
    app.add_handler(CallbackQueryHandler(polish_newissue_start, pattern="^polish_newissue$"))
    app.add_handler(CallbackQueryHandler(polish_apply_callback, pattern="^polish:apply$"))
    app.add_handler(CallbackQueryHandler(polish_regen_callback, pattern="^polish:regen$"))

    # Pull Requests & Merging
    app.add_handler(CallbackQueryHandler(prs_proj_page_callback, pattern="^prs_proj_page:"))
    app.add_handler(CallbackQueryHandler(prs_proj_selected_callback, pattern="^prs_proj:"))
    app.add_handler(CallbackQueryHandler(prs_browse_github_callback, pattern="^prs_browse_github$"))
    app.add_handler(CallbackQueryHandler(prs_repo_page_callback, pattern="^prs_repo_page:"))
    app.add_handler(CallbackQueryHandler(prs_for_repo_callback, pattern="^prs_for:"))
    app.add_handler(CallbackQueryHandler(pr_page_callback, pattern="^pr_page:"))
    app.add_handler(CallbackQueryHandler(pr_selected_callback, pattern="^pr:\\d+$"))
    app.add_handler(CallbackQueryHandler(merge_pr_start_callback, pattern="^merge_pr:"))
    app.add_handler(CallbackQueryHandler(merge_pr_execute_callback, pattern="^do_merge:"))

    # Schedule: delete a pending job
    app.add_handler(CallbackQueryHandler(sched_delete_callback, pattern="^sched_del:\\d+$"))
    # Schedule: noop for info-only buttons
    app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^sched_noop:"))

    # Antigravity Accounts & Resumption
    app.add_handler(
        CallbackQueryHandler(
            accounts_callback,
            pattern=r"^(menu:accounts|acc_switch:|acc_resume:|acc_save)",
        )
    )

    logger.info("Telegram Application built with all handlers registered")
    return app
