"""
OpenCode agent implementation.

Uses: opencode run "<prompt>" --auto --format json
Output is JSONL (one JSON object per line).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from app.agents.base import AgentContext, AgentResult, BaseAgent, ProgressCallback
from app.config.settings import settings

logger = logging.getLogger(__name__)

# Minimum seconds between Telegram progress updates (throttle)
_PROGRESS_THROTTLE_SECS = 10


class OpenCodeAgent(BaseAgent):
    name = "opencode"

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        if self._process and self._process.returncode is None:
            try:
                # Try SIGTERM first
                self._process.terminate()
                await asyncio.sleep(3)
                if self._process.returncode is None:
                    self._process.kill()
                logger.info("OpenCode process terminated")
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

        prompt = self.build_prompt(context)

        # Write prompt to a temp file inside the workspace
        prompt_file = workspace_path / ".ai_agent_task.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        logger.info("Agent task prompt written to %s", prompt_file)

        # Build command
        cmd = settings.OPENCODE_COMMAND
        # Use opencode run "<prompt>" --auto --format json
        full_cmd = [
            cmd, "run",
            f"@{prompt_file.name}",  # Reference the file via @filename
            "--auto",
            "--format", "json",
        ]

        # Environment: forward AI provider keys
        env = os.environ.copy()
        env.update(settings.get_agent_env())

        timeout_secs = settings.MAX_AGENT_RUNTIME_MINUTES * 60
        collected_output: list[str] = []
        last_progress_time = 0.0
        last_phase = ""
        error_output: list[str] = []

        await on_progress("🤖 Starting OpenCode agent...")

        try:
            self._process = await asyncio.create_subprocess_exec(
                *full_cmd,
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            logger.info("OpenCode started (pid=%s): %s", self._process.pid, " ".join(full_cmd))

            async def read_stderr() -> None:
                assert self._process and self._process.stderr
                async for line in self._process.stderr:
                    decoded = line.decode(errors="replace").rstrip()
                    if decoded:
                        error_output.append(decoded)
                        logger.debug("[opencode stderr] %s", decoded)

            # Read stdout (JSONL events) and stderr concurrently
            stderr_task = asyncio.create_task(read_stderr())

            assert self._process.stdout
            async for raw_line in self._process.stdout:
                if self._stopped:
                    break

                line = raw_line.decode(errors="replace").rstrip()
                if not line:
                    continue

                collected_output.append(line)
                if len(collected_output) > 500:
                    collected_output = collected_output[-500:]

                # Parse JSONL event
                phase_msg = self._parse_event(line)
                if phase_msg:
                    logger.debug("[opencode event] %s", phase_msg)
                    now = time.monotonic()
                    should_send = (
                        now - last_progress_time >= _PROGRESS_THROTTLE_SECS
                        and phase_msg != last_phase
                    )
                    if should_send:
                        await on_progress(phase_msg)
                        last_progress_time = now
                        last_phase = phase_msg

            await stderr_task

            # Wait for process to finish (with timeout)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=timeout_secs)
            except TimeoutError:
                await self.stop()
                return AgentResult(
                    success=False,
                    exit_code=-1,
                    summary="Agent timed out",
                    error=(
                        f"Exceeded MAX_AGENT_RUNTIME_MINUTES="
                        f"{settings.MAX_AGENT_RUNTIME_MINUTES}"
                    ),
                    raw_output="\n".join(collected_output[-50:]),
                )

            exit_code = self._process.returncode or 0
            success = exit_code == 0 and not self._stopped

            summary = self._build_summary(collected_output, exit_code, error_output)

            logger.info("OpenCode finished (exit=%d, success=%s)", exit_code, success)
            return AgentResult(
                success=success,
                exit_code=exit_code,
                summary=summary,
                error=("\n".join(error_output[-10:]) if not success else None),
                raw_output="\n".join(collected_output[-100:]),
            )

        except FileNotFoundError:
            msg = (
                f"opencode not found. Install it and set OPENCODE_COMMAND in .env. "
                f"Command: {cmd}"
            )
            logger.error(msg)
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="opencode executable not found",
                error=msg,
            )
        except Exception as exc:
            logger.exception("Unexpected error running OpenCode: %s", exc)
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="Agent execution error",
                error=str(exc),
            )
        finally:
            # Clean up prompt file
            try:
                prompt_file.unlink(missing_ok=True)
            except OSError:
                pass

    def _parse_event(self, line: str) -> str | None:
        """
        Parse a JSONL event from opencode and return a human-readable message,
        or None if not worth surfacing to Telegram.
        """
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Plain text output — surface if it looks meaningful
            if len(line) > 5 and not line.startswith("{"):
                return None  # Skip non-JSON noise
            return None

        event_type = event.get("type", "")

        # Map opencode event types to friendly messages
        if event_type in ("step_start", "session_start"):
            return "🔍 Agent is inspecting the repository..."
        if event_type == "tool_use":
            tool = event.get("name", "")
            if "read" in tool.lower() or "view" in tool.lower():
                return "📖 Reading files..."
            if "write" in tool.lower() or "edit" in tool.lower():
                return "🛠 Implementing changes..."
            if "bash" in tool.lower() or "run" in tool.lower() or "exec" in tool.lower():
                input_text = str(event.get("input", ""))
                if any(w in input_text for w in ("test", "pytest", "jest", "flutter test")):
                    return "🧪 Running tests..."
                if any(w in input_text for w in ("lint", "ruff", "eslint", "analyze")):
                    return "🔍 Running linter..."
                if any(w in input_text for w in ("build", "compile", "make")):
                    return "🏗 Building..."
                return "⚙️ Running command..."
        if event_type == "text":
            text = event.get("text", "")
            if "error" in text.lower() or "fail" in text.lower():
                return "❌ Encountered errors — investigating..."
            if "fix" in text.lower():
                return "🔧 Fixing issues..."
            if "complet" in text.lower() or "done" in text.lower() or "finish" in text.lower():
                return "✅ Implementation complete!"
        if event_type == "session_complete":
            return "✅ Agent finished."

        return None

    def _build_summary(
        self, output_lines: list[str], exit_code: int, errors: list[str]
    ) -> str:
        """Build a readable summary from the last N output lines."""
        relevant = []
        for line in output_lines[-30:]:
            try:
                event = json.loads(line)
                if event.get("type") == "text":
                    relevant.append(event.get("text", ""))
            except json.JSONDecodeError:
                if line.strip():
                    relevant.append(line)

        summary_text = "\n".join(relevant[-10:]) if relevant else "(no output captured)"
        status = "succeeded" if exit_code == 0 else f"failed (exit={exit_code})"
        return f"Agent {status}.\n\n{summary_text[:1000]}"
