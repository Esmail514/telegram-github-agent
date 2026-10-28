"""CSV-backed schedules for prompts that run in a selected local repository."""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_FIELDNAMES = (
    "id",
    "project_id",
    "project_name",
    "prompt",
    "scheduled_at",
    "chat_id",
    "agent",
    "model",
    "reasoning_effort",
    "status",
    "created_at",
    "updated_at",
    "last_error",
)
_locks: dict[Path, asyncio.Lock] = {}


class UntrustedScheduleStoreError(ValueError):
    """A pre-existing CSV must be explicitly approved before import or mutation."""


class LocalPromptStatus:
    PENDING = "PENDING"
    LAUNCHED = "LAUNCHED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class LocalPromptSchedule:
    id: str
    project_id: str
    project_name: str
    prompt: str
    scheduled_at: str
    chat_id: int
    agent: str
    model: str
    reasoning_effort: str
    status: str
    created_at: str
    updated_at: str
    last_error: str | None = None

    @property
    def scheduled_dt(self) -> datetime:
        return datetime.fromisoformat(self.scheduled_at.replace("Z", "+00:00"))

    def display_time(self) -> str:
        return self.scheduled_dt.astimezone().strftime("%d/%m %H:%M")


class LocalPromptScheduleStore:
    """Small, atomically-updated CSV store suitable for user editing."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = _locks.setdefault(self.path, asyncio.Lock())

    @classmethod
    def from_settings(cls) -> LocalPromptScheduleStore:
        from app.config.settings import settings

        return cls(settings.local_prompt_schedules_path)

    @classmethod
    def for_project(cls, project_path: Path) -> LocalPromptScheduleStore:
        """Store schedules beside the selected local project."""
        return cls(project_path / "local_prompt_schedules.csv")

    @classmethod
    async def _candidate_stores(cls, db) -> list[LocalPromptScheduleStore]:
        from app.config.settings import settings
        from app.database.repository import SavedProjectRepository

        default_store = cls.from_settings()
        stores = {default_store.path: default_store}
        if db is not None:
            for project in await SavedProjectRepository(db).list_all_projects():
                if project.user_id != settings.TELEGRAM_ALLOWED_USER_ID:
                    continue
                path = Path(project.path)
                if path.is_dir():
                    store = cls.for_project(path)
                    stores.setdefault(store.path, store)
        return list(stores.values())

    @classmethod
    async def discover(cls, db) -> list[LocalPromptScheduleStore]:
        """Only controller-approved files may supply executable instructions."""
        from app.config.settings import settings

        if db is None:
            return []
        return [
            store for store in await cls._candidate_stores(db)
            if store.path.is_file() and await store.is_trusted(db, settings.TELEGRAM_ALLOWED_USER_ID)
        ]

    @classmethod
    async def discover_untrusted(cls, db) -> list[LocalPromptScheduleStore]:
        from app.config.settings import settings

        if db is None:
            return []
        return [
            store for store in await cls._candidate_stores(db)
            if store.path.is_file() and not await store.is_trusted(db, settings.TELEGRAM_ALLOWED_USER_ID)
        ]

    async def is_trusted(self, db, owner_id: int) -> bool:
        async with db.conn.execute(
            "SELECT 1 FROM trusted_local_schedule_stores WHERE path = ? AND owner_id = ?",
            (str(self.path), owner_id),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def _register_trust(self, db, owner_id: int) -> None:
        await db.conn.execute(
            "INSERT INTO trusted_local_schedule_stores(path, owner_id, approved_at) VALUES (?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET owner_id=excluded.owner_id, approved_at=excluded.approved_at",
            (str(self.path), owner_id, _utc_now()),
        )
        await db.conn.commit()

    async def trust_existing(self, db, owner_id: int) -> None:
        """Called only after an authenticated, explicit approval of this CSV."""
        from app.config.settings import settings
        from app.database.repository import SavedProjectRepository

        if owner_id != settings.TELEGRAM_ALLOWED_USER_ID:
            raise ValueError("CSV owner is not the configured operator.")
        candidates = await self._candidate_stores(db)
        if self.path not in {candidate.path for candidate in candidates}:
            raise ValueError("CSV location does not belong to this owner.")
        async with self._lock:
            if not self.path.is_file():
                raise ValueError("The local prompt CSV does not exist.")
            rows = await asyncio.to_thread(self._read_rows)
            schedules = [_from_row(row) for row in rows]
            if len({item.id for item in schedules}) != len(schedules):
                raise ValueError("Local prompt CSV contains duplicate schedule IDs.")
            for item in schedules:
                if item.chat_id != owner_id:
                    raise ValueError("CSV row owner does not match the configured operator.")
                if item.project_id.startswith("saved:"):
                    project = await SavedProjectRepository(db).get_by_id(int(item.project_id[6:]))
                    if project is None or project.user_id != owner_id:
                        raise ValueError("CSV project owner does not match the configured operator.")
            await self._register_trust(db, owner_id)

    async def _prepare_for_creation(self, db) -> None:
        from app.config.settings import settings

        owner_id = settings.TELEGRAM_ALLOWED_USER_ID
        if await self.is_trusted(db, owner_id):
            return

        def create_empty_exclusively() -> bool:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.path.open("x", encoding="utf-8", newline="") as file:
                    csv.DictWriter(file, fieldnames=_FIELDNAMES).writeheader()
                return True
            except FileExistsError:
                return False

        if not await asyncio.to_thread(create_empty_exclusively):
            raise UntrustedScheduleStoreError(
                "ملف CSV موجود مسبقًا. راجعه ثم اعتمده من المهام المجدولة قبل استخدامه."
            )
        await self._register_trust(db, owner_id)

    async def ensure_exists(self, db=None) -> None:
        """Create an editable empty CSV before the first local schedule exists."""
        async with self._lock:
            if db is not None:
                await self._prepare_for_creation(db)
            if not self.path.exists():
                await asyncio.to_thread(self._write_rows, [])

    async def create(
        self,
        project_id: str,
        project_name: str,
        prompt: str,
        scheduled_at: datetime,
        chat_id: int,
        agent: str = "codex",
        model: str = "gpt-5.6-terra",
        reasoning_effort: str = "high",
        db=None,
    ) -> LocalPromptSchedule:
        clean_prompt = prompt.strip()
        if not project_id or not clean_prompt:
            raise ValueError("A project and prompt are required.")
        if scheduled_at.tzinfo is None:
            raise ValueError("The scheduled time must be timezone-aware.")
        now = _utc_now()
        schedule = LocalPromptSchedule(
            id=f"local-{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            project_name=project_name,
            prompt=clean_prompt,
            scheduled_at=_format_time(scheduled_at),
            chat_id=chat_id,
            agent=agent,
            model=model,
            reasoning_effort=reasoning_effort,
            status=LocalPromptStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        _from_row(_to_row(schedule))
        async with self._lock:
            if db is not None:
                from app.config.settings import settings

                if chat_id != settings.TELEGRAM_ALLOWED_USER_ID:
                    raise ValueError("CSV owner is not the configured operator.")
                await self._prepare_for_creation(db)
            rows = await asyncio.to_thread(self._read_rows)
            rows.append(_to_row(schedule))
            await asyncio.to_thread(self._write_rows, rows)
        return schedule

    async def get(self, schedule_id: str) -> LocalPromptSchedule | None:
        return next(
            (item for item in await self.list_all(ignore_invalid=True) if item.id == schedule_id), None
        )

    async def list_all(
        self, chat_id: int | None = None, *, ignore_invalid: bool = False
    ) -> list[LocalPromptSchedule]:
        async with self._lock:
            rows = await asyncio.to_thread(self._read_rows)
        schedules = []
        for row in rows:
            try:
                schedules.append(_from_row(row))
            except ValueError:
                if not ignore_invalid:
                    raise
        counts = Counter(row["id"] for row in rows)
        if not ignore_invalid and any(count > 1 for count in counts.values()):
            raise ValueError("Local prompt CSV contains duplicate schedule IDs.")
        if ignore_invalid:
            schedules = [item for item in schedules if counts[item.id] == 1]
        if chat_id is not None:
            schedules = [item for item in schedules if item.chat_id == chat_id]
        return sorted(schedules, key=lambda item: item.scheduled_dt)

    async def list_due(self, before: datetime | None = None) -> list[LocalPromptSchedule]:
        cutoff = before or datetime.now(UTC)
        return [
            item
            for item in await self.list_all()
            if item.status == LocalPromptStatus.PENDING and item.scheduled_dt <= cutoff
        ]

    async def list_due_with_errors(
        self, before: datetime
    ) -> tuple[list[LocalPromptSchedule], list[str]]:
        async with self._lock:
            rows = await asyncio.to_thread(self._read_rows)
            counts = Counter(row["id"] for row in rows)
            due: list[LocalPromptSchedule] = []
            errors: list[str] = []
            changed = False
            for number, row in enumerate(rows, start=2):
                try:
                    schedule = _from_row(row)
                    if counts[schedule.id] != 1:
                        raise ValueError("Duplicate schedule ID")
                except ValueError:
                    if row.get("status") in {
                        LocalPromptStatus.FAILED, LocalPromptStatus.COMPLETED,
                        LocalPromptStatus.CANCELLED, LocalPromptStatus.LAUNCHED,
                    }:
                        continue
                    error = f"Invalid schedule data in CSV row {number}."
                    errors.append(error)
                    row.update(
                        status=LocalPromptStatus.FAILED, last_error=error, updated_at=_utc_now()
                    )
                    changed = True
                    continue
                if not row.get("status"):
                    row.update(status=schedule.status, updated_at=_utc_now())
                    changed = True
                if schedule.status == LocalPromptStatus.PENDING and schedule.scheduled_dt <= before:
                    due.append(schedule)
            if changed:
                await asyncio.to_thread(self._write_rows, rows)
        return sorted(due, key=lambda item: item.scheduled_dt), errors

    async def update_time(self, schedule_id: str, scheduled_at: datetime) -> bool:
        if scheduled_at.tzinfo is None:
            raise ValueError("The scheduled time must be timezone-aware.")
        return await self._update_pending(
            schedule_id, {"scheduled_at": _format_time(scheduled_at)}
        )

    async def mark_launched(self, schedule_id: str) -> bool:
        return await self._update_pending(schedule_id, {"status": LocalPromptStatus.LAUNCHED})

    async def release_launch(self, schedule_id: str) -> bool:
        return await self._update_status(
            schedule_id, LocalPromptStatus.LAUNCHED, {"status": LocalPromptStatus.PENDING}
        )

    async def mark_resumed(self, schedule_id: str) -> bool:
        async with self._lock:
            rows = await asyncio.to_thread(self._read_rows)
            matches = [row for row in rows if row.get("id") == schedule_id]
            if len(matches) != 1 or matches[0].get("status") not in {
                LocalPromptStatus.LAUNCHED, LocalPromptStatus.FAILED,
            }:
                return False
            matches[0].update(
                status=LocalPromptStatus.LAUNCHED, last_error="", updated_at=_utc_now()
            )
            await asyncio.to_thread(self._write_rows, rows)
            return True

    async def cancel(self, schedule_id: str) -> bool:
        return await self._update_pending(schedule_id, {"status": LocalPromptStatus.CANCELLED})

    async def mark_failed(self, schedule_id: str, error: str) -> bool:
        changes = {"status": LocalPromptStatus.FAILED, "last_error": error[:300]}
        async with self._lock:
            rows = await asyncio.to_thread(self._read_rows)
            for row in rows:
                if row.get("id") != schedule_id or row.get("status") not in {
                    LocalPromptStatus.PENDING,
                    LocalPromptStatus.LAUNCHED,
                }:
                    continue
                row.update(changes)
                row["updated_at"] = _utc_now()
                await asyncio.to_thread(self._write_rows, rows)
                return True
        return False

    async def mark_completed(self, schedule_id: str) -> bool:
        return await self._update_status(
            schedule_id, LocalPromptStatus.LAUNCHED, {"status": LocalPromptStatus.COMPLETED}
        )

    async def _update_pending(self, schedule_id: str, changes: dict[str, str]) -> bool:
        return await self._update_status(schedule_id, LocalPromptStatus.PENDING, changes)

    async def _update_status(
        self, schedule_id: str, expected_status: str, changes: dict[str, str]
    ) -> bool:
        async with self._lock:
            rows = await asyncio.to_thread(self._read_rows)
            found = False
            for row in rows:
                if row.get("id") != schedule_id or row.get("status") != expected_status:
                    continue
                row.update(changes)
                row["updated_at"] = _utc_now()
                found = True
                break
            if found:
                await asyncio.to_thread(self._write_rows, rows)
            return found

    def _read_rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                if reader.fieldnames is None or not set(_FIELDNAMES).issubset(reader.fieldnames):
                    raise ValueError("Local prompt CSV has missing required columns.")
                return [{key: row.get(key) or "" for key in _FIELDNAMES} for row in reader]
        except (OSError, csv.Error, UnicodeError) as exc:
            raise ValueError("Could not read the local prompt CSV.") from exc

    def _write_rows(self, rows: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, self.path)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_row(schedule: LocalPromptSchedule) -> dict[str, str]:
    return {
        "id": schedule.id,
        "project_id": schedule.project_id,
        "project_name": schedule.project_name,
        "prompt": schedule.prompt,
        "scheduled_at": schedule.scheduled_at,
        "chat_id": str(schedule.chat_id),
        "agent": schedule.agent,
        "model": schedule.model,
        "reasoning_effort": schedule.reasoning_effort,
        "status": schedule.status,
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
        "last_error": schedule.last_error or "",
    }


def _from_row(row: dict[str, str]) -> LocalPromptSchedule:
    try:
        schedule = LocalPromptSchedule(
            id=row["id"],
            project_id=row["project_id"],
            project_name=row["project_name"],
            prompt=row["prompt"],
            scheduled_at=row["scheduled_at"],
            chat_id=int(row["chat_id"]),
            agent=row.get("agent") or "codex",
            model=row.get("model") or "gpt-5.6-terra",
            reasoning_effort=row.get("reasoning_effort") or "high",
            status=row.get("status") or LocalPromptStatus.PENDING,
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
            last_error=row.get("last_error") or None,
        )
        scheduled_dt = schedule.scheduled_dt
        if scheduled_dt.tzinfo is None or scheduled_dt.utcoffset() is None:
            raise ValueError("Missing schedule timezone")
        if not schedule.id or not schedule.project_id or not schedule.prompt.strip():
            raise ValueError("Missing schedule data")
        if schedule.project_id.startswith("saved:"):
            if not re.fullmatch(r"saved:[1-9][0-9]{0,18}", schedule.project_id):
                raise ValueError("Invalid saved-project identity")
            if int(schedule.project_id.removeprefix("saved:")) > 9223372036854775807:
                raise ValueError("Saved-project identity is out of range")
        if schedule.status not in {
            LocalPromptStatus.PENDING, LocalPromptStatus.LAUNCHED, LocalPromptStatus.COMPLETED,
            LocalPromptStatus.CANCELLED, LocalPromptStatus.FAILED,
        }:
            raise ValueError("Invalid schedule status")
        if schedule.agent not in {"codex", "antigravity"}:
            raise ValueError("Unsupported schedule agent")
        if schedule.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ValueError("Unsupported reasoning effort")
        return schedule
    except (KeyError, ValueError) as exc:
        raise ValueError("Local prompt CSV contains an invalid schedule row.") from exc
