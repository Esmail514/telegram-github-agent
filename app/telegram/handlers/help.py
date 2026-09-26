"""
/help handler.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.telegram.auth import auth_required

HELP_TEXT = """\
📖 *دليل الأوامر والاستخدام | Help Guide*
━━━━━━━━━━━━━━━━━━━━
📌 *الأوامر الأساسية:*
• /start أو /menu — القائمة الرئيسية
• /projects — تصفح المشاريع المحلية
• /setdir — عرض أو تغيير مجلد المشاريع
• /repos — استعراض مستودعات GitHub
• /issues — استعراض ومتابعة الـ Issues
• /prs — تصفح ودمج طلبات السحب (PRs)
• /newissue — إنشاء Issue جديد (يدعم الصور والشرح)
• /run — تشغيل الـ Agent لحل Issue تلقائياً
• /schedule — جدولة تنفيذ Issue في وقت محدد
• /scheduled — عرض وإدارة المهام المجدولة
• /status — متابعة حالة العملية الجارية
• /stop — إيقاف العملية النشطة فوراً
• /sysinfo — عرض موارد الجهاز (RAM/CPU/Disk)
• /accounts — إدارة وتبديل حسابات Antigravity
• /deleteaccount <اسم> — حذف ملف حساب محفوظ
• /help — عرض هذا الدليل

━━━━━━━━━━━━━━━━━━━━
🔄 *دورة عمل الـ AI Agent:*
🔍 فحص ➔ 🛠 برمجة ➔ 🧪 اختبار ➔ 🔧 إصلاح ➔ 📦 حفظ ➔ ⬆️ رفع ➔ 🔀 إنشاء PR

🛡 *الأمان والموثوقية:*
• الاستجابة لحسابك المعتمد فقط
• لا يتم الدفع مباشرة إلى main أو master
• فحص أمني للمفاتيح والبيانات الحساسة قبل كل Commit
• إيقاف تلقائي آمن عند انتهاء المهلة المحددة
"""

PERSONAL_HELP_SECTION = """
━━━━━━━━━━━━━━━━━━━━
💻 *وضع الاستخدام الشخصي المتقدم (Personal Mode):*
• /personal — قائمة الأوامر الشخصية
• /task <مهمة> — تشغيل الـ AI Agent على الجهاز مع بث مباشر
• /task_stop — إيقاف مهمة الـ Agent الحالية
• /getfile <مسار> — تحميل ملف من الجهاز إلى تيليجرام
• إرسال أي ملف/مستند — يتم حفظه في مجلد personal/incoming
• /shell <أمر> — تشغيل أمر terminal مباشرة
• /ls [مسار] — تصفح محتويات مجلد
• /ps — عرض العمليات الجارية (مرتبة بالذاكرة)
• /pkill <اسم> — إيقاف عملية بالاسم (مع تأكيد)
• /personal_off — إغلاق وقفل الوضع نهائياً (One-Way Kill Switch)
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
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu:start")]
    ])
    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

