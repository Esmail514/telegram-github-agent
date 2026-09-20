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
from app.config.settings import settings

logger = logging.getLogger(__name__)


class AntigravityAgent(BaseAgent):
    name = "antigravity"

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass

    async def run(
        self,
        context: AgentContext,
        workspace_path: Path,
        on_progress: ProgressCallback,
    ) -> AgentResult:
        self._stopped = False
        self._process = None

        cmd_name = settings.ANTIGRAVITY_COMMAND

        # Verify if command is available on PATH or standard install paths
        cmd_path = shutil.which(cmd_name)
        if not cmd_path:
            for candidate in ("antigravity", "agy", "antigravity-ide"):
                resolved = shutil.which(candidate)
                if resolved:
                    cmd_path = resolved
                    break

        if not cmd_path:
            ide_cmd = (
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Programs"
                / "Antigravity IDE"
                / "bin"
                / "antigravity-ide.cmd"
            )
            if ide_cmd.exists():
                cmd_path = str(ide_cmd)

        prompt = self.build_prompt(context)
        prompt_file = workspace_path / ".antigravity_task.md"
        prompt_file.write_text(prompt, encoding="utf-8")

        await on_progress(f"🤖 Launching Antigravity CLI ({cmd_name})...")

        # Command arguments for non-interactive execution
        # Supports both: agy --task "<prompt>" or agy run <file>
        executable = cmd_path or cmd_name
        full_cmd = [executable, "--prompt", prompt]

        env = os.environ.copy()
        env.update(settings.get_agent_env())

        collected_output: list[str] = []
        error_output: list[str] = []

        try:
            self._process = await asyncio.create_subprocess_exec(
                *full_cmd,
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            await on_progress("🔍 Antigravity CLI is inspecting and implementing changes...")

            async def read_stream(stream: asyncio.StreamReader | None, target_list: list[str], is_stderr: bool = False) -> None:
                if not stream:
                    return
                while not stream.at_eof():
                    line = await stream.readline()
                    if not line:
                        break
                    decoded = line.decode(errors="replace").rstrip()
                    if decoded:
                        target_list.append(decoded)
                        if is_stderr:
                            logger.debug("[antigravity stderr] %s", decoded)
                        else:
                            logger.debug("[antigravity stdout] %s", decoded)

            stdout_task = asyncio.create_task(read_stream(self._process.stdout, collected_output, False))
            stderr_task = asyncio.create_task(read_stream(self._process.stderr, error_output, True))

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

            summary_text = "\n".join(collected_output[-15:]) if collected_output else "Antigravity CLI completed."

            return AgentResult(
                success=success,
                exit_code=exit_code,
                summary=summary_text,
                error=("\n".join(error_output[-10:]) if not success else None),
                raw_output="\n".join(collected_output[-100:]),
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
            prompt_file.unlink(missing_ok=True)
