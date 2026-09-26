"""
Controller-Enforced Verification Pipeline.

The *controller* (this bot) executes verification checks against the agent's
workspace — never trust the agent's self-report. Checks are discovered only
from a trusted policy (env-configured JSON, or the built-in default); commands
are NEVER read from untrusted repository files.

Every check:
- runs as a subprocess with an explicit timeout (terminate → kill fallback),
- captures and sanitises (secret-redacted + truncated) output,
- persists a structured result in `verification_results`,
- and blocks commit/push/PR when a required check fails.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.database.repository import (
    Database,
    VerificationCheckStatus,
    VerificationRepository,
)
from app.runner.progress import ProgressReporter

logger = logging.getLogger(__name__)

# Hard cap on captured output per stream (bytes).
_MAX_CAPTURE_BYTES = 16 * 1024
# Hard cap on stored/displayed output (chars).
_MAX_OUTPUT_CHARS = 4096

_BLOCKING_STATUSES = {
    VerificationCheckStatus.FAIL,
    VerificationCheckStatus.TIMED_OUT,
    VerificationCheckStatus.ERROR,
}


@dataclass
class VerificationCheckConfig:
    name: str
    command: list[str]
    timeout: float = 60.0
    required: bool = True
    skip_on_previous_failure: bool = True


@dataclass
class VerificationCheckResult:
    name: str
    status: str
    required: bool
    started_at: str
    finished_at: str | None
    duration_ms: float | None
    exit_code: int | None
    output: str | None
    command_id: int | None = None

    @property
    def passed(self) -> bool:
        return self.status == VerificationCheckStatus.PASS


@dataclass
class VerificationResult:
    checks: list[VerificationCheckResult]
    blocking: bool = False

    def summary(self, limit: int = 500) -> str:
        parts = [f"Verification: {len(self.checks)} check(s) run"]
        for c in self.checks:
            icon = {
                VerificationCheckStatus.PASS: "✅",
                VerificationCheckStatus.FAIL: "❌",
                VerificationCheckStatus.TIMED_OUT: "⏰",
                VerificationCheckStatus.ERROR: "⚠️",
                VerificationCheckStatus.NOT_RUN: "⏭️",
            }.get(c.status, "❓")
            required = " (required)" if c.required else ""
            parts.append(f"{icon} `{c.name}` → `{c.status}`{required}")
        text = "\n".join(parts)
        return text[:limit]


def default_checks() -> list[VerificationCheckConfig]:
    """Built-in trusted default policy (used when no JSON policy is set)."""
    return [
        VerificationCheckConfig(
            name="git-diff-check",
            command=["git", "diff", "--check"],
            timeout=60.0,
            required=True,
        ),
    ]


def parse_checks(payload: str | None) -> list[VerificationCheckConfig]:
    """Parse the trusted JSON policy from settings.

    Accepted schema (list of objects):
        [{"name": "...", "command": ["cmd", "--arg"], "timeout": 60,
          "required": true, "skip_on_previous_failure": true}]

    Never used with a shell — every item is an explicit argv list.
    """
    if not payload or not payload.strip():
        return default_checks()
    try:
        raw = json.loads(payload)
    except ValueError as exc:
        logger.warning("Invalid VERIFICATION_CHECKS_JSON, using defaults: %s", exc)
        return default_checks()
    if not isinstance(raw, list) or not raw:
        logger.warning("VERIFICATION_CHECKS_JSON is not a list, using defaults")
        return default_checks()

    checks: list[VerificationCheckConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        command = item.get("command")
        if not name or not isinstance(command, list) or not command:
            logger.warning("Ignoring malformed verification check entry: %r", item)
            continue
        if not all(isinstance(a, str) and a for a in command):
            logger.warning("Ignoring verification check with non-string argv: %r", item)
            continue
        checks.append(
            VerificationCheckConfig(
                name=name,
                command=[str(a) for a in command],
                timeout=float(item.get("timeout") or 60.0),
                required=bool(item.get("required", True)),
                skip_on_previous_failure=bool(
                    item.get("skip_on_previous_failure", True)
                ),
            )
        )
    return checks or default_checks()


def _redact(text: str) -> str:
    """Secret-redact then truncate command output."""
    from app.utils.secrets import _redact_line

    redacted = _redact_line(text)
    if len(redacted) > _MAX_OUTPUT_CHARS:
        return redacted[:_MAX_OUTPUT_CHARS] + "\n...[truncated]"
    return redacted


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class VerificationPipeline:
    """Runs the configured verification checks against a workspace."""

    def __init__(
        self,
        db: Database,
        checks: list[VerificationCheckConfig] | None = None,
    ) -> None:
        self._db = db
        self._checks = checks
        self._repo = VerificationRepository(db)

    async def run(
        self,
        job_id: str,
        workspace_path: Path,
        progress: ProgressReporter | None = None,
    ) -> VerificationResult:
        checks = self._checks if self._checks is not None else self._load_trusted_checks()
        results: list[VerificationCheckResult] = []
        previous_failed = False

        for idx, check in enumerate(checks, start=1):
            if check.skip_on_previous_failure and previous_failed:
                started = _now_iso()
                result = VerificationCheckResult(
                    name=check.name,
                    status=VerificationCheckStatus.NOT_RUN,
                    required=check.required,
                    started_at=started,
                    finished_at=started,
                    duration_ms=0.0,
                    exit_code=None,
                    output=None,
                    command_id=idx,
                )
                await self._persist(job_id, result)
                results.append(result)
                continue

            if progress is not None:
                await progress.report(
                    f"🔎 Running verification check `{check.name}`...",
                    phase=f"verify:{check.name}",
                )
            result = await self._run_check(workspace_path, check, idx)
            results.append(result)
            await self._persist(job_id, result)
            if result.status in _BLOCKING_STATUSES and check.required:
                previous_failed = True

        blocking = any(
            r.status in _BLOCKING_STATUSES and r.required for r in results
        )
        return VerificationResult(checks=results, blocking=blocking)

    def _load_trusted_checks(self) -> list[VerificationCheckConfig]:
        from app.config.settings import settings

        return parse_checks(settings.VERIFICATION_CHECKS_JSON)

    async def _run_check(
        self, workspace_path: Path, check: VerificationCheckConfig, command_id: int
    ) -> VerificationCheckResult:
        started = _now_iso()
        timeout = max(1.0, check.timeout)
        proc = await asyncio.create_subprocess_exec(
            *check.command,
            cwd=str(workspace_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            await self._terminate(proc)
            try:
                exit_code = proc.returncode
            except Exception:  # pragma: no cover
                exit_code = None
            finished = _now_iso()
            return VerificationCheckResult(
                name=check.name,
                status=VerificationCheckStatus.TIMED_OUT,
                required=check.required,
                started_at=started,
                finished_at=finished,
                duration_ms=timeout * 1000.0,
                exit_code=exit_code,
                output=f"[timed out after {timeout:g}s]",
                command_id=command_id,
            )
        except asyncio.CancelledError:
            await self._terminate(proc)
            raise

        finished = _now_iso()
        duration_ms = _elapsed_ms(started, finished)
        stdout = stdout_b[:_MAX_CAPTURE_BYTES].decode(errors="replace")
        stderr = stderr_b[:_MAX_CAPTURE_BYTES].decode(errors="replace")
        output = _redact((stdout + "\n" + stderr).strip() or "✔ (no output)")

        if proc.returncode == 0:
            status = VerificationCheckStatus.PASS
        else:
            status = VerificationCheckStatus.FAIL
        return VerificationCheckResult(
            name=check.name,
            status=status,
            required=check.required,
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            exit_code=proc.returncode,
            output=output,
            command_id=command_id,
        )

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass

    async def _persist(self, job_id: str, result: VerificationCheckResult) -> None:
        await self._repo.save_result(
            job_id=job_id,
            check_name=result.name,
            status=result.status,
            required=result.required,
            started_at=result.started_at,
            finished_at=result.finished_at,
            duration_ms=result.duration_ms,
            exit_code=result.exit_code,
            output=result.output,
            command_id=result.command_id,
        )


def _elapsed_ms(started_iso: str, finished_iso: str) -> float:
    try:
        start = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(finished_iso.replace("Z", "+00:00"))
        return (end - start).total_seconds() * 1000.0
    except ValueError:
        return 0.0
