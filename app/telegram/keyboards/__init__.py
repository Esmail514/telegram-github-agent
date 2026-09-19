"""
Telegram inline keyboard builders.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.github.client import RepoInfo
from app.github.issues import IssueInfo

# Callback data prefixes
CB_REPO = "repo:"
CB_ISSUE = "issue:"
CB_CONFIRM_RUN = "confirm_run:"
CB_CANCEL = "cancel"
CB_REPO_PAGE = "repo_page:"
CB_ISSUE_PAGE = "issue_page:"
CB_NEWISSUE_REPO = "newissue_repo:"
CB_POLISH = "polish_issue:"
CB_POLISH_APPLY = "polish:apply"
CB_POLISH_REGEN = "polish:regen"

_REPOS_PER_PAGE = 8
_ISSUES_PER_PAGE = 10


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Repositories", callback_data="menu:repos")],
        [InlineKeyboardButton("📋 Browse Issues", callback_data="menu:issues")],
        [InlineKeyboardButton("🚀 Run Agent", callback_data="menu:run")],
        [InlineKeyboardButton("➕ Create Issue", callback_data="menu:newissue")],
        [InlineKeyboardButton("📊 Agent Status", callback_data="menu:status")],
    ])


def repos_keyboard(
    repos: list[RepoInfo],
    page: int = 0,
    callback_prefix: str = CB_REPO,
    page_prefix: str = CB_REPO_PAGE,
    include_cancel: bool = False,
) -> InlineKeyboardMarkup:
    """Build paginated repository selection keyboard."""
    buttons: list[list[InlineKeyboardButton]] = []

    for repo in repos:
        buttons.append([
            InlineKeyboardButton(
                f"{'🔒 ' if repo.private else ''}{repo.name}",
                callback_data=f"{callback_prefix}{repo.full_name}",
            )
        ])

    # Pagination row
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"{page_prefix}{page - 1}"))
    if len(repos) == _REPOS_PER_PAGE:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"{page_prefix}{page + 1}"))
    if nav:
        buttons.append(nav)

    if include_cancel:
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)])

    return InlineKeyboardMarkup(buttons)


def issues_keyboard(
    issues: list[IssueInfo],
    repo_full_name: str,
    page: int = 0,
    callback_prefix: str = CB_ISSUE,
    page_prefix: str = CB_ISSUE_PAGE,
    include_cancel: bool = False,
) -> InlineKeyboardMarkup:
    """Build paginated issue selection keyboard."""
    buttons: list[list[InlineKeyboardButton]] = []

    for issue in issues:
        label = f"#{issue.number} {issue.title[:40]}"
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"{callback_prefix}{issue.number}")
        ])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton("◀ Prev", callback_data=f"{page_prefix}{page - 1}")
        )
    if len(issues) == _ISSUES_PER_PAGE:
        nav.append(
            InlineKeyboardButton("Next ▶", callback_data=f"{page_prefix}{page + 1}")
        )
    if nav:
        buttons.append(nav)

    if include_cancel:
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)])

    return InlineKeyboardMarkup(buttons)


def confirm_run_keyboard(job_preview_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚡ Run Antigravity (agy)",
                callback_data=f"confirm_run:antigravity:{job_preview_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🧠 Run Codex (CLI)",
                callback_data=f"confirm_run:codex:{job_preview_id}",
            ),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ]
    ])


def confirm_issue_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Create Issue", callback_data="newissue:confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ]
    ])


def issue_detail_keyboard(issue: IssueInfo) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Open Issue", url=issue.html_url)],
        [InlineKeyboardButton("✨ Polish with AI", callback_data=f"{CB_POLISH}{issue.number}")],
        [InlineKeyboardButton("🚀 Start Agent", callback_data=f"run_issue:{issue.number}")],
    ])


def polish_result_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown after the AI returns a polished issue preview."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Apply", callback_data=CB_POLISH_APPLY),
            InlineKeyboardButton("🔄 Regenerate", callback_data=CB_POLISH_REGEN),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
    ])


def polish_newissue_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown on the new-issue confirm screen, with a Polish option."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Create Issue", callback_data="newissue:confirm"),
            InlineKeyboardButton("✨ Polish with AI", callback_data="polish_newissue"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)]
    ])


def status_keyboard(is_active: bool = False) -> InlineKeyboardMarkup:
    """Keyboard shown on the /status page."""
    rows: list[list[InlineKeyboardButton]] = []
    if is_active:
        rows.append([InlineKeyboardButton("🛑 Stop Job", callback_data="stop_job")])
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="status:refresh")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="menu:start")])
    return InlineKeyboardMarkup(rows)
