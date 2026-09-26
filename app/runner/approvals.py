"""
Human-in-the-Loop Approval Policies.

Three cooperating pieces:

1. `RiskClassifier` — deterministic LOW / MEDIUM / HIGH classification of the
   changed file set. Protected paths are always HIGH. AI analysis is never
   allowed to lower this deterministic risk (and no AI analysis is performed).

2. `ApprovalPolicy` — decides, from the configured mode and risk level,
   whether an operator approval must be obtained at the commit / push / PR gate.

3. `ApprovalCoordinator` — owns the in-memory + persisted approval lifecycle.
   Approvals are bound to a *diff fingerprint* (git state) and are
   auto-invalidated (STALE) if the code changes after approval. Double-decisions
   on the same approval are refused.

No Telegram imports: the coordinator exposes `request()` / `wait()` / `decide()`
and the handler layer maps callback data onto `decide()`.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from fnmatch import fnmatch
from typing import Any

from app.database.repository import (
    ApprovalRecord,
    ApprovalRepository,
    ApprovalStatus,
    Database,
)

logger = logging.getLogger(__name__)

# Approval gate stages
BEFORE_COMMIT = "before_commit"
BEFORE_PUSH = "before_push"
BEFORE_PR = "before_pr"

# Operator decision outcomes returned by decide() / wait()
APPROVED = "APPROVED"
REJECTED = "REJECTED"
STALE = "STALE"
EXPIRED = "EXPIRED"
ALREADY_DECIDED = "ALREADY_DECIDED"
UNKNOWN = "UNKNOWN"

# Risk levels
LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"

# Configurable fallback fingerprinter: async Callable[[], str].
FingerprintFn = Callable[[], Any]


def _looks_sensitive(path: str) -> bool:
    lowered = path.lower()
    sensitive_glob = (
        ".env*", "*id_rsa*", "*.pem", "*.key", "*.p12", "*.pfx",
        "*credential*", "*password*", "*.crt", "*.jks",
    )
    for glob in sensitive_glob:
        if fnmatch(lowered, glob) or fnmatch(lowered, "*" + glob):
            return True
    return False


def _looks_security_zone(path: str) -> bool:
    lowered = path.lower()
    for needle in ("security", "auth", "token", "secret", "credential"):
        if needle in lowered:
            return True
    return False


def _looks_dependency_manifest(path: str) -> bool:
    lowered = path.lower()
    names = (
        "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
        "cargo.toml", "package.json", "package-lock.json", "go.mod",
        "gemfile", "gemfile.lock", "pipfile", "pipfile.lock",
        "poetry.lock", "conda-*.yml", "environment.yml", ".pth", ".lock",
        "agent.db", "*.sqlite", "*.sqlite3",
    )
    return any(fnmatch(lowered, n) for n in names)


def _looks_ci_workflow(path: str) -> bool:
    lowered = path.lower()
    return ".github/workflows" in lowered or fnmatch(lowered, ".github/workflows/*")


def _looks_migration(path: str) -> bool:
    lowered = path.lower()
    # Windows/normalised path separators
    lowered = lowered.replace("\\", "/")
    for seg in ("migrations/", "/migrations", "alembic", "schemas/"):
        if seg in lowered:
            return True
    return False


class RiskClassifier:
    """Deterministic risk classification of a changed file set."""

    def __init__(self, protected_paths: list[str] | None = None) -> None:
        # Protected paths are always HIGH risk and can never be de-protected
        # by repository content.
        self._protected = protected_paths or []

    def classify(self, changed_files: list[str]) -> str:
        if not changed_files:
            return LOW
        for path in changed_files:
            if self.is_protected(path):
                return HIGH
            if _looks_sensitive(path) or _looks_security_zone(path):
                return HIGH
        for path in changed_files:
            if (
                _looks_dependency_manifest(path)
                or _looks_ci_workflow(path)
                or _looks_migration(path)
            ):
                return MEDIUM
        return LOW

    def is_protected(self, path: str) -> bool:
        norm = path.replace("\\", "/")
        for pattern in self._protected:
            pat = pattern.replace("\\", "/")
            if fnmatch(norm, pat) or fnmatch(norm, "*" + pat):
                return True
        return False


class ApprovalPolicy:
    """Decides whether a gate requires operator approval."""

    MODES = ("autonomous", "before_commit", "before_push", "before_pr")

    def __init__(self, mode: str | None = None) -> None:
        from app.config.settings import settings

        self._mode = (mode or settings.APPROVAL_POLICY).lower()
        if self._mode not in self.MODES:
            logger.warning(
                "Unknown approval policy mode %r, falling back to 'autonomous'", self._mode
            )
            self._mode = "autonomous"

    @property
    def mode(self) -> str:
        return self._mode

    def gate_required(self, stage: str, risk: str) -> bool:
        """True when the operator must approve this gate."""
        if self._mode == "autonomous":
            return False
        if risk == LOW:
            return False
        mode_gate = {
            "before_commit": BEFORE_COMMIT,
            "before_push": BEFORE_PUSH,
            "before_pr": BEFORE_PR,
        }[self._mode]
        return mode_gate == stage and risk in (MEDIUM, HIGH)


class _PendingApproval:
    __slots__ = ("event", "outcome")

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.outcome: str | None = None


async def _compute_fingerprint(workspace_path: str) -> str | None:
    """SHA-256 over the current git state (porcelain + HEAD + branch)."""
    import asyncio as _a

    cmd = (
        "git status --porcelain && git rev-parse HEAD && git symbolic-ref --short HEAD"
    )
    # Deliberately via argv, never via a shell.
    try:
        proc = await _a.create_subprocess_exec(
            "cmd", "/c", cmd,
            cwd=workspace_path,
            stdout=_a.subprocess.PIPE,
            stderr=_a.subprocess.PIPE,
        )
        stdout_b, _ = await _a.wait_for(proc.communicate(), timeout=20.0)
        if proc.returncode != 0:
            return None
        raw = stdout_b.decode(errors="replace")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fingerprint computation failed: %s", exc)
        return None


class ApprovalCoordinator:
    """Lifecycle owner for operator approval requests."""

    def __init__(
        self,
        db: Database,
        fingerprint_fn: FingerprintFn | None = None,
        approval_timeout: float = 30.0 * 60.0,
    ) -> None:
        self._db = db
        self._repo = ApprovalRepository(db)
        self._fingerprint_fn = fingerprint_fn
        self._pending: dict[int, _PendingApproval] = {}
        self._timeout = approval_timeout

    # ------------------------------------------------------------------
    # Request / wait / decide
    # ------------------------------------------------------------------

    async def request(
        self,
        job_id: str,
        stage: str,
        risk: str,
        workspace_path: str | None,
    ) -> ApprovalRecord:
        fingerprint = None
        if workspace_path:
            fingerprint = await self._compute_fingerprint(workspace_path)
        record = await self._repo.create(job_id, stage, risk, fingerprint)
        self._pending[record.id] = _PendingApproval()
        logger.info(
            "Approval requested: job=%s stage=%s risk=%s approval_id=%d",
            job_id, stage, risk, record.id,
        )
        return record

    async def wait(self, approval_id: int, timeout: float | None = None) -> str:
        """Wait until the operator (or expiry) resolves this approval."""
        pending = self._pending.get(approval_id)
        if pending is None:
            return UNKNOWN
        timeout = self._timeout if timeout is None else timeout
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=timeout)
        except TimeoutError:
            await self._expire(approval_id)
            return EXPIRED
        return pending.outcome or UNKNOWN

    async def decide(
        self,
        approval_id: int,
        decision: str,
        decision_by: int,
        workspace_path: str | None = None,
    ) -> str:
        """Operator presses Approve / Reject.

        Returns one of APPROVED / REJECTED / STALE / UNKNOWN / ALREADY_DECIDED.
        A second decision on the same approval is refused.
        """
        if decision not in (APPROVED, REJECTED):
            return UNKNOWN
        record = await self._repo.get(approval_id)
        if record is None:
            return UNKNOWN
        if record.status != ApprovalStatus.PENDING:
            return ALREADY_DECIDED

        # Code changed since the approval was requested → stale.
        if await self._fingerprint_stale(record, workspace_path):
            await self._repo.decide(approval_id, ApprovalStatus.STALE, decision_by, note=decision)
            self._resolve(approval_id, STALE)
            return STALE

        await self._repo.decide(approval_id, decision, decision_by)
        self._resolve(approval_id, decision)
        logger.info("Approval %d decided as %s by user %d", approval_id, decision, decision_by)
        return decision

    def is_pending(self, approval_id: int) -> bool:
        return approval_id in self._pending

    async def cancel_pending(self, job_id: str) -> None:
        """Cancel approvals still awaiting a decision (e.g. job stopped)."""
        await self._repo.cancel_pending(job_id)
        pending_ids = [aid for aid, p in self._pending.items() if not p.event.is_set()]
        for aid in pending_ids:
            self._resolve(aid, "CANCELLED")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve(self, approval_id: int, outcome: str) -> None:
        pending = self._pending.pop(approval_id, None)
        if pending is not None:
            pending.outcome = outcome
            pending.event.set()

    async def _expire(self, approval_id: int) -> None:
        await self._repo.decide(approval_id, ApprovalStatus.EXPIRED, 0, note="approval timeout")
        self._resolve(approval_id, EXPIRED)

    async def _fingerprint_stale(
        self, record: ApprovalRecord, workspace_path: str | None
    ) -> bool:
        if not record.diff_fingerprint or not workspace_path:
            return False
        current = await self._compute_fingerprint(workspace_path)
        return bool(current and current != record.diff_fingerprint)

    async def _compute_fingerprint(self, workspace_path: str) -> str | None:
        if self._fingerprint_fn is not None:
            result = self._fingerprint_fn()
            if asyncio.iscoroutine(result):
                return await result
            return str(result) if result is not None else None
        return await _compute_fingerprint(workspace_path)
