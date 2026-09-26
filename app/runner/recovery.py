"""
Crash recovery — sweeps orphaned jobs left in an active state after a restart.

On startup the bot calls `RecoveryService.recover()`:
- Any job whose status is still "active" (CLONING … CREATING_PR, VERIFYING,
  AWAITING_APPROVAL) cannot actually be running (the process just started),
  so it is marked FAILED with an "Interrupted by restart" marker.
- The workspace path, resume metadata and the structured event log are all
  preserved so the operator can continue the work with `/resume <job_id>`.

The marker prefix is the single source of truth for "this job was interrupted
by a restart" — used by `JobExecutor.resume_job` to gate resumability.
"""
from __future__ import annotations

import logging

from app.database.repository import (
    Database,
    Job,
    JobEventRepository,
    JobRepository,
    JobStatus,
)

logger = logging.getLogger(__name__)

INTERRUPTED_MARKER = "Interrupted by restart"
TOKEN_LIMIT_MARKER = "Paused: Account token limit reached"


def interrupted_error(status: str) -> str:
    return (
        f"{INTERRUPTED_MARKER} — the bot restarted while this job was {status}. "
        "The workspace and event log were preserved. "
        "Use /resume <job_id> to continue on the same branch."
    )


def token_limit_error(account_name: str | None = None) -> str:
    acc_info = f" for account '{account_name}'" if account_name else ""
    return (
        f"{TOKEN_LIMIT_MARKER}{acc_info}. "
        "The workspace and event log were preserved. "
        "Switch your Antigravity account and use /resume to continue on the same branch."
    )


def is_interrupted(job: Job) -> bool:
    """True if this job was marked failed because the bot restarted mid-run."""
    return bool(job.error and job.error.startswith(INTERRUPTED_MARKER))


def is_token_limit(job: Job) -> bool:
    """True if this job was paused/interrupted due to account token exhaustion."""
    return bool(job.error and job.error.startswith(TOKEN_LIMIT_MARKER))


def is_resumable(job: Job) -> bool:
    """True if this job can be resumed on its preserved workspace."""
    return is_interrupted(job) or is_token_limit(job)


class RecoveryService:
    """Marks orphaned active jobs as interrupted after a restart."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._repo = JobRepository(db)
        self._events = JobEventRepository(db)

    @classmethod
    async def recover(cls, db: Database) -> list[Job]:
        """Mark every orphaned active job FAILED / interrupted. Returns them."""
        service = cls(db)
        return await service._recover()

    async def _recover(self) -> list[Job]:
        orphaned = await self._repo.list_active_jobs()
        for job in orphaned:
            await self._repo.set_status(
                job.job_id, JobStatus.FAILED, phase="Interrupted by restart"
            )
            await self._repo.update_job(job.job_id, error=interrupted_error(job.status))
            await self._events.append(
                job.job_id,
                interrupted_error(job.status),
                level="ERROR",
                phase=job.status,
            )
            logger.warning(
                "Recovery: job %s (was %s) marked interrupted by restart",
                job.job_id,
                job.status,
            )
        return orphaned
