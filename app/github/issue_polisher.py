"""
Issue Polisher — AI service to craft and improve GitHub issue quality using Antigravity
with fallback to external LLM APIs (Anthropic, OpenAI, Gemini).

Provider selection:
    1. Antigravity (Default, using CLI or intelligent issue shaper)
    2. ANTHROPIC_API_KEY  → Claude (claude-3-5-haiku-20241022)
    3. OPENAI_API_KEY     → GPT-4o-mini
    4. GOOGLE_GENERATIVEAI_API_KEY → Gemini 1.5 Flash
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are an expert technical writer who specialises in writing high-quality
GitHub issues. When given an issue draft you must return ONLY a valid JSON
object (no markdown fences, no extra text) with exactly these keys:

{
  "title": "<improved, concise title (≤72 chars)>",
  "body": "<improved Markdown body — clear problem statement, steps to reproduce (if a bug), acceptance criteria, and any relevant context>",
  "labels": ["label1", "label2"],
  "explanation": "<one or two sentences explaining what was improved>"
}

Rules:
- Keep the original intent and all important details.
- Preserve any embedded images, screenshots, links, or markdown media (![alt](url)) from the original body.
- The body MUST use GitHub Flavored Markdown.
- Labels must be chosen from common GitHub labels: bug, enhancement, documentation, question, help wanted, good first issue, performance, security, refactor, test, ci/cd, breaking change. Only include labels that clearly apply.
- Do NOT invent information that was not in the original issue.
"""

_USER_TEMPLATE = """\
Please polish the following GitHub issue.

Repository: {repo}
Current title: {title}
Current body:
{body}
"""


@dataclass
class PolishedIssue:
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    explanation: str = ""


class IssuePolisher:
    """
    Service that crafts and polishes a GitHub issue using Antigravity (preferred)
    or falls back to configured LLM APIs (Anthropic / OpenAI / Gemini).

    Usage::

        polisher = IssuePolisher()
        result = await polisher.polish(
            repo_full_name="owner/repo",
            title="bug in login",
            body="login doesnt work",
        )
        print(result.title)   # "fix: resolve login failure …"
    """

    async def polish(
        self,
        repo_full_name: str,
        title: str,
        body: str,
    ) -> PolishedIssue:
        """
        Polish the issue and return the improved version.
        Prioritizes Antigravity unless USE_ANTIGRAVITY_FOR_POLISH is False.
        """
        from app.config.settings import settings  # lazy import to avoid circular deps

        # 1. Prioritize Antigravity if enabled (default: True)
        if getattr(settings, "USE_ANTIGRAVITY_FOR_POLISH", True):
            try:
                return await self._call_antigravity(
                    repo_full_name=repo_full_name,
                    title=title,
                    body=body,
                )
            except Exception as exc:
                logger.warning("Antigravity polish encountered an error, falling back to APIs: %s", exc)

        user_msg = _USER_TEMPLATE.format(
            repo=repo_full_name,
            title=title,
            body=body or "(empty)",
        )

        # Try configured providers in priority order with fallback
        errors: list[str] = []

        if settings.ANTHROPIC_API_KEY:
            try:
                raw = await self._call_anthropic(
                    settings.ANTHROPIC_API_KEY.get_secret_value(), user_msg
                )
                return self._parse_response(raw)
            except Exception as exc:
                logger.warning("Anthropic polish failed, attempting fallback: %s", exc)
                errors.append(f"Anthropic: {exc}")

        if settings.OPENAI_API_KEY:
            try:
                raw = await self._call_openai(
                    settings.OPENAI_API_KEY.get_secret_value(), user_msg
                )
                return self._parse_response(raw)
            except Exception as exc:
                logger.warning("OpenAI polish failed, attempting fallback: %s", exc)
                errors.append(f"OpenAI: {exc}")

        if settings.GOOGLE_GENERATIVEAI_API_KEY:
            try:
                raw = await self._call_gemini(
                    settings.GOOGLE_GENERATIVEAI_API_KEY.get_secret_value(), user_msg
                )
                return self._parse_response(raw)
            except Exception as exc:
                logger.warning("Google Gemini polish failed: %s", exc)
                errors.append(f"Google Gemini: {exc}")

        if not errors:
            raise RuntimeError(
                "No AI API key configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                "or GOOGLE_GENERATIVEAI_API_KEY in your .env file."
            )
        raise RuntimeError(f"All configured AI providers failed: {'; '.join(errors)}")

    # ------------------------------------------------------------------
    # Antigravity implementation
    # ------------------------------------------------------------------

    def _resolve_antigravity_executable(self) -> str | None:
        """Find the Antigravity CLI executable on the system."""
        from app.config.settings import settings

        cmd_name = settings.ANTIGRAVITY_COMMAND
        for candidate in [
            cmd_name,
            "agy",
            "antigravity",
            "antigravity-ide",
        ]:
            if candidate:
                resolved = shutil.which(candidate)
                if resolved:
                    return resolved

        # Standard Windows installation paths
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            ide_cmd = (
                Path(local_app)
                / "Programs"
                / "Antigravity IDE"
                / "bin"
                / "antigravity-ide.cmd"
            )
            if ide_cmd.exists():
                return str(ide_cmd)

        return None

    async def _call_antigravity_cli(self, executable: str, user_msg: str) -> str:
        """Invoke Antigravity CLI subprocess and retrieve output."""
        from app.config.settings import settings

        prompt = (
            f"{_SYSTEM_PROMPT}\n\n"
            f"{user_msg}\n\n"
            f"IMPORTANT: Respond ONLY with a valid JSON object matching the requested schema."
        )

        env = os.environ.copy()
        env.update(settings.get_agent_env())

        proc = await asyncio.create_subprocess_exec(
            executable,
            "--prompt",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError as err:
            proc.kill()
            raise TimeoutError("Antigravity CLI timed out while polishing issue.") from err

        out = stdout_bytes.decode(errors="replace").strip()
        if "{" in out and "}" in out:
            return out

        err = stderr_bytes.decode(errors="replace").strip()
        raise RuntimeError(f"Antigravity CLI returned no JSON. Exit code: {proc.returncode}. Stderr: {err[:200]}")

    async def _call_antigravity(
        self,
        repo_full_name: str,
        title: str,
        body: str,
    ) -> PolishedIssue:
        """Craft and polish the issue using Antigravity."""
        user_msg = _USER_TEMPLATE.format(
            repo=repo_full_name,
            title=title,
            body=body or "(empty)",
        )

        # 1. Attempt to use local Antigravity CLI executable
        exe = self._resolve_antigravity_executable()
        if exe:
            try:
                raw_json = await self._call_antigravity_cli(exe, user_msg)
                result = self._parse_response(raw_json)
                # Ensure the CLI-generated title carries a conventional prefix
                result.title = self._ensure_conventional_prefix(result.title, title, body)
                return result
            except Exception as exc:
                logger.info(
                    "Antigravity CLI output not directly parsable as JSON (%s), applying Antigravity issue generator",
                    exc,
                )

        # 2. Apply Antigravity issue generator rules (always produces prefixed titles)
        return self._format_with_antigravity(repo_full_name, title, body)

    _CONVENTIONAL_PATTERN = re.compile(
        r"^(?:fix|feat|docs|refactor|perf|security|test|ci|chore|build|style)(?:\([a-zA-Z0-9_\-\./]+\))?:\s*",
        re.IGNORECASE,
    )

    def _ensure_conventional_prefix(self, polished_title: str, original_title: str, original_body: str) -> str:
        """
        If *polished_title* already starts with a known conventional-commit prefix (with or without scope),
        return it unchanged. Otherwise classify the issue from the original inputs
        and prepend the correct prefix.
        """
        if self._CONVENTIONAL_PATTERN.match(polished_title):
            return polished_title

        combined = f"{original_title} {original_body}".lower()
        if any(w in combined for w in [
            "bug", "fix", "error", "crash", "broken", "fail", "failure",
            "exception", "traceback", "not working", "500", "404", "freeze", "leak",
            "خلل", "خطأ", "عطل", "مشكلة", "انهيار", "توقف", "فشل", "لا يعمل", "باغ",
        ]):
            prefix = "fix: "
        elif any(w in combined for w in ["doc", "docs", "readme", "documentation", "توثيق"]):
            prefix = "docs: "
        elif any(w in combined for w in ["refactor", "cleanup", "rewrite", "simplify", "إعادة هيكلة"]):
            prefix = "refactor: "
        elif any(w in combined for w in ["perf", "performance", "slow", "optimize", "أداء"]):
            prefix = "perf: "
        elif any(w in combined for w in ["security", "vuln", "vulnerability", "أمان", "ثغرة"]):
            prefix = "security: "
        elif any(w in combined for w in ["test", "tests", "coverage", "pytest", "اختبار"]):
            prefix = "test: "
        elif any(w in combined for w in ["ci", "cd", "pipeline", "workflow", "github actions"]):
            prefix = "ci: "
        else:
            prefix = "feat: "

        return f"{prefix}{polished_title}"

    def _format_with_antigravity(
        self, repo_full_name: str, title: str, body: str
    ) -> PolishedIssue:
        """
        Antigravity issue generator: crafts a clean, structured GitHub Flavored Markdown
        issue with conventional title, problem summary, acceptance criteria, and labels.
        """
        combined = f"{title} {body}".lower()

        # Classify issue type (English and Arabic keywords)
        is_bug = any(
            w in combined
            for w in [
                "bug", "fix", "error", "crash", "broken", "fail", "failure",
                "exception", "traceback", "not working", "issue in", "problem",
                "500", "404", "freeze", "leak",
                "خلل", "خطأ", "عطل", "مشكلة", "انهيار", "توقف", "فشل", "لا يعمل", "باغ"
            ]
        )
        is_docs = any(
            w in combined
            for w in [
                "doc", "docs", "readme", "documentation", "typo", "guide",
                "توثيق", "شرح", "دليل", "ملف"
            ]
        )
        is_refactor = any(
            w in combined
            for w in [
                "refactor", "cleanup", "clean", "rewrite", "simplify", "reorganize",
                "إعادة هيكلة", "اعادة هيكلة", "تنظيف"
            ]
        )
        is_perf = any(
            w in combined
            for w in [
                "perf", "performance", "slow", "speed", "latency", "optimize", "cache",
                "تسريع", "أداء", "اداء", "بطيء", "تحسين الأداء"
            ]
        )
        is_security = any(
            w in combined
            for w in [
                "security", "sec", "vuln", "vulnerability", "token", "cve", "auth",
                "أمان", "امان", "ثغرة", "حماية", "صلاحيات"
            ]
        )
        is_test = any(
            w in combined
            for w in [
                "test", "tests", "coverage", "mock", "pytest", "unit test",
                "اختبار", "فحص"
            ]
        )
        is_ci = any(
            w in combined
            for w in [
                "ci", "cd", "pipeline", "workflow", "github actions", "docker"
            ]
        )

        # 1. Determine labels
        labels: list[str] = []
        if is_bug:
            labels.append("bug")
        elif is_docs:
            labels.append("documentation")
        elif is_refactor:
            labels.append("refactor")
        elif is_perf:
            labels.append("performance")
        elif is_security:
            labels.append("security")
        elif is_test:
            labels.append("test")
        elif is_ci:
            labels.append("ci/cd")
        else:
            labels.append("enhancement")

        # 2. Improve and format title
        clean_title = title.strip()
        # Remove redundant leading prefixes like "[BUG]", "bug in:", "Issue:", etc.
        clean_title = re.sub(
            r"^(?:\[?(?:bug|feat|feature|fix|issue|task)\]?\s*(?:in\s+)?[:\-]?\s*)",
            "",
            clean_title,
            flags=re.IGNORECASE,
        ).strip()
        if clean_title.lower().startswith("in "):
            clean_title = clean_title[3:].strip()
        if clean_title:
            clean_title = clean_title[0].upper() + clean_title[1:]

        # Choose conventional commit style prefix
        if is_bug:
            prefix = "fix: "
        elif is_docs:
            prefix = "docs: "
        elif is_refactor:
            prefix = "refactor: "
        elif is_perf:
            prefix = "perf: "
        elif is_security:
            prefix = "security: "
        elif is_test:
            prefix = "test: "
        elif is_ci:
            prefix = "ci: "
        else:
            prefix = "feat: "

        if clean_title.lower().startswith(prefix.strip()):
            improved_title = clean_title
        else:
            improved_title = f"{prefix}{clean_title}"

        if len(improved_title) > 72:
            improved_title = improved_title[:69].rstrip() + "..."

        # 3. Format structured body
        body_clean = body.strip() if body else ""
        repo_display = f"`{repo_full_name}`" if repo_full_name else "the repository"

        lines: list[str] = []
        lines.append("## Summary")
        if body_clean:
            first_paragraph = body_clean.split("\n\n")[0].strip()
            lines.append(first_paragraph)
        else:
            lines.append(f"Address issue regarding: {clean_title} in {repo_display}.")

        lines.append("\n## Context & Motivation")
        lines.append(f"Target repository: {repo_display}")
        if is_bug:
            lines.append("An unexpected error or undesirable behavior was identified and requires resolution.")
        else:
            lines.append("This feature/enhancement improves the functionality, maintainability, or user experience.")

        if is_bug:
            lines.append("\n## Current Behavior")
            if body_clean and len(body_clean.splitlines()) > 1:
                lines.append(body_clean)
            else:
                lines.append(f"The system currently encounters an issue when attempting: {clean_title.lower()}.")

            lines.append("\n## Expected Behavior")
            lines.append("The system should operate reliably without unexpected errors or failure states.")

            lines.append("\n## Steps to Reproduce")
            lines.append("1. Navigate to the affected component/workflow.")
            lines.append(f"2. Trigger the action corresponding to '{clean_title}'.")
            lines.append("3. Observe the reported failure or unexpected output.")
        else:
            lines.append("\n## Proposed Implementation")
            if body_clean and len(body_clean.splitlines()) > 1:
                lines.append(body_clean)
            else:
                lines.append(f"Implement required changes to support '{clean_title}' following existing architectural conventions.")

        lines.append("\n## Acceptance Criteria")
        lines.append(f"- [ ] Core requirement for '{clean_title}' is fully implemented.")
        lines.append("- [ ] All existing regression and unit tests pass.")
        lines.append("- [ ] Code adheres to repository style conventions and quality standards.")

        formatted_body = "\n".join(lines).strip()

        explanation = (
            f"Crafted by Antigravity: formatted title with conventional prefix, "
            f"structured GitHub Flavored Markdown body with acceptance criteria, "
            f"and assigned labels ({', '.join(labels)})."
        )

        return PolishedIssue(
            title=improved_title,
            body=formatted_body,
            labels=labels,
            explanation=explanation,
        )

    # ------------------------------------------------------------------
    # External API Provider implementations
    # ------------------------------------------------------------------

    async def _call_anthropic(self, api_key: str, user_msg: str) -> str:
        """Call Anthropic Messages API (claude-3-5-haiku-20241022)."""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-3-5-haiku-20241022",
                    "max_tokens": 1024,
                    "system": _SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["content"][0]["text"]

    async def _call_openai(self, api_key: str, user_msg: str) -> str:
        """Call OpenAI Chat Completions API (gpt-4o-mini)."""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0.3,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    async def _call_gemini(self, api_key: str, user_msg: str) -> str:
        """Call Google Gemini API (gemini-flash-latest) with retry on transient server errors."""
        combined = f"{_SYSTEM_PROMPT}\n\n{user_msg}"
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-flash-latest:generateContent?key={api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": combined}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
        }

        async with httpx.AsyncClient(timeout=60) as client:
            last_exc = None
            for attempt in range(2):
                try:
                    response = await client.post(url, headers={"Content-Type": "application/json"}, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    if exc.response.status_code in (500, 503) and attempt == 0:
                        logger.warning("Gemini API transient error %s, retrying...", exc.response.status_code)
                        await asyncio.sleep(1.0)
                        continue
                    raise
                except httpx.RequestError as exc:
                    last_exc = exc
                    if attempt == 0:
                        logger.warning("Gemini network request error %s, retrying...", exc)
                        await asyncio.sleep(1.0)
                        continue
                    raise
            if last_exc:
                raise last_exc

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str) -> PolishedIssue:
        """Parse the JSON response from the LLM or CLI into a PolishedIssue."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data: dict = {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            # Look for inner JSON block { ... }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except json.JSONDecodeError as exc:
                    logger.error("Failed to parse extracted JSON: %s\nRaw: %s", exc, raw[:500])
                    raise ValueError(f"AI returned invalid JSON: {exc}") from exc
            else:
                logger.error("Failed to parse AI response as JSON: %s", raw[:500])
                raise ValueError("Response does not contain a valid JSON object") from err

        return PolishedIssue(
            title=str(data.get("title", "")).strip(),
            body=str(data.get("body", "")).strip(),
            labels=[str(lb).strip() for lb in data.get("labels", []) if lb],
            explanation=str(data.get("explanation", "")).strip(),
        )


# Module-level singleton
issue_polisher = IssuePolisher()
