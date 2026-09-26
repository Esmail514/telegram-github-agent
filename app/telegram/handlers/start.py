"""
/start and main menu handler.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.runner.personal_guard import is_personal_mode_active
from app.telegram.auth import auth_required
from app.telegram.keyboards import github_menu_keyboard, main_menu_keyboard

WELCOME = (
    "⚡️ *Antigravity Coding Assistant*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "مرحباً! اختر ما تريد من القائمة:\n\n"
    "🚀 *Agent* — تشغيل ومتابعة حالة المهام\n"
    "🐙 *GitHub* — المستودعات، Issues، PRs، جدولة\n"
    "📁 *المشاريع* — مشاريعك البرمجية المحلية\n"
    "👥 *الحسابات* — التبديل بين حسابات Antigravity\n"
    "🖥️ *موارد الجهاز* — استهلاك الـ RAM والـ CPU والقرص"
)


@auth_required
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    personal_active, _ = is_personal_mode_active()
    await update.message.reply_text(
        WELCOME,
        reply_markup=main_menu_keyboard(personal_mode=personal_active),
        parse_mode="Markdown",
    )


@auth_required
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle main menu inline button taps."""
    query = update.callback_query
    assert query
    await query.answer()

    data = query.data or ""

    if data == "menu:github":
        await query.edit_message_text(
            "🐙 *قائمة GitHub*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "اختر الإجراء المطلوب:",
            reply_markup=github_menu_keyboard(),
            parse_mode="Markdown",
        )
    elif data == "menu:projects":
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
    elif data == "menu:schedule":
        from app.telegram.handlers.schedule import schedule_start
        await schedule_start(update, context)
    elif data == "menu:status":
        from app.telegram.handlers.status import status_handler
        await status_handler(update, context)
    elif data == "menu:accounts":
        from app.telegram.handlers.accounts import accounts_command
        await accounts_command(update, context)
    elif data == "menu:sysinfo":
        from app.telegram.handlers.status import sysinfo_handler
        await sysinfo_handler(update, context)
    elif data == "menu:personal":
        from app.telegram.handlers.personal import personal_menu_handler
        await personal_menu_handler(update, context)
    elif data == "menu:start":
        personal_active, _ = is_personal_mode_active()
        await query.edit_message_text(
            WELCOME, reply_markup=main_menu_keyboard(personal_mode=personal_active), parse_mode="Markdown"
        )
