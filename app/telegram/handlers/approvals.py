"""Telegram callbacks for controller-enforced agent approval gates."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.runner.approvals import APPROVED, REJECTED
from app.telegram.auth import auth_required


@auth_required
async def approval_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    action, _, raw_id = (query.data or "").partition(":")
    try:
        approval_id = int(raw_id)
    except ValueError:
        await query.answer("طلب موافقة غير صالح", show_alert=True)
        return
    executor = context.bot_data.get("executor")
    if executor is None or update.effective_user is None:
        await query.answer("نظام التنفيذ غير متاح", show_alert=True)
        return
    decision = APPROVED if action == "approve" else REJECTED
    outcome = await executor.decide_approval(approval_id, decision, update.effective_user.id)
    messages = {
        APPROVED: "تمت الموافقة. سيكمل الـ Agent المهمة.",
        REJECTED: "تم رفض التغيير. تم إيقاف المهمة مع حفظ مساحة العمل.",
        "STALE": "تغيرت الملفات بعد طلب الموافقة. راجع الفرق الجديد.",
        "ALREADY_DECIDED": "تم اتخاذ قرار لهذا الطلب مسبقًا.",
    }
    await query.answer(messages.get(outcome, "تعذر تطبيق قرار الموافقة."), show_alert=True)
    await query.edit_message_reply_markup(reply_markup=None)
