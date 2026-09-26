"""
/newissue handler — multi-step ConversationHandler to create a GitHub issue.

Flow: Select Repo → Title → Body → Labels (optional) → Confirm → Create
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.github.assets import asset_uploader
from app.github.client import github_client
from app.github.issues import issue_service
from app.runner.project_scanner import project_scanner
from app.telegram.auth import auth_required
from app.telegram.keyboards import (
    cancel_keyboard,
    polish_newissue_keyboard,
    projects_keyboard,
    repos_keyboard,
)

logger = logging.getLogger(__name__)

# Conversation states
(
    NI_SELECT_REPO,
    NI_ENTER_TITLE,
    NI_ENTER_BODY,
    NI_ENTER_LABELS,
    NI_CONFIRM,
) = range(5)


@auth_required
async def newissue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    projects = project_scanner.scan()
    if projects:
        return await start_newissue_project_flow(update, context, page=0)
    assert update.message
    await update.message.reply_text("⏳ جاري جلب المستودعات من GitHub...")
    return await _show_repo_select_for_newissue(update, context)


async def start_newissue_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> int:
    """Entry point from menu:newissue."""
    projects = project_scanner.scan()
    if projects:
        return await start_newissue_project_flow(update, context, page=page)
    return await _show_repo_select_for_newissue(update, context, page=page)


async def start_newissue_project_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> int:
    """Show local projects for issue creation."""
    projects = project_scanner.scan()
    if not projects:
        return await _show_repo_select_for_newissue(update, context, page=page)

    if context.user_data is not None:
        context.user_data["ni_scanned_projects"] = projects

    keyboard = projects_keyboard(
        projects,
        page=page,
        callback_prefix="ni_proj:",
        page_prefix="ni_proj_page:",
        include_cancel=True,
        include_setdir=False,
    )
    from telegram import InlineKeyboardButton
    keyboard.inline_keyboard.insert(-1, [
        InlineKeyboardButton("🌐 تصفح كافة مستودعات GitHub", callback_data="ni_browse_github")
    ])

    text = f"➕ *إنشاء Issue جديد — اختر المشروع* (صفحة {page + 1}):\n\nاختر المشروع الذي ترغب في فتح Issue له:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    return NI_SELECT_REPO


async def ni_proj_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("ni_proj_page:", ""))
    return await start_newissue_project_flow(update, context, page=page)


async def ni_proj_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    raw_val = (query.data or "").replace("ni_proj:", "")
    projects = (context.user_data or {}).get("ni_scanned_projects") or project_scanner.scan()

    project = None
    if raw_val.isdigit():
        idx = int(raw_val)
        if 0 <= idx < len(projects):
            project = projects[idx]
    if not project:
        project = project_scanner.get_project_by_name(raw_val)

    if not project or not project.repo_full_name:
        await query.edit_message_text(
            f"⚠️ المشروع `{project.name if project else raw_val}` غير مرتبط بمستودع GitHub.\n"
            f"لا يمكن إنشاء Issue لهذا المجلد.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["ni_repo"] = project.repo_full_name

    await query.edit_message_text(
        f"✏️ *المشروع:* `{project.name}`\n"
        f"📦 *المستودع:* `{project.repo_full_name}`\n\n"
        f"يرجى إدخال *عنوان الـ Issue*:\n_(أو أرسل /cancel للإلغاء)_",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    return NI_ENTER_TITLE


async def start_newissue_for_repo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Direct entry when user tapped [➕ New Issue] on project detail view."""
    query = update.callback_query
    assert query
    await query.answer()

    full_name = (query.data or "").replace("newissue_for:", "")
    if context.user_data is not None:
        context.user_data["ni_repo"] = full_name

    await query.edit_message_text(
        f"✏️ *المستودع:* `{full_name}`\n\nيرجى إدخال *عنوان الـ Issue*:\n_(أو أرسل /cancel للإلغاء)_",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    return NI_ENTER_TITLE


async def _show_repo_select_for_newissue(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> int:
    try:
        repos = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.list_repos(page=page, per_page=8)
        )
    except Exception as exc:
        logger.error("Failed to list repos for newissue: %s", exc)
        msg = "❌ تعذر جلب المستودعات من GitHub. يرجى التحقق من بيانات الربط."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return ConversationHandler.END

    keyboard = repos_keyboard(
        repos,
        page=page,
        callback_prefix="newissue_repo:",
        page_prefix="newissue_repo_page:",
        include_cancel=True,
    )
    text = f"➕ *إنشاء Issue جديد*\n\nاختر المستودع المطلوب (صفحة {page + 1}):"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    return NI_SELECT_REPO


async def ni_repo_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("newissue_repo_page:", ""))
    return await _show_repo_select_for_newissue(update, context, page=page)


async def ni_repo_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    full_name = (query.data or "").replace("newissue_repo:", "")
    if context.user_data is not None:
        context.user_data["ni_repo"] = full_name

    await query.edit_message_text(
        f"✏️ *إنشاء Issue جديد*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *المستودع:* `{full_name}`\n\n"
        "📝 يرجى إدخال *عنوان الـ Issue* (Title):",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    return NI_ENTER_TITLE


async def ni_enter_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message and update.message.text
    title = update.message.text.strip()

    if len(title) < 3:
        await update.message.reply_text(
            "⚠️ يجب أن يتكون العنوان من 3 أحرف على الأقل. يرجى المحاولة مرة أخرى:",
            reply_markup=cancel_keyboard(),
        )
        return NI_ENTER_TITLE

    if context.user_data is not None:
        context.user_data["ni_title"] = title

    await update.message.reply_text(
        f"📝 *العنوان:* {title}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "الآن يرجى إدخال *وصف الـ Issue* (Description):\n"
        "📷 _يمكنك إرسال صورة/لقطة شاشة مع شرح، أو إرسال نص عادي، أو إرسال /skip لتركه فارغاً._",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return NI_ENTER_BODY


async def ni_enter_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message
    repo = (context.user_data or {}).get("ni_repo", "")

    # Check for photo attachment
    if update.message.photo:
        photo = update.message.photo[-1]
        caption = (update.message.caption or "").strip()
        status_msg = await update.message.reply_text("⏳ جاري رفع الصورة إلى GitHub...")
        try:
            tg_file = await context.bot.get_file(photo.file_id)
            photo_bytes = await tg_file.download_as_bytearray()
            asset_url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: asset_uploader.upload_issue_image(
                    repo_full_name=repo,
                    file_bytes=bytes(photo_bytes),
                    filename="screenshot.jpg",
                    mime_type="image/jpeg",
                ),
            )
            img_md = f"![Screenshot]({asset_url})"
            body = f"{caption}\n\n{img_md}".strip() if caption else img_md
            try:
                await status_msg.delete()
            except Exception:
                pass
        except Exception as exc:
            logger.error("Failed to upload image: %s", exc)
            await status_msg.edit_text(
                f"⚠️ فشل رفع الصورة: {str(exc)[:150]}\nيرجى كتابة وصف الـ Issue كنص:"
            )
            return NI_ENTER_BODY

    # Check for document image attachment
    elif (
        update.message.document
        and update.message.document.mime_type
        and update.message.document.mime_type.startswith("image/")
    ):
        doc = update.message.document
        caption = (update.message.caption or "").strip()
        status_msg = await update.message.reply_text("⏳ جاري رفع الصورة إلى GitHub...")
        try:
            tg_file = await context.bot.get_file(doc.file_id)
            doc_bytes = await tg_file.download_as_bytearray()
            filename = doc.file_name or "image.png"
            mime_type = doc.mime_type or "image/png"
            asset_url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: asset_uploader.upload_issue_image(
                    repo_full_name=repo,
                    file_bytes=bytes(doc_bytes),
                    filename=filename,
                    mime_type=mime_type,
                ),
            )
            img_md = f"![Screenshot]({asset_url})"
            body = f"{caption}\n\n{img_md}".strip() if caption else img_md
            try:
                await status_msg.delete()
            except Exception:
                pass
        except Exception as exc:
            logger.error("Failed to upload document image: %s", exc)
            await status_msg.edit_text(
                f"⚠️ فشل رفع الصورة: {str(exc)[:150]}\nيرجى كتابة وصف الـ Issue كنص:"
            )
            return NI_ENTER_BODY

    # Plain text input
    else:
        text = update.message.text or ""
        body = "" if text.strip() == "/skip" else text.strip()

    if context.user_data is not None:
        context.user_data["ni_body"] = body

    await update.message.reply_text(
        "🏷 *التصنيفات (Labels)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "أدخل التصنيفات مفصولة بفواصل (مثال: `bug, enhancement`)،\n"
        "أو أرسل `/skip` لعدم إضافة تصنيفات:",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return NI_ENTER_LABELS


async def ni_enter_labels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message
    text = update.message.text or ""

    labels: list[str] = []
    if text.strip() != "/skip":
        labels = [lb.strip() for lb in text.split(",") if lb.strip()]

    if context.user_data is not None:
        context.user_data["ni_labels"] = labels

    return await _show_confirm(update, context)


async def _show_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    ud = context.user_data or {}
    repo = ud.get("ni_repo", "?")
    title = ud.get("ni_title", "?")
    body = ud.get("ni_body", "")
    labels = ud.get("ni_labels", [])

    labels_str = ", ".join(f"`{lb}`" for lb in labels) if labels else "none"
    body_preview = (body[:200] + "...") if len(body) > 200 else body

    confirm_text = (
        f"📋 *مراجعة تفاصيل الـ Issue قبل الإنشاء*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *المستودع:* `{repo}`\n"
        f"📌 *العنوان:* {title}\n"
        f"🏷 *التصنيفات:* {labels_str}\n\n"
        f"📝 *الوصف:*\n{body_preview or '_(فارغ)_'}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "تأكد من صحة البيانات ثم اضغط تأكيد:"
    )

    if update.message:
        await update.message.reply_text(
            confirm_text,
            reply_markup=polish_newissue_keyboard(),
            parse_mode="Markdown",
        )
    return NI_CONFIRM


async def ni_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    if (query.data or "") == "cancel":
        await query.edit_message_text("❌ تم إلغاء إنشاء الـ Issue.")
        return ConversationHandler.END

    ud = context.user_data or {}
    repo = ud.get("ni_repo", "")
    title = ud.get("ni_title", "")
    body = ud.get("ni_body", "")
    labels = ud.get("ni_labels", [])

    await query.edit_message_text("⏳ جاري إنشاء الـ Issue على GitHub...")

    try:
        issue = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: issue_service.create_issue(repo, title, body, labels or None),
        )
    except Exception as exc:
        logger.error("Failed to create issue: %s", exc)
        await query.edit_message_text(f"❌ تعذر إنشاء الـ Issue: {str(exc)[:200]}")
        return ConversationHandler.END

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    success_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 فتح في GitHub", url=issue.html_url),
            InlineKeyboardButton("🚀 تشغيل الـ Agent", callback_data=f"run_issue:{issue.number}"),
        ],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu:start")],
    ])
    await query.edit_message_text(
        f"✅ *تم إنشاء الـ Issue بنجاح على GitHub!*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *المستودع:* `{repo}`\n"
        f"📌 *الـ Issue:* `#{issue.number}`\n"
        f"📝 *العنوان:* {issue.title}\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=success_kb,
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def ni_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data:
        for k in list(context.user_data.keys()):
            if k.startswith("ni_"):
                context.user_data.pop(k, None)
    if update.callback_query:
        await update.callback_query.answer()
        from app.telegram.handlers.start import WELCOME
        from app.telegram.keyboards import main_menu_keyboard
        await update.callback_query.edit_message_text(
            WELCOME, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
        )
    return ConversationHandler.END


async def ni_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ تم إلغاء إنشاء الـ Issue.")
    elif update.message:
        await update.message.reply_text("❌ تم إلغاء إنشاء الـ Issue.")
    return ConversationHandler.END


def build_newissue_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("newissue", newissue_command),
            CallbackQueryHandler(start_newissue_flow, pattern="^menu:newissue$"),
            CallbackQueryHandler(start_newissue_for_repo, pattern="^newissue_for:"),
        ],
        states={
            NI_SELECT_REPO: [
                CallbackQueryHandler(ni_proj_page_callback, pattern="^ni_proj_page:"),
                CallbackQueryHandler(ni_proj_selected, pattern="^ni_proj:"),
                CallbackQueryHandler(
                    lambda u, c: _show_repo_select_for_newissue(u, c, 0),
                    pattern="^ni_browse_github$",
                ),
                CallbackQueryHandler(ni_repo_page_callback, pattern="^newissue_repo_page:"),
                CallbackQueryHandler(ni_repo_selected, pattern="^newissue_repo:"),
                CallbackQueryHandler(ni_cancel, pattern="^cancel$"),
            ],
            NI_ENTER_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ni_enter_title),
                CallbackQueryHandler(ni_cancel, pattern="^cancel$"),
            ],
            NI_ENTER_BODY: [
                MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.IMAGE, ni_enter_body),
                CallbackQueryHandler(ni_cancel, pattern="^cancel$"),
            ],
            NI_ENTER_LABELS: [
                MessageHandler(filters.TEXT, ni_enter_labels),
                CallbackQueryHandler(ni_cancel, pattern="^cancel$"),
            ],
            NI_CONFIRM: [
                CallbackQueryHandler(ni_confirm, pattern="^(newissue:confirm|cancel)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", ni_cancel),
            CallbackQueryHandler(ni_cancel, pattern="^cancel$"),
            CallbackQueryHandler(ni_to_menu, pattern="^menu:start$"),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )
