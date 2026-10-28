"""
Codex CLI Agent implementation.

Supports running Codex via:
1. Codex CLI executable (`codex exec ...` on PATH)
2. Direct autonomous OpenAI execution via httpx fallback if OPENAI_API_KEY is provided
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

import httpx

from app.agents.base import AgentContext, AgentResult, BaseAgent, ProgressCallback
from app.agents.output_capture import OutputCapture
from app.agents.process_cleanup import finish_process, terminate_process
from app.config.settings import settings

logger = logging.getLogger(__name__)

DEFAULT_CODEX_MODEL = "gpt-4o"
DEFAULT_CLI_CODEX_MODEL = "gpt-5.6-terra"
OPENAI_API_BASE = "https://api.openai.com/v1"


def build_codex_cli_command(executable: str, model: str, reasoning_effort: str) -> list[str]:
    """Build a non-interactive, workspace-scoped Codex CLI invocation."""
    return [
        executable,
        "exec",
        "--json",
        "--full-auto",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "-",
    ]


class CodexAgent(BaseAgent):
    name = "codex"

    def __init__(self) -> None:
        self._stopped = False
        self._active_proc: asyncio.subprocess.Process | None = None

    async def stop(self) -> None:
        self._stopped = True
        await terminate_process(self._active_proc)

    async def run(
        self,
        context: AgentContext,
        workspace_path: Path,
        on_progress: ProgressCallback,
    ) -> AgentResult:
        self._stopped = False
        self._active_proc = None

        prompt = self.build_prompt(context)
        cmd_name = settings.CODEX_COMMAND

        # 1. Check if a local Codex CLI executable is available on PATH
        cli_executable = shutil.which(cmd_name)
        if cli_executable:
            return await self._run_cli(
                cli_executable,
                prompt,
                workspace_path,
                on_progress,
                model=context.model or DEFAULT_CLI_CODEX_MODEL,
                reasoning_effort=context.reasoning_effort or "high",
            )

        # 2. Fallback to direct OpenAI execution if API key is provided
        api_key_secret = settings.OPENAI_API_KEY
        if api_key_secret and api_key_secret.get_secret_value():
            return await self._run_direct_api(api_key_secret.get_secret_value(), prompt, context, workspace_path, on_progress)

        msg = (
            f"Codex CLI ('{cmd_name}') is not found on your system PATH, "
            "and OPENAI_API_KEY is not configured in .env.\n"
            "Please ensure 'codex' is installed on your PATH or provide an OPENAI_API_KEY."
        )
        logger.error(msg)
        return AgentResult(
            success=False,
            exit_code=-1,
            summary="Codex not available",
            error=msg,
        )

    async def _run_cli(
        self,
        executable: str,
        prompt: str,
        workspace_path: Path,
        on_progress: ProgressCallback,
        model: str,
        reasoning_effort: str,
    ) -> AgentResult:
        await on_progress(f"Launching Codex ({model}, {reasoning_effort})...")

        full_cmd = build_codex_cli_command(executable, model, reasoning_effort)
        env = os.environ.copy()
        env.update(settings.get_agent_env())

        collected_output = OutputCapture()
        error_output = OutputCapture()
        readers: list[asyncio.Task] = []

        try:
            self._active_proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                cwd=str(workspace_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            if self._active_proc.stdin is not None:
                self._active_proc.stdin.write(prompt.encode("utf-8"))
                await self._active_proc.stdin.drain()
                self._active_proc.stdin.close()

            await on_progress("Codex is implementing changes...")

            stdout_task = asyncio.create_task(collected_output.drain(self._active_proc.stdout))
            stderr_task = asyncio.create_task(error_output.drain(self._active_proc.stderr))
            readers.extend((stdout_task, stderr_task))

            timeout_secs = settings.MAX_AGENT_RUNTIME_MINUTES * 60
            try:
                await asyncio.wait_for(self._active_proc.wait(), timeout=timeout_secs)
            except TimeoutError:
                await self.stop()
                return AgentResult(
                    success=False,
                    exit_code=-1,
                    summary="Codex timed out",
                    error="Exceeded runtime limit",
                )

            await asyncio.gather(stdout_task, stderr_task)

            exit_code = self._active_proc.returncode or 0
            success = exit_code == 0 and not self._stopped

            return AgentResult(
                success=success,
                exit_code=exit_code,
                summary=collected_output.last_lines(15) or "Codex CLI finished.",
                error=error_output.last_lines(10) if not success else None,
                raw_output=collected_output.last_lines(100),
                token_exhausted=collected_output.token_exhausted or error_output.token_exhausted,
            )

        except FileNotFoundError:
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="Codex not available",
                error=f"Codex CLI ('{executable}') was not found.",
            )
        except Exception as exc:
            logger.exception("Codex CLI execution failed: %s", exc)
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="Codex CLI failed",
                error=str(exc),
            )
        finally:
            await finish_process(self._active_proc, readers)

    async def _run_direct_api(
        self,
        api_key: str,
        prompt: str,
        context: AgentContext,
        workspace_path: Path,
        on_progress: ProgressCallback,
    ) -> AgentResult:
        model = context.model or os.environ.get("CODEX_MODEL", DEFAULT_CODEX_MODEL)
        await on_progress(f"Running Codex agent ({model})...")

        system_instruction = (
            "You are an expert autonomous software engineer. "
            "Your goal is to inspect the issue and codebase context, and implement the necessary changes.\n\n"
            "Respond in JSON format with two keys:\n"
            "1. 'summary': A concise markdown explanation of the changes made.\n"
            "2. 'files': A list of objects with 'path' (relative to workspace root) and 'content' (the complete, full new file content).\n\n"
            "Output valid JSON only."
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        await on_progress("Codex is planning and generating changes...")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    f"{OPENAI_API_BASE}/chat/completions",
                    headers=headers,
                    json=payload,
                )

                if response.status_code != 200:
                    return AgentResult(
                        success=False,
                        exit_code=response.status_code,
                        summary="OpenAI API error",
                        error=f"HTTP {response.status_code}: {response.text[:300]}",
                    )

                data = response.json()
                content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="OpenAI error",
                error=str(exc),
            )

        parsed_result = self._parse_json_response(content)
        files = parsed_result.get("files", []) if parsed_result else []
        summary = parsed_result.get("summary", "Codex updated files.") if parsed_result else "Updated files."

        modified_paths: list[str] = []
        for file_info in files:
            rel_path = file_info.get("path")
            file_content = file_info.get("content")
            if rel_path and file_content is not None:
                target_file = (workspace_path / rel_path).resolve()
                if target_file.is_relative_to(workspace_path.resolve()):
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    target_file.write_text(file_content, encoding="utf-8")
                    modified_paths.append(rel_path)

        await on_progress(f"📝 Modified {len(modified_paths)} file(s)")
        return AgentResult(
            success=True,
            exit_code=0,
            summary=summary,
            files_changed=modified_paths,
            raw_output=content[:500],
        )

    def _parse_json_response(self, text: str) -> dict[str, Any] | None:
        cleaned = text.strip()
        if "```json" in cleaned:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
            if match:
                cleaned = match.group(1)
        elif cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = cleaned.strip("`").strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"(\{[\s\S]*\})", cleaned)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
        return None
