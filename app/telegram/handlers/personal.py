"""
Advanced Personal Use Mode handlers.

Features:
1. One-Way Safety Kill Switch:
   - Can be turned OFF from Telegram at any time via /personal_off.
   - CANNOT be turned ON from Telegram (blocked by design).
   - Can only be re-enabled locally on the machine via: python -m app.main --enable-personal.
2. AI Agent Task Execution:
   - Run custom coding and analysis tasks directly on the machine via /task <prompt>.
   - Streams progress to Telegram in real-time.
   - Cancel anytime via /task_stop.
3. Bidirectional File Transfer:
   - Upload: Send any document/file to Telegram, saved safely to workspaces/personal/incoming/.
   - Download: Fetch files from the machine via /getfile <path> (with sensitive file protection).
4. System & Shell Utilities:
   - /shell <cmd>
   - /ls [path]
   - /ps
   - /pkill <name>
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.config.settings import settings
from app.runner.file_transfer import save_incoming_file, validate_download_path
from app.runner.personal_guard import (
    disable_personal_mode_from_telegram,
    is_personal_mode_active,
)
from app.runner.personal_task import personal_task_manager
from app.telegram.auth import auth_required
from app.telegram.keyboards import (
    main_menu_button,
    personal_keyboard,
    personal_killswitch_confirm_keyboard,
    personal_task_running_keyboard,
)

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 3500


def _check_personal_guard() -> tuple[bool, str]:
    """Check if Personal Mode is active. Returns (is_active, message_to_user)."""
    active, reason = is_personal_mode_active()
    if active:
        return True, ""

    if reason == "disabled_by_lockfile":
        msg = (
            "🔒 *وضع الاستخدام الشخصي مغلق نهائياً*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "تم إغلاق هذا الوضع عبر **قفل الأمان (Kill Switch)**.\n\n"
            "⛔ *لا يمكن إعادة تفعيله من تيليجرام لدواعي الأمان.*\n\n"
            "لإعادة التفعيل، يجب عليك تسجيل الدخول محلياً على الجهاز وتشغيل:\n"
            "`python -m app.main --enable-personal`"
        )
    else:
        msg = (
            "🔒 *وضع الاستخدام الشخصي غير مفعّل*\n\n"
            "أضف `PERSONAL_MODE=true` في ملف `.env` وأعد تشغيل البوت."
        )
    return False, msg


# ---------------------------------------------------------------------------
# Menu and Navigation
# ---------------------------------------------------------------------------

@auth_required
async def personal_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the personal use mode menu."""
    active, guard_msg = _check_personal_guard()
    if not active:
        if update.message:
            await update.message.reply_text(guard_msg, parse_mode="Markdown")
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(guard_msg, parse_mode="Markdown")
        return

    text = (
        "💻 *وضع الاستخدام الشخصي المتقدم (Personal Mode)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "تحكم مباشر وآمن في الجهاز عبر الـ AI:\n\n"
        "🤖 *الـ Agent:* `/task <المهمة>` — تنفيذ مهام برمجية وتحليل ملفات\n"
        "🛑 *إيقاف الـ Agent:* `/task_stop` — إيقاف المهمة الجارية\n"
        "📥 *إرسال ملف:* أرسل أي ملف/كود هنا لحفظه واستخدامه\n"
        "📤 *تحميل ملف:* `/getfile <مسار>` — تحميل ملف من الجهاز\n"
        "📁 *تصفح المجلدات:* `/ls [مسار]`\n"
        "⚡ *تشغيل أمر:* `/shell <أمر>`\n"
        "📊 *العمليات الجارية:* `/ps`\n"
        "🔒 *إغلاق أمني نهائي:* `/personal_off`\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    keyboard = personal_keyboard()
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# One-Way Safety Kill Switch (/personal_off & /personal_on)
# ---------------------------------------------------------------------------

@auth_required
async def personal_on_rejection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explicitly reject any attempt to enable Personal Mode from Telegram."""
    text = (
        "⛔ *عملية مرفوضة أمنياً*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "لا يمكن تفعيل وضع الاستخدام الشخصي من داخل تيليجرام تحت أي ظرف.\n\n"
        "🛡 تم تصميم هذا القفل لحماية جهازك من أي وصول غير مصرح به.\n\n"
        "لإعادة التفعيل، ادخل إلى الجهاز محلياً وشغّل:\n"
        "`python -m app.main --enable-personal`"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.answer("عملية مرفوضة", show_alert=True)
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")


@auth_required
async def personal_off_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt confirmation to permanently disable Personal Mode."""
    text = (
        "⚠️ *تأكيد إغلاق وضع الاستخدام الشخصي*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "هل أنت متأكد من رغبتك في إغلاق وضع الاستخدام الشخصي نهائياً؟\n\n"
        "🔴 *تنبيه أمني هام:*\n"
        "بمجرد الإغلاق، سيتم إنشاء قفل أمني محلي `.personal_mode_disabled`.\n"
        "**لن تتمكن من إعادة تفعيله من تيليجرام أبداً**، بل سيتطلب تشغيل أمر محلي من داخل السيرفر نفسه."
    )
    keyboard = personal_killswitch_confirm_keyboard()
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


@auth_required
async def personal_killswitch_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the permanent kill switch."""
    query = update.callback_query
    assert query
    await query.answer()

    user_id = update.effective_user.id if update.effective_user else 0
    success = disable_personal_mode_from_telegram(user_id)

    if success:
        text = (
            "🔒 *تم إغلاق وضع الاستخدام الشخصي وقفل الصلاحيات*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✅ تم إنشاء القفل الأمني `.personal_mode_disabled` بنجاح.\n"
            "✅ تم تعطيل كافة أوامر /task و /shell و /getfile واستقبال الملفات فوراً.\n\n"
            "💡 لإعادة التفعيل في المستقبل، يجب تشغيل هذا الأمر من terminal الجهاز نفسه:\n"
            "`python -m app.main --enable-personal`"
        )
    else:
        text = "❌ حدث خطأ أثناء تطبيق القفل الأمني على القرص."

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
    )


# ---------------------------------------------------------------------------
# AI Agent Tasks (/task and /task_stop)
# ---------------------------------------------------------------------------

@auth_required
async def task_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/task <instructions> — Execute an autonomous agent task locally."""
    active, guard_msg = _check_personal_guard()
    if not active:
        if update.message:
            await update.message.reply_text(guard_msg, parse_mode="Markdown")
        return

    assert update.message
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "📝 *الاستخدام:* `/task <تعليمات المهمة>`\n\n"
            "مثال: `/task افحص الملفات واكتب سكربت لتنظيف مجلد الـ logs`",
            parse_mode="Markdown",
        )
        return

    prompt = " ".join(args).strip()

    if personal_task_manager.is_busy():
        info = personal_task_manager.get_active_info()
        task_id = info.get("task_id", "") if info else ""
        await update.message.reply_text(
            f"⚠️ هناك مهمة أخرى قيد التشغيل حالياً (`{task_id}`).\n"
            "استخدم `/task_stop` لإيقافها أولاً.",
            parse_mode="Markdown",
            reply_markup=personal_task_running_keyboard(),
        )
        return

    status_msg = await update.message.reply_text(
        f"🤖 *بدء مهمة الـ Agent...*\n\n📋 *المهمة:* {prompt[:100]}...\n⏳ جاري الإعداد...",
        parse_mode="Markdown",
        reply_markup=personal_task_running_keyboard(),
    )

    last_update_time = [asyncio.get_event_loop().time()]

    async def _on_progress(progress_text: str) -> None:
        now = asyncio.get_event_loop().time()
        if now - last_update_time[0] < 2.0:
            return  # Throttle Telegram message edits
        last_update_time[0] = now
        try:
            display_text = (
                f"🤖 *مهمة الـ Agent قيد التنفيذ*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 *المهمة:* {prompt[:80]}\n\n"
                f"📡 *التقدم:*\n`{progress_text[-500:]}`"
            )
            await status_msg.edit_text(
                display_text,
                parse_mode="Markdown",
                reply_markup=personal_task_running_keyboard(),
            )
        except Exception:
            pass

    try:
        result = await personal_task_manager.run_task(
            prompt=prompt,
            on_progress=_on_progress,
        )

        icon = "✅" if result.success else "❌"
        summary_lines = [
            f"{icon} *اكتملت مهمة الـ Agent*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"⏱ *المدة:* `{result.duration_seconds}s`",
            f"📁 *مجلد العمل:* `{result.workspace_path.name}`",
            "",
        ]

        if result.files_created:
            summary_lines.append("📄 *ملفات جديدة تم إنشاؤها:*")
            for f in result.files_created[:8]:
                summary_lines.append(f"• `{f}`")
            summary_lines.append("")

        if result.files_modified:
            summary_lines.append("📝 *ملفات تم تعديلها:*")
            for f in result.files_modified[:8]:
                summary_lines.append(f"• `{f}`")
            summary_lines.append("")

        summary_lines.append(f"📋 *الملخص:*\n{result.summary[:1500]}")
        if result.error and not result.success:
            summary_lines.append(f"\n⚠️ *الخطأ:* `{result.error[:500]}`")

        final_text = "\n".join(summary_lines)
        if len(final_text) > _MAX_OUTPUT_CHARS:
            final_text = final_text[:_MAX_OUTPUT_CHARS] + "\n..."

        await status_msg.edit_text(
            final_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
        )

    except Exception as exc:
        logger.exception("Unexpected error in /task handler: %s", exc)
        await status_msg.edit_text(
            f"❌ حدث خطأ غير متوقع أثناء تشغيل المهمة: `{exc}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
        )


@auth_required
async def task_stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/task_stop — Cancel the active agent task."""
    stopped = await personal_task_manager.stop_task()
    msg = "🛑 تم إيقاف مهمة الـ Agent الجارية بنجاح." if stopped else "ℹ️ لا توجد مهمة قيد التشغيل حالياً."
    if update.message:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup([[main_menu_button()]]))
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            msg,
            reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
        )


# ---------------------------------------------------------------------------
# File Transfer: Upload (Telegram -> Machine)
# ---------------------------------------------------------------------------

@auth_required
async def incoming_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle files or documents sent by the authorized user."""
    active, guard_msg = _check_personal_guard()
    if not active:
        # If personal mode is off, ignore or explain
        if update.message:
            await update.message.reply_text(guard_msg, parse_mode="Markdown")
        return

    assert update.message
    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or "file.bin"
    status_msg = await update.message.reply_text(f"📥 جاري استقبال وحفظ الملف `{filename}`...", parse_mode="Markdown")

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = await tg_file.download_as_bytearray()

        success, msg, saved_path = save_incoming_file(
            content=bytes(file_bytes),
            original_filename=filename,
        )

        if not success or not saved_path:
            await status_msg.edit_text(f"❌ فشل الحفظ: {msg}")
            return

        size_kb = round(len(file_bytes) / 1024, 1)
        reply_text = (
            "📥 *تم حفظ الملف بنجاح على الجهاز*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 *اسم الملف:* `{saved_path.name}`\n"
            f"📊 *الحجم:* `{size_kb} KB`\n"
            f"📁 *المسار:* `{saved_path}`\n\n"
            "💡 *الإجراءات المتاحة:*\n"
            f"• تكليف الـ Agent: `/task افحص الملف {saved_path.name} و...`\n"
            f"• تشغيل سكربت: `/shell py \"{saved_path}\"`"
        )
        await status_msg.edit_text(
            reply_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🤖 تشغيل Agent على الملف", callback_data=f"personal:task_prompt:{saved_path.name}")],
                [main_menu_button()],
            ]),
        )
    except Exception as exc:
        logger.exception("Failed to process incoming file: %s", exc)
        await status_msg.edit_text(f"❌ حدث خطأ أثناء معالجة الملف: `{exc}`", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# File Transfer: Download (Machine -> Telegram)
# ---------------------------------------------------------------------------

@auth_required
async def getfile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/getfile <path> — Download a file from the host machine to Telegram."""
    active, guard_msg = _check_personal_guard()
    if not active:
        if update.message:
            await update.message.reply_text(guard_msg, parse_mode="Markdown")
        return

    assert update.message
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "📝 *الاستخدام:* `/getfile <مسار الملف>`\n\n"
            "مثال: `/getfile workspaces/personal/incoming/data.txt`",
            parse_mode="Markdown",
        )
        return

    target_str = " ".join(args).strip()
    valid, reason, file_path = validate_download_path(target_str)

    if not valid or not file_path:
        await update.message.reply_text(reason, parse_mode="Markdown")
        return

    status_msg = await update.message.reply_text(f"📤 جاري إرسال الملف `{file_path.name}`...", parse_mode="Markdown")

    try:
        with open(file_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=file_path.name,
                caption=f"📄 `{file_path.name}`\n📊 الحجم: {round(file_path.stat().st_size / 1024, 1)} KB",
                parse_mode="Markdown",
            )
        await status_msg.delete()
    except Exception as exc:
        logger.exception("Failed to send file %s: %s", file_path, exc)
        await status_msg.edit_text(f"❌ تعذر إرسال الملف: `{exc}`", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Shell / System Execution (/shell, /ls, /ps, /pkill)
# ---------------------------------------------------------------------------

@auth_required
async def shell_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/shell <command> — run a shell command and return output."""
    active, guard_msg = _check_personal_guard()
    if not active:
        if update.message:
            await update.message.reply_text(guard_msg, parse_mode="Markdown")
        return

    assert update.message
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "📝 *الاستخدام:* `/shell <الأمر>`\n\nمثال: `/shell dir` أو `/shell echo hello`",
            parse_mode="Markdown",
        )
        return

    command = " ".join(args)
    await update.message.reply_text(f"⚙️ جارٍ تشغيل: `{command}`", parse_mode="Markdown")

    try:
        result = await _run_shell(command)
        output = result.strip() or "(لا يوجد مخرجات)"
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[:_MAX_OUTPUT_CHARS] + "\n\n... (تم اقتصاص المخرجات)"
        await update.message.reply_text(
            f"```\n{output}\n```",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
        )
    except TimeoutError:
        await update.message.reply_text(
            f"⏱ انتهت المهلة الزمنية ({settings.PERSONAL_COMMAND_TIMEOUT}s) — الأمر أُوقف."
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ خطأ: `{exc}`", parse_mode="Markdown")


async def _run_shell(command: str) -> str:
    """Run a shell command and return combined stdout+stderr."""
    timeout = settings.PERSONAL_COMMAND_TIMEOUT
    if os.name == "nt":
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    else:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(errors="replace")
    except TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        raise


@auth_required
async def ls_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ls [path] — list directory contents."""
    active, guard_msg = _check_personal_guard()
    if not active:
        if update.message:
            await update.message.reply_text(guard_msg, parse_mode="Markdown")
        return

    assert update.message
    args = context.args or []
    target = Path(" ".join(args)) if args else Path.home()

    if not target.is_absolute():
        target = Path.home() / target

    target = target.resolve()

    if not target.exists():
        await update.message.reply_text(f"❌ المسار غير موجود: `{target}`", parse_mode="Markdown")
        return

    if not target.is_dir():
        await update.message.reply_text(f"⚠️ المسار ليس مجلداً: `{target}`", parse_mode="Markdown")
        return

    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        await update.message.reply_text("🚫 لا توجد صلاحيات لقراءة هذا المجلد.")
        return

    lines = [f"📂 `{target}`", ""]
    for entry in entries[:50]:
        if entry.is_dir():
            lines.append(f"📁 `{entry.name}/`")
        else:
            try:
                size_kb = round(entry.stat().st_size / 1024, 1)
                lines.append(f"📄 `{entry.name}` _({size_kb} KB)_")
            except OSError:
                lines.append(f"📄 `{entry.name}`")

    if len(entries) > 50:
        lines.append("\n_... (يُعرض أول 50 عنصر فقط)_")

    lines.append("\n💡 لتحميل أي ملف: `/getfile <المسار>`")

    text = "\n".join(lines)
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS] + "\n..."

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
    )


@auth_required
async def ps_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ps — list top processes sorted by memory usage."""
    active, guard_msg = _check_personal_guard()
    if not active:
        if update.message:
            await update.message.reply_text(guard_msg, parse_mode="Markdown")
        return

    assert update.message

    try:
        import psutil
    except ImportError:
        await update.message.reply_text("❌ مكتبة psutil غير مثبتة.", parse_mode="Markdown")
        return

    procs = []
    for proc in psutil.process_iter(["pid", "name", "memory_info", "status"]):
        try:
            info = proc.info
            mem_mb = round(info["memory_info"].rss / (1024 ** 2), 1) if info["memory_info"] else 0
            procs.append((info["pid"], info["name"] or "?", mem_mb, info.get("status", "?")))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    procs.sort(key=lambda x: x[2], reverse=True)
    top = procs[:15]

    lines = ["⚙️ *العمليات الجارية (أعلى 15 استهلاكاً للذاكرة)*", ""]
    for pid, name, mem_mb, status in top:
        lines.append(f"`{pid:6}` `{name[:20]:<20}` `{mem_mb:6.1f} MB` _{status}_")

    lines.append(f"\n_إجمالي العمليات: {len(procs)}_")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
    )


@auth_required
async def pkill_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pkill <name> — kill a process by name (with confirmation)."""
    active, guard_msg = _check_personal_guard()
    if not active:
        if update.message:
            await update.message.reply_text(guard_msg, parse_mode="Markdown")
        return

    assert update.message
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "📝 *الاستخدام:* `/pkill <اسم العملية>`\n\nمثال: `/pkill notepad`",
            parse_mode="Markdown",
        )
        return

    proc_name = " ".join(args).strip()

    try:
        import psutil
    except ImportError:
        await update.message.reply_text("❌ psutil غير مثبت.")
        return

    matched = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc_name.lower() in (proc.info["name"] or "").lower():
                matched.append((proc.info["pid"], proc.info["name"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if not matched:
        await update.message.reply_text(f"⚠️ لم يُعثر على عملية باسم: `{proc_name}`", parse_mode="Markdown")
        return

    proc_list = "\n".join(f"• PID `{pid}` — `{name}`" for pid, name in matched[:10])
    context.user_data["pkill_targets"] = matched[:10]

    await update.message.reply_text(
        f"🚫 *تأكيد إيقاف العمليات*\n\n{proc_list}\n\nهل تريد إيقاف هذه العمليات؟",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ نعم، إيقاف", callback_data="pkill:confirm"),
                InlineKeyboardButton("❌ إلغاء", callback_data="pkill:cancel"),
            ]
        ]),
    )


@auth_required
async def pkill_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm and execute process kill."""
    query = update.callback_query
    assert query
    await query.answer()

    targets = context.user_data.pop("pkill_targets", [])
    if not targets:
        await query.edit_message_text("⚠️ لا يوجد عمليات محددة للإيقاف.")
        return

    try:
        import psutil
    except ImportError:
        await query.edit_message_text("❌ psutil غير مثبت.")
        return

    killed = []
    failed = []
    for pid, name in targets:
        try:
            proc = psutil.Process(pid)
            proc.kill()
            killed.append(f"✅ PID `{pid}` (`{name}`)")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            failed.append(f"❌ PID `{pid}`: {exc}")

    lines = ["🔴 *نتيجة الإيقاف*", ""] + killed + ([""] + failed if failed else [])
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[main_menu_button()]]),
    )


@auth_required
async def pkill_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel process kill."""
    query = update.callback_query
    assert query
    context.user_data.pop("pkill_targets", None)
    await query.answer("تم الإلغاء")
    await query.edit_message_text("❌ تم إلغاء عملية الإيقاف.", reply_markup=InlineKeyboardMarkup([[main_menu_button()]]))
