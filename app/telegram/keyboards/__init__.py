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
CB_MENU = "menu:start"
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


def main_menu_button() -> InlineKeyboardButton:
    """Standard return-to-main-menu button."""
    return InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data=CB_MENU)


def main_menu_keyboard(personal_mode: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        # ── Agent ──────────────────────────────────────────────────────────
        [InlineKeyboardButton("🚀 تشغيل الـ Agent", callback_data="menu:run"),
         InlineKeyboardButton("📊 حالة العمليات", callback_data="menu:status")],
        # ── GitHub ─────────────────────────────────────────────────────────
        [InlineKeyboardButton("🐙 قائمة GitHub", callback_data="menu:github")],
        # ── Projects ───────────────────────────────────────────────────────
        [InlineKeyboardButton("📁 المشاريع المحلية", callback_data="menu:projects")],
        # ── Settings / Accounts / System ───────────────────────────────────
        [InlineKeyboardButton("👥 حسابات Antigravity", callback_data="menu:accounts"),
         InlineKeyboardButton("🖥️ موارد الجهاز (System)", callback_data="menu:sysinfo")],
    ]
    if personal_mode:
        rows.append([InlineKeyboardButton("💻 الاستخدام الشخصي", callback_data="menu:personal")])
    return InlineKeyboardMarkup(rows)


def github_menu_keyboard() -> InlineKeyboardMarkup:
    """Submenu for all GitHub-related actions."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 المستودعات (Repos)", callback_data="menu:repos"),
         InlineKeyboardButton("📋 الـ Issues", callback_data="menu:issues")],
        [InlineKeyboardButton("🔀 طلبات السحب (PRs)", callback_data="menu:prs"),
         InlineKeyboardButton("➕ إنشاء Issue", callback_data="menu:newissue")],
        [InlineKeyboardButton("📅 جدولة Issue", callback_data="menu:schedule")],
        [main_menu_button()],
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
        nav.append(InlineKeyboardButton("◀ السابق", callback_data=f"{page_prefix}{page - 1}"))
    if len(repos) == _REPOS_PER_PAGE:
        nav.append(InlineKeyboardButton("التالي ▶", callback_data=f"{page_prefix}{page + 1}"))
    if nav:
        buttons.append(nav)

    if include_cancel:
        buttons.append([
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ])

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
            InlineKeyboardButton("◀ السابق", callback_data=f"{page_prefix}{page - 1}")
        )
    if len(issues) == _ISSUES_PER_PAGE:
        nav.append(
            InlineKeyboardButton("التالي ▶", callback_data=f"{page_prefix}{page + 1}")
        )
    if nav:
        buttons.append(nav)

    if include_cancel:
        buttons.append([
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ])

    return InlineKeyboardMarkup(buttons)


def confirm_run_keyboard(job_preview_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚡ تشغيل Antigravity (agy)",
                callback_data=f"confirm_run:antigravity:{job_preview_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🧠 تشغيل Codex (CLI)",
                callback_data=f"confirm_run:codex:{job_preview_id}",
            ),
        ],
        [
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ],
    ])


def workspace_source_keyboard() -> InlineKeyboardMarkup:
    """Ask user whether to clone from GitHub or use an existing local path."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬇️ الاستنساخ من GitHub (Clone)",
                callback_data=CB_WS_CLONE,
            )
        ],
        [
            InlineKeyboardButton(
                "📁 استخدام مسار محلي (Local Path)",
                callback_data=CB_WS_LOCAL,
            )
        ],
        [
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ],
    ])


def confirm_issue_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ إنشاء الـ Issue", callback_data="newissue:confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
        ],
        [main_menu_button()],
    ])


def issue_detail_keyboard(issue: IssueInfo) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 فتح في GitHub", url=issue.html_url)],
        [InlineKeyboardButton("✨ تحسين الوصف بالذكاء الاصطناعي", callback_data=f"{CB_POLISH}{issue.number}")],
        [InlineKeyboardButton("🚀 تشغيل الـ Agent لحل المشكلة", callback_data=f"run_issue:{issue.number}")],
        [main_menu_button()],
    ])


def polish_result_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown after the AI returns a polished issue preview."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تطبيق التعديل", callback_data=CB_POLISH_APPLY),
            InlineKeyboardButton("🔄 إعادة الصياغة", callback_data=CB_POLISH_REGEN),
        ],
        [
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ],
    ])


def polish_newissue_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown on the new-issue confirm screen, with a Polish option."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ إنشاء الـ Issue", callback_data="newissue:confirm"),
            InlineKeyboardButton("✨ تحسين بالذكاء الاصطناعي", callback_data="polish_newissue"),
        ],
        [
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ]
    ])


def status_keyboard(is_active: bool = False) -> InlineKeyboardMarkup:
    """Keyboard shown on the /status page."""
    rows: list[list[InlineKeyboardButton]] = []
    if is_active:
        rows.append([InlineKeyboardButton("🛑 إيقاف المهمة الحالية", callback_data="stop_job")])
    rows.append([
        InlineKeyboardButton("🔄 تحديث الحالة", callback_data="status:refresh"),
        main_menu_button(),
    ])
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
            InlineKeyboardButton("◀ السابق", callback_data=f"{CB_PR_PAGE}{page - 1}")
        )
    if len(prs) == _PRS_PER_PAGE:
        nav.append(
            InlineKeyboardButton("التالي ▶", callback_data=f"{CB_PR_PAGE}{page + 1}")
        )
    if nav:
        buttons.append(nav)

    if include_cancel:
        buttons.append([
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ])

    return InlineKeyboardMarkup(buttons)


def pr_detail_keyboard(pr: PRInfo, repo_full_name: str) -> InlineKeyboardMarkup:
    """Keyboard shown on PR detail view."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 فتح على GitHub", url=pr.html_url)],
        [InlineKeyboardButton("🔀 دمج الـ Pull Request", callback_data=f"{CB_MERGE_PR}{repo_full_name}:{pr.number}")],
        [
            InlineKeyboardButton("◀ رجوع للـ PRs", callback_data=f"prs_for:{repo_full_name}"),
            main_menu_button(),
        ],
    ])


def pr_action_keyboard(pr_url: str, repo_full_name: str, pr_number: int) -> InlineKeyboardMarkup:
    """Action keyboard attached to agent completion message."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 فتح الـ Pull Request", url=pr_url)],
        [InlineKeyboardButton("🔀 دمج الـ Pull Request", callback_data=f"{CB_MERGE_PR}{repo_full_name}:{pr_number}")],
        [main_menu_button()],
    ])


def merge_options_keyboard(repo_full_name: str, pr_number: int) -> InlineKeyboardMarkup:
    """Keyboard shown to choose merge strategy."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔀 دمج مع ضغط (Squash and Merge)",
                callback_data=f"{CB_DO_MERGE}squash:{repo_full_name}:{pr_number}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔀 إنشاء التزام دمج (Merge Commit)",
                callback_data=f"{CB_DO_MERGE}merge:{repo_full_name}:{pr_number}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔀 إعادة تأسيس ودمج (Rebase and Merge)",
                callback_data=f"{CB_DO_MERGE}rebase:{repo_full_name}:{pr_number}",
            )
        ],
        [
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ],
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
        nav.append(InlineKeyboardButton("◀ السابق", callback_data=f"{page_prefix}{page - 1}"))
    if end < len(projects):
        nav.append(InlineKeyboardButton("التالي ▶", callback_data=f"{page_prefix}{page + 1}"))
    if nav:
        buttons.append(nav)

    # Actions row
    action_row: list[InlineKeyboardButton] = []
    if include_setdir:
        action_row.append(InlineKeyboardButton("⚙️ تغيير المسار", callback_data=CB_SETDIR))
    action_row.append(InlineKeyboardButton("🔄 تحديث", callback_data=CB_REFRESH_PROJ))
    if action_row:
        buttons.append(action_row)

    if include_cancel:
        buttons.append([
            InlineKeyboardButton("❌ إلغاء", callback_data=CB_CANCEL),
            main_menu_button(),
        ])

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
            InlineKeyboardButton("📋 عرض الـ Issues", callback_data=f"issues_for:{repo_full_name}"),
            InlineKeyboardButton("➕ Issue جديد", callback_data=f"newissue_for:{repo_full_name}"),
        ])
        rows.append([
            InlineKeyboardButton("🚀 تشغيل الـ Agent", callback_data=f"run_proj:{project_idx}"),
            InlineKeyboardButton("🔀 الـ Pull Requests", callback_data=f"prs_for:{repo_full_name}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("🚀 تشغيل محلياً", callback_data=f"run_proj:{project_idx}"),
        ])
    rows.append([
        InlineKeyboardButton("◀ رجوع للمشاريع", callback_data="menu:projects"),
        main_menu_button(),
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
                InlineKeyboardButton("🗑 حذف", callback_data=f"{CB_SCHEDULE_DEL}{sj.id}")
            )
        rows.append(row)
    if include_cancel:
        rows.append([main_menu_button()])
    return InlineKeyboardMarkup(rows)


def schedule_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm/Cancel keyboard for scheduling a job."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد الجدولة", callback_data="sched_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="cancel"),
        ],
        [main_menu_button()],
    ])


# ---------------------------------------------------------------------------
# Sysinfo keyboards
# ---------------------------------------------------------------------------

def sysinfo_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for /sysinfo resource monitoring view."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث الموارد", callback_data="sysinfo:refresh"),
            InlineKeyboardButton("🧹 تنظيف الـ DB", callback_data="sysinfo:cleanup"),
        ],
        [main_menu_button()],
    ])


# ---------------------------------------------------------------------------
# Personal Mode keyboards
# ---------------------------------------------------------------------------

def personal_keyboard() -> InlineKeyboardMarkup:
    """Main menu keyboard for Personal Use Mode."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 مهمة جديدة للـ Agent", callback_data="personal:task"),
        ],
        [
            InlineKeyboardButton("📁 تصفح الملفات", callback_data="personal:ls"),
            InlineKeyboardButton("📤 تحميل ملف", callback_data="personal:getfile"),
        ],
        [
            InlineKeyboardButton("⚡ أمر Shell", callback_data="personal:shell"),
            InlineKeyboardButton("📊 العمليات الجارية", callback_data="personal:ps"),
        ],
        [
            InlineKeyboardButton("🖥️ حالة وموارد الجهاز (System)", callback_data="menu:sysinfo"),
        ],
        [
            InlineKeyboardButton("🔒 إغلاق Personal Mode نهائياً", callback_data="personal:killswitch_ask"),
        ],
        [main_menu_button()],
    ])


def personal_killswitch_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirmation keyboard for the one-way safety kill switch."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 نعم، إغلاق نهائي وقفل", callback_data="personal:killswitch_confirm"),
        ],
        [
            InlineKeyboardButton("❌ إلغاء والعودة", callback_data="personal:menu"),
        ],
    ])


def personal_task_running_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown when a personal agent task is running."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 إيقاف المهمة الآن", callback_data="personal:task_stop"),
        ],
        [main_menu_button()],
    ])


def accounts_keyboard(
    profiles: list[dict],
    paused_job_id: str | None = None,
) -> InlineKeyboardMarkup:
    """Keyboard for managing and switching Antigravity account profiles."""
    rows: list[list[InlineKeyboardButton]] = []

    if paused_job_id:
        rows.append([
            InlineKeyboardButton(
                "▶️ استئناف المهمة المعلقة الآن",
                callback_data=f"acc_resume:{paused_job_id}",
            )
        ])

    for p in profiles:
        active_mark = "✅ " if p.get("is_active") else ""
        name = p.get("name", "")
        username = p.get("username", "")
        btn_text = f"{active_mark}👤 {name} ({username})"
        rows.append([
            InlineKeyboardButton(btn_text, callback_data=f"acc_switch:{name}")
        ])

    rows.append([
        InlineKeyboardButton("💾 حفظ الجلسة الحالية كـ Profile", callback_data="acc_save"),
        InlineKeyboardButton("🔄 تحديث", callback_data="menu:accounts"),
    ])
    rows.append([main_menu_button()])
    return InlineKeyboardMarkup(rows)

