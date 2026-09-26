"""
Agent fallback architecture — failure classification + routing.

Fallback ONLY happens for *retryable infrastructure* failures :
UNAVAILABLE, STARTUP_FAILURE, RATE_LIMIT, TRANSPORT_FAILURE (and TIMEOUT only
when the operator explicitly enables it). It NEVER happens for
SECURITY_VIOLATION, SECRET_DETECTED, USER_CANCELLED, APPROVAL_REJECTED,
INVALID_REPOSITORY, VERIFICATION_FAILURE or POLICY_VIOLATION.

Every attempt is persisted to `agent_attempts` so the audit trail is complete.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.agents.base import AgentContext, AgentResult, ProgressCallback
from app.database.repository import (
    AgentAttemptRecord,
    AgentAttemptRepository,
    AgentAttemptStatus,
    Database,
)
from app.runner.progress import ProgressReporter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Failure codes
# ---------------------------------------------------------------------------

class FailureCode:
    UNAVAILABLE = "UNAVAILABLE"
    STARTUP_FAILURE = "STARTUP_FAILURE"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSPORT_FAILURE = "TRANSPORT_FAILURE"
    TIMEOUT = "TIMEOUT"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    SECRET_DETECTED = "SECRET_DETECTED"
    USER_CANCELLED = "USER_CANCELLED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    INVALID_REPOSITORY = "INVALID_REPOSITORY"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    UNKNOWN = "UNKNOWN"


RETRYABLE_INFRA = {
    FailureCode.UNAVAILABLE,
    FailureCode.STARTUP_FAILURE,
    FailureCode.RATE_LIMIT,
    FailureCode.TRANSPORT_FAILURE,
}

NEVER_RETRY = {
    FailureCode.SECURITY_VIOLATION,
    FailureCode.SECRET_DETECTED,
    FailureCode.USER_CANCELLED,
    FailureCode.APPROVAL_REJECTED,
    FailureCode.INVALID_REPOSITORY,
    FailureCode.VERIFICATION_FAILURE,
    FailureCode.POLICY_VIOLATION,
}


def is_retryable(code: str, timeout_allowed: bool = False) -> bool:
    """Eligibility matrix: which failure codes may trigger a fallback."""
    if code in NEVER_RETRY:
        return False
    if code == FailureCode.TIMEOUT:
        return timeout_allowed
    return code in RETRYABLE_INFRA


def classify_failure(
    result: AgentResult | None,
    exc: Exception | None = None,
) -> str:
    """Map an agent result / exception to a canonical FailureCode."""
    if exc is not None:
        text = f"{type(exc).__name__}: {exc}".lower()
        if "timeout" in text:
            return FailureCode.TIMEOUT
        if "not found on path" in text or "executable" in text or "[errno 2]" in text:
            return FailureCode.UNAVAILABLE
        if "ratelimit" in text or "rate limit" in text or "429" in text:
            return FailureCode.RATE_LIMIT
        if "secret" in text or "credential" in text or "scan" in text:
            return FailureCode.SECURITY_VIOLATION
        if "cancel" in text:
            return FailureCode.USER_CANCELLED
        return FailureCode.TRANSPORT_FAILURE

    if result is None:
        return FailureCode.UNKNOWN

    if getattr(result, "token_exhausted", False):
        return FailureCode.RATE_LIMIT

    if result.exit_code == -1:
        text = (result.error or result.summary or "").lower()
        if "timed out" in text or "timeout" in text:
            return FailureCode.TIMEOUT
        if "cancel" in text:
            return FailureCode.USER_CANCELLED
        if "secret" in text or "credential" in text:
            return FailureCode.SECRET_DETECTED

    text = (result.error or result.summary or "").lower()
    if "not found on path" in text or "executable" in text:
        return FailureCode.UNAVAILABLE
    if any(k in text for k in ("ratelimit", "rate limit", "429", "quota", "token limit", "exhausted")):
        return FailureCode.RATE_LIMIT
    if "secret" in text or "credential" in text:
        return FailureCode.SECURITY_VIOLATION
    if "cancel" in text:
        return FailureCode.USER_CANCELLED
    if "policy" in text:
        return FailureCode.POLICY_VIOLATION
    return FailureCode.UNKNOWN


# ---------------------------------------------------------------------------
# Fallback policy
# ---------------------------------------------------------------------------

@dataclass
class FallbackPolicy:
    mode: str = "automatic"       # automatic | ask_before_fallback | disabled
    timeout_allowed: bool = False

    @classmethod
    def from_settings(cls) -> FallbackPolicy:
        from app.config.settings import settings

        return cls(
            mode=(settings.FALLBACK_MODE or "automatic").lower(),
            timeout_allowed=settings.FALLBACK_TIMEOUT_ALLOWED,
        )

    @property
    def enabled(self) -> bool:
        return self.mode in ("automatic", "ask_before_fallback")

    def candidates(self, primary: str) -> list[str]:
        """Fallback candidates after `primary`, in priority order."""
        from app.config.settings import settings

        if not self.enabled:
            return []
        order = [a.strip() for a in settings.AGENT_FALLBACK_ORDER.split(",") if a.strip()]
        if not order:
            return []
        seen = [primary.lower()]
        result: list[str] = []
        for name in order:
            low = name.lower()
            if low not in seen:
                result.append(low)
                seen.append(low)
        return result


AgentFactory = Callable[[str], object]


@dataclass
class AgentOutcome:
    result: AgentResult
    attempts: list[AgentAttemptRecord]
    used_fallback: bool
    code: str


class AgentRouter:
    """Runs the primary agent and falls back to candidates on retryable infra failures."""

    def __init__(
        self, db: Database, policy: FallbackPolicy | None = None
    ) -> None:
        self._db = db
        self._repo = AgentAttemptRepository(db)
        self._policy = policy or FallbackPolicy.from_settings()

    @property
    def policy(self) -> FallbackPolicy:
        return self._policy

    async def run(
        self,
        job_id: str,
        primary: str,
        context: AgentContext,
        workspace_path: Path,
        agent_factory: AgentFactory,
        timeout_secs: float,
        on_progress: ProgressCallback,
        progress: ProgressReporter | None = None,
    ) -> AgentOutcome:
        """Execute primary, then candidates if failures are retryable infra failures."""
        candidates = [primary, *self._policy.candidates(primary)]
        attempts: list[AgentAttemptRecord] = []
        used_fallback = False
        last_code = FailureCode.UNKNOWN

        for index, name in enumerate(candidates):
            attempt_id = await self._repo.start_attempt(job_id, index + 1, name, "cli")
            if progress is not None:
                await progress.report(
                    f"🤖 Running agent `{name}` (attempt {index + 1}/{len(candidates)})...",
                    phase="implementing",
                )

            try:
                agent = agent_factory(name)
                run_coro = _call_run(agent, context, workspace_path, on_progress)
                result = await asyncio.wait_for(run_coro, timeout=timeout_secs)
            except TimeoutError:
                await self._repo.finish_attempt(attempt_id, AgentAttemptStatus.TIMED_OUT, "exceeded runtime limit")
                attempts.append(await self._last_attempt(job_id))
                last_code = FailureCode.TIMEOUT
                if is_retryable(last_code, self._policy.timeout_allowed) and index < len(candidates) - 1:
                    used_fallback = True
                    await self._announce_fallback(name, candidates[index + 1], last_code, progress)
                    continue
                return AgentOutcome(
                    result=AgentResult(success=False, exit_code=-1, summary="Agent timed out", error="exceeded runtime limit"),
                    attempts=attempts,
                    used_fallback=used_fallback,
                    code=last_code,
                )
            except ValueError as exc:
                await self._repo.finish_attempt(attempt_id, AgentAttemptStatus.UNAVAILABLE, str(exc)[:300])
                attempts.append(await self._last_attempt(job_id))
                last_code = FailureCode.UNAVAILABLE
                if index < len(candidates) - 1:
                    used_fallback = True
                    await self._announce_fallback(name, candidates[index + 1], last_code, progress)
                    continue
                return AgentOutcome(
                    result=AgentResult(success=False, exit_code=-1, summary=f"Unknown agent {name}", error=str(exc)),
                    attempts=attempts,
                    used_fallback=used_fallback,
                    code=last_code,
                )
            except Exception as exc:  # noqa: BLE001
                await self._repo.finish_attempt(attempt_id, AgentAttemptStatus.FAILED, str(exc)[:300])
                attempts.append(await self._last_attempt(job_id))
                last_code = classify_failure(None, exc)
                if is_retryable(last_code, self._policy.timeout_allowed) and index < len(candidates) - 1:
                    used_fallback = True
                    await self._announce_fallback(name, candidates[index + 1], last_code, progress)
                    continue
                return AgentOutcome(
                    result=AgentResult(success=False, exit_code=-1, summary="Agent raised", error=str(exc)[:500]),
                    attempts=attempts,
                    used_fallback=used_fallback,
                    code=last_code,
                )

            if result.success:
                await self._repo.finish_attempt(attempt_id, AgentAttemptStatus.SUCCEEDED)
                attempts.append(await self._last_attempt(job_id))
                return AgentOutcome(result=result, attempts=attempts, used_fallback=used_fallback, code=FailureCode.UNKNOWN)

            await self._repo.finish_attempt(
                attempt_id,
                AgentAttemptStatus.FAILED,
                (result.error or result.summary)[:300] if (result.error or result.summary) else None,
            )
            attempts.append(await self._last_attempt(job_id))
            last_code = classify_failure(result, None)
            if is_retryable(last_code, self._policy.timeout_allowed) and index < len(candidates) - 1:
                used_fallback = True
                await self._announce_fallback(name, candidates[index + 1], last_code, progress)
                continue
            break

        return AgentOutcome(result=result, attempts=attempts, used_fallback=used_fallback, code=last_code)

    async def _last_attempt(self, job_id: str) -> AgentAttemptRecord:
        records = await self._repo.list_attempts(job_id)
        return records[-1] if records else AgentAttemptRecord(
            id=0, job_id=job_id, attempt_number=0, agent_name="?", transport=None,
            status=AgentAttemptStatus.FAILED, started_at=None, finished_at=None,
            error=None, created_at="",
        )

    async def _announce_fallback(
        self,
        from_agent: str,
        to_agent: str,
        code: str,
        progress: ProgressReporter | None,
    ) -> None:
        msg = (
            f"↩️ Agent `{from_agent}` failed with retryable infra failure "
            f"`{code}` — falling back to `{to_agent}`."
        )
        logger.warning(msg)
        if progress is not None:
            await progress.report(msg, level="WARNING", phase="implementing")


async def _call_run(agent, context, workspace_path, on_progress) -> AgentResult:
    from app.agents.base import BaseAgent

    if not isinstance(agent, BaseAgent):
        raise ValueError(f"agent factory returned unsupported object: {type(agent).__name__}")
    return await agent.run(context, workspace_path, on_progress)
