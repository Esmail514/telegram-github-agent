"""
Progress reporter — one source of truth for job progress.

Every progress signal is:
1. Persisted as a structured row in `job_events` (powers `/logs`).
2. Optionally pushed to Telegram via a `notify_fn`, throttled so fast
   agent output cannot spam the operator.

No Telegram imports here — `notify_fn` is injected by the executor.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from app.database.repository import Database, JobEventRepository

NotifyFn = Callable[..., Awaitable[None]]

DEFAULT_THROTTLE_SECONDS = 10.0


class ProgressReporter:
    """Persists structured events and throttles Telegram notifications."""

    def __init__(
        self,
        db: Database,
        job_id: str,
        notify_fn: NotifyFn | None = None,
        throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    ) -> None:
        self._events = JobEventRepository(db)
        self._job_id = job_id
        self._notify = notify_fn
        self._throttle = max(0.0, throttle_seconds)
        self._last_notify_at: float | None = None

    async def report(
        self,
        message: str,
        level: str = "INFO",
        phase: str | None = None,
        notify: bool | None = None,
    ) -> None:
        """Persist an event and (optionally) notify Telegram.

        Args:
            message: Human-readable progress message.
            level: INFO | WARNING | ERROR
            phase: Optional phase label for the event.
            notify: True to force a Telegram push, False to suppress it,
                    None (default) to push only if throttling allows.
        """
        event = await self._events.append(self._job_id, message, level, phase)
        if self._notify is None or notify is False:
            return

        send = notify is True
        if not send:
            now = time.monotonic()
            if self._last_notify_at is None:
                send = True
            elif (now - self._last_notify_at) >= self._throttle:
                send = True
        if send:
            self._last_notify_at = time.monotonic()
            await self._safe_notify(event.message)

    async def _safe_notify(self, message: str) -> None:
        if self._notify is None:
            return
        try:
            await self._notify(message)
        except Exception:
            # Progress delivery must never crash the job pipeline.
            import logging

            logging.getLogger(__name__).exception("Failed to send progress notification")
