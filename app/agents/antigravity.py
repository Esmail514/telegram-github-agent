"""
Antigravity CLI Agent implementation.

Executes the Antigravity CLI (agy / antigravity) directly on the repository workspace.
Streams progress back to Telegram.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from app.agents.base import AgentContext, AgentResult, BaseAgent, ProgressCallback
from app.agents.output_capture import OutputCapture
from app.agents.process_cleanup import finish_process, terminate_process
from app.config.settings import settings

logger = logging.getLogger(__name__)


class AntigravityAgent(BaseAgent):
    name = "antigravity"

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        await terminate_process(self._process)

    async def run(
        self,
        context: AgentContext,
        workspace_path: Path,
        on_progress: ProgressCallback,
    ) -> AgentResult:
        self._stopped = False
        self._process = None

        cmd_name = settings.ANTIGRAVITY_COMMAND

        # Only documented Antigravity CLI executables may run work.
        cmd_path = _resolve_antigravity_cli(cmd_name)

        prompt_file = workspace_path / ".antigravity_task.md"
        is_continuation = prompt_file.exists()
        base_prompt = self.build_prompt(context)
        if is_continuation:
            prompt = (
                f"{base_prompt}\n\n"
                "## ⚠️ RESUMED TASK CONTINUATION\n"
                "This task was paused previously (e.g. token quota reached) and is now RESUMED with a renewed token/account budget.\n"
                "IMPORTANT INSTRUCTIONS FOR RESUMPTION:\n"
                "1. Run `git status` and `git diff` first to review all changes already made in the previous run.\n"
                "2. DO NOT wipe or redo completed work; continue implementing the remaining requirements.\n"
                "3. Verify all changes and run tests before finishing.\n"
            )
        else:
            prompt = base_prompt

        prompt_file.write_text(prompt, encoding="utf-8")

        await on_progress(f"🤖 Launching Antigravity CLI ({cmd_name})...")

        # Command arguments for non-interactive execution
        # Supports both: agy --task "<prompt>" or agy run <file>
        executable = cmd_path or cmd_name
        full_cmd = [executable, "--prompt", prompt]

        env = os.environ.copy()
        env.update(settings.get_agent_env())

        collected_output = OutputCapture()
        error_output = OutputCapture()
        readers: list[asyncio.Task] = []

        try:
            self._process = await asyncio.create_subprocess_exec(
                *full_cmd,
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            await on_progress("🔍 Antigravity CLI is inspecting and implementing changes...")

            stdout_task = asyncio.create_task(collected_output.drain(self._process.stdout))
            stderr_task = asyncio.create_task(error_output.drain(self._process.stderr))
            readers.extend((stdout_task, stderr_task))

            timeout_secs = settings.MAX_AGENT_RUNTIME_MINUTES * 60
            try:
                await asyncio.wait_for(self._process.wait(), timeout=timeout_secs)
            except TimeoutError:
                await self.stop()
                return AgentResult(
                    success=False,
                    exit_code=-1,
                    summary="Antigravity agent timed out",
                    error=f"Exceeded {settings.MAX_AGENT_RUNTIME_MINUTES} minutes timeout.",
                )

            await asyncio.gather(stdout_task, stderr_task)

            exit_code = self._process.returncode or 0
            success = exit_code == 0 and not self._stopped

            token_exhausted = collected_output.token_exhausted or error_output.token_exhausted

            if not success and token_exhausted:
                return AgentResult(
                    success=False,
                    exit_code=-2,
                    summary="Antigravity account token limit / quota reached.",
                    error="Account token limit or quota exceeded. Switch Antigravity account to continue.",
                    raw_output=collected_output.last_lines(100),
                    token_exhausted=True,
                )

            summary_text = collected_output.last_lines(15) or "Antigravity CLI completed."

            return AgentResult(
                success=success,
                exit_code=exit_code,
                summary=summary_text,
                error=(error_output.last_lines(10) if not success else None),
                raw_output=collected_output.last_lines(100),
                token_exhausted=token_exhausted,
            )

        except FileNotFoundError:
            # If binary not on path, return informative message
            msg = (
                f"Antigravity CLI ('{cmd_name}') executable not found on system PATH.\n"
                "Please make sure 'agy' or 'antigravity' is installed and accessible."
            )
            logger.error(msg)
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="CLI not found",
                error=msg,
            )
        except Exception as exc:
            logger.exception("Error executing Antigravity CLI: %s", exc)
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="CLI execution failed",
                error=str(exc),
            )
        finally:
            try:
                await finish_process(self._process, readers)
            finally:
                prompt_file.unlink(missing_ok=True)


def _resolve_antigravity_cli(configured_command: str) -> str | None:
    """Find only supported Antigravity CLI executables on PATH."""
    for candidate in dict.fromkeys((configured_command, "agy", "antigravity")):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None
