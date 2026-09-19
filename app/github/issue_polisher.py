"""
Issue Polisher — lightweight AI service to improve GitHub issue quality.

Sends the issue title + body to an LLM and returns a polished version with
a better title, clearer description, and suggested labels.

Provider selection (first key found wins):
    ANTHROPIC_API_KEY  → Claude (claude-3-5-haiku-20241022)
    OPENAI_API_KEY     → GPT-4o-mini
    GOOGLE_GENERATIVEAI_API_KEY → Gemini 1.5 Flash
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

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
    Async service that calls an LLM API to polish a GitHub issue.

    Usage::

        polisher = IssuePolisher()
        result = await polisher.polish(
            repo_full_name="owner/repo",
            title="bug in login",
            body="login doesnt work",
        )
        print(result.title)   # "Login fails when …"
    """

    async def polish(
        self,
        repo_full_name: str,
        title: str,
        body: str,
    ) -> PolishedIssue:
        """
        Send the issue to an LLM and return the polished version.

        Raises:
            RuntimeError: if no AI API key is configured.
            httpx.HTTPStatusError / httpx.RequestError: on API failure.
        """
        from app.config.settings import settings  # lazy import to avoid circular deps

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
    # Provider implementations
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
        """Parse the JSON response from the LLM into a PolishedIssue."""
        # Strip markdown fences if the model added them despite instructions
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse AI response as JSON: %s\nRaw: %s", exc, raw[:500])
            raise ValueError(f"AI returned invalid JSON: {exc}") from exc

        return PolishedIssue(
            title=str(data.get("title", "")).strip(),
            body=str(data.get("body", "")).strip(),
            labels=[str(lb).strip() for lb in data.get("labels", []) if lb],
            explanation=str(data.get("explanation", "")).strip(),
        )


# Module-level singleton
issue_polisher = IssuePolisher()
