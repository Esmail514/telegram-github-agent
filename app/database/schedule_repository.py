"""
Repository for scheduled jobs — CRUD operations on the scheduled_jobs table.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.database.repository import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ScheduledJobStatus:
    PENDING = "PENDING"
    LAUNCHED = "LAUNCHED"
    CANCELLED = "CANCELLED"


@dataclass
class ScheduledJob:
    id: int
    repo_full_name: str
    issue_number: int
    agent: str
    scheduled_at: str       # ISO format UTC string
    chat_id: int
    status: str
    created_at: str

    @property
    def scheduled_dt(self) -> datetime:
        return datetime.fromisoformat(self.scheduled_at.replace("Z", "+00:00"))

    def display_time(self) -> str:
        """Return local-friendly display string (UTC)."""
        dt = self.scheduled_dt
        return dt.strftime("%d/%m %H:%M UTC")


class ScheduleRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        repo_full_name: str,
        issue_number: int,
        agent: str,
        scheduled_at: datetime,
        chat_id: int,
    ) -> ScheduledJob:
        """Insert a new scheduled job and return it."""
        scheduled_at_str = scheduled_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        now = _now()
        cursor = await self._db.conn.execute(
            """
            INSERT INTO scheduled_jobs
                (repo_full_name, issue_number, agent, scheduled_at, chat_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_full_name, issue_number, agent, scheduled_at_str,
             chat_id, ScheduledJobStatus.PENDING, now),
        )
        await self._db.conn.commit()
        row_id = cursor.lastrowid
        return await self.get(row_id)  # type: ignore[return-value]

    async def get(self, job_id: int) -> ScheduledJob | None:
        async with self._db.conn.execute(
            "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_job(row) if row else None

    async def list_pending(self, before: datetime | None = None) -> list[ScheduledJob]:
        """Return PENDING jobs whose scheduled_at <= now (or custom threshold)."""
        cutoff = (before or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with self._db.conn.execute(
            """
            SELECT * FROM scheduled_jobs
            WHERE status = ? AND scheduled_at <= ?
            ORDER BY scheduled_at ASC
            """,
            (ScheduledJobStatus.PENDING, cutoff),
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_job(r) for r in rows]

    async def list_all_pending(self, chat_id: int | None = None) -> list[ScheduledJob]:
        """Return all PENDING scheduled jobs ordered by scheduled_at ASC."""
        if chat_id is not None:
            async with self._db.conn.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE status = ? AND chat_id = ?
                ORDER BY scheduled_at ASC
                """,
                (ScheduledJobStatus.PENDING, chat_id),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with self._db.conn.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE status = ?
                ORDER BY scheduled_at ASC
                """,
                (ScheduledJobStatus.PENDING,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]

    async def count_pending(self, chat_id: int | None = None) -> int:
        """Return the count of PENDING scheduled jobs."""
        if chat_id is not None:
            async with self._db.conn.execute(
                "SELECT COUNT(*) FROM scheduled_jobs WHERE status = ? AND chat_id = ?",
                (ScheduledJobStatus.PENDING, chat_id),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 0
        else:
            async with self._db.conn.execute(
                "SELECT COUNT(*) FROM scheduled_jobs WHERE status = ?",
                (ScheduledJobStatus.PENDING,),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 0

    async def list_all(self, chat_id: int | None = None, limit: int = 20) -> list[ScheduledJob]:
        """Return all jobs, optionally filtered by chat_id."""
        if chat_id is not None:
            async with self._db.conn.execute(
                "SELECT * FROM scheduled_jobs WHERE chat_id = ? ORDER BY scheduled_at ASC LIMIT ?",
                (chat_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with self._db.conn.execute(
                "SELECT * FROM scheduled_jobs ORDER BY scheduled_at ASC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]

    async def mark_launched(self, job_id: int) -> None:
        await self._db.conn.execute(
            "UPDATE scheduled_jobs SET status = ? WHERE id = ?",
            (ScheduledJobStatus.LAUNCHED, job_id),
        )
        await self._db.conn.commit()

    async def cancel(self, job_id: int) -> bool:
        """Cancel a PENDING scheduled job. Returns True if it was actually pending."""
        async with self._db.conn.execute(
            "SELECT status FROM scheduled_jobs WHERE id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row or row["status"] != ScheduledJobStatus.PENDING:
            return False
        await self._db.conn.execute(
            "UPDATE scheduled_jobs SET status = ? WHERE id = ?",
            (ScheduledJobStatus.CANCELLED, job_id),
        )
        await self._db.conn.commit()
        return True


def _row_to_job(row: Any) -> ScheduledJob:
    d = dict(row)
    return ScheduledJob(
        id=d["id"],
        repo_full_name=d["repo_full_name"],
        issue_number=d["issue_number"],
        agent=d["agent"],
        scheduled_at=d["scheduled_at"],
        chat_id=d["chat_id"],
        status=d["status"],
        created_at=d["created_at"],
    )
