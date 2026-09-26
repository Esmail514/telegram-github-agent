"""
Personal Task Runner — executes AI coding agents locally on custom tasks.

Allows running Antigravity, OpenCode, Claude, etc., directly on the local
machine without requiring a GitHub issue or repository clone.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.manager import agent_manager
from app.config.settings import settings

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class PersonalTaskResult:
    """Result of a personal agent task."""
    task_id: str
    success: bool
    summary: str
    workspace_path: Path
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0


class PersonalTaskManager:
    """
    Manages personal agent task lifecycle:
    - Enforces single active task.
    - Tracks running agents and allows clean cancellation.
    - Tracks modified and created files in the target directory.
    """

    def __init__(self) -> None:
        self._active_agent: BaseAgent | None = None
        self._active_asyncio_task: asyncio.Task | None = None
        self._current_info: dict | None = None
        self._stopped_flag: bool = False
        self._lock = asyncio.Lock()

    def is_busy(self) -> bool:
        return self._active_agent is not None or self._active_asyncio_task is not None

    def get_active_info(self) -> dict | None:
        return self._current_info

    async def stop_task(self) -> bool:
        """Terminate the active personal agent task."""
        if not self.is_busy():
            return False

        logger.info("Stopping active personal agent task...")
        self._stopped_flag = True
        if self._active_agent:
            try:
                await self._active_agent.stop()
            except Exception as exc:
                logger.warning("Error stopping active agent: %s", exc)

        if self._active_asyncio_task and not self._active_asyncio_task.done():
            self._active_asyncio_task.cancel()
            try:
                await self._active_asyncio_task
            except (asyncio.CancelledError, Exception):
                pass

        return True

    def _snapshot_dir(self, directory: Path) -> dict[str, float]:
        """Return a mapping of relative file paths to their modification times."""
        snapshot: dict[str, float] = {}
        if not directory.exists():
            return snapshot
        for root, _, files in os.walk(directory):
            # Ignore git, cache, and hidden folders
            if any(part.startswith(".") or part in ("__pycache__", "venv", "node_modules") for part in Path(root).parts):
                continue
            for f in files:
                p = Path(root) / f
                try:
                    rel = str(p.relative_to(directory))
                    snapshot[rel] = p.stat().st_mtime
                except (OSError, ValueError):
                    pass
        return snapshot

    def _build_structure_tree(self, directory: Path) -> str:
        """Build a simple directory structure text for prompt context."""
        lines: list[str] = []
        count = 0
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "venv", "node_modules")]
            rel = os.path.relpath(root, directory)
            indent = "  " * (rel.count(os.sep) if rel != "." else 0)
            if rel != ".":
                lines.append(f"{indent}📁 {os.path.basename(root)}/")
            for f in files[:20]:
                if not f.startswith("."):
                    lines.append(f"{indent}  📄 {f}")
                    count += 1
                    if count >= 60:
                        lines.append(f"{indent}  ... (remaining files truncated)")
                        return "\n".join(lines)
        return "\n".join(lines) or "(Empty directory)"

    async def run_task(
        self,
        prompt: str,
        workspace_path: Path | None = None,
        agent_name: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> PersonalTaskResult:
        """
        Execute an agent task on the given or default personal workspace.
        """
        async with self._lock:
            if self.is_busy():
                raise RuntimeError("توجد مهمة شخصية قيد التشغيل بالفعل. أوقفها أولاً باستخدام /task_stop")

            task_id = f"task_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
            target_dir = workspace_path or (settings.PERSONAL_WORKSPACE_DIR / "tasks" / task_id)
            target_dir.mkdir(parents=True, exist_ok=True)

            agent = agent_manager.get_agent(agent_name or settings.DEFAULT_AGENT)
            self._active_agent = agent
            self._active_asyncio_task = asyncio.current_task()
            self._stopped_flag = False
            start_time = asyncio.get_event_loop().time()

            self._current_info = {
                "task_id": task_id,
                "prompt": prompt,
                "workspace": str(target_dir),
                "agent": agent.name,
                "start_time": datetime.datetime.now().isoformat(),
            }

        async def _noop(msg: str) -> None:
            pass

        progress_cb = on_progress or _noop

        before_files = self._snapshot_dir(target_dir)

        # Build context
        first_line = prompt.strip().split("\n")[0][:80]
        context = AgentContext(
            repo_full_name="local/personal-workspace",
            repo_url=str(target_dir),
            repo_description="Local Personal Task Directory",
            default_branch="local",
            issue_number=1,
            issue_title=first_line,
            issue_body=prompt,
            issue_labels=["personal-task", "local"],
            branch="local",
            repo_structure=self._build_structure_tree(target_dir),
            key_files={},
        )

        try:
            logger.info("Starting personal task %s with agent %s in %s", task_id, agent.name, target_dir)
            await progress_cb(f"🚀 بدء مهمة الـ Agent (`{agent.name}`)...\n📁 المجلد: `{target_dir.name}`")

            result: AgentResult = await agent.run(context, target_dir, progress_cb)
            duration = asyncio.get_event_loop().time() - start_time

            if self._stopped_flag:
                return PersonalTaskResult(
                    task_id=task_id,
                    success=False,
                    summary="تم إيقاف المهمة من قبل المستخدم.",
                    workspace_path=target_dir,
                    error="Stopped by user",
                    duration_seconds=round(duration, 1),
                )

            after_files = self._snapshot_dir(target_dir)
            files_created = [f for f in after_files if f not in before_files]
            files_modified = [f for f, mtime in after_files.items() if f in before_files and mtime > before_files[f]]

            return PersonalTaskResult(
                task_id=task_id,
                success=result.success,
                summary=result.summary,
                workspace_path=target_dir,
                files_created=files_created,
                files_modified=files_modified,
                error=result.error,
                duration_seconds=round(duration, 1),
            )

        except asyncio.CancelledError:
            duration = asyncio.get_event_loop().time() - start_time
            return PersonalTaskResult(
                task_id=task_id,
                success=False,
                summary="تم إلغاء المهمة من قبل المستخدم.",
                workspace_path=target_dir,
                error="Cancelled",
                duration_seconds=round(duration, 1),
            )
        except Exception as exc:
            duration = asyncio.get_event_loop().time() - start_time
            logger.exception("Error executing personal task %s: %s", task_id, exc)
            return PersonalTaskResult(
                task_id=task_id,
                success=False,
                summary=f"فشلت المهمة بسبب خطأ: {exc}",
                workspace_path=target_dir,
                error=str(exc),
                duration_seconds=round(duration, 1),
            )
        finally:
            self._active_agent = None
            self._active_asyncio_task = None
            self._current_info = None
            self._stopped_flag = False



# Module singleton
personal_task_manager = PersonalTaskManager()
