"""
Polish Issue handler — AI-powered GitHub issue improver.

Flow:
    1. User taps "✨ Polish with AI" on an issue detail view
       OR taps "✨ Polish with AI" on the new-issue confirm screen.
    2. Bot sends the issue content to an LLM and shows a spinner.
    3. Bot presents the polished title/body/labels with an action keyboard.
    4. User taps:
       - ✅ Apply    → update the issue on GitHub
       - 🔄 Regenerate → call AI again
       - ❌ Cancel   → discard, return to where the user was

Entry points registered in bot.py:
    CallbackQueryHandler(polish_issue_start, pattern="^polish_issue:\\d+$")
    CallbackQueryHandler(polish_newissue_start, pattern="^polish_newissue$")

This handler does NOT use ConversationHandler — it is stateless between
messages, storing context in context.user_data.  This avoids conflicts
with the existing newissue ConversationHandler.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.github.issue_polisher import issue_polisher
from app.github.issues import issue_service
from app.telegram.auth import auth_required
from app.telegram.keyboards import (
    issue_detail_keyboard,
    polish_result_keyboard,
)

logger = logging.getLogger(__name__)

# Keys stored in context.user_data during the polish flow
_KEY_REPO = "polish_repo"
_KEY_ISSUE_NUM = "polish_issue_num"
_KEY_POLISHED_TITLE = "polish_title"
_KEY_POLISHED_BODY = "polish_body"
_KEY_POLISHED_LABELS = "polish_labels"
# For new-issue flow
_KEY_SOURCE = "polish_source"   # "existing" | "newissue"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

@auth_required
async def polish_issue_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point: user tapped "✨ Polish with AI" on an existing issue detail.
    Callback data: polish_issue:<number>
    """
    query = update.callback_query
    assert query
    await query.answer()

    issue_number = int((query.data or "").replace("polish_issue:", ""))
    repo = (context.user_data or {}).get("selected_repo", "")

    if not repo:
        await query.edit_message_text(
            "❌ Repository context lost. Use /issues to browse again."
        )
        return

    if context.user_data is not None:
        context.user_data[_KEY_REPO] = repo
        context.user_data[_KEY_ISSUE_NUM] = issue_number
        context.user_data[_KEY_SOURCE] = "existing"

    await query.edit_message_text(
        f"✨ *Fetching issue #{issue_number}...*",
        parse_mode="Markdown",
    )

    # Fetch the current issue from GitHub
    try:
        issue = await asyncio.get_event_loop().run_in_executor(
            None, lambda: issue_service.get_issue(repo, issue_number)
        )
    except Exception as exc:
        logger.error("polish: failed to get issue #%d from %s: %s", issue_number, repo, exc)
        await query.edit_message_text(f"❌ Could not load issue #{issue_number}.")
        return

    await query.edit_message_text(
        f"🤖 *Polishing issue #{issue_number} with AI…*\n\n"
        f"_(This may take a few seconds)_",
        parse_mode="Markdown",
    )

    await _run_polish(
        query_or_message=query,
        context=context,
        repo=repo,
        title=issue.title,
        body=issue.body,
        issue_number=issue_number,
        is_callback=True,
    )


@auth_required
async def polish_newissue_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point: user tapped "✨ Polish with AI" on the new-issue confirm screen.
    """
    query = update.callback_query
    assert query
    await query.answer()

    ud = context.user_data or {}
    repo = ud.get("ni_repo", "")
    title = ud.get("ni_title", "")
    body = ud.get("ni_body", "")

    if not repo or not title:
        await query.edit_message_text(
            "❌ New issue context lost. Please start /newissue again."
        )
        return

    if context.user_data is not None:
        context.user_data[_KEY_REPO] = repo
        context.user_data[_KEY_SOURCE] = "newissue"
        context.user_data[_KEY_ISSUE_NUM] = None

    await query.edit_message_text(
        "🤖 *Polishing your draft issue with AI…*\n\n_(This may take a few seconds)_",
        parse_mode="Markdown",
    )

    await _run_polish(
        query_or_message=query,
        context=context,
        repo=repo,
        title=title,
        body=body,
        issue_number=None,
        is_callback=True,
    )


# ---------------------------------------------------------------------------
# Polish logic (shared)
# ---------------------------------------------------------------------------

async def _run_polish(
    query_or_message,
    context: ContextTypes.DEFAULT_TYPE,
    repo: str,
    title: str,
    body: str,
    issue_number: int | None,
    is_callback: bool,
) -> None:
    """Call the AI polisher and display the result."""
    try:
        polished = await issue_polisher.polish(
            repo_full_name=repo,
            title=title,
            body=body,
        )
    except RuntimeError as exc:
        # No API key configured
        msg = f"⚠️ *AI not configured*\n\n{exc}"
        if is_callback:
            await query_or_message.edit_message_text(msg, parse_mode="Markdown")
        else:
            await query_or_message.reply_text(msg, parse_mode="Markdown")
        return
    except Exception as exc:
        logger.error("polish: AI call failed: %s", exc)
        msg = f"❌ AI request failed: {str(exc)[:200]}"
        if is_callback:
            await query_or_message.edit_message_text(msg)
        else:
            await query_or_message.reply_text(msg)
        return

    # Store polished result for the Apply step
    if context.user_data is not None:
        context.user_data[_KEY_POLISHED_TITLE] = polished.title
        context.user_data[_KEY_POLISHED_BODY] = polished.body
        context.user_data[_KEY_POLISHED_LABELS] = polished.labels

    # Build preview message
    labels_str = ", ".join(f"`{lb}`" for lb in polished.labels) if polished.labels else "none"
    body_preview = (polished.body[:500] + "\n…") if len(polished.body) > 500 else polished.body

    issue_ref = f"issue #{issue_number}" if issue_number else "draft issue"
    text = (
        f"✨ *AI-Polished {issue_ref}*\n\n"
        f"*Title:* {polished.title}\n\n"
        f"*Labels:* {labels_str}\n\n"
        f"*Description:*\n{body_preview}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💡 _{polished.explanation}_\n\n"
        f"Tap *✅ Apply* to update GitHub, *🔄 Regenerate* for a new attempt, "
        f"or *❌ Cancel* to discard."
    )

    if is_callback:
        await query_or_message.edit_message_text(
            text,
            reply_markup=polish_result_keyboard(),
            parse_mode="Markdown",
        )
    else:
        await query_or_message.reply_text(
            text,
            reply_markup=polish_result_keyboard(),
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

@auth_required
async def polish_apply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped ✅ Apply — update the issue on GitHub."""
    query = update.callback_query
    assert query
    await query.answer()

    ud = context.user_data or {}
    repo = ud.get(_KEY_REPO, "")
    issue_num = ud.get(_KEY_ISSUE_NUM)
    source = ud.get(_KEY_SOURCE, "existing")
    title = ud.get(_KEY_POLISHED_TITLE, "")
    body = ud.get(_KEY_POLISHED_BODY, "")
    labels = ud.get(_KEY_POLISHED_LABELS, [])

    if not repo or not title:
        await query.edit_message_text("❌ Context lost. Please start over.")
        return

    if source == "newissue":
        # Update the draft that lives in ni_* user_data, then tell user to confirm
        if context.user_data is not None:
            context.user_data["ni_title"] = title
            context.user_data["ni_body"] = body
            context.user_data["ni_labels"] = labels
        await query.edit_message_text(
            "✅ *Draft updated with AI improvements!*\n\n"
            f"*Title:* {title}\n\n"
            "The polished content has been applied to your draft. "
            "Use /newissue to continue and create the issue.",
            parse_mode="Markdown",
        )
        return

    # Existing issue — apply via GitHub API
    if issue_num is None:
        await query.edit_message_text("❌ No issue number found. Cannot apply.")
        return

    await query.edit_message_text(f"⏳ Updating issue #{issue_num} on GitHub…")

    try:
        updated = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: issue_service.update_issue(
                repo_full_name=repo,
                number=issue_num,
                title=title,
                body=body,
                labels=labels or None,
            ),
        )
    except Exception as exc:
        logger.error("polish apply: failed to update issue #%d: %s", issue_num, exc)
        await query.edit_message_text(
            f"❌ Failed to update issue #{issue_num}: {str(exc)[:200]}"
        )
        return

    await query.edit_message_text(
        f"✅ *Issue #{updated.number} updated!*\n\n"
        f"*Title:* {updated.title}\n\n"
        f"[🔗 View on GitHub]({updated.html_url})",
        reply_markup=issue_detail_keyboard(updated),
        parse_mode="Markdown",
    )

    # Clean up polish keys from user_data
    _clear_polish_data(context)


# ---------------------------------------------------------------------------
# Regenerate
# ---------------------------------------------------------------------------

@auth_required
async def polish_regen_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped 🔄 Regenerate — call AI again with the original content."""
    query = update.callback_query
    assert query
    await query.answer()

    ud = context.user_data or {}
    repo = ud.get(_KEY_REPO, "")
    issue_num = ud.get(_KEY_ISSUE_NUM)
    source = ud.get(_KEY_SOURCE, "existing")

    await query.edit_message_text(
        "🔄 *Regenerating with AI…*\n\n_(This may take a few seconds)_",
        parse_mode="Markdown",
    )

    if source == "newissue":
        title = ud.get("ni_title", "")
        body = ud.get("ni_body", "")
    else:
        if not repo or issue_num is None:
            await query.edit_message_text("❌ Context lost. Please start over.")
            return
        try:
            issue = await asyncio.get_event_loop().run_in_executor(
                None, lambda: issue_service.get_issue(repo, issue_num)
            )
            title = issue.title
            body = issue.body
        except Exception as exc:
            logger.error("polish regen: failed to get issue: %s", exc)
            await query.edit_message_text("❌ Could not reload the issue.")
            return

    await _run_polish(
        query_or_message=query,
        context=context,
        repo=repo,
        title=title,
        body=body,
        issue_number=issue_num,
        is_callback=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_polish_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data is None:
        return
    for key in (
        _KEY_REPO, _KEY_ISSUE_NUM, _KEY_SOURCE,
        _KEY_POLISHED_TITLE, _KEY_POLISHED_BODY, _KEY_POLISHED_LABELS,
    ):
        context.user_data.pop(key, None)
