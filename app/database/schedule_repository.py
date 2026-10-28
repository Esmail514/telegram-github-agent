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


def _format_time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("The scheduled time must be timezone-aware.")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    project_id: str | None
    project_name: str | None
    workspace_path: str | None
    status: str
    created_at: str
    model: str | None = None
    reasoning_effort: str | None = None

    @property
    def scheduled_dt(self) -> datetime:
        return datetime.fromisoformat(self.scheduled_at.replace("Z", "+00:00"))

    def display_time(self) -> str:
        """Return a schedule time in the timezone configured on this device."""
        return self.scheduled_dt.astimezone().strftime("%d/%m %H:%M")


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
        project_id: str | None = None,
        project_name: str | None = None,
        workspace_path: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> ScheduledJob:
        """Insert a new scheduled job and return it."""
        scheduled_at_str = _format_time(scheduled_at)
        now = _now()
        cursor = await self._db.conn.execute(
            """
            INSERT INTO scheduled_jobs
                (repo_full_name, issue_number, agent, scheduled_at, chat_id,
                 project_id, project_name, workspace_path, status, created_at, model, reasoning_effort)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo_full_name, issue_number, agent, scheduled_at_str,
             chat_id, project_id, project_name, workspace_path, ScheduledJobStatus.PENDING, now,
             model, reasoning_effort),
        )
        await self._db.conn.commit()
        row_id = cursor.lastrowid
        assert row_id is not None
        created = await self.get(row_id)
        assert created is not None
        return created

    async def get(self, job_id: int) -> ScheduledJob | None:
        async with self._db.conn.execute(
            "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_job(row) if row else None

    async def list_pending(self, before: datetime | None = None) -> list[ScheduledJob]:
        """Return PENDING jobs whose scheduled_at <= now (or custom threshold)."""
        cutoff = _format_time(before or datetime.now(UTC))
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

    async def mark_launched(self, job_id: int) -> bool:
        cursor = await self._db.conn.execute(
            "UPDATE scheduled_jobs SET status = ? WHERE id = ? AND status = ?",
            (ScheduledJobStatus.LAUNCHED, job_id, ScheduledJobStatus.PENDING),
        )
        await self._db.conn.commit()
        return cursor.rowcount == 1

    async def release_launch(self, job_id: int) -> None:
        await self._db.conn.execute(
            "UPDATE scheduled_jobs SET status = ? WHERE id = ? AND status = ?",
            (ScheduledJobStatus.PENDING, job_id, ScheduledJobStatus.LAUNCHED),
        )
        await self._db.conn.commit()

    async def cancel(self, job_id: int) -> bool:
        """Cancel a PENDING scheduled job. Returns True if it was actually pending."""
        cursor = await self._db.conn.execute(
            "UPDATE scheduled_jobs SET status = ? WHERE id = ? AND status = ?",
            (ScheduledJobStatus.CANCELLED, job_id, ScheduledJobStatus.PENDING),
        )
        await self._db.conn.commit()
        return cursor.rowcount == 1

    async def update_time(self, job_id: int, scheduled_at: datetime) -> bool:
        """Change the time of a pending job without changing its identity."""
        cursor = await self._db.conn.execute(
            "UPDATE scheduled_jobs SET scheduled_at = ? WHERE id = ? AND status = ?",
            (_format_time(scheduled_at), job_id, ScheduledJobStatus.PENDING),
        )
        await self._db.conn.commit()
        return cursor.rowcount == 1


def _row_to_job(row: Any) -> ScheduledJob:
    d = dict(row)
    return ScheduledJob(
        id=d["id"],
        repo_full_name=d["repo_full_name"],
        issue_number=d["issue_number"],
        agent=d["agent"],
        scheduled_at=d["scheduled_at"],
        chat_id=d["chat_id"],
        project_id=d.get("project_id"),
        project_name=d.get("project_name"),
        workspace_path=d.get("workspace_path"),
        status=d["status"],
        created_at=d["created_at"],
        model=d.get("model"),
        reasoning_effort=d.get("reasoning_effort"),
    )
