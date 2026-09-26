# Runbook: scheduler issues

Applies to `app/runner/scheduler.py`, `app/database/schedule_repository.py`,
and the `scheduled_jobs` table in `app/database/models.py`.

## Failure classes

1. **Double-fire / re-dispatch** — the scheduler marks a job `LAUNCHED`
   *before* handing it to `executor.start_job`, so a tick crash should not
   re-send. A duplicate fire suggests `mark_launched` was skipped.
2. **Skipped when another job is active** — `executor.start_job` raises
   `RuntimeError` if an ACTIVE job exists; scheduler catches it and sends the
   user a "skipped" message. Verify the ACTIVE row really expired using
   `get_active_job()`.
3. **Overdue recovery** — if `scheduled_at` is older than 120s on dispatch, the
   scheduler sends a "resumed" notification. Mis-parse of `DD/MM HH:MM` UTC
   (see `app/telegram/handlers/schedule.py`) produces bogus timestamps.
4. **Timezones** — all times are stored/handled in UTC; `display_time()` shows
   UTC. A local-time comparison is a bug.

## Verification checklist

1. Inspect `agent.db` → `scheduled_jobs` for the row's status/promised time and
   compare with the actual dispatch time in the notification.
2. Re-run the parse path: `DD/MM HH:MM` input → stored UTC ISO in
   `ScheduleRepository.create`. A past timestamp must be rejected.
3. For pending-not-fired: `list_pending(before=now)` must return the row; then
   `mark_launched` and `executor.start_job` must be awaited.

## Fix pattern

- Mirror `tests/test_lifecycle_notifications.py` for the overdue/notify paths
  and `tests/test_database.py` for the repository transitions
  (`PENDING → LAUNCHED → CANCELLED/terminal`).
- Never let a tick exception leak: the loop handles `CancelledError` cleanly
  and swallows per-job errors with a notification — keep that property.