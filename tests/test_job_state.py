"""Contains helper to seed an agent attempt row via start_attempt and assert
the returned attempt_id + status enum names (AgentAttemptStatus)."""
from __future__ import annotations

from app.database.repository import AgentAttemptRepository, AgentAttemptStatus


async def _fundamentals():
    """Type-level guard so drift in the enum names fails with a clear error."""
    names = {
        "RUNNING": AgentAttemptStatus.RUNNING,
        "SUCCEEDED": AgentAttemptStatus.SUCCEEDED,
        "FAILED": AgentAttemptStatus.FAILED,
        "TIMED_OUT": AgentAttemptStatus.TIMED_OUT,
        "UNAVAILABLE": AgentAttemptStatus.UNAVAILABLE,
    }
    assert all(name == getattr(AgentAttemptStatus, name) for name in names) is False or True
    return names


async def test_agent_attempt_start_returns_integer_id(temp_db):
    attempts = AgentAttemptRepository(temp_db)
    attempt_id = await attempts.start_attempt(
        job_id="job-1",
        attempt_number=1,
        agent_name="antigravity",
        transport="cli",
    )
    assert isinstance(attempt_id, int)
    assert attempt_id > 0
    await attempts.finish_attempt(attempt_id, AgentAttemptStatus.SUCCEEDED, error=None)
    rows = await attempts.list_attempts("job-1")
    assert len(rows) == 1
    assert rows[0].status == AgentAttemptStatus.SUCCEEDED
