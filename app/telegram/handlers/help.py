"""
/help handler.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.telegram.auth import auth_required

HELP_TEXT = """\
*الأوامر الأساسية*
/start القائمة الرئيسية
/status حالة المهمة
/stop إيقاف المهمة
/projects المشاريع المحلية
/issues المشكلات
/newissue إنشاء مشكلة
/schedule جدولة مهمة
/scheduled المهام المجدولة
/accounts الحسابات
/sysinfo موارد الجهاز
/help هذه المساعدة

لا يدفع البوت مباشرة إلى master ويفحص الأسرار قبل كل commit.
"""

PERSONAL_HELP_SECTION = """

*الوضع الشخصي*
/personal القائمة الشخصية
/task تشغيل مهمة محلية
/task_stop إيقاف المهمة
/getfile تنزيل ملف
/shell تشغيل أمر
/ls تصفح الملفات
/ps عرض العمليات
/pkill إيقاف عملية
/personal_off إغلاق الوضع نهائياً
"""


@auth_required
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    from app.runner.personal_guard import is_personal_mode_active
    text = HELP_TEXT
    active, _ = is_personal_mode_active()
    if active:
        text = text + PERSONAL_HELP_SECTION
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("القائمة الرئيسية", callback_data="menu:start")]
    ])
    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

