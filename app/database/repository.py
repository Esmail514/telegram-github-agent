"""
Async database repository using aiosqlite.

Usage:
    db = await Database.create()
    repo = JobRepository(db)
    job = await repo.create_job(...)
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.database.models import ALL_DDL, MIGRATIONS

logger = logging.getLogger(__name__)

DB_PATH = Path("agent.db")


class Database:
    """Singleton async database connection wrapper."""

    _instance: Database | None = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def create(cls, path: Path = DB_PATH) -> Database:
        async with cls._lock:
            if cls._instance is None:
                conn = await aiosqlite.connect(path)
                conn.row_factory = aiosqlite.Row
                try:
                    await conn.execute("PRAGMA journal_mode=WAL")
                    await conn.execute("PRAGMA foreign_keys=ON")
                    # Faster writes — still safe; WAL protects durability
                    await conn.execute("PRAGMA synchronous=NORMAL")
                    # Cache 4 MB of pages in memory for read performance
                    await conn.execute("PRAGMA cache_size=-4096")
                    # Migrations FIRST so legacy tables gain their new columns
                    # before index/trigger DDL referencing them is applied.
                    await cls._apply_migrations(conn)
                    for ddl in ALL_DDL:
                        await conn.execute(ddl)
                    await conn.commit()
                except BaseException:
                    await conn.close()
                    raise
                cls._instance = cls(conn)
                logger.info("Database initialised at %s", path)
            return cls._instance

    @staticmethod
    async def _apply_migrations(conn: aiosqlite.Connection) -> None:
        """Idempotently add columns/objects that older databases may be missing."""
        for table, column, ddl in MIGRATIONS:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ) as cursor:
                exists = await cursor.fetchone() is not None
            if not exists:
                # Fresh database — ALL_DDL will create the table with the column.
                continue
            cols = await conn.execute_fetchall(f"PRAGMA table_info({table})")
            names = {row[1] for row in cols}
            if column not in names:
                await conn.execute(ddl)
                logger.info("DB migration: added column %s.%s", table, column)

    @property
    def conn(self) -> aiosqlite.Connection:
        return self._conn

    async def close(self) -> None:
        await self._conn.close()


# ---------------------------------------------------------------------------
# Job Status constants
# ---------------------------------------------------------------------------

class JobStatus:
    QUEUED = "QUEUED"
    CLONING = "CLONING"
    INSPECTING = "INSPECTING"
    PLANNING = "PLANNING"
    IMPLEMENTING = "IMPLEMENTING"
    TESTING = "TESTING"
    FIXING = "FIXING"
    VERIFYING = "VERIFYING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMMITTING = "COMMITTING"
    PUSHING = "PUSHING"
    CREATING_PR = "CREATING_PR"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"

    TERMINAL_STATES = {COMPLETED, FAILED, STOPPED}
    ACTIVE_STATES = {
        QUEUED, CLONING, INSPECTING, PLANNING,
        IMPLEMENTING, TESTING, FIXING, VERIFYING,
        AWAITING_APPROVAL, COMMITTING, PUSHING, CREATING_PR
    }


# ---------------------------------------------------------------------------
# Job data class
# ---------------------------------------------------------------------------



@dataclass
class Job:
    job_id: str
    repo_full_name: str
    issue_number: int
    branch: str
    agent: str
    status: str
    started_at: str | None
    finished_at: str | None
    current_phase: str | None
    fix_iterations: int
    files_changed: int
    error: str | None
    pr_url: str | None
    pr_number: int | None
    telegram_chat_id: int
    created_at: str
    workspace_path: str | None = None
    local_workspace: int = 0

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Job:
        return cls(**dict(row))

    def elapsed_seconds(self) -> int:
        if not self.started_at:
            return 0
        start = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        return int((datetime.now(UTC) - start).total_seconds())

    def elapsed_display(self) -> str:
        secs = self.elapsed_seconds()
        m, s = divmod(secs, 60)
        return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_job(
        self,
        job_id: str,
        repo_full_name: str,
        issue_number: int,
        branch: str,
        agent: str,
        telegram_chat_id: int,
        workspace_path: str | None = None,
        local_workspace: int = 0,
    ) -> Job:
        now = _now()
        await self._db.conn.execute(
            """
            INSERT INTO jobs
                (job_id, repo_full_name, issue_number, branch, agent,
                 status, started_at, telegram_chat_id, created_at,
                 workspace_path, local_workspace)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, repo_full_name, issue_number, branch, agent,
                JobStatus.QUEUED, now, telegram_chat_id, now,
                workspace_path, local_workspace,
            ),
        )
        await self._db.conn.commit()
        return await self.get_job(job_id)  # type: ignore[return-value]

    async def get_job(self, job_id: str) -> Job | None:
        async with self._db.conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return Job.from_row(row) if row else None

    async def get_active_job(self) -> Job | None:
        placeholders = ",".join("?" * len(JobStatus.ACTIVE_STATES))
        async with self._db.conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY created_at ASC LIMIT 1",
            tuple(JobStatus.ACTIVE_STATES),
        ) as cursor:
            row = await cursor.fetchone()
            return Job.from_row(row) if row else None

    async def list_active_jobs(self) -> list[Job]:
        """Return all jobs in a non-terminal state (used by crash recovery)."""
        placeholders = ",".join("?" * len(JobStatus.ACTIVE_STATES))
        async with self._db.conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY created_at ASC",
            tuple(JobStatus.ACTIVE_STATES),
        ) as cursor:
            rows = await cursor.fetchall()
        return [Job.from_row(r) for r in rows]

    async def update_job(self, job_id: str, **fields: Any) -> Job | None:
        if not fields:
            return await self.get_job(job_id)
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        await self._db.conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE job_id = ?", values
        )
        await self._db.conn.commit()
        return await self.get_job(job_id)

    async def set_status(
        self, job_id: str, status: str, phase: str | None = None
    ) -> Job | None:
        fields: dict[str, Any] = {"status": status}
        if phase is not None:
            fields["current_phase"] = phase
        if status in JobStatus.TERMINAL_STATES:
            fields["finished_at"] = _now()
        return await self.update_job(job_id, **fields)

    async def list_recent_jobs(self, limit: int = 10) -> list[Job]:
        async with self._db.conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [Job.from_row(r) for r in rows]

    async def cleanup_old_jobs(self, keep_last: int = 50) -> int:
        """
        Delete old terminal-state jobs, keeping only the `keep_last` most recent.
        Active/pending jobs are never deleted.
        Returns the number of rows deleted.
        """
        terminal = list(JobStatus.TERMINAL_STATES)
        placeholders = ",".join("?" * len(terminal))
        # Find the cut-off created_at timestamp
        async with self._db.conn.execute(
            f"""
            SELECT created_at FROM jobs
            WHERE status IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT 1 OFFSET ?
            """,
            (*terminal, keep_last),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return 0  # fewer than keep_last terminal jobs — nothing to delete

        cutoff = row[0]
        await self._db.conn.execute(
            f"""
            DELETE FROM jobs
            WHERE status IN ({placeholders})
              AND created_at <= ?
            """,
            (*terminal, cutoff),
        )
        await self._db.conn.commit()
        # SQLite changes() to get row count
        async with self._db.conn.execute("SELECT changes()") as cur:
            result = await cur.fetchone()
            deleted = result[0] if result else 0
        if deleted:
            logger.info("DB cleanup: deleted %d old terminal jobs (kept last %d)", deleted, keep_last)
        return deleted

    async def count_jobs(self) -> dict[str, int]:
        """Return count of jobs per status — useful for the sysinfo display."""
        async with self._db.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ) as cursor:
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}


# ---------------------------------------------------------------------------
# Structured job events (live progress + /logs)
# ---------------------------------------------------------------------------

@dataclass
class JobEvent:
    id: int
    job_id: str
    level: str
    phase: str | None
    message: str
    created_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> JobEvent:
        return cls(**dict(row))


class JobEventRepository:
    """Persisted, structured event stream for a job (powers /logs + live progress)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(
        self, job_id: str, message: str, level: str = "INFO", phase: str | None = None
    ) -> JobEvent:
        await self._db.conn.execute(
            """
            INSERT INTO job_events (job_id, level, phase, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, level, phase, message, _now()),
        )
        await self._db.conn.commit()
        async with self._db.conn.execute(
            "SELECT * FROM job_events WHERE id = last_insert_rowid()"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return JobEvent.from_row(row)

    async def get_events(
        self, job_id: str, limit: int = 50, offset: int = 0
    ) -> list[JobEvent]:
        limit = max(1, min(limit, 500))
        async with self._db.conn.execute(
            """
            SELECT * FROM job_events
            WHERE job_id = ?
            ORDER BY id ASC
            LIMIT ? OFFSET ?
            """,
            (job_id, limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        return [JobEvent.from_row(r) for r in rows]

    async def count_events(self, job_id: str) -> int:
        async with self._db.conn.execute(
            "SELECT COUNT(*) FROM job_events WHERE job_id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Persistent per-job state (crash recovery / resume bundle)
# ---------------------------------------------------------------------------

@dataclass
class JobStateRecord:
    job_id: str
    stage: str | None
    metadata: dict[str, Any]
    updated_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> JobStateRecord:
        data = dict(row)
        raw = data.pop("metadata", "{}")
        try:
            data["metadata"] = json.loads(raw) if raw else {}
        except ValueError:
            data["metadata"] = {}
        return cls(**data)


class JobStateRepository:
    """Persistent per-job runtime state — survives bot restarts."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def set_state(
        self,
        job_id: str,
        stage: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        current = await self.get_state(job_id)
        merged = dict(current.metadata) if current else {}
        if metadata:
            merged.update(metadata)
        await self._db.conn.execute(
            """
            INSERT INTO job_state (job_id, stage, metadata, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                stage = excluded.stage,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (job_id, stage, json.dumps(merged), _now()),
        )
        await self._db.conn.commit()

    async def get_state(self, job_id: str) -> JobStateRecord | None:
        async with self._db.conn.execute(
            "SELECT * FROM job_state WHERE job_id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return JobStateRecord.from_row(row) if row else None

    async def clear(self, job_id: str) -> None:
        await self._db.conn.execute(
            "DELETE FROM job_state WHERE job_id = ?", (job_id,)
        )
        await self._db.conn.commit()


# ---------------------------------------------------------------------------
# Verification results
# ---------------------------------------------------------------------------

class VerificationCheckStatus:
    PASS = "PASS"
    FAIL = "FAIL"
    TIMED_OUT = "TIMED_OUT"
    ERROR = "ERROR"
    NOT_RUN = "NOT_RUN"


@dataclass
class VerificationRecord:
    id: int
    job_id: str
    check_name: str
    command_id: int | None
    status: str
    required: int
    started_at: str | None
    finished_at: str | None
    duration_ms: float | None
    exit_code: int | None
    output: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> VerificationRecord:
        return cls(**dict(row))


class VerificationRepository:
    """Persists controller-run verification check results."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_result(
        self,
        job_id: str,
        check_name: str,
        status: str,
        required: bool,
        started_at: str,
        finished_at: str | None,
        duration_ms: float | None,
        exit_code: int | None,
        output: str | None,
        command_id: int | None = None,
    ) -> int:
        await self._db.conn.execute(
            """
            INSERT INTO verification_results
                (job_id, check_name, command_id, status, required,
                 started_at, finished_at, duration_ms, exit_code, output, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, check_name, command_id, status, 1 if required else 0,
                started_at, finished_at, duration_ms, exit_code, output, _now(),
            ),
        )
        await self._db.conn.commit()
        async with self._db.conn.execute(
            "SELECT last_insert_rowid()"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def list_results(self, job_id: str) -> list[VerificationRecord]:
        async with self._db.conn.execute(
            "SELECT * FROM verification_results WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [VerificationRecord.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Agent run attempts (fallback audit trail)
# ---------------------------------------------------------------------------

class AgentAttemptStatus:
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    CANCELLED = "CANCELLED"


@dataclass
class AgentAttemptRecord:
    id: int
    job_id: str
    attempt_number: int
    agent_name: str
    transport: str | None
    status: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> AgentAttemptRecord:
        return cls(**dict(row))


class AgentAttemptRepository:
    """Persists every agent run attempt for fallback + audit purposes."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def start_attempt(
        self, job_id: str, attempt_number: int, agent_name: str, transport: str | None
    ) -> int:
        await self._db.conn.execute(
            """
            INSERT INTO agent_attempts
                (job_id, attempt_number, agent_name, transport, status,
                 started_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, attempt_number, agent_name, transport, AgentAttemptStatus.RUNNING, _now(), _now()),
        )
        await self._db.conn.commit()
        async with self._db.conn.execute("SELECT last_insert_rowid()") as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def finish_attempt(
        self, attempt_id: int, status: str, error: str | None = None
    ) -> None:
        await self._db.conn.execute(
            "UPDATE agent_attempts SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, error, _now(), attempt_id),
        )
        await self._db.conn.commit()

    async def list_attempts(self, job_id: str) -> list[AgentAttemptRecord]:
        async with self._db.conn.execute(
            "SELECT * FROM agent_attempts WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [AgentAttemptRecord.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Human-in-the-loop approvals
# ---------------------------------------------------------------------------

class ApprovalStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass
class ApprovalRecord:
    id: int
    job_id: str
    stage: str
    risk: str | None
    status: str
    diff_fingerprint: str | None
    requested_at: str
    decided_at: str | None
    decision_by: int | None
    note: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> ApprovalRecord:
        return cls(**dict(row))


class ApprovalRepository:
    """Persists approval requests bound to a diff fingerprint."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        job_id: str,
        stage: str,
        risk: str | None,
        diff_fingerprint: str | None,
    ) -> ApprovalRecord:
        now = _now()
        await self._db.conn.execute(
            """
            INSERT INTO approvals
                (job_id, stage, risk, status, diff_fingerprint, requested_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, stage, risk, ApprovalStatus.PENDING, diff_fingerprint, now, now),
        )
        await self._db.conn.commit()
        async with self._db.conn.execute("SELECT last_insert_rowid()") as cursor:
            row = await cursor.fetchone()
        approval_id = int(row[0]) if row else 0
        returned = await self.get(approval_id)
        assert returned is not None
        return returned

    async def get(self, approval_id: int) -> ApprovalRecord | None:
        async with self._db.conn.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return ApprovalRecord.from_row(row) if row else None

    async def decide(
        self,
        approval_id: int,
        status: str,
        decision_by: int,
        note: str | None = None,
    ) -> None:
        await self._db.conn.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decision_by = ?, note = ? WHERE id = ?",
            (status, _now(), decision_by, note, approval_id),
        )
        await self._db.conn.commit()

    async def list_pending_by_job(self, job_id: str) -> list[ApprovalRecord]:
        async with self._db.conn.execute(
            "SELECT * FROM approvals WHERE job_id = ? AND status = ? ORDER BY id ASC",
            (job_id, ApprovalStatus.PENDING),
        ) as cursor:
            rows = await cursor.fetchall()
        return [ApprovalRecord.from_row(r) for r in rows]

    async def cancel_pending(self, job_id: str) -> int:
        """Mark all pending approvals for a job as CANCELLED. Returns count."""
        cursor = await self._db.conn.execute(
            """
            UPDATE approvals SET status = ?, decided_at = ?, note = ?
            WHERE job_id = ? AND status = ?
            """,
            (ApprovalStatus.CANCELLED, _now(), "job cancelled", job_id, ApprovalStatus.PENDING),
        )
        await self._db.conn.commit()
        return cursor.rowcount
