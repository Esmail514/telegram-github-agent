"""
Telegram inline keyboard builders.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.github.client import RepoInfo
from app.github.issues import IssueInfo
from app.github.pull_requests import PRInfo

if TYPE_CHECKING:
    from app.runner.project_scanner import LocalProject

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
CB_PR = "pr:"
CB_PR_PAGE = "pr_page:"
CB_MERGE_PR = "merge_pr:"
CB_DO_MERGE = "do_merge:"
CB_WS_CLONE = "ws:clone"
CB_WS_LOCAL = "ws:local"
CB_PROJECT = "proj:"
CB_PROJECT_PAGE = "proj_page:"
CB_SETDIR = "cmd_setdir"
CB_REFRESH_PROJ = "proj_refresh"
CB_SCHEDULE_DEL = "sched_del:"
CB_SCHEDULE_LIST = "sched_list"

_REPOS_PER_PAGE = 8
_PROJECTS_PER_PAGE = 8
_ISSUES_PER_PAGE = 10
_PRS_PER_PAGE = 10


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Local Projects", callback_data="menu:projects")],
        [InlineKeyboardButton("📦 GitHub Repositories", callback_data="menu:repos")],
        [InlineKeyboardButton("📋 Browse Issues", callback_data="menu:issues")],
        [InlineKeyboardButton("🔀 Pull Requests", callback_data="menu:prs")],
        [InlineKeyboardButton("🚀 Run Agent", callback_data="menu:run")],
        [InlineKeyboardButton("➕ Create Issue", callback_data="menu:newissue")],
        [InlineKeyboardButton("📅 Schedule Issue", callback_data="menu:schedule")],
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


def workspace_source_keyboard() -> InlineKeyboardMarkup:
    """Ask user whether to clone from GitHub or use an existing local path."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬇️ Clone from GitHub",
                callback_data=CB_WS_CLONE,
            )
        ],
        [
            InlineKeyboardButton(
                "📁 Use Local Path",
                callback_data=CB_WS_LOCAL,
            )
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
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


def prs_keyboard(
    prs: list[PRInfo],
    repo_full_name: str,
    page: int = 0,
    include_cancel: bool = False,
) -> InlineKeyboardMarkup:
    """Build paginated pull requests selection keyboard."""
    buttons: list[list[InlineKeyboardButton]] = []

    for pr in prs:
        label = f"#{pr.number} {pr.title[:38]}"
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"{CB_PR}{pr.number}")
        ])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton("◀ Prev", callback_data=f"{CB_PR_PAGE}{page - 1}")
        )
    if len(prs) == _PRS_PER_PAGE:
        nav.append(
            InlineKeyboardButton("Next ▶", callback_data=f"{CB_PR_PAGE}{page + 1}")
        )
    if nav:
        buttons.append(nav)

    if include_cancel:
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)])

    return InlineKeyboardMarkup(buttons)


def pr_detail_keyboard(pr: PRInfo, repo_full_name: str) -> InlineKeyboardMarkup:
    """Keyboard shown on PR detail view."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Open on GitHub", url=pr.html_url)],
        [InlineKeyboardButton("🔀 Merge Pull Request", callback_data=f"{CB_MERGE_PR}{repo_full_name}:{pr.number}")],
        [InlineKeyboardButton("◀ Back to PRs", callback_data=f"prs_for:{repo_full_name}")],
    ])


def pr_action_keyboard(pr_url: str, repo_full_name: str, pr_number: int) -> InlineKeyboardMarkup:
    """Action keyboard attached to agent completion message."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Open Pull Request", url=pr_url)],
        [InlineKeyboardButton("🔀 Merge Pull Request", callback_data=f"{CB_MERGE_PR}{repo_full_name}:{pr_number}")],
    ])


def merge_options_keyboard(repo_full_name: str, pr_number: int) -> InlineKeyboardMarkup:
    """Keyboard shown to choose merge strategy."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔀 Squash and Merge",
                callback_data=f"{CB_DO_MERGE}squash:{repo_full_name}:{pr_number}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔀 Create Merge Commit",
                callback_data=f"{CB_DO_MERGE}merge:{repo_full_name}:{pr_number}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔀 Rebase and Merge",
                callback_data=f"{CB_DO_MERGE}rebase:{repo_full_name}:{pr_number}",
            )
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
    ])


def projects_keyboard(
    projects: list[LocalProject],
    page: int = 0,
    callback_prefix: str = CB_PROJECT,
    page_prefix: str = CB_PROJECT_PAGE,
    include_cancel: bool = False,
    include_setdir: bool = True,
) -> InlineKeyboardMarkup:
    """Build paginated local projects keyboard."""
    buttons: list[list[InlineKeyboardButton]] = []
    start = page * _PROJECTS_PER_PAGE
    end = start + _PROJECTS_PER_PAGE
    page_projects = projects[start:end]

    for i, proj in enumerate(page_projects, start=start):
        icon = "🐙 " if proj.has_github_remote else ("📁 " if proj.is_git else "📂 ")
        label = f"{icon}{proj.name}"
        if len(label) > 40:
            label = label[:37] + "..."
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"{callback_prefix}{i}")
        ])

    # Pagination row
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"{page_prefix}{page - 1}"))
    if end < len(projects):
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"{page_prefix}{page + 1}"))
    if nav:
        buttons.append(nav)

    # Actions row
    action_row: list[InlineKeyboardButton] = []
    if include_setdir:
        action_row.append(InlineKeyboardButton("⚙️ Change Dir", callback_data=CB_SETDIR))
    action_row.append(InlineKeyboardButton("🔄 Refresh", callback_data=CB_REFRESH_PROJ))
    if action_row:
        buttons.append(action_row)

    if include_cancel:
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)])

    return InlineKeyboardMarkup(buttons)


def project_detail_keyboard(
    project_idx: int,
    has_github_remote: bool,
    repo_full_name: str | None = None,
) -> InlineKeyboardMarkup:
    """Actions available for a selected local project."""
    rows: list[list[InlineKeyboardButton]] = []
    if has_github_remote and repo_full_name:
        rows.append([
            InlineKeyboardButton("📋 View Issues", callback_data=f"issues_for:{repo_full_name}"),
            InlineKeyboardButton("➕ New Issue", callback_data=f"newissue_for:{repo_full_name}"),
        ])
        rows.append([
            InlineKeyboardButton("🚀 Run Agent", callback_data=f"run_proj:{project_idx}"),
            InlineKeyboardButton("🔀 Pull Requests", callback_data=f"prs_for:{repo_full_name}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("🚀 Run Agent Locally", callback_data=f"run_proj:{project_idx}"),
        ])
    rows.append([
        InlineKeyboardButton("◀ Back to Projects", callback_data="menu:projects")
    ])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Schedule keyboards
# ---------------------------------------------------------------------------

def scheduled_jobs_keyboard(
    jobs: list,
    include_cancel: bool = True,
) -> InlineKeyboardMarkup:
    """Keyboard listing scheduled jobs with a delete button per row."""
    rows: list[list[InlineKeyboardButton]] = []
    for sj in jobs:
        label = (
            f"{'✅' if sj.status == 'LAUNCHED' else '⏳'} "
            f"{sj.repo_full_name.split('/')[-1]} #{sj.issue_number} — {sj.display_time()}"
        )
        row = [InlineKeyboardButton(label, callback_data=f"sched_noop:{sj.id}")]
        if sj.status == "PENDING":
            row.append(
                InlineKeyboardButton("🗑 Delete", callback_data=f"{CB_SCHEDULE_DEL}{sj.id}")
            )
        rows.append(row)
    if include_cancel:
        rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="menu:start")])
    return InlineKeyboardMarkup(rows)


def schedule_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm/Cancel keyboard for scheduling a job."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="sched_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]
    ])
