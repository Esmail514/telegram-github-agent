# Cross-layer contract checks

Read only the section matching the request. These are questions to verify in
current code, not assertions that the corresponding feature already works.
Source entry points and test anchors are in `architecture.md`.

## Project selection and filesystem

Follow drive discovery -> folder/subfolder -> Back/Parent/Drives -> explicit
confirmation -> path validation -> saved/selected-project write. Check both the
generic folder browser and the shortcut for a Git folder: a navigation shortcut
must not silently commit a selection. Exercise a missing drive, deleted folder,
unreadable directory, and Windows paths with spaces using temporary fixtures.

Trace project IDs through schedule creation and dispatch. A reused name is not
a stable identity. Keep browsing-root changes separate from saved-project
changes and from historical job paths. Do not test by clearing the real database.

## Schedule options and timing

Follow prompt/Issue -> time -> agent/model -> reasoning effort -> project ->
confirmation -> persisted record -> due dispatcher -> `AgentContext` -> CLI args.
Compare the local CSV and Issue paths independently. Test changing choices
before confirmation, expired custom time, immediate execution, and editing a
pending time. Include offset-aware timestamps rather than only UTC examples.

Inspect queue behavior while another job runs, claim-before-dispatch, failed
dispatch, completion wakeup, stop, and process restart. If a task becomes
`LAUNCHED`, establish how it reaches a terminal state or is safely recovered.
Do not requeue potentially active work blindly; duplicate agent runs can modify
the same workspace.

Local schedules are user-editable CSV data. Check malformed dates, missing
columns, duplicate IDs, empty fields, encoding failures, and one bad row beside
a valid pending row. Atomic replacement prevents partial writes but does not
automatically make edits from Excel or a second bot process safe.

## Lifecycle and resume

Trace startup callers, not only `RecoveryService` unit tests. Follow a job left
in an active SQLite state through restart, the executor's active check, scheduler
dispatch, and `/resume`. Check shutdown of agent processes, pending approvals,
the scheduler task, and database connection ordering.

For a resumed local prompt, verify that the original prompt, selected model,
effort, `local_only`, workspace/branch, and completion bookkeeping survive.
Returning to an Issue workflow must not silently enable push/PR actions for a
task created as local-only.

## Processes, fallbacks and approval

Trace the actual CLI subprocess and every owning coroutine. Check that timeout,
cancellation, and `/stop` terminate and reap it, drain/close output tasks, and
release ownership only after cleanup. A timeout around `agent.run` does not by
itself guarantee child termination. Prefer harmless fake processes for tests.

Test approval against the exact diff the operator saw. Editing content again
in the same named file must invalidate an earlier decision, as must staged and
untracked changes where applicable. Distinguish controller diff approval from
an agent CLI asking an interactive multiple-choice question; one is not a bridge
for the other unless input/output wiring actually exists.

Check whether personal tasks, issue polishing, and queued work share the global
agent manager. A separate task runner can invalidate stop/ownership assumptions
even when the main executor enforces one active job.

## Telegram integration and UI state

Start with `build_application` registration order and each conversation's
entry/state/fallback filters. Check ambiguous callback prefixes, bottom reply
keyboard input while a conversation is active, and Back from each intermediate
screen. Match callback bytes to Telegram's 64-byte limit. Test long repository
names and Markdown punctuation rather than just short ASCII labels.

Trace each dynamic string through redaction, escaping and length handling.
`sanitize_for_telegram` does not escape Markdown. A saved prompt may need a
bounded preview plus a full redacted document rather than a single message.
Keep authorization checks on callbacks as well as command entry points.

## Packaging and verification

Compare runtime imports against both dependency declarations, including lazy
imports reached only by a particular menu. Inspect CI's installation command:
a passing developer environment may contain undeclared packages. Check actual
test call paths; tests of a helper do not prove it is registered/called from
startup or Telegram.

Keep audit findings outside reusable instructions: a missing integration is a
bug to report, not a rule to preserve in future development. Do not relax security
invariants to make a test or live CLI session pass.
