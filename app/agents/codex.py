"""
Codex agent implementation.

Uses OpenAI API (via httpx) to inspect repository context, plan changes,
modify files, run automated tests, and fix errors iteratively.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from app.agents.base import AgentContext, AgentResult, BaseAgent, ProgressCallback
from app.config.settings import settings

logger = logging.getLogger(__name__)

# Default OpenAI model used for Codex tasks
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

        api_key_secret = settings.OPENAI_API_KEY
        if not api_key_secret or not api_key_secret.get_secret_value():
            msg = (
                "OPENAI_API_KEY is not configured. "
                "Please set OPENAI_API_KEY in your .env file to use the codex agent."
            )
            logger.error(msg)
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="Missing OPENAI_API_KEY",
                error=msg,
            )

        api_key = api_key_secret.get_secret_value()
        model = os.environ.get("CODEX_MODEL", DEFAULT_CODEX_MODEL)

        await on_progress(f"🤖 Initialising Codex agent ({model})...")

        prompt = self.build_prompt(context)
        system_instruction = (
            "You are an expert autonomous software engineer. "
            "Your goal is to inspect the issue and codebase context, and implement the necessary changes.\n\n"
            "Respond in JSON format with two keys:\n"
            "1. 'summary': A concise markdown explanation of the changes made.\n"
            "2. 'files': A list of objects with 'path' (relative to workspace root) and 'content' (the complete, full new file content).\n\n"
            "Output valid JSON only. Do not wrap in backticks if possible, or use standard markdown ```json."
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # ------------------------------------------------------------------
        # Phase 1: Planning & Implementation
        # ------------------------------------------------------------------
        await on_progress("🔍 Codex is analysing repository context and planning changes...")

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
                    err_text = response.text[:500]
                    return AgentResult(
                        success=False,
                        exit_code=response.status_code,
                        summary="OpenAI API error",
                        error=f"HTTP {response.status_code}: {err_text}",
                    )

                data = response.json()
                content = data["choices"][0]["message"]["content"]

        except Exception as exc:
            logger.exception("Error calling OpenAI API: %s", exc)
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="OpenAI connection error",
                error=str(exc),
            )

        if self._stopped:
            return AgentResult(
                success=False, exit_code=-1, summary="Stopped by user", error="Stopped"
            )

        # ------------------------------------------------------------------
        # Phase 2: Apply file modifications
        # ------------------------------------------------------------------
        await on_progress("🛠 Applying code modifications...")
        parsed_result = self._parse_json_response(content)
        if not parsed_result or not parsed_result.get("files"):
            # Fallback: try to extract code blocks if direct JSON parsing failed
            files = self._extract_code_blocks(content)
            summary = parsed_result.get("summary") if parsed_result else content[:300]
        else:
            files = parsed_result.get("files", [])
            summary = parsed_result.get("summary", "Codex implemented the requested changes.")

        if not files:
            return AgentResult(
                success=False,
                exit_code=-1,
                summary="No file changes produced",
                error="Codex did not produce any valid file modifications in its response.",
                raw_output=content[:500],
            )

        modified_paths: list[str] = []
        for file_info in files:
            rel_path = file_info.get("path")
            file_content = file_info.get("content")
            if not rel_path or file_content is None:
                continue

            target_file = (workspace_path / rel_path).resolve()
            # Safety check: prevent escaping workspace
            if not str(target_file).startswith(str(workspace_path.resolve())):
                logger.warning("Skipping path outside workspace: %s", rel_path)
                continue

            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(file_content, encoding="utf-8")
            modified_paths.append(rel_path)
            logger.info("Codex wrote: %s", target_file)

        await on_progress(f"📝 Modified {len(modified_paths)} file(s): {', '.join(modified_paths[:5])}")

        # ------------------------------------------------------------------
        # Phase 3: Run automated verification / tests
        # ------------------------------------------------------------------
        test_cmd = self._detect_test_command(workspace_path)
        if test_cmd:
            await on_progress(f"🧪 Running verification tests: `{test_cmd}`...")
            test_rc, test_out, test_err = await self._run_shell(test_cmd, workspace_path)

            iteration = 0
            while test_rc != 0 and iteration < settings.MAX_FIX_ITERATIONS and not self._stopped:
                iteration += 1
                await on_progress(f"🔧 Test failed. Fix iteration {iteration}/{settings.MAX_FIX_ITERATIONS}...")

                fix_prompt = (
                    f"The previous implementation caused tests to fail with exit code {test_rc}.\n\n"
                    f"Test output:\n{test_out[-1500:]}\n{test_err[-1500:]}\n\n"
                    "Please fix the code and output the full updated files in the same JSON format."
                )

                fix_payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": fix_prompt},
                    ],
                    "temperature": 0.2,
                }

                try:
                    async with httpx.AsyncClient(timeout=180.0) as client:
                        fix_resp = await client.post(
                            f"{OPENAI_API_BASE}/chat/completions",
                            headers=headers,
                            json=fix_payload,
                        )
                        if fix_resp.status_code == 200:
                            fix_data = fix_resp.json()
                            fix_content = fix_data["choices"][0]["message"]["content"]
                            fix_parsed = self._parse_json_response(fix_content)
                            fix_files = (
                                fix_parsed.get("files", [])
                                if fix_parsed
                                else self._extract_code_blocks(fix_content)
                            )
                            for file_info in fix_files:
                                rel_path = file_info.get("path")
                                file_content = file_info.get("content")
                                if rel_path and file_content is not None:
                                    target = (workspace_path / rel_path).resolve()
                                    if str(target).startswith(str(workspace_path.resolve())):
                                        target.parent.mkdir(parents=True, exist_ok=True)
                                        target.write_text(file_content, encoding="utf-8")

                            # Re-test
                            test_rc, test_out, test_err = await self._run_shell(test_cmd, workspace_path)
                            if test_rc == 0:
                                await on_progress("✅ All tests passed after fix!")
                                break
                except Exception as exc:
                    logger.warning("Fix iteration %d encountered error: %s", iteration, exc)
                    break

        await on_progress("✅ Codex completed changes!")

        return AgentResult(
            success=True,
            exit_code=0,
            summary=f"Codex implemented the changes.\n\n{summary}",
            files_modified=modified_paths,
            raw_output=content[:1000],
        )

    def _parse_json_response(self, text: str) -> dict[str, Any] | None:
        """Attempt to extract and parse JSON from the model response."""
        # Clean markdown code fences if present
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
            # Try to find JSON object substring
            match = re.search(r"(\{[\s\S]*\})", cleaned)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
        return None

    def _extract_code_blocks(self, text: str) -> list[dict[str, str]]:
        """Fallback: extract file paths and contents from markdown code fences."""
        files: list[dict[str, str]] = []
        pattern = re.compile(r"(?:###?\s*`?([^\n`]+)`?|File:\s*`?([^\n`]+)`?)\n+```[a-zA-Z]*\n([\s\S]*?)\n```")
        for match in pattern.finditer(text):
            filename = (match.group(1) or match.group(2) or "").strip()
            content = match.group(3)
            if filename and content:
                files.append({"path": filename, "content": content})
        return files

    def _detect_test_command(self, workspace: Path) -> str | None:
        """Detect automated test runner present in workspace."""
        if (workspace / "pytest.ini").exists() or (workspace / "setup.cfg").exists() or (workspace / "pyproject.toml").exists():
            return "py -m pytest"
        if (workspace / "package.json").exists():
            return "npm test"
        if (workspace / "Cargo.toml").exists():
            return "cargo test"
        if (workspace / "go.mod").exists():
            return "go test ./..."
        return None

    async def _run_shell(self, cmd: str, cwd: Path) -> tuple[int, str, str]:
        """Run a command inside workspace."""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._active_proc = proc
            stdout, stderr = await proc.communicate()
            return (
                proc.returncode or 0,
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
            )
        except Exception as exc:
            return -1, "", str(exc)
