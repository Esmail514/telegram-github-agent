# Analysis template (issue-analyzer)

Copy the skeleton below and fill it in. Keep it short; if a section has nothing
to add, write "N/A" rather than deleting it.

```markdown
## Requirements
- [ ] <one requirement per line, testable>

## Affected components
- `app/.../file.py` — role: <why it matters, one clause>

## Root-cause hypothesis (bugs only)
- <most likely cause; evidence that supports it>

## Unknowns & assumptions
- [x] <verified fact>
- [ ] <unknown — flag for follow-up>

## Acceptance criteria
- Given <precondition>, when <action>, then <observable result>
```

## Verification hooks for this repo

- The bot reads issue data through `app/github/issues.py::IssueService`
  (`get_issue`, `list_issues`). Local-project issues come through
  `app/runner/personal_task.py::PersonalTaskManager`.
- Acceptance criteria should be phrased so `pytest` (asyncio_mode=auto,
  testpaths=tests) covers them; most tests here use `pytest-mock`, no real
  subprocess or network.
- If the affected area is a Telegram callback, note the callback-data prefix
  used in `app/telegram/keyboards/__init__.py` so the fix targets the right path.

## Definition of "done" for this skill

- Requirements are concrete enough to plan without re-asking the user.
- Every referenced file path exists (verified, not remembered).
- Unknowns are explicitly listed and later resolved by debugging or planning.