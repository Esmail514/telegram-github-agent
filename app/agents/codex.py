"""
Codex CLI Agent implementation.

Supports running Codex via:
1. Codex CLI executable (e.g. `codex run ...` or `codex --task ...` on PATH)
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
from app.config.settings import settings

logger = logging.getLogger(__name__)

DEFAULT_CODEX_MODEL = "gpt-4o"
OPENAI_API_BASE = "https://api.openai.com/v1"


class CodexAgent(BaseAgent):
    name = "codex"

    def __init__(self) -> None:
        self._stopped = False
        self._active_proc: asyncio.subprocess.Process | None = None

    async def stop(self) -> None:
        self._stopped = True
        if self._active_proc and self._active_proc.returncode is None:
            try:
                self._active_proc.terminate()
            except ProcessLookupError:
                pass

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
            return await self._run_cli(cli_executable, prompt, workspace_path, on_progress)

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
    ) -> AgentResult:
        await on_progress(f"🤖 Launching Codex CLI ({executable})...")

        prompt_file = workspace_path / ".codex_task.md"
        prompt_file.write_text(prompt, encoding="utf-8")

        full_cmd = [executable, "run", f"@{prompt_file.name}"]
        env = os.environ.copy()
        env.update(settings.get_agent_env())

        collected_output: list[str] = []
        error_output: list[str] = []

        try:
            self._active_proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                cwd=str(workspace_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            await on_progress("🔍 Codex CLI is implementing changes...")

            async def read_stream(stream: asyncio.StreamReader | None, target_list: list[str]) -> None:
                if not stream:
                    return
                while not stream.at_eof():
                    line = await stream.readline()
                    if not line:
                        break
                    decoded = line.decode(errors="replace").rstrip()
                    if decoded:
                        target_list.append(decoded)

            stdout_task = asyncio.create_task(read_stream(self._active_proc.stdout, collected_output))
            stderr_task = asyncio.create_task(read_stream(self._active_proc.stderr, error_output))

            timeout_secs = settings.MAX_AGENT_RUNTIME_MINUTES * 60
            try:
                await asyncio.wait_for(self._active_proc.wait(), timeout=timeout_secs)
            except asyncio.TimeoutError:
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
                summary="\n".join(collected_output[-15:]) if collected_output else "Codex CLI finished.",
                error="\n".join(error_output[-10:]) if not success else None,
                raw_output="\n".join(collected_output[-100:]),
            )
        finally:
            prompt_file.unlink(missing_ok=True)

    async def _run_direct_api(
        self,
        api_key: str,
        prompt: str,
        context: AgentContext,
        workspace_path: Path,
        on_progress: ProgressCallback,
    ) -> AgentResult:
        model = os.environ.get("CODEX_MODEL", DEFAULT_CODEX_MODEL)
        await on_progress(f"🤖 Running Codex agent ({model})...")

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

        await on_progress("🔍 Codex is planning and generating changes...")

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
                if str(target_file).startswith(str(workspace_path.resolve())):
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    target_file.write_text(file_content, encoding="utf-8")
                    modified_paths.append(rel_path)

        await on_progress(f"📝 Modified {len(modified_paths)} file(s)")
        return AgentResult(
            success=True,
            exit_code=0,
            summary=summary,
            files_modified=modified_paths,
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
