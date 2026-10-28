"""Scheduling flows for GitHub Issues and user-authored local Codex prompts."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.helpers import escape_markdown

from app.github.client import github_client
from app.github.issues import issue_service
from app.runner.local_prompt_schedules import (
    LocalPromptScheduleStore,
    LocalPromptStatus,
    UntrustedScheduleStoreError,
)
from app.telegram.auth import auth_required
from app.telegram.callback_payloads import decode_callback_payload, encode_callback_payload
from app.telegram.keyboards import (
    CB_SCHEDULE_DEL,
    MAIN_MENU_GITHUB,
    MAIN_MENU_PERSONAL,
    MAIN_MENU_PROJECTS,
    MAIN_MENU_SCHEDULES,
    MAIN_MENU_STATUS,
    MAIN_MENU_SYSTEM,
    issues_keyboard,
    repos_keyboard,
)
from app.telegram.repository_search import filter_repositories
from app.utils.security import sanitize_for_telegram

logger = logging.getLogger(__name__)

(
    SCHED_SELECT_MODE,
    SCHED_SELECT_PROJECT,
    SCHED_SEARCH_PROJECT,
    SCHED_SELECT_REPO,
    SCHED_SEARCH_REPO,
    SCHED_SELECT_ISSUE,
    SCHED_ENTER_PROMPT,
    SCHED_ENTER_TIME,
    SCHED_CONFIRM,
    SCHED_EDIT_LOCAL_TIME,
    SCHED_EDIT_ISSUE_TIME,
    SCHED_SELECT_AGENT,
    SCHED_SELECT_EFFORT,
) = range(20, 33)

_PROJECTS_PER_PAGE = 8
_ISSUES_PER_PAGE = 10
_MENU_TEXT = filters.Regex(
    r"^(" + "|".join((MAIN_MENU_GITHUB, MAIN_MENU_PROJECTS, MAIN_MENU_SCHEDULES,
                       MAIN_MENU_STATUS, MAIN_MENU_SYSTEM, MAIN_MENU_PERSONAL)) + r")$"
)
_INPUT_TEXT = filters.TEXT & ~filters.COMMAND & ~_MENU_TEXT

def _device_now() -> datetime:
    """Return the current time using the host device's configured timezone."""
    return datetime.now().astimezone()


def _utc_now_from_device() -> datetime:
    return _device_now().astimezone(UTC)


def _schedule_time_hint() -> str:
    now = _device_now()
    example = now.replace(second=0, microsecond=0)
    future_example = example + timedelta(hours=1)
    return (
        f"وقت جهازك الآن: `{example.strftime('%d/%m %H:%M')}`. "
        f"مثال لموعد بعد ساعة: `{future_example.strftime('%d/%m %H:%M')}`."
    )


def _schedule_data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    """Return Telegram's per-user scheduler state after validating its availability."""
    if context.user_data is None:
        raise RuntimeError("Scheduler user state is unavailable")
    return context.user_data


def _mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📅 جدولة Issue", callback_data="sched_mode:issue")],
            [InlineKeyboardButton("🖥️ جدولة Prompt محلي", callback_data="sched_mode:local")],
        ]
    )


def _project_keyboard(projects: list, page: int) -> InlineKeyboardMarkup:
    start = page * _PROJECTS_PER_PAGE
    rows: list[list[InlineKeyboardButton]] = []
    for index, project in enumerate(projects[start : start + _PROJECTS_PER_PAGE], start=start):
        rows.append([InlineKeyboardButton(project.name[:56], callback_data=f"sched_project:{index}")])
    navigation: list[InlineKeyboardButton] = []
    if page:
        navigation.append(InlineKeyboardButton("السابق", callback_data=f"sched_project_page:{page - 1}"))
    if start + _PROJECTS_PER_PAGE < len(projects):
        navigation.append(InlineKeyboardButton("التالي", callback_data=f"sched_project_page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.extend(
        [
            [InlineKeyboardButton("بحث عن مشروع", callback_data="sched_project_search")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def _time_keyboard(mode: str = "local") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶️ تشغيل الآن", callback_data="sched_time:now")],
            [InlineKeyboardButton("🕐 بعد ساعة", callback_data="sched_time:hour")],
            [InlineKeyboardButton("✏️ اختر وقتًا آخر", callback_data="sched_time:custom")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data=f"sched_back:{'issues' if mode == 'issue' else 'prompt'}")],
        ]
    )


def _agent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Codex", callback_data="sched_agent:codex")],
            [InlineKeyboardButton("Antigravity", callback_data="sched_agent:antigravity")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="sched_back:time")],
        ]
    )


def _effort_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Low", callback_data="sched_effort:low")],
            [InlineKeyboardButton("Medium", callback_data="sched_effort:medium")],
            [InlineKeyboardButton("High", callback_data="sched_effort:high")],
            [InlineKeyboardButton("XHigh", callback_data="sched_effort:xhigh")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="sched_back:agent")],
        ]
    )


def _confirm_keyboard(mode: str = "local") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("تجربة قبل الجدولة", callback_data="sched_dryrun")],
            [InlineKeyboardButton("تأكيد الجدولة", callback_data="sched_confirm")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="sched_back:effort")],
        ]
    )


def _local_schedule_keyboard(schedules: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for schedule in schedules:
        label = f"{schedule.project_name} | {schedule.prompt.splitlines()[0]} — {schedule.display_time()}"
        rows.append([InlineKeyboardButton(label, callback_data=f"local_sched_open:{schedule.id}")])
    return InlineKeyboardMarkup(rows)


async def _local_schedule_stores(context: ContextTypes.DEFAULT_TYPE) -> list[LocalPromptScheduleStore]:
    return await LocalPromptScheduleStore.discover(context.bot_data.get("db"))


async def _local_schedules_for_chat(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> list:
    schedules = []
    for store in await _local_schedule_stores(context):
        try:
            schedules.extend(await store.list_all(chat_id=chat_id, ignore_invalid=True))
        except ValueError:
            continue
    return sorted(schedules, key=lambda item: item.scheduled_dt)


async def _find_local_schedule(
    context: ContextTypes.DEFAULT_TYPE, schedule_id: str, chat_id: int | None
) -> tuple[LocalPromptScheduleStore, Any] | None:
    if chat_id is None:
        return None
    for store in await _local_schedule_stores(context):
        try:
            schedule = await store.get(schedule_id)
        except ValueError:
            continue
        if schedule is not None and schedule.chat_id == chat_id:
            return store, schedule
    return None


def _current_chat_id(update: Update) -> int | None:
    return update.effective_chat.id if update.effective_chat else None


async def _find_issue_schedule(
    update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int
) -> Any | None:
    db = context.bot_data.get("db")
    chat_id = _current_chat_id(update)
    if db is None or chat_id is None:
        return None
    from app.database.schedule_repository import ScheduleRepository

    job = await ScheduleRepository(db).get(job_id)
    return job if job is not None and job.chat_id == chat_id else None


async def _show(update: Update, text: str, keyboard: InlineKeyboardMarkup) -> None:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


def _prompt_preview(prompt: str) -> str:
    return escape_markdown(sanitize_for_telegram(prompt, max_length=1800))


async def _send_long_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    if len(prompt) > 1800 and update.effective_chat is not None:
        content = sanitize_for_telegram(prompt, max_length=len(prompt) + 1)
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=BytesIO(content.encode("utf-8")), filename="prompt.md",
            caption="📝 نص الـPrompt كاملًا.",
        )


def _clear_schedule_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = _schedule_data(context)
    for key in list(data):
        if key.startswith("sched_"):
            data.pop(key, None)


@auth_required
async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_schedule_context(context)
    await _show(update, "*الجدولة*\nاختر نوع المهمة.", _mode_keyboard())
    return SCHED_SELECT_MODE


@auth_required
async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_schedule_context(context)
    requested_mode = (update.callback_query.data or "").removeprefix("menu:schedule:") if update.callback_query else ""
    if requested_mode == "issue":
        _schedule_data(context)["sched_mode"] = requested_mode
        _schedule_data(context)["sched_issue_from_github"] = True
        return await _show_repo_select(update, context)
    if requested_mode == "local":
        _schedule_data(context)["sched_mode"] = requested_mode
        _schedule_data(context)["sched_from_github"] = True
        return await _show_projects(update, context)
    await _show(update, "*الجدولة*\nاختر نوع المهمة.", _mode_keyboard())
    return SCHED_SELECT_MODE


@auth_required
async def sched_mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    mode = (query.data or "").removeprefix("sched_mode:")
    if mode not in {"issue", "local"}:
        await query.answer("اختيار غير صالح", show_alert=True)
        return ConversationHandler.END
    _schedule_data(context)["sched_mode"] = mode
    return await _show_projects(update, context)


async def _show_projects(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> int:
    db = context.bot_data.get("db")
    user = update.effective_user
    if db is None or user is None:
        await _show(update, "قاعدة البيانات غير متاحة.", _mode_keyboard())
        return ConversationHandler.END
    from app.database.repository import SavedProjectRepository

    projects = await SavedProjectRepository(db).list_projects(user.id)
    data = _schedule_data(context)
    data["sched_projects"] = projects
    if not projects:
        await _show(update, "📁 لا توجد مشاريع محلية محفوظة. أضف مشروعًا من زر المشاريع المحلية أولًا.", _mode_keyboard())
        return ConversationHandler.END
    mode = data.get("sched_mode")
    title = "اختر المشروع المحلي أولًا." if mode == "issue" else "اختر المشروع المحلي لتشغيل الـ Prompt عليه."
    keyboard = _project_keyboard(projects, page)
    rows = [list(row) for row in keyboard.inline_keyboard]
    back = "github" if data.get("sched_from_github") else "mode"
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data=f"sched_back:{back}")])
    await _show(update, f"*الجدولة*\n{title}", InlineKeyboardMarkup(rows))
    return SCHED_SELECT_PROJECT


@auth_required
async def sched_project_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    page = int((query.data or "").removeprefix("sched_project_page:"))
    return await _show_projects(update, context, page)


@auth_required
async def sched_project_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    await query.edit_message_text(
        "أرسل حرفًا أو جزءًا من اسم المشروع.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="sched_back:projects")]]),
    )
    return SCHED_SEARCH_PROJECT


@auth_required
async def sched_project_search_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message
    search = (update.message.text or "").strip().casefold()
    data = _schedule_data(context)
    projects = data.get("sched_projects", [])
    matches = [project for project in projects if search and search in project.name.casefold()]
    if not matches:
        await update.message.reply_text("لم يتم العثور على مشروع. أرسل بحثًا آخر.")
        return SCHED_SEARCH_PROJECT
    data["sched_projects"] = matches
    await update.message.reply_text("نتائج البحث:", reply_markup=_project_keyboard(matches, 0))
    return SCHED_SELECT_PROJECT


async def _register_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    assert query
    data = _schedule_data(context)
    projects = data.get("sched_projects", [])
    try:
        index = int((query.data or "").removeprefix("sched_project:"))
        project = projects[index]
    except (IndexError, ValueError):
        await query.answer("تعذر العثور على المشروع", show_alert=True)
        return None
    data = _schedule_data(context)
    data["sched_project_id"] = f"saved:{project.id}"
    data["sched_project_name"] = project.name
    data["sched_project_path"] = project.path
    data["sched_project_repo"] = project.repo_full_name

    db = context.bot_data.get("db")
    if db is not None and update.effective_user is not None:
        from app.database.repository import SelectedProjectRepository

        await SelectedProjectRepository(db).save(
            update.effective_user.id,
            project.name,
            project.path,
            project.repo_full_name,
        )
    return project


@auth_required
async def sched_project_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    project = await _register_project(update, context)
    if project is None:
        return SCHED_SELECT_PROJECT
    if _schedule_data(context).get("sched_mode") == "local":
        return await _ask_for_prompt(update, context)
    return await _show_repo_select(update, context)


async def _show_repo_select(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> int:
    try:
        repos = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.list_repos(page=page, per_page=8)
        )
    except Exception as exc:
        logger.warning("Failed to list repositories for schedule: %s", exc)
        await _show(update, "تعذر تحميل المستودعات.", _mode_keyboard())
        return ConversationHandler.END
    keyboard = repos_keyboard(
        repos,
        page=page,
        callback_prefix="sched_repo:",
        page_prefix="sched_repo_page:",
        include_cancel=False,
    )
    rows = [[InlineKeyboardButton("بحث عن مستودع", callback_data="sched_repo_search")]]
    rows.extend([list(row) for row in keyboard.inline_keyboard])
    back_target = "github" if _schedule_data(context).get("sched_issue_from_github") else "projects"
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data=f"sched_back:{back_target}")])
    keyboard = InlineKeyboardMarkup(rows)
    await _show(update, "*الـ Repository*\nاختر المستودع الذي يحتوي على الـ Issue.", keyboard)
    return SCHED_SELECT_REPO


@auth_required
async def sched_repo_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    page = int((query.data or "").removeprefix("sched_repo_page:"))
    return await _show_repo_select(update, context, page)


@auth_required
async def sched_repo_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    await query.edit_message_text(
        "أرسل حرفًا أو جزءًا من اسم المستودع.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="sched_back:repos")]]),
    )
    return SCHED_SEARCH_REPO


@auth_required
async def sched_repo_search_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message
    search_text = (update.message.text or "").strip()
    if not search_text:
        await update.message.reply_text("أرسل حرفًا واحدًا على الأقل.")
        return SCHED_SEARCH_REPO
    try:
        repos = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.list_repos(page=0, per_page=100)
        )
    except Exception as exc:
        logger.warning("Failed to search repositories: %s", exc)
        await update.message.reply_text("تعذر البحث في المستودعات.")
        return SCHED_SEARCH_REPO
    matches = filter_repositories(repos, search_text)
    if not matches:
        await update.message.reply_text("لم يتم العثور على مستودع. أرسل بحثًا آخر.")
        return SCHED_SEARCH_REPO
    await update.message.reply_text(
        "نتائج البحث:",
        reply_markup=repos_keyboard(matches, callback_prefix="sched_repo:", page_prefix="sched_repo_page:"),
    )
    return SCHED_SELECT_REPO


@auth_required
async def sched_repo_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    full_name = await decode_callback_payload(query, "sched_repo:")
    if full_name is None:
        return SCHED_SELECT_REPO
    data = _schedule_data(context)
    expected_repo = data.get("sched_project_repo")
    if expected_repo and expected_repo.casefold() != full_name.casefold():
        await _show(
            update,
            f"المستودع المحلي مرتبط بـ `{expected_repo}`. اختر نفس المستودع لتجنب تنفيذ الـ Issue في مشروع خاطئ.",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ العودة للمستودعات", callback_data="sched_back:repos")]]),
        )
        return SCHED_SELECT_REPO
    data["sched_repo"] = full_name
    return await _show_issue_select(update, context, full_name)


async def _show_issue_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, full_name: str, page: int = 0
) -> int:
    try:
        issues = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: issue_service.list_issues(full_name, state="open", page=page, per_page=_ISSUES_PER_PAGE),
        )
    except Exception as exc:
        logger.warning("Failed to list issues for schedule: %s", exc)
        await _show(update, "تعذر تحميل الـ Issues.", _mode_keyboard())
        return ConversationHandler.END
    if not issues:
        await _show(
            update,
            "لا توجد Issues مفتوحة في هذا المستودع.",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="sched_back:repos")]]),
        )
        return SCHED_SELECT_REPO
    keyboard = issues_keyboard(
        issues,
        full_name,
        page=page,
        callback_prefix="sched_issue:",
        page_prefix="sched_issue_page:",
        include_cancel=False,
    )
    rows = [list(row) for row in keyboard.inline_keyboard]
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="sched_back:repos")])
    keyboard = InlineKeyboardMarkup(rows)
    await _show(update, f"*الـ Issues*\n`{full_name}`", keyboard)
    return SCHED_SELECT_ISSUE


@auth_required
async def sched_issue_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    full_name = _schedule_data(context).get("sched_repo", "")
    if not full_name:
        return await _show_projects(update, context)
    page = int((query.data or "").removeprefix("sched_issue_page:"))
    return await _show_issue_select(update, context, full_name, page)


@auth_required
async def sched_issue_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    _schedule_data(context)["sched_issue"] = int((query.data or "").removeprefix("sched_issue:"))
    return await _ask_for_time(update, context)


async def _ask_for_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = _schedule_data(context)
    if not data.get("sched_project_name"):
        db = context.bot_data.get("db")
        user = update.effective_user
        if db is not None and user is not None:
            from app.database.repository import SavedProjectRepository, SelectedProjectRepository

            selected = await SelectedProjectRepository(db).get(user.id)
            if selected and selected.path:
                saved = await SavedProjectRepository(db).list_projects(user.id)
                for p in saved:
                    if p.path == selected.path:
                        data["sched_project_id"] = f"saved:{p.id}"
                        data["sched_project_name"] = selected.name
                        data["sched_project_path"] = selected.path
                        data["sched_project_repo"] = selected.repo_full_name
                        break

    if not data.get("sched_project_name"):
        return await _show_projects(update, context)

    project_name = data.get("sched_project_name", "")
    project_path = data.get("sched_project_path", "")
    if project_name and project_path:
        project_header = f"📁 *المشروع المستهدف:* `{project_name}`\n📍 *المسار:* `{project_path}`\n\n"
    else:
        project_header = f"📁 *المشروع المستهدف:* `{project_name}`\n\n"

    back = "projects" if data.get("sched_mode") == "local" else ("github" if data.get("sched_from_github") else "mode")
    await _show(
        update,
        f"{project_header}📝 اكتب الـ Prompt بوضوح:\n"
        "1. الهدف المطلوب.\n2. الملفات أو السلوك الذي تريد تغييره.\n"
        "3. شروط القبول أو الاختبار.\n\n"
        "مثال: `أضف تحققًا لرقم الهاتف في التسجيل، واكتب اختبارًا للحالات الصحيحة والخاطئة.`",
        InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data=f"sched_back:{back}")]]),
    )
    return SCHED_ENTER_PROMPT


@auth_required
async def sched_prompt_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message
    prompt = (update.message.text or "").strip()
    if not prompt:
        await update.message.reply_text("أرسل Prompt غير فارغ.")
        return SCHED_ENTER_PROMPT
    if len(prompt) > 12000:
        await update.message.reply_text("الـ Prompt طويل جدًا. الحد هو 12000 حرف.")
        return SCHED_ENTER_PROMPT
    data = _schedule_data(context)
    data["sched_prompt"] = prompt
    data["sched_prompt_label"] = prompt.splitlines()[0]
    return await _ask_for_time(update, context)


async def _ask_for_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = _schedule_data(context)
    if data.get("sched_mode") == "issue":
        heading = f"📅 Issue `#{data.get('sched_issue', '?')}` في `{data.get('sched_repo', '?')}`"
    else:
        project_name = data.get("sched_project_name", "")
        project_path = data.get("sched_project_path", "")
        if project_name and project_path:
            project_info = f"📁 *المشروع المستهدف:* `{project_name}`\n📍 *المسار:* `{project_path}`\n"
        elif project_name:
            project_info = f"📁 *المشروع المستهدف:* `{project_name}`\n"
        else:
            project_info = ""
        prompt_snippet = _prompt_preview(data.get("sched_prompt", ""))
        prompt_info = f"📝 *الـ Prompt:*\n{prompt_snippet}\n\n" if prompt_snippet else ""
        heading = f"{project_info}{prompt_info}📅 اختر موعد تشغيل الـPrompt."
    await _show(
        update,
        f"{heading}\n{_schedule_time_hint()}",
        _time_keyboard(str(data.get("sched_mode", "local"))),
    )
    return SCHED_ENTER_TIME


@auth_required
async def sched_time_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    choice = (query.data or "").removeprefix("sched_time:")
    if choice == "now":
        _schedule_data(context)["sched_run_now"] = True
        _schedule_data(context)["sched_dt"] = _utc_now_from_device().isoformat()
        return await _show_agent_select(update, context)
    if choice == "hour":
        _schedule_data(context)["sched_run_now"] = False
        _schedule_data(context)["sched_dt"] = (_utc_now_from_device() + timedelta(hours=1)).isoformat()
        return await _show_agent_select(update, context)
    if choice == "custom":
        await query.answer()
        await query.edit_message_text(
            _schedule_time_hint(), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="sched_back:time")]]),
        )
        return SCHED_ENTER_TIME
    await query.answer("اختيار وقت غير صالح", show_alert=True)
    return SCHED_ENTER_TIME


def _parse_user_time(text: str, year: int) -> datetime:
    try:
        local_time = datetime.strptime(f"{text.strip()} {year}", "%d/%m %H:%M %Y")
        return local_time.replace(tzinfo=_device_now().tzinfo).astimezone(UTC)
    except ValueError as exc:
        raise ValueError("Invalid schedule time") from exc


@auth_required
async def sched_time_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message
    try:
        scheduled_dt = _parse_user_time(update.message.text or "", _device_now().year)
    except ValueError:
        await update.message.reply_text(
            f"لم أتمكن من قراءة الوقت.\n{_schedule_time_hint()}", parse_mode="Markdown"
        )
        return SCHED_ENTER_TIME
    if scheduled_dt <= _utc_now_from_device():
        await update.message.reply_text("أدخل موعدًا مستقبليًا.")
        return SCHED_ENTER_TIME
    _schedule_data(context)["sched_dt"] = scheduled_dt.isoformat()
    _schedule_data(context)["sched_run_now"] = False
    return await _show_agent_select(update, context)


async def _show_agent_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = _schedule_data(context)
    project_name = data.get("sched_project_name", "")
    target = f" لمشروع `{project_name}`" if project_name else ""
    await _show(update, f"🤖 اختر الـ Agent الذي سينفذ المهمة{target}.", _agent_keyboard())
    return SCHED_SELECT_AGENT


@auth_required
async def sched_agent_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    agent = (query.data or "").removeprefix("sched_agent:")
    if agent not in {"codex", "antigravity"}:
        await query.answer("اختيار Agent غير صالح", show_alert=True)
        return SCHED_SELECT_AGENT
    data = _schedule_data(context)
    data["sched_agent"] = agent
    data["sched_model"] = "gpt-5.6-terra" if agent == "codex" else "default"
    project_name = data.get("sched_project_name", "")
    target = f" للمشروع `{project_name}`" if project_name else ""
    await _show(update, f"⚙️ اختر قوة التنفيذ{target}.", _effort_keyboard())
    return SCHED_SELECT_EFFORT


@auth_required
async def sched_effort_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    effort = (query.data or "").removeprefix("sched_effort:")
    if effort not in {"low", "medium", "high", "xhigh"}:
        await query.answer("اختيار قوة غير صالح", show_alert=True)
        return SCHED_SELECT_EFFORT
    _schedule_data(context)["sched_effort"] = effort
    await _show_confirmation(update, context)
    return SCHED_CONFIRM


def _confirmation_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    data = _schedule_data(context)
    mode = data.get("sched_mode")
    scheduled_dt = datetime.fromisoformat(data["sched_dt"])
    lines = ["📅 *تأكيد الجدولة*"]
    if mode == "issue":
        lines.extend([f"المستودع: `{data.get('sched_repo', '?')}`", f"Issue: `#{data.get('sched_issue', '?')}`"])
    else:
        lines.extend(
            [
                f"المشروع: `{data.get('sched_project_name', '?')}`",
                f"Prompt:\n{_prompt_preview(data.get('sched_prompt', ''))}",
            ]
        )
    lines.extend(
        [
            f"Agent: `{data.get('sched_agent', '?')}`",
            f"القوة: `{data.get('sched_effort', '?')}`",
        ]
    )
    lines.append(f"الموعد: `{scheduled_dt.astimezone().strftime('%d/%m/%Y %H:%M')}`")
    return "\n".join(lines)


async def _show_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, extra: str = "") -> None:
    text = _confirmation_text(context)
    if extra:
        text += f"\n\n{extra}"
    await _show(update, text, _confirm_keyboard(str(_schedule_data(context).get('sched_mode', 'local'))))
    await _send_long_prompt(update, context, _schedule_data(context).get("sched_prompt", ""))


@auth_required
async def sched_dryrun_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = _schedule_data(context)
    result = "فشل الفحص"
    project_path = data.get("sched_project_path")
    if project_path and Path(project_path).is_dir():
        result = f"نجح الفحص: المشروع `{data.get('sched_project_name', 'المشروع')}` جاهز للتشغيل المحلي."
    else:
        result = "فشل الفحص: لم يتم العثور على مسار المشروع المحفوظ."
    await _show_confirmation(update, context, result)
    return SCHED_CONFIRM


@auth_required
async def sched_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    data = _schedule_data(context)
    mode = data.get("sched_mode")
    project_id = data.get("sched_project_id")
    project_name = data.get("sched_project_name")
    dt_iso = data.get("sched_dt")
    agent = data.get("sched_agent")
    effort = data.get("sched_effort")
    if not dt_iso or not agent or not effort or (mode == "local" and (not project_id or not project_name)):
        await _show(update, "البيانات ناقصة. ابدأ الجدولة مرة أخرى.", _mode_keyboard())
        return ConversationHandler.END
    scheduled_dt = datetime.fromisoformat(dt_iso)
    if data.get("sched_run_now"):
        scheduled_dt = _utc_now_from_device()
    elif scheduled_dt <= _utc_now_from_device():
        await _show(update, "الموعد مرّ بالفعل. اختر موعدًا جديدًا.", _time_keyboard(str(mode)))
        return SCHED_ENTER_TIME
    chat_id = update.effective_chat.id if update.effective_chat else 0
    if mode == "local":
        prompt = data.get("sched_prompt", "")
        local_project_id = str(project_id)
        local_project_name = str(project_name)
        project_path = Path(str(data.get("sched_project_path", "")))
        if not project_path.is_dir():
            await _show(update, "مسار المشروع غير متاح.", _mode_keyboard())
            return ConversationHandler.END
        try:
            schedule = await LocalPromptScheduleStore.for_project(project_path).create(
                project_id=local_project_id,
                project_name=local_project_name,
                prompt=prompt,
                scheduled_at=scheduled_dt,
                chat_id=chat_id,
                agent=str(agent),
                model=str(data.get("sched_model", "default")),
                reasoning_effort=str(effort),
                db=context.bot_data.get("db"),
            )
        except UntrustedScheduleStoreError:
            return await _show_csv_trust_preview(
                update, context, LocalPromptScheduleStore.for_project(project_path), retry=True
            )
        except ValueError as exc:
            await _show(update, str(exc), _mode_keyboard())
            return ConversationHandler.END
        from app.runner.scheduler import job_scheduler

        job_scheduler.wake()
        _clear_schedule_context(context)
        await _show(
            update,
            f"تمت جدولة Prompt محلي.\nالمعرف: `{schedule.id}`\nالموعد: `{schedule.display_time()}`\nCSV: `{project_path / 'local_prompt_schedules.csv'}`",
            InlineKeyboardMarkup([[InlineKeyboardButton("المهام المجدولة", callback_data="sched_list")]]),
        )
        return ConversationHandler.END
    repo_full_name = data.get("sched_repo", "")
    issue_number = data.get("sched_issue")
    if not repo_full_name or issue_number is None:
        await _show(update, "بيانات الـ Issue ناقصة.", _mode_keyboard())
        return ConversationHandler.END
    db = context.bot_data.get("db")
    if db is None:
        await _show(update, "قاعدة البيانات غير متاحة.", _mode_keyboard())
        return ConversationHandler.END
    from app.database.schedule_repository import ScheduleRepository

    saved = await ScheduleRepository(db).create(
        repo_full_name=repo_full_name,
        issue_number=int(issue_number),
        agent=str(agent),
        scheduled_at=scheduled_dt,
        chat_id=chat_id,
        project_id=project_id,
        project_name=project_name,
        workspace_path=data.get("sched_project_path"),
        model=data.get("sched_model"),
        reasoning_effort=str(effort),
    )
    project_line = f" للمشروع `{project_name}`" if project_name else ""
    from app.runner.scheduler import job_scheduler

    job_scheduler.wake()
    _clear_schedule_context(context)
    await _show(
        update,
        f"تمت جدولة Issue `#{saved.issue_number}`{project_line}.\nالموعد: `{saved.display_time()}`",
        InlineKeyboardMarkup([[InlineKeyboardButton("المهام المجدولة", callback_data="sched_list")]]),
    )
    return ConversationHandler.END


async def _show_all_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else 0
    issue_jobs = []
    db = context.bot_data.get("db")
    if db is not None:
        from app.database.schedule_repository import ScheduleRepository

        issue_jobs = await ScheduleRepository(db).list_all(chat_id=chat_id)
    local_jobs = await _local_schedules_for_chat(context, chat_id)
    rows: list[list[InlineKeyboardButton]] = []
    for job in issue_jobs:
        project = f" | {job.project_name}" if job.project_name else ""
        label = f"{job.repo_full_name.split('/')[-1]} #{job.issue_number}{project} — {job.display_time()}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"sched_open:{job.id}")])
    rows.extend([list(row) for row in _local_schedule_keyboard(local_jobs).inline_keyboard])
    rows.insert(0, [InlineKeyboardButton("➕ إنشاء مهمة مجدولة", callback_data="menu:schedule")])
    if db is not None:
        for store in (await LocalPromptScheduleStore.discover_untrusted(db))[:8]:
            rows.append([InlineKeyboardButton(
                f"📄 اعتماد CSV: {store.path.parent.name}",
                callback_data=encode_callback_payload("csv_trust:", str(store.path)),
            )])
    text = "📅 *المهام المجدولة*\nاختر مهمة لعرض خياراتها." if issue_jobs or local_jobs else "📅 لا توجد مهام مجدولة حاليًا."
    await _show(update, text, InlineKeyboardMarkup(rows))


async def _show_csv_trust_preview(
    update: Update, context: ContextTypes.DEFAULT_TYPE, store: LocalPromptScheduleStore,
    *, retry: bool = False,
) -> int:
    db = context.bot_data.get("db")
    candidates = await LocalPromptScheduleStore.discover_untrusted(db) if db is not None else []
    if store.path not in {item.path for item in candidates}:
        assert update.callback_query
        await update.callback_query.answer("ملف الجدولة غير متاح لهذا المستخدم.", show_alert=True)
        return SCHED_CONFIRM if retry else ConversationHandler.END
    try:
        schedules = await store.list_all()
    except (ValueError, OSError):
        await _show(update, "تعذر قراءة CSV. صحح الملف ثم أعد المحاولة.", InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ رجوع للمهام", callback_data="sched_list")],
        ]))
        return ConversationHandler.END
    _schedule_data(context)["csv_trust_pending"] = {"path": str(store.path), "retry": retry}
    previews = "\n".join(_prompt_preview(item.prompt.splitlines()[0][:120]) for item in schedules[:5])
    text = (
        "📄 *اعتماد ملف جدولة محلي*\n"
        f"{_prompt_preview(str(store.path))}\nعدد المهام: {len(schedules)}\n{previews}\n\n"
        "بعد التأكيد ستُشغّل مهام هذا الملف حسب مواعيدها، بما فيها المهام التي تضيفها إليه لاحقًا. "
        "راجع محتواه أولًا واعتمده فقط إذا كان من إعدادك."
    )
    await _show(update, text, InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اعتماد ملف CSV", callback_data="csv_trust_confirm")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="csv_trust_back")],
    ]))
    return SCHED_CONFIRM if retry else ConversationHandler.END


@auth_required
async def csv_trust_preview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    path = await decode_callback_payload(query, "csv_trust:")
    if path is None:
        return ConversationHandler.END
    return await _show_csv_trust_preview(update, context, LocalPromptScheduleStore(Path(path)))


@auth_required
async def csv_trust_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    pending = _schedule_data(context).pop("csv_trust_pending", None)
    db = context.bot_data.get("db")
    if pending is None or db is None or update.effective_user is None:
        await query.answer("انتهى هذا التأكيد. افتح المهام المجدولة مجددًا.", show_alert=True)
        return ConversationHandler.END
    store = LocalPromptScheduleStore(Path(pending["path"]))
    try:
        await store.trust_existing(db, update.effective_user.id)
    except (ValueError, OSError):
        await _show(update, "تعذر اعتماد الملف. تحقق من محتواه وملكية المشروع.", InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ رجوع للمهام", callback_data="sched_list")],
        ]))
        return ConversationHandler.END
    if pending.get("retry"):
        return await sched_confirm_callback(update, context)
    from app.runner.scheduler import job_scheduler

    job_scheduler.wake()
    await _show_all_schedules(update, context)
    return ConversationHandler.END


@auth_required
async def csv_trust_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pending = _schedule_data(context).pop("csv_trust_pending", None)
    if pending and pending.get("retry"):
        await _show_confirmation(update, context)
        return SCHED_CONFIRM
    await _show_all_schedules(update, context)
    return ConversationHandler.END


@auth_required
async def schedule_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_all_schedules(update, context)


@auth_required
async def sched_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    db = context.bot_data.get("db")
    if db is not None:
        from app.database.schedule_repository import ScheduleRepository

        job_id = int((query.data or "").removeprefix(CB_SCHEDULE_DEL))
        if await _find_issue_schedule(update, context, job_id) is not None:
            await ScheduleRepository(db).cancel(job_id)
    await _show_all_schedules(update, context)


@auth_required
async def sched_local_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    schedule_id = (query.data or "").removeprefix("local_sched_del:")
    found = await _find_local_schedule(context, schedule_id, _current_chat_id(update))
    if found is not None:
        store, _ = found
        await store.cancel(schedule_id)
    await _show_all_schedules(update, context)


async def _show_issue_schedule_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int) -> None:
    job = await _find_issue_schedule(update, context, job_id)
    if job is None:
        await _show_all_schedules(update, context)
        return
    text = (
        f"📅 *مهمة Issue مجدولة*\nالمستودع: `{job.repo_full_name}`\n"
        f"Issue: `#{job.issue_number}`\nالموعد: `{job.display_time()}`\nالحالة: `{job.status}`"
    )
    rows: list[list[InlineKeyboardButton]] = []
    if job.status == "PENDING":
        rows.append([InlineKeyboardButton("▶️ ابدأ الآن", callback_data=f"sched_now:{job.id}")])
        rows.append([InlineKeyboardButton("✏️ تعديل الموعد", callback_data=f"sched_time:{job.id}")])
        rows.append([InlineKeyboardButton("🗑️ حذف المهمة", callback_data=f"{CB_SCHEDULE_DEL}{job.id}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع للمهام", callback_data="sched_list")])
    await _show(update, text, InlineKeyboardMarkup(rows))


@auth_required
async def sched_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    await _show_issue_schedule_detail(update, context, int((query.data or "").removeprefix("sched_open:")))


@auth_required
async def sched_local_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    schedule_id = (query.data or "").removeprefix("local_sched_open:")
    found = await _find_local_schedule(context, schedule_id, _current_chat_id(update))
    if found is None:
        await _show_all_schedules(update, context)
        return
    _, schedule = found
    text = (
        f"🖥️ *Prompt محلي مجدول*\nالمشروع: `{schedule.project_name}`\n"
        f"Prompt:\n{_prompt_preview(schedule.prompt)}\nالموعد: `{schedule.display_time()}`\n"
        f"Agent: `{schedule.agent}` | القوة: `{schedule.reasoning_effort}`\nالحالة: `{schedule.status}`"
    )
    rows: list[list[InlineKeyboardButton]] = []
    if schedule.status == LocalPromptStatus.PENDING:
        rows.append([InlineKeyboardButton("▶️ ابدأ الآن", callback_data=f"local_sched_now:{schedule.id}")])
        rows.append([InlineKeyboardButton("✏️ تعديل الموعد", callback_data=f"local_sched_time:{schedule.id}")])
        rows.append([InlineKeyboardButton("🗑️ حذف المهمة", callback_data=f"local_sched_del:{schedule.id}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع للمهام", callback_data="sched_list")])
    await _show(update, text, InlineKeyboardMarkup(rows))
    await _send_long_prompt(update, context, schedule.prompt)


@auth_required
async def sched_start_now_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    job_id = int((query.data or "").removeprefix("sched_now:"))
    db = context.bot_data.get("db")
    changed = False
    if db is not None and await _find_issue_schedule(update, context, job_id) is not None:
        from app.database.schedule_repository import ScheduleRepository

        changed = await ScheduleRepository(db).update_time(job_id, _utc_now_from_device())
    if changed:
        from app.runner.scheduler import job_scheduler

        job_scheduler.wake()
        await query.answer("تم وضع المهمة للتشغيل الآن.")
    await _show_issue_schedule_detail(update, context, job_id)


@auth_required
async def sched_local_start_now_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    schedule_id = (query.data or "").removeprefix("local_sched_now:")
    found = await _find_local_schedule(context, schedule_id, _current_chat_id(update))
    changed = await found[0].update_time(schedule_id, _utc_now_from_device()) if found else False
    if changed:
        from app.runner.scheduler import job_scheduler

        job_scheduler.wake()
        await query.answer("تم وضع المهمة للتشغيل الآن.")
    await _show_all_schedules(update, context)


@auth_required
async def sched_local_edit_time_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    schedule_id = (query.data or "").removeprefix("local_sched_time:")
    if await _find_local_schedule(context, schedule_id, _current_chat_id(update)) is None:
        await _show_all_schedules(update, context)
        return ConversationHandler.END
    _schedule_data(context)["sched_edit_local_id"] = schedule_id
    await query.answer()
    await query.edit_message_text(f"أرسل الموعد الجديد.\n{_schedule_time_hint()}", parse_mode="Markdown")
    return SCHED_EDIT_LOCAL_TIME


@auth_required
async def sched_issue_edit_time_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    job_id = int((query.data or "").removeprefix("sched_time:"))
    if await _find_issue_schedule(update, context, job_id) is None:
        await _show_all_schedules(update, context)
        return ConversationHandler.END
    _schedule_data(context)["sched_edit_issue_id"] = job_id
    await query.answer()
    await query.edit_message_text(
        f"✏️ أرسل الموعد الجديد.\n{_schedule_time_hint()}", parse_mode="Markdown"
    )
    return SCHED_EDIT_ISSUE_TIME


@auth_required
async def sched_local_edit_time_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message
    try:
        scheduled_dt = _parse_user_time(update.message.text or "", _device_now().year)
    except ValueError:
        await update.message.reply_text(
            f"لم أتمكن من قراءة الوقت.\n{_schedule_time_hint()}", parse_mode="Markdown"
        )
        return SCHED_EDIT_LOCAL_TIME
    if scheduled_dt <= _utc_now_from_device():
        await update.message.reply_text("أدخل موعدًا مستقبليًا.")
        return SCHED_EDIT_LOCAL_TIME
    schedule_id = _schedule_data(context).get("sched_edit_local_id", "")
    found = await _find_local_schedule(context, schedule_id, _current_chat_id(update))
    changed = await found[0].update_time(schedule_id, scheduled_dt) if found else False
    await update.message.reply_text(
        "تم تعديل الموعد." if changed else "لا يمكن تعديل هذه المهمة بعد بدئها.",
    )
    return ConversationHandler.END


@auth_required
async def sched_issue_edit_time_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message
    try:
        scheduled_dt = _parse_user_time(update.message.text or "", _device_now().year)
    except ValueError:
        await update.message.reply_text(
            f"لم أتمكن من قراءة الوقت.\n{_schedule_time_hint()}", parse_mode="Markdown"
        )
        return SCHED_EDIT_ISSUE_TIME
    if scheduled_dt <= _utc_now_from_device():
        await update.message.reply_text("أدخل موعدًا مستقبليًا.")
        return SCHED_EDIT_ISSUE_TIME
    db = context.bot_data.get("db")
    job_id = _schedule_data(context).get("sched_edit_issue_id")
    changed = False
    if (db is not None and isinstance(job_id, int)
            and await _find_issue_schedule(update, context, job_id) is not None):
        from app.database.schedule_repository import ScheduleRepository

        changed = await ScheduleRepository(db).update_time(job_id, scheduled_dt)
    await update.message.reply_text("✅ تم تعديل الموعد." if changed else "لا يمكن تعديل هذه المهمة بعد بدئها.")
    return ConversationHandler.END


@auth_required
async def sched_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    destination = (query.data or "").removeprefix("sched_back:")
    if destination == "github":
        from app.telegram.keyboards import github_menu_keyboard

        await _show(update, "🐙 *GitHub*\nاختر الإجراء المطلوب.", github_menu_keyboard())
        return ConversationHandler.END
    if destination == "projects":
        return await _show_projects(update, context)
    if destination == "mode":
        await _show(update, "*الجدولة*\nاختر نوع المهمة.", _mode_keyboard())
        return SCHED_SELECT_MODE
    if destination == "repos":
        return await _show_repo_select(update, context)
    if destination == "issues":
        repo_full_name = _schedule_data(context).get("sched_repo", "")
        if repo_full_name:
            return await _show_issue_select(update, context, repo_full_name)
        return await _show_repo_select(update, context)
    if destination == "agent":
        return await _show_agent_select(update, context)
    if destination == "effort":
        await _show(update, "⚙️ اختر قوة التنفيذ.", _effort_keyboard())
        return SCHED_SELECT_EFFORT
    if destination == "prompt":
        return await _ask_for_prompt(update, context)
    return await _ask_for_time(update, context)


async def schedule_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_schedule_context(context)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("⬅️ تم الرجوع.")
    return ConversationHandler.END


@auth_required
async def schedule_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from app.telegram.handlers.start import main_menu_message_handler

    _clear_schedule_context(context)
    await main_menu_message_handler(update, context)
    return ConversationHandler.END


def build_schedule_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("schedule", schedule_command),
            CallbackQueryHandler(schedule_start, pattern="^menu:schedule$"),
            CallbackQueryHandler(schedule_start, pattern="^menu:schedule:(issue|local)$"),
            CallbackQueryHandler(sched_local_edit_time_start, pattern="^local_sched_time:"),
            CallbackQueryHandler(sched_issue_edit_time_start, pattern=r"^sched_time:\d+$"),
        ],
        states={
            SCHED_SELECT_MODE: [CallbackQueryHandler(sched_mode_selected, pattern="^sched_mode:")],
            SCHED_SELECT_PROJECT: [
                CallbackQueryHandler(sched_project_page_callback, pattern="^sched_project_page:"),
                CallbackQueryHandler(sched_project_search_start, pattern="^sched_project_search$"),
                CallbackQueryHandler(sched_project_selected_callback, pattern="^sched_project:"),
            ],
            SCHED_SEARCH_PROJECT: [MessageHandler(_INPUT_TEXT, sched_project_search_received)],
            SCHED_SELECT_REPO: [
                CallbackQueryHandler(sched_repo_page_callback, pattern="^sched_repo_page:"),
                CallbackQueryHandler(sched_repo_search_start, pattern="^sched_repo_search$"),
                CallbackQueryHandler(sched_repo_selected_callback, pattern="^sched_repo:"),
            ],
            SCHED_SEARCH_REPO: [
                MessageHandler(_INPUT_TEXT, sched_repo_search_received),
                CallbackQueryHandler(sched_repo_selected_callback, pattern="^sched_repo:"),
            ],
            SCHED_SELECT_ISSUE: [
                CallbackQueryHandler(sched_issue_page_callback, pattern="^sched_issue_page:"),
                CallbackQueryHandler(sched_issue_selected_callback, pattern=r"^sched_issue:\d+$"),
            ],
            SCHED_ENTER_PROMPT: [MessageHandler(_INPUT_TEXT, sched_prompt_received)],
            SCHED_ENTER_TIME: [
                CallbackQueryHandler(sched_time_choice_callback, pattern="^sched_time:"),
                MessageHandler(_INPUT_TEXT, sched_time_received),
            ],
            SCHED_SELECT_AGENT: [CallbackQueryHandler(sched_agent_selected_callback, pattern="^sched_agent:")],
            SCHED_SELECT_EFFORT: [CallbackQueryHandler(sched_effort_selected_callback, pattern="^sched_effort:")],
            SCHED_CONFIRM: [
                CallbackQueryHandler(sched_dryrun_callback, pattern="^sched_dryrun$"),
                CallbackQueryHandler(sched_confirm_callback, pattern="^sched_confirm$"),
            ],
            SCHED_EDIT_LOCAL_TIME: [MessageHandler(_INPUT_TEXT, sched_local_edit_time_received)],
            SCHED_EDIT_ISSUE_TIME: [MessageHandler(_INPUT_TEXT, sched_issue_edit_time_received)],
        },
        fallbacks=[
            CallbackQueryHandler(csv_trust_confirm_callback, pattern="^csv_trust_confirm$"),
            CallbackQueryHandler(csv_trust_back_callback, pattern="^csv_trust_back$"),
            MessageHandler(_MENU_TEXT, schedule_main_menu),
            CallbackQueryHandler(sched_back_callback, pattern="^sched_back:"),
            CallbackQueryHandler(schedule_to_menu, pattern="^menu:start$"),
            CommandHandler("schedule", schedule_command),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
