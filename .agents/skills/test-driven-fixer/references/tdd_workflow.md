# TDD workflow example (test-driven-fixer)

This repo's tests are all mocked: no real subprocess, no real network. Follow
the same isolation in your regression test.

## Pattern A — fix a handler bug

The bot's Telegram handlers pull issue bodies from
`app/github/issues.py::IssueService`. A regression test would:

1. Mock `issue_service.get_issue` to return a crafted `IssueInfo`.
2. Drive the handler with a `Update`/`ContextTypes` stub.
3. Assert the reply text via a captured `context.bot.send_message`.

```python
async def test_issue_detail_truncates_long_body(mocker, update, context):
    mock = mocker.patch("app.github.issues.IssueService.get_issue")
    mock.return_value = IssueInfo(...)
    await handler(update, context)
    sent = await context.bot.send_message.assert_awaited_once_with(...)
```

## Pattern B — fix a runner/subprocess bug

Patch the subprocess boundary instead of spawning one:

```python
mocker.patch("asyncio.create_subprocess_exec", return_value=FakeProc("timeout"))
```

See `tests/test_git_timeout.py`, which already patches
`asyncio.create_subprocess_exec` to simulate a command that times out and must
be killed (`process.kill()`). Mirror that when adding coverage for executor or
workspace timeouts.

## Pattern C — fix a scheduler/db bug

Use the temp DB fixtures already exercised in `tests/test_database.py`; drive
`JobRepository`/`ScheduleRepository` against the temp aiosqlite DB, never the
real `agent.db`.

## Order-of-operations checklist

- [ ] Red: new test fails for the correct reason.
- [ ] Green: minimal implementation change.
- [ ] Refactor only if needed and kept tiny.
- [ ] Full suite + ruff + mypy green.