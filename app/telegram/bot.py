"""
Telegram bot assembly — registers all handlers and builds the Application.
"""
from __future__ import annotations

import logging

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
)

from app.telegram.handlers.help import help_handler
from app.telegram.handlers.issues import (
    issue_page_callback,
    issue_selected_callback,
    issues_for_repo_callback,
    issues_handler,
    issues_repo_page_callback,
)
from app.telegram.handlers.newissue import build_newissue_handler
from app.telegram.handlers.repos import (
    repo_page_callback,
    repo_selected_callback,
    repos_handler,
)
from app.telegram.handlers.run import build_run_handler
from app.telegram.handlers.start import menu_callback, start_handler
from app.telegram.handlers.status import status_handler
from app.telegram.handlers.stop import stop_handler

logger = logging.getLogger(__name__)


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
    }
    if settings.TELEGRAM_PROXY_URL:
        req_kwargs["proxy_url"] = settings.TELEGRAM_PROXY_URL
        logger.info("Using Telegram proxy: %s", settings.TELEGRAM_PROXY_URL)

    request = HTTPXRequest(**req_kwargs)
    app = ApplicationBuilder().token(token).request(request).build()

    # ------------------------------------------------------------------
    # Conversation handlers (must be registered first — they have priority)
    # ------------------------------------------------------------------
    app.add_handler(build_newissue_handler())
    app.add_handler(build_run_handler())

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("repos", repos_handler))
    app.add_handler(CommandHandler("issues", issues_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("stop", stop_handler))
    app.add_handler(CommandHandler("help", help_handler))

    # ------------------------------------------------------------------
    # Callback query handlers (inline keyboard buttons)
    # ------------------------------------------------------------------

    # Main menu buttons
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu:"))

    # Repos
    app.add_handler(CallbackQueryHandler(repo_page_callback, pattern="^repo_page:"))
    app.add_handler(CallbackQueryHandler(repo_selected_callback, pattern="^repo:"))

    # Issues
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

    logger.info("Telegram Application built with all handlers registered")
    return app
