"""
Async database repository using aiosqlite.

Usage:
    db = await Database.create()
    repo = JobRepository(db)
    job = await repo.create_job(...)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.database.models import ALL_DDL

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
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA foreign_keys=ON")
                for ddl in ALL_DDL:
                    await conn.execute(ddl)
                await conn.commit()
                cls._instance = cls(conn)
                logger.info("Database initialised at %s", path)
            return cls._instance

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
    COMMITTING = "COMMITTING"
    PUSHING = "PUSHING"
    CREATING_PR = "CREATING_PR"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"

    TERMINAL_STATES = {COMPLETED, FAILED, STOPPED}
    ACTIVE_STATES = {
        QUEUED, CLONING, INSPECTING, PLANNING,
        IMPLEMENTING, TESTING, FIXING, COMMITTING, PUSHING, CREATING_PR
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
    ) -> Job:
        now = _now()
        await self._db.conn.execute(
            """
            INSERT INTO jobs
                (job_id, repo_full_name, issue_number, branch, agent,
                 status, started_at, telegram_chat_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, repo_full_name, issue_number, branch, agent,
                JobStatus.QUEUED, now, telegram_chat_id, now,
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
