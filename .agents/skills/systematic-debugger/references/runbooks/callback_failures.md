# Runbook: Telegram callback failures

Applies to the inline-keyboard callbacks defined in
`app/telegram/keyboards/__init__.py` (constant prefixes `CB_REPO`, `CB_ISSUE`,
`CB_CONFIRM_RUN`, `CB_POLISH*`, `CB_MERGE_PR`, `CB_WS_CLONE/LOCAL`,
`CB_PROJECT*`, `CB_SETDIR`, `CB_SCHEDULE_DEL`, menu routing, and the
`personal_killswitch_confirm_keyboard`).

## Common failure classes

1. **Callback not registered** — handler added in one file but not wired in
   `app/telegram/bot.py`. Check the router registration.
2. **Prefix mismatch** — callback data produced by the keyboard builder does
   not match the `pattern`/prefix the query handler filters on.
3. **Crash inside the handler** — usually an unawaited blocking call (PyGithub /
   httpx) raised `GithubException`, or the handler called an async service
   without `await`.
4. **Auth silently dropped** — `app/telegram/auth.py::auth_required` silently
   drops unauthorized users; a test user_id mismatch produces "nothing happens"
   with no error.

## Verification checklist

1. Reconstruct the exact `callback_data` string from the builder function that
   produced the button; confirm which handler owns that prefix.
2. Check `auth_required` accepts the acting user's `user_id`
   (compare against `TELEGRAM_ALLOWED_USER_ID`).
3. Reproduce with `pytest` using the keyboard builder + handler directly
   (see `tests/test_keyboards.py` and `tests/test_pull_requests.py` patterns:
   build a `CallbackQuery`-like object, call the handler, assert on
   `context.bot.send_message` / `edit_message_text`).
4. If the callback triggers a backend service (`run`, `polish`), also review
   [agent_timeouts.md](./agent_timeouts.md) — a long agent run will not answer a
   callback until it completes.

## Fix pattern

- Keep constants in one place (the keyboards package); never hand-write
  callback strings in two modules.
- For long-running actions, always trigger the executor/service (which sends
  notifications via `notify_fn`) rather than doing blocking work inside a
  `ConversationHandler`. Drop-in test = assert the executor method was awaited.