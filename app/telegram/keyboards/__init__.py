"""
Telegram inline keyboard builders.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.github.client import RepoInfo
from app.github.issues import IssueInfo
from app.github.pull_requests import PRInfo
from app.telegram.callback_payloads import encode_callback_payload

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
CB_SAVED_PROJ = "saved_proj:"
CB_SAVED_PROJ_PAGE = "saved_proj_page:"
CB_SAVED_PROJ_DEL = "saved_proj_del:"
CB_SAVED_PROJ_DEL_CONFIRM = "saved_proj_del_confirm:"
CB_ADD_PROJ = "proj_add"

_REPOS_PER_PAGE = 8
_PROJECTS_PER_PAGE = 8
_ISSUES_PER_PAGE = 10
_PRS_PER_PAGE = 10


MAIN_MENU_GITHUB = "🐙 GitHub"
MAIN_MENU_PROJECTS = "📁 المشاريع المحلية"
MAIN_MENU_SCHEDULES = "📅 المهام المجدولة"
MAIN_MENU_STATUS = "📊 حالة المهمة"
MAIN_MENU_SYSTEM = "🖥️ موارد الجهاز"
MAIN_MENU_PERSONAL = "💻 الاستخدام الشخصي"


def main_menu_keyboard(personal_mode: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [
            KeyboardButton(MAIN_MENU_GITHUB),
            KeyboardButton(MAIN_MENU_PROJECTS),
        ],
        [
            KeyboardButton(MAIN_MENU_SCHEDULES),
            KeyboardButton(MAIN_MENU_STATUS),
        ],
        [
            KeyboardButton(MAIN_MENU_SYSTEM),
            KeyboardButton(MAIN_MENU_PERSONAL),
        ],
    ], resize_keyboard=True, is_persistent=True)


def github_menu_keyboard() -> InlineKeyboardMarkup:
    """GitHub exposes scheduling-only actions; navigation stays on the bottom keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 إنشاء Issue", callback_data="menu:newissue")],
        [InlineKeyboardButton("📅 جدولة Issue", callback_data="menu:schedule:issue")],
        [InlineKeyboardButton("🖥️ جدولة Prompt محلي", callback_data="menu:schedule:local")],
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
                f"🔒 {repo.name}" if repo.private else repo.name,
                callback_data=encode_callback_payload(callback_prefix, repo.full_name),
            )
        ])

    # Pagination row
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("السابق", callback_data=f"{page_prefix}{page - 1}"))
    if len(repos) == _REPOS_PER_PAGE:
        nav.append(InlineKeyboardButton("التالي ", callback_data=f"{page_prefix}{page + 1}"))
    if nav:
        buttons.append(nav)

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
            InlineKeyboardButton("السابق", callback_data=f"{page_prefix}{page - 1}")
        )
    if len(issues) == _ISSUES_PER_PAGE:
        nav.append(
            InlineKeyboardButton("التالي ", callback_data=f"{page_prefix}{page + 1}")
        )
    if nav:
        buttons.append(nav)

    return InlineKeyboardMarkup(buttons)


def confirm_run_keyboard(job_preview_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "تشغيل Antigravity (agy)",
                callback_data=f"confirm_run:antigravity:{job_preview_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "تشغيل Codex (CLI)",
                callback_data=f"confirm_run:codex:{job_preview_id}",
            ),
        ],
    ])


def workspace_source_keyboard() -> InlineKeyboardMarkup:
    """Ask user whether to clone from GitHub or use an existing local path."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "الاستنساخ من GitHub (Clone)",
                callback_data=CB_WS_CLONE,
            )
        ],
        [
            InlineKeyboardButton(
                "استخدام مسار محلي (Local Path)",
                callback_data=CB_WS_LOCAL,
            )
        ],
    ])


def confirm_issue_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("إنشاء الـ Issue", callback_data="newissue:confirm"),
        ],
    ])


def issue_detail_keyboard(issue: IssueInfo) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("تعديل Issue", callback_data=f"{CB_POLISH}{issue.number}")],
    ])


def polish_result_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown after the AI returns a polished issue preview."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("تطبيق التعديل", callback_data=CB_POLISH_APPLY),
            InlineKeyboardButton("إعادة الصياغة", callback_data=CB_POLISH_REGEN),
        ],
    ])


def polish_newissue_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown on the new-issue confirm screen, with a Polish option."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("إنشاء الآن", callback_data="newissue:confirm"),
            InlineKeyboardButton("تعديل المسودة", callback_data="polish_newissue"),
        ],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([])


def status_keyboard(is_active: bool = False) -> InlineKeyboardMarkup:
    """Keyboard shown on the /status page."""
    rows: list[list[InlineKeyboardButton]] = []
    if is_active:
        rows.append([InlineKeyboardButton("إيقاف المهمة الحالية", callback_data="stop_job")])
    rows.append([
        InlineKeyboardButton("تحديث الحالة", callback_data="status:refresh"),
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
            InlineKeyboardButton("السابق", callback_data=f"{CB_PR_PAGE}{page - 1}")
        )
    if len(prs) == _PRS_PER_PAGE:
        nav.append(
            InlineKeyboardButton("التالي ", callback_data=f"{CB_PR_PAGE}{page + 1}")
        )
    if nav:
        buttons.append(nav)

    return InlineKeyboardMarkup(buttons)


def pr_detail_keyboard(pr: PRInfo, repo_full_name: str) -> InlineKeyboardMarkup:
    """Keyboard shown on PR detail view."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("فتح على GitHub", url=pr.html_url)],
        [InlineKeyboardButton("دمج الـ Pull Request", callback_data=encode_callback_payload(CB_MERGE_PR, f"{repo_full_name}:{pr.number}"))],
        [
            InlineKeyboardButton("رجوع للـ PRs", callback_data=encode_callback_payload("prs_for:", repo_full_name)),
        ],
    ])


def pr_action_keyboard(pr_url: str, repo_full_name: str, pr_number: int) -> InlineKeyboardMarkup:
    """Action keyboard attached to agent completion message."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("فتح الـ Pull Request", url=pr_url)],
        [InlineKeyboardButton("دمج الـ Pull Request", callback_data=encode_callback_payload(CB_MERGE_PR, f"{repo_full_name}:{pr_number}"))],
    ])


def merge_options_keyboard(repo_full_name: str, pr_number: int) -> InlineKeyboardMarkup:
    """Keyboard shown to choose merge strategy."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "دمج مع ضغط (Squash and Merge)",
                callback_data=encode_callback_payload(CB_DO_MERGE, f"squash:{repo_full_name}:{pr_number}"),
            )
        ],
        [
            InlineKeyboardButton(
                "إنشاء التزام دمج (Merge Commit)",
                callback_data=encode_callback_payload(CB_DO_MERGE, f"merge:{repo_full_name}:{pr_number}"),
            )
        ],
        [
            InlineKeyboardButton(
                "إعادة تأسيس ودمج (Rebase and Merge)",
                callback_data=encode_callback_payload(CB_DO_MERGE, f"rebase:{repo_full_name}:{pr_number}"),
            )
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
        icon = ""if proj.has_github_remote else (""if proj.is_git else "")
        label = f"{icon}{proj.name}"
        if len(label) > 40:
            label = label[:37] + "..."
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"{callback_prefix}{i}")
        ])

    # Pagination row
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("السابق", callback_data=f"{page_prefix}{page - 1}"))
    if end < len(projects):
        nav.append(InlineKeyboardButton("التالي ", callback_data=f"{page_prefix}{page + 1}"))
    if nav:
        buttons.append(nav)

    # Actions row
    action_row: list[InlineKeyboardButton] = []
    if include_setdir:
        action_row.append(InlineKeyboardButton("تغيير المسار", callback_data=CB_SETDIR))
    action_row.append(InlineKeyboardButton("تحديث", callback_data=CB_REFRESH_PROJ))
    if action_row:
        buttons.append(action_row)

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
            InlineKeyboardButton("تعديل Issue", callback_data=encode_callback_payload("issues_for:", repo_full_name)),
            InlineKeyboardButton("Issue جديد", callback_data=encode_callback_payload("newissue_for:", repo_full_name)),
        ])
        rows.append([
            InlineKeyboardButton("تشغيل الـ Agent", callback_data=f"run_proj:{project_idx}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("تشغيل محلياً", callback_data=f"run_proj:{project_idx}"),
        ])
    rows.append([
        InlineKeyboardButton("رجوع للمشاريع", callback_data="menu:projects"),
    ])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Saved Projects keyboards
# ---------------------------------------------------------------------------

def saved_projects_keyboard(
    projects: list,
    page: int = 0,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    """List of saved projects with add and paginate buttons."""
    rows: list[list[InlineKeyboardButton]] = []
    start = page * page_size
    for idx, proj in enumerate(projects[start : start + page_size], start=start + 1):
        label = f"{idx}. {proj.name}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_SAVED_PROJ}{proj.id}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("السابق", callback_data=f"{CB_SAVED_PROJ_PAGE}{page - 1}"))
    if start + page_size < len(projects):
        nav.append(InlineKeyboardButton("التالي", callback_data=f"{CB_SAVED_PROJ_PAGE}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("إضافة مشروع", callback_data=CB_ADD_PROJ)])
    return InlineKeyboardMarkup(rows)


def saved_project_detail_keyboard(
    project_id: int,
    has_github_remote: bool,
    repo_full_name: str | None = None,
) -> InlineKeyboardMarkup:
    """Detail view for a single saved project — run or delete."""
    rows: list[list[InlineKeyboardButton]] = []
    if has_github_remote and repo_full_name:
        rows.append([
            InlineKeyboardButton("تعديل Issue", callback_data=encode_callback_payload("issues_for:", repo_full_name)),
            InlineKeyboardButton("Issue جديد", callback_data=encode_callback_payload("newissue_for:", repo_full_name)),
        ])
        rows.append([
            InlineKeyboardButton("تشغيل الـ Agent", callback_data=f"run_saved_proj:{project_id}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("تشغيل محلياً", callback_data=f"run_saved_proj:{project_id}"),
        ])
    rows.append([
        InlineKeyboardButton("حذف المشروع", callback_data=f"{CB_SAVED_PROJ_DEL}{project_id}"),
    ])
    rows.append([
        InlineKeyboardButton("رجوع للمشاريع", callback_data="menu:projects"),
    ])
    return InlineKeyboardMarkup(rows)


def saved_project_delete_confirm_keyboard(project_id: int) -> InlineKeyboardMarkup:
    """Confirmation keyboard before deleting a saved project."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("نعم، احذف المشروع", callback_data=f"{CB_SAVED_PROJ_DEL_CONFIRM}{project_id}"),
        ],
        [
            InlineKeyboardButton("⬅️ رجوع", callback_data=f"{CB_SAVED_PROJ}{project_id}"),
        ],
    ])



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
        project = f" | {sj.project_name}" if sj.project_name else ""
        label = (
            f"{'' if sj.status == 'LAUNCHED' else ''} "
            f"{sj.repo_full_name.split('/')[-1]} #{sj.issue_number}{project} — {sj.display_time()}"
        )
        row = [InlineKeyboardButton(label, callback_data=f"sched_noop:{sj.id}")]
        if sj.status == "PENDING":
            row.append(
                InlineKeyboardButton("حذف", callback_data=f"{CB_SCHEDULE_DEL}{sj.id}")
            )
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def schedule_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm/Cancel keyboard for scheduling a job."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("تأكيد الجدولة", callback_data="sched_confirm"),
        ],
    ])


# ---------------------------------------------------------------------------
# Sysinfo keyboards
# ---------------------------------------------------------------------------

def sysinfo_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for /sysinfo resource monitoring view."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث الموارد", callback_data="sysinfo:refresh")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="sysinfo:back")],
    ])


# ---------------------------------------------------------------------------
# Personal Mode keyboards
# ---------------------------------------------------------------------------

def personal_keyboard() -> InlineKeyboardMarkup:
    """Main menu keyboard for Personal Use Mode."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("مهمة جديدة للـ Agent", callback_data="personal:task"),
        ],
        [
            InlineKeyboardButton("تصفح الملفات", callback_data="personal:ls"),
            InlineKeyboardButton("تحميل ملف", callback_data="personal:getfile"),
        ],
        [
            InlineKeyboardButton("أمر Shell", callback_data="personal:shell"),
            InlineKeyboardButton("العمليات الجارية", callback_data="personal:ps"),
        ],
        [
            InlineKeyboardButton("حالة وموارد الجهاز (System)", callback_data="menu:sysinfo"),
        ],
        [
            InlineKeyboardButton("إغلاق Personal Mode نهائياً", callback_data="personal:killswitch_ask"),
        ],
    ])


def personal_killswitch_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirmation keyboard for the one-way safety kill switch."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("نعم، إغلاق نهائي وقفل", callback_data="personal:killswitch_confirm"),
        ],
        [
            InlineKeyboardButton("رجوع والعودة", callback_data="personal:menu"),
        ],
    ])


def personal_task_running_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown when a personal agent task is running."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("إيقاف المهمة الآن", callback_data="personal:task_stop"),
        ],
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
                "استئناف المهمة المعلقة الآن",
                callback_data=f"acc_resume:{paused_job_id}",
            )
        ])

    for p in profiles:
        active_mark = ""if p.get("is_active") else ""
        name = p.get("name", "")
        username = p.get("username", "")
        btn_text = f"{active_mark} {name} ({username})"
        rows.append([
            InlineKeyboardButton(btn_text, callback_data=f"acc_switch:{name}")
        ])

    rows.append([
        InlineKeyboardButton("حفظ الجلسة الحالية كـ Profile", callback_data="acc_save"),
        InlineKeyboardButton("تحديث", callback_data="menu:accounts"),
    ])
    return InlineKeyboardMarkup(rows)
