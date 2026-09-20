"""
/start and main menu handler.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.telegram.auth import auth_required
from app.telegram.keyboards import main_menu_keyboard

WELCOME = (
    "🤖 *AI Coding Agent*\n\n"
    "I can browse your GitHub repositories, create issues, "
    "and run an AI agent to implement them — then push a PR.\n\n"
    "What would you like to do?"
)


@auth_required
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    await update.message.reply_text(
        WELCOME,
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


@auth_required
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle main menu inline button taps."""
    query = update.callback_query
    assert query
    await query.answer()

    data = query.data or ""

    if data == "menu:projects":
        from app.telegram.handlers.projects import show_projects
        await show_projects(update, context)
    elif data == "menu:repos":
        from app.telegram.handlers.repos import show_repos
        await show_repos(update, context)
    elif data == "menu:issues":
        from app.telegram.handlers.issues import issues_handler
        await issues_handler(update, context)
    elif data == "menu:prs":
        from app.telegram.handlers.prs import prs_handler
        await prs_handler(update, context)
    elif data == "menu:run":
        from app.telegram.handlers.run import show_run_start
        await show_run_start(update, context)
    elif data == "menu:newissue":
        from app.telegram.handlers.newissue import start_newissue_flow
        await start_newissue_flow(update, context)
    elif data == "menu:status":
        from app.telegram.handlers.status import status_handler
        await status_handler(update, context)
    elif data == "menu:start":
        await query.edit_message_text(
            WELCOME, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
        )
