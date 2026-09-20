# Telegram Inline Keyboards & Navigation Guide

## 1. Callback Data Layout
Telegram limits `callback_data` strings to **64 UTF-8 bytes**. Exceeding this triggers `BadRequest: Button_data_invalid`.

### Best Practices:
1. **Short Prefixes**:
   - `repo:` for selecting repo
   - `issue:` for selecting issue
   - `pr:` for selecting PR
   - `merge_pr:` for initiating merge
   - `do_merge:` for executing merge
2. **Compact Encodings**:
   - Instead of storing long branch names or descriptions, store IDs or numbers:
     `do_merge:squash:owner/repo:42` (30 bytes, well under 64 bytes).
3. **Session Store (`context.user_data`)**:
   - For items larger than 30 bytes, store state in `context.user_data["active_pr"]` and pass only keys or numeric references in `callback_data`.

## 2. Dynamic Update Patterns
When an action is initiated:
1. Immediately acknowledge via `await query.answer()`.
2. Update the message text to give visual feedback:
   ```python
   await query.edit_message_text("⏳ Processing request...")
   ```
3. Once completed, replace with final status and back-to-menu keyboard:
   ```python
   await query.edit_message_text("✅ Operation succeeded!", reply_markup=main_menu_keyboard())
   ```
