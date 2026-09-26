"""Recovery tests against the real RecoveryService API.

Contract (verified against app/runner/recovery.py + app/database/repository.py):
- `RecoveryService.recover(db)` returns the orphaned active jobs as originally
  loaded (original status preserved on the *returned* objects).
- The DB rows for those jobs are updated to FAILED with an interrupted-error
  marker, and an ERROR event is appended — THAT is the persisted truth.
"""
from __future__ import annotations

from app.database.repository import (
    JobEventRepository,
    JobRepository,
    JobStatus,
)
from app.runner.recovery import (
    INTERRUPTED_MARKER,
    RecoveryService,
    interrupted_error,
    is_interrupted,
)


async def _seed_job(db, job_id: str, status: JobStatus) -> str:
    repo = JobRepository(db)
    await repo.create_job(
        job_id=job_id,
        repo_full_name="test/repo",
        issue_number=1,
        branch=f"ai/{status.lower()}",
        agent="antigravity",
        telegram_chat_id=123,
        workspace_path=None,
        local_workspace=0,
    )
    await repo.set_status(job_id, status)
    return job_id


async def test_interrupted_marker_detection(temp_db):
    status = JobStatus.IMPLEMENTING
    msg = interrupted_error(status)
    assert msg.startswith(INTERRUPTED_MARKER)
    assert "IMPLEMENTING" in msg


async def test_recovery_fails_active_jobs_and_records_reason(temp_db):
    job_id = await _seed_job(temp_db, "job-int", JobStatus.IMPLEMENTING)
    recovered = await RecoveryService.recover(temp_db)
    assert [j.job_id for j in recovered] == [job_id]

    stored = await JobRepository(temp_db).get_job(job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED
    assert is_interrupted(stored)

    events = await JobEventRepository(temp_db).get_events(job_id)
    assert events and events[-1].message.startswith(INTERRUPTED_MARKER)
    assert events[-1].level == "ERROR"


async def test_verifying_and_awaiting_approval_are_failed_too(temp_db):
    for status in (JobStatus.VERIFYING, JobStatus.AWAITING_APPROVAL):
        await _seed_job(temp_db, f"job-{status.lower()}", status)
    recovered = await RecoveryService.recover(temp_db)
    assert {j.job_id for j in recovered} == {
        "job-verifying",
        "job-awaiting_approval",
    }

    repo = JobRepository(temp_db)
    for job_id in ("job-verifying", "job-awaiting_approval"):
        stored = await repo.get_job(job_id)
        assert stored is not None
        assert stored.status == JobStatus.FAILED
        assert is_interrupted(stored)


async def test_terminal_jobs_are_left_untouched(temp_db):
    for status in (JobStatus.COMPLETED, JobStatus.FAILED):
        await _seed_job(temp_db, f"job-{status.lower()}", status)
    assert await RecoveryService.recover(temp_db) == []
    repo = JobRepository(temp_db)
    assert (await repo.get_job("job-completed")).status == JobStatus.COMPLETED
    assert (await repo.get_job("job-failed")).status == JobStatus.FAILED
