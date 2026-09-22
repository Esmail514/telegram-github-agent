"""
/help handler.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.telegram.auth import auth_required

HELP_TEXT = """
🤖 *AI Coding Agent — Help*

*Commands:*
/start — Show main menu
/projects — Browse local projects on your computer
/setdir — View or change local projects root directory
/repos — Browse your GitHub repositories
/issues — Browse issues in a repository
/prs — Browse and merge Pull Requests
/newissue — Create a new GitHub issue
/run — Start the AI agent on an issue
/schedule — 📅 جدولة Issue لتُنفَّذ تلقائياً في وقت محدد
/scheduled — 📋 عرض جميع المهام المجدولة وحذفها
/status — View current agent job status
/stop — Stop the running agent
/help — Show this help

*Typical workflow:*
1. Use /repos or /issues to find a repository and issue
2. Use /run to select that issue and launch the agent
3. Watch progress updates arrive automatically
4. When done, the bot sends a PR link

*Scheduling:*
1. Use /schedule to pick a repo → issue → date/time
2. Format: `DD/MM HH:MM` (e.g. `25/09 14:00`) — UTC time
3. The agent runs automatically when the time arrives ✨
4. Use /scheduled to view or cancel pending jobs

*Agent flow:*
🔍 Inspect → 🛠 Implement → 🧪 Test → 🔧 Fix → 📦 Commit → ⬆️ Push → 🔀 PR

*Safety:*
• The bot only responds to your Telegram account
• The agent never pushes to main/master
• Secrets are scanned before every commit
• The agent stops after the configured time limit
"""


@auth_required
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
