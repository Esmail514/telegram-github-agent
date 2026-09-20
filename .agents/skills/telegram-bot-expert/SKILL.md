---
name: telegram-bot-expert
description: >-
  Expert guidelines, patterns, and runbooks for building and maintaining
  Telegram bots using python-telegram-bot (v20+ and v21+). Use when modifying
  Telegram handlers, inline keyboards, callback routing, message formatting,
  rate limiting, or bot authentication.
---

# Telegram Bot Expert Skill

This skill provides best practices, common patterns, and troubleshooting steps
for developing, debugging, and scaling Telegram bots powered by `python-telegram-bot`.

## 1. Core Architecture Pattern

The bot uses the async `Application` architecture from `telegram.ext`.

### Standard Lifecycle
1. **Initialize HTTP client**: Configure `HTTPXRequest` with explicit connection pool size and timeouts.
2. **Register Handlers**:
   - `ConversationHandler` instances **MUST** be registered first (they evaluate priority before generic callbacks).
   - `CommandHandler` for top-level commands (`/start`, `/repos`, `/issues`, `/prs`, `/status`, `/help`).
   - `CallbackQueryHandler` for inline keyboard button interactions.
   - Global error handler registered via `app.add_error_handler(_error_handler)`.

### Registration Precedence
```python
# 1. High priority: multi-step conversation flows
app.add_handler(build_newissue_handler())
app.add_handler(build_run_handler())

# 2. Command handlers
app.add_handler(CommandHandler("start", start_handler))
app.add_handler(CommandHandler("prs", prs_handler))

# 3. Callback queries (prefix patterns)
app.add_handler(CallbackQueryHandler(merge_pr_start_callback, pattern="^merge_pr:"))
app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu:"))
```

---

## 2. Inline Keyboards & Navigation

### Best Practices
- **Always answer callbacks**: Every `CallbackQueryHandler` **MUST** call `await query.answer()` to prevent the Telegram loading spinner from hanging.
- **Callback data length limit**: Telegram limits `callback_data` to **64 bytes**. Always use short prefixes and delimiters (e.g. `do_merge:squash:owner/repo:12`).
- **Clean Message Edits**: Edit existing messages with `await query.edit_message_text(...)` instead of sending new messages to keep the user chat clean.

### Pagination Pattern
```python
def paginate_keyboard(items, page, per_page, item_cb_prefix, page_cb_prefix):
    buttons = []
    start = page * per_page
    for item in items[start : start + per_page]:
        buttons.append([InlineKeyboardButton(item.title, callback_data=f"{item_cb_prefix}{item.id}")])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"{page_cb_prefix}{page - 1}"))
    if len(items) > start + per_page:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"{page_cb_prefix}{page + 1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(buttons)
```

For detailed patterns, see [keyboards_and_navigation.md](./references/keyboards_and_navigation.md).

---

## 3. Formatting & Security

### Telegram Text Escaping
- **Parse Mode**: When using `parse_mode="Markdown"`, characters like `_`, `*`, `` ` ``, and `[` must match properly or Telegram will reject the payload with `BadRequest: Can't parse entities`.
- **Sanitizing dynamic text**: Always pass dynamic content (issue titles, commit messages, git output) through a sanitizer before sending:
  ```python
  from app.utils.security import sanitize_for_telegram
  text = sanitize_for_telegram(raw_output)
  ```

### Authorization Decorator
All sensitive bot commands and callbacks **MUST** enforce the `@auth_required` decorator to restrict access exclusively to `settings.TELEGRAM_ALLOWED_USER_ID`:
```python
@auth_required
async def my_secure_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ...
```

For detailed security guidelines, see [security_and_auth.md](./references/security_and_auth.md).

---

## 4. Rate Limiting and Resilience

- **Global Rate Limit**: Telegram allows maximum ~30 messages per second across all chats and ~1 message per second to a single chat.
- **Transient Network Errors**: Log `TimedOut` and `NetworkError` as `WARNING` rather than `ERROR`, allowing the library's internal retry engine to recover without restarting the process.
